"""Uçtan uca pipeline (asyncio): kare → ROI → değişim tespiti → OCR → stabilizasyon → çeviri.

Qt'den bağımsızdır; sonuçlar ``emit`` geri çağrısıyla olay olarak bildirilir. Böylece
aynı çekirdek hem overlay uygulamasında hem de başsız replay testinde çalışır.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np
from loguru import logger

from .capture.base import Frame, FrameSource
from .config import Rect, Settings
from .detect import ChangeDetector, bright_mask
from .metrics import Metrics
from .ocr import OcrEngine, OcrResult, read_roi
from .stabilize import Stabilizer
from .translate import Router, TranslationResult, cache_key


@dataclass(frozen=True, slots=True)
class ShowEvent:
    """Overlay'de gösterilecek (veya güncellenecek) çeviri."""

    job_id: int
    source: str  # İngilizce (OCR)
    text: str  # Türkçe
    layer: str  # cache | cloud | local | fallback
    partial: bool
    first: bool  # bu satır için ekrana giden ilk sonuç mu
    speaker: str
    bbox: tuple[int, int, int, int] | None  # orijinal metnin ROI içindeki kutusu (px)
    roi_size: tuple[int, int]  # ROI (w, h) px
    line_count: int
    line_height: float
    latency_ms: float  # altyazının belirmesi → bu sonucun gösterimi
    stable_ms: float  # belirme → "kararlı" kararı
    translate_ms: float  # çeviri isteği → bu sonuç
    ocr_ms: float
    media_ts: float | None = None  # video kaynağında satırın belirdiği an (sn)
    extends_previous: bool = False


@dataclass(frozen=True, slots=True)
class HideEvent:
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StatsEvent:
    snapshot: dict
    mode: str
    preview: np.ndarray | None = None  # BGR, küçültülmüş ROI önizlemesi
    last_source: str = ""


@dataclass(frozen=True, slots=True)
class ManualEvent:
    """Test panelinden elle gönderilen bir satırın çeviri sonucu."""

    source: str
    result: TranslationResult | None  # None + done: hiçbir katman yanıt vermedi
    done: bool = False


Event = ShowEvent | HideEvent | StatsEvent | ManualEvent


@dataclass(slots=True)
class _Job:
    id: int
    text: str
    change_ts: float
    started: float
    ocr: OcrResult
    roi_size: tuple[int, int]
    media_ts: float | None = None
    visible: bool = False
    stable_ts: float = 0.0
    latest: TranslationResult | None = None
    emitted: bool = False
    speaker: str = ""
    extends_previous: bool = False
    task: asyncio.Task | None = field(default=None, repr=False)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        source: FrameSource,
        ocr: OcrEngine,
        router: Router,
        metrics: Metrics,
        emit: Callable[[Event], None],
    ) -> None:
        self.settings = settings
        self.source = source
        self.ocr = ocr
        self.router = router
        self.metrics = metrics
        self._emit = emit

        self.detector = ChangeDetector(settings.detect)
        self.stabilizer = Stabilizer(settings.stabilize)
        # UI thread'inden atomik olarak değiştirilen durum (tek atama)
        self._roi: Rect = settings.roi.subtitle
        self._speaker_roi: Rect | None = settings.roi.speaker
        self._paused = False
        self.preview_enabled = False
        self._stop = False

        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._job: _Job | None = None  # ekrandaki (veya ekrana gidecek) satır
        self._spec: _Job | None = None  # spekülatif (henüz kararlı olmayan) satır
        self._next_id = 0
        self._unsettled = False
        self._change_ts: float | None = None
        self._change_media_ts: float | None = None
        self._empty_since: float | None = None
        self._last_ocr = 0.0
        self._ocr_result: OcrResult | None = None  # kararsız dönemdeki son OCR
        self._ocr_snapshot: np.ndarray | None = None  # o OCR'ın yapıldığı maskenin kopyası
        self._last_roi: np.ndarray | None = None
        self._last_mask: np.ndarray | None = None
        self._last_source = ""
        self._ocr_error_at = 0.0
        self._manual_tasks: set[asyncio.Task] = set()
        self.frames_processed = 0

    # --------------------------------------------------- UI thread'inden kontrol
    def set_roi(self, subtitle: Rect, speaker: Rect | None) -> None:
        self._roi = subtitle
        self._speaker_roi = speaker
        self._call_soon(self._reset_state)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self._call_soon(self._reset_state)

    def cycle_mode(self) -> None:
        self._call_soon(self.router.cycle_mode)

    def set_mode(self, mode: str) -> None:
        def apply() -> None:
            self.router.mode = mode

        self._call_soon(apply)

    def translate_manual(self, text: str) -> None:
        """Verilen metni yönlendiriciden geçirir; sonuçlar ``ManualEvent`` olarak bildirilir."""

        def start() -> None:
            task = asyncio.create_task(self._manual(text))
            self._manual_tasks.add(task)
            task.add_done_callback(self._manual_tasks.discard)

        self._call_soon(start)

    async def _manual(self, text: str) -> None:
        final = None
        try:
            final = await self.router.translate(text, lambda r: self._emit(ManualEvent(text, r)))
        except Exception:
            logger.exception("Manuel çeviri hatası")
        self._emit(ManualEvent(text, final, done=True))

    def stop(self) -> None:
        self._stop = True
        self._call_soon(lambda: None)

    def _call_soon(self, fn: Callable[[], object]) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._run_and_wake, fn)
        except RuntimeError:  # döngü kapandı
            pass

    def _run_and_wake(self, fn: Callable[[], object]) -> None:
        fn()
        if self._wake is not None:
            self._wake.set()

    def _notify_frame(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is None or wake is None:
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------ döngü
    async def run(self, warm_up: bool = True) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self.source.latest.set_notifier(self._notify_frame)
        background: list[asyncio.Task] = [asyncio.create_task(self._stats_loop())]
        if warm_up:
            background.append(asyncio.create_task(self.router.warm_up()))
        try:
            while not self._stop:
                frame = self.source.latest.take()
                if frame is None:
                    if self.source.finished.is_set():
                        break
                    self._wake.clear()
                    frame = self.source.latest.take()
                    if frame is None:
                        try:
                            await asyncio.wait_for(self._wake.wait(), timeout=0.25)
                        except asyncio.TimeoutError:
                            pass
                        continue
                if not self._paused:
                    try:
                        await self._process(frame)
                    except Exception:  # tek karelik hata tüm döngüyü öldürmesin
                        logger.exception("Kare işlenirken hata")
                await asyncio.sleep(0)  # çeviri task'larına sıra ver
        finally:
            self.source.latest.set_notifier(None)
            self._cancel_job(self._job)
            self._cancel_job(self._spec)
            self._job = self._spec = None
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            self._emit(HideEvent("stop"))

    async def drain(self, timeout: float = 3.0) -> None:
        """Devam eden çevirinin bitmesini bekler (replay testi sonu için)."""
        job = self._job
        if job is not None and job.task is not None and not job.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(job.task), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def _stats_loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            self.metrics.set_value("dropped", str(self.source.latest.dropped))
            preview = None
            if self.preview_enabled and self._last_roi is not None:
                preview = self._render_preview(self._last_roi, self._last_mask)
            self._emit(StatsEvent(self.metrics.snapshot(), self.router.mode, preview, self._last_source))

    @staticmethod
    def _render_preview(roi: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        """ROI + maske (yeşil) önizlemesi, en fazla 640 px genişlik."""
        h, w = roi.shape[:2]
        scale = min(1.0, 640 / w)
        size = (max(1, int(w * scale)), max(1, int(h * scale)))
        view = cv2.resize(roi, size, interpolation=cv2.INTER_AREA)
        if mask is not None and mask.shape[:2] == (h, w):
            small_mask = cv2.resize(mask, size, interpolation=cv2.INTER_AREA) > 96
            view = view.copy()
            view[small_mask] = (view[small_mask] * 0.3 + np.array([0, 255, 0]) * 0.7).astype(np.uint8)
        return view

    # ----------------------------------------------------------------- durum
    def _reset_state(self) -> None:
        self._cancel_job(self._job)
        self._cancel_job(self._spec)
        self._job = self._spec = None
        self.detector.reset()
        self.stabilizer.reset()
        self._unsettled = False
        self._change_ts = None
        self._empty_since = None
        self._emit(HideEvent("reset"))

    @staticmethod
    def _cancel_job(job: _Job | None) -> None:
        if job is not None and job.task is not None and not job.task.done():
            job.task.cancel()

    def _hide(self, reason: str) -> None:
        had_visible = self._job is not None
        self._cancel_job(self._job)
        self._cancel_job(self._spec)
        self._job = self._spec = None
        self.stabilizer.clear_shown()
        if had_visible:
            self.metrics.incr("hides")
        self._emit(HideEvent(reason))

    # -------------------------------------------------------------- kare işle
    async def _process(self, frame: Frame) -> None:
        cfg = self.settings
        image = frame.image
        height, width = image.shape[:2]
        x, y, w, h = self._roi.to_pixels(width, height)
        roi = image[y : y + h, x : x + w]
        if roi.ndim == 3 and roi.shape[2] == 4:
            roi = roi[:, :, :3]

        t0 = time.perf_counter()
        det = self.detector.update(roi)
        self.metrics.record("detect_ms", (time.perf_counter() - t0) * 1000.0)
        self.metrics.tick("fps")
        self.frames_processed += 1
        self._last_roi, self._last_mask = roi, det.mask
        now = frame.ts

        if det.empty:
            # Altyazı yok: kısa bir gecikmeyle (fade/geçiş titremesine karşı) gizle
            self._unsettled = False
            self._change_ts = None
            self._ocr_result = None
            self._ocr_snapshot = None
            if self._empty_since is None:
                self._empty_since = now
            if self._spec is not None:
                self._cancel_job(self._spec)
                self._spec = None
            showing = self._job is not None or self.stabilizer.shown is not None
            if showing and (now - self._empty_since) * 1000.0 >= cfg.detect.hide_delay_ms:
                self._hide("empty")
            return
        self._empty_since = None

        if det.changed:
            if not self._unsettled:
                self._change_ts = now
                self._change_media_ts = frame.media_ts
            self._unsettled = True
        if not self._unsettled:
            return
        prior = self._ocr_result
        if (
            cfg.stabilize.fast_confirm
            and prior is not None
            and prior.text
            and self.detector.distance(self._ocr_snapshot) <= cfg.stabilize.fast_confirm_diff
        ):
            # Görüntü, az önce OCR'lanan kareyle (neredeyse) aynı: metin de aynıdır.
            # İkinci OCR'ı beklemeden "ardışık görülme" say → ~1 kare erken kararlı.
            result = prior
            self.metrics.incr("fast_confirms")
        else:
            if time.perf_counter() - self._last_ocr < 1.0 / max(cfg.ocr.max_hz, 1.0):
                return
            # ---- OCR (yalnızca değişim sonrası, kararlı olana dek, ≤ max_hz)
            self._last_ocr = time.perf_counter()
            try:
                result = await read_roi(self.ocr, roi, det.mask, cfg.ocr)
            except Exception as exc:  # noqa: BLE001
                if time.monotonic() - self._ocr_error_at > 5.0:
                    self._ocr_error_at = time.monotonic()
                    logger.error("OCR hatası: {}: {}", type(exc).__name__, exc)
                result = OcrResult(text="")
            self.metrics.record("ocr_ms", result.ms)
            self.metrics.tick("ocr")
            self._ocr_result = result
            self._ocr_snapshot = self.detector.snapshot()

        stab = self.stabilizer.feed(result.text or None)
        change_ts = self._change_ts if self._change_ts is not None else now
        if stab.kind == "candidate":
            if cfg.stabilize.speculative:
                self._cancel_job(self._spec)
                self._spec = self._new_job(stab.text, result, (w, h), change_ts)
        elif stab.kind == "stable":
            await self._on_stable(stab.text, stab.extends_previous, result, (w, h), change_ts, image)
        elif stab.kind == "gone":
            self._hide("gone")
        elif stab.kind == "same" and self._job is not None:
            self._job.ocr = result  # kutu konumunu güncel tut
        if stab.settled:
            self._unsettled = False
            self._change_ts = None
            self._ocr_result = None
            self._ocr_snapshot = None

    def _new_job(self, text: str, ocr: OcrResult, roi_size: tuple[int, int], change_ts: float) -> _Job:
        self._next_id += 1
        job = _Job(
            id=self._next_id, text=text, change_ts=change_ts, started=time.perf_counter(),
            ocr=ocr, roi_size=roi_size, media_ts=self._change_media_ts,
        )
        job.task = asyncio.create_task(self._run_job(job))
        return job

    async def _on_stable(
        self,
        text: str,
        extends_previous: bool,
        ocr: OcrResult,
        roi_size: tuple[int, int],
        change_ts: float,
        image: np.ndarray,
    ) -> None:
        spec, self._spec = self._spec, None
        if spec is not None and cache_key(spec.text) == cache_key(text):
            job = spec  # önden başlatılan çeviri aynı metin içindi: devral
            job.ocr = ocr
        else:
            self._cancel_job(spec)
            job = self._new_job(text, ocr, roi_size, change_ts)
        # eski satırın bekleyen istekleri iptal
        self._cancel_job(self._job)
        self._job = job
        job.visible = True
        job.extends_previous = extends_previous
        job.stable_ts = time.perf_counter()
        self._last_source = text
        self.metrics.record("stable_ms", (job.stable_ts - job.change_ts) * 1000.0)
        self.metrics.incr("lines")
        logger.debug("Kararlı altyazı #{}: {!r}", job.id, text)

        if self._speaker_roi is not None:
            job.speaker = await self._read_speaker(image)

        if job.latest is not None:
            self._emit_show(job, job.latest)
        else:
            # Çeviri gelene dek eski Türkçe metin yeni İngilizcenin üstünde kalmasın
            self._emit(HideEvent("pending"))

    async def _read_speaker(self, image: np.ndarray) -> str:
        assert self._speaker_roi is not None
        height, width = image.shape[:2]
        x, y, w, h = self._speaker_roi.to_pixels(width, height)
        crop = image[y : y + h, x : x + w]
        if crop.ndim == 3 and crop.shape[2] == 4:
            crop = crop[:, :, :3]
        try:
            mask = bright_mask(crop, self.settings.detect)
            result = await read_roi(self.ocr, crop, mask, self.settings.ocr)
        except Exception:  # noqa: BLE001
            return ""
        return result.text.rstrip(":").strip()

    async def _run_job(self, job: _Job) -> None:
        try:
            await self.router.translate(job.text, lambda result: self._on_result(job, result))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Çeviri hatası")
        if job.latest is None and job.visible and self._job is job:
            logger.warning("Çevrilemedi (hiçbir katman yanıt vermedi): {!r}", job.text)

    def _on_result(self, job: _Job, result: TranslationResult) -> None:
        job.latest = result
        if job.visible and self._job is job:
            self._emit_show(job, result)

    def _emit_show(self, job: _Job, result: TranslationResult) -> None:
        now = time.perf_counter()
        latency = (now - job.change_ts) * 1000.0
        first = not job.emitted
        job.emitted = True
        if first:
            self.metrics.record("first_text_ms", latency)
            self.metrics.record("latency_cache_ms" if result.layer == "cache" else "latency_new_ms", latency)
        if not result.partial:
            self.metrics.set_value("layer", result.layer)
        self._emit(
            ShowEvent(
                job_id=job.id,
                source=job.text,
                text=result.text,
                layer=result.layer,
                partial=result.partial,
                first=first,
                speaker=job.speaker,
                bbox=job.ocr.bbox,
                roi_size=job.roi_size,
                line_count=max(1, len(job.ocr.lines)),
                line_height=job.ocr.line_height,
                latency_ms=latency,
                stable_ms=(job.stable_ts - job.change_ts) * 1000.0,
                translate_ms=result.ms,
                ocr_ms=job.ocr.ms,
                media_ts=job.media_ts,
                extends_previous=job.extends_previous,
            )
        )
