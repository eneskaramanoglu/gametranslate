"""Çeviri yönlendirici: cache → (bulut ‖ yerel yarış) → yedek, devre kesici ile.

``translate`` tek bir satır için çalışır ve sonuçları ``emit`` ile bildirir (aynı
satır için birden çok kez çağrılabilir: kısmi akış, sonra yerel, sonra bulut).
Yeni satır geldiğinde çağıran taraf task'ı iptal eder; bekleyen istekler de iptal olur.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Callable

from loguru import logger

from ..config import CacheConfig, TranslateConfig
from ..metrics import Metrics
from .base import TranslationError, TranslationResult, Translator
from .cache import TranslationCache
from .prompts import Glossary

Emit = Callable[[TranslationResult], None]
MODES = ("race", "cloud_first", "local_only")
MODE_LABELS = {"race": "yarış", "cloud_first": "bulut", "local_only": "yerel"}


class CircuitBreaker:
    """Ardışık ``failures`` hatadan sonra ``cooldown`` sn açık kalır; sonra tek bir
    deneme isteğine (half-open) izin verir. Başarılıysa kapanır, değilse yeniden açılır."""

    def __init__(
        self, failures: int = 2, cooldown: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.failures = max(1, failures)
        self.cooldown = cooldown
        self._clock = clock
        self._count = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._probing or self._clock() - self._opened_at >= self.cooldown:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        """İstek gönderilebilir mi? Half-open'da yalnızca tek deneme ayrılır."""
        if self._opened_at is None:
            return True
        if self._probing:
            return False
        if self._clock() - self._opened_at >= self.cooldown:
            self._probing = True
            return True
        return False

    def success(self) -> None:
        self._count = 0
        self._opened_at = None
        self._probing = False

    def failure(self) -> None:
        self._count += 1
        if self._probing or self._count >= self.failures:
            self._opened_at = self._clock()
        self._probing = False

    def release(self) -> None:
        """İstek sonuçlanmadan iptal edildi: hata sayma, deneme hakkını geri ver."""
        self._probing = False


class Router:
    def __init__(
        self,
        cfg: TranslateConfig,
        cache: TranslationCache,
        cloud: Translator | None,
        local: Translator | None,
        fallback: Translator | None = None,
        glossary: Glossary | None = None,
        metrics: Metrics | None = None,
        cache_cfg: CacheConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.cache = cache
        self.cloud = cloud
        self.local = local
        self.fallback = fallback
        self.glossary = glossary or Glossary()
        self.metrics = metrics or Metrics()
        self.cache_local = cache_cfg.cache_local_results if cache_cfg else True
        self.mode = cfg.mode
        self.breaker = CircuitBreaker(cfg.breaker_failures, cfg.breaker_cooldown_s, clock)
        self.simulate_offline = False
        self._context: deque[tuple[str, str]] = deque(maxlen=max(0, cfg.context_lines))
        self._last_breaker_state = "closed"

    # ------------------------------------------------------------------ durum
    def cycle_mode(self) -> str:
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        logger.info("Çeviri modu: {}", MODE_LABELS[self.mode])
        return self.mode

    def _cloud_configured(self) -> bool:
        return self.simulate_offline or (self.cloud is not None and self.cloud.available)

    def _local_usable(self) -> bool:
        return self.local is not None and self.local.available

    def _note_breaker(self) -> None:
        state = self.breaker.state
        if state != self._last_breaker_state:
            if state == "open":
                logger.warning("Bulut devre dışı (devre kesici açık): {} sn yalnızca yerel", self.breaker.cooldown)
            elif state == "closed":
                logger.info("Bulut yeniden kullanılabilir (devre kesici kapandı)")
            self._last_breaker_state = state
        self.metrics.set_value("breaker", state)

    def _remember(self, source: str, translation: str) -> None:
        if self._context.maxlen == 0:
            return
        if self._context and self._context[-1][0] == source:
            self._context[-1] = (source, translation)
        else:
            self._context.append((source, translation))

    def _finalize(self, source: str, translation: str) -> str:
        fixed, issues = self.glossary.enforce(source, translation)
        if issues:
            self.metrics.incr("glossary.unfixed")
            logger.debug("Sözlük: çeviride korunamayan isimler {}: {!r}", issues, translation)
        return fixed

    # --------------------------------------------------------------- katmanlar
    async def _run_cloud(self, text: str, on_partial: Callable[[str], None] | None) -> str | None:
        """Bulut isteği; zaman aşımı/hata devre kesiciye işlenir. Başarısızsa None."""
        start = time.perf_counter()
        try:
            if self.simulate_offline:
                await asyncio.sleep(0.05)
                raise TranslationError("simüle edilen ağ kesintisi")
            assert self.cloud is not None
            out = await asyncio.wait_for(
                self.cloud.translate(text, list(self._context), on_partial),
                timeout=self.cfg.cloud_timeout_ms / 1000.0,
            )
        except asyncio.CancelledError:
            self.breaker.release()
            raise
        except (TranslationError, asyncio.TimeoutError) as exc:
            self.breaker.failure()
            self.metrics.incr("cloud.errors")
            reason = "zaman aşımı" if isinstance(exc, asyncio.TimeoutError) else str(exc)
            logger.debug("Bulut hatası: {}", reason)
            self._note_breaker()
            return None
        self.breaker.success()
        self._note_breaker()
        self.metrics.record("translate.cloud_ms", (time.perf_counter() - start) * 1000.0)
        return self._finalize(text, out)

    async def _run_local(self, text: str) -> str | None:
        start = time.perf_counter()
        try:
            assert self.local is not None
            out = await self.local.translate(text, list(self._context), None)
        except TranslationError as exc:
            self.metrics.incr("local.errors")
            logger.debug("Yerel hata: {}", exc)
            return None
        self.metrics.record("translate.local_ms", (time.perf_counter() - start) * 1000.0)
        return self._finalize(text, out)

    async def _run_fallback(self, text: str) -> str | None:
        if self.fallback is None or not self.fallback.available:
            return None
        start = time.perf_counter()
        try:
            out = await self.fallback.translate(text, [], None)
        except TranslationError as exc:
            logger.debug("Yedek hata: {}", exc)
            return None
        self.metrics.record("translate.fallback_ms", (time.perf_counter() - start) * 1000.0)
        return self._finalize(text, out)

    # ------------------------------------------------------------------- ana
    async def translate(self, text: str, emit: Emit) -> TranslationResult | None:
        """Satırı çevirir; her (kısmi/tam) sonucu ``emit`` ile bildirir, son sonucu döndürür."""
        t0 = time.perf_counter()

        def elapsed() -> float:
            return (time.perf_counter() - t0) * 1000.0

        def model_of(translator: Translator | None) -> str:
            return getattr(translator, "model", "") if translator is not None else ""

        # Katman 0: cache
        entry = self.cache.get(text)
        if entry is not None:
            result = TranslationResult(entry.text, "cache", elapsed(), model=entry.model)
            self.metrics.incr("layer.cache")
            emit(result)
            self._remember(text, entry.text)
            # Düşük kaliteli (yerel/yedek) kayıt: bulut erişilebilirse sessizce yükselt
            if (
                entry.layer != "cloud"
                and self.mode != "local_only"
                and self._cloud_configured()
                and self.breaker.allow()
            ):
                upgraded = await self._run_cloud(text, None)
                if upgraded is not None and upgraded != entry.text:
                    result = TranslationResult(upgraded, "cloud", elapsed(), model=model_of(self.cloud))
                    self.cache.put(text, upgraded, "cloud", result.model)
                    self._remember(text, upgraded)
                    emit(result)
                elif upgraded is not None:
                    self.cache.put(text, upgraded, "cloud", model_of(self.cloud))
            return result

        self.metrics.incr("cache.miss")
        use_cloud = self.mode != "local_only" and self._cloud_configured() and self.breaker.allow()
        self._note_breaker()
        best: TranslationResult | None = None

        def on_cloud_partial(partial: str) -> None:
            # Tam bir yerel çeviri gösteriliyorsa onu yarım bulut metniyle bozma
            if best is None:
                emit(TranslationResult(partial, "cloud", elapsed(), partial=True, model=model_of(self.cloud)))

        if self.mode == "race" and use_cloud and self._local_usable():
            tasks: dict[asyncio.Task[str | None], str] = {
                asyncio.create_task(self._run_cloud(text, on_cloud_partial)): "cloud",
                asyncio.create_task(self._run_local(text)): "local",
            }
            try:
                pending = set(tasks)
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    # aynı anda bittiyse önce bulutu işle
                    for task in sorted(done, key=lambda t: tasks[t] != "cloud"):
                        out = task.result()
                        if out is None:
                            continue
                        layer = tasks[task]
                        if layer == "cloud":
                            best = TranslationResult(out, "cloud", elapsed(), model=model_of(self.cloud))
                            emit(best)
                            for other in pending:
                                other.cancel()
                            pending = set()
                            break
                        if best is None:
                            best = TranslationResult(out, "local", elapsed(), model=model_of(self.local))
                            emit(best)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
        else:
            if use_cloud:
                out = await self._run_cloud(text, on_cloud_partial)
                if out is not None:
                    best = TranslationResult(out, "cloud", elapsed(), model=model_of(self.cloud))
                    emit(best)
            if best is None and self._local_usable():
                out = await self._run_local(text)
                if out is not None:
                    best = TranslationResult(out, "local", elapsed(), model=model_of(self.local))
                    emit(best)

        if best is None:
            out = await self._run_fallback(text)
            if out is not None:
                best = TranslationResult(out, "fallback", elapsed(), model=model_of(self.fallback))
                emit(best)

        if best is None:
            self.metrics.incr("translate.failed")
            return None

        self.metrics.incr(f"layer.{best.layer}")
        if best.layer == "cloud" or self.cache_local:
            self.cache.put(text, best.text, best.layer, best.model)
        self._remember(text, best.text)
        return best

    async def warm_up(self) -> None:
        """Yerel modeli belleğe al, bulut bağlantısını ısıt (paralel)."""
        jobs = []
        for translator in (self.local, self.cloud, self.fallback):
            if translator is not None:
                jobs.append(translator.warm_up())
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def close(self) -> None:
        for translator in (self.cloud, self.local, self.fallback):
            if translator is not None:
                try:
                    await translator.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Kapanış hatası: {}", exc)
        self.cache.close()
