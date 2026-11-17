"""Katman 2 — yerel LLM: Ollama + TranslateGemma (offline çalışır)."""

from __future__ import annotations

import json
import time

import httpx
from loguru import logger

from ..config import OllamaConfig
from .base import OnPartial, TranslationError
from .prompts import (
    Context,
    Glossary,
    build_system_prompt,
    build_translategemma_prompt,
    build_user_prompt,
    clean_output,
)

REPROBE_SECONDS = 20.0


class OllamaTranslator:
    layer = "local"

    def __init__(self, cfg: OllamaConfig, glossary: Glossary | None = None) -> None:
        self.cfg = cfg
        self.model = cfg.model
        self.glossary = glossary
        self._client: httpx.AsyncClient | None = None
        self._ready = False
        self._next_probe = 0.0
        self._warned = False

    @property
    def available(self) -> bool:
        # Hazır değilse bile, yeniden yoklama zamanı geldiyse bir isteğe izin ver
        return self.cfg.enabled and (self._ready or time.monotonic() >= self._next_probe)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.cfg.url,
                timeout=httpx.Timeout(self.cfg.timeout_ms / 1000.0, connect=0.5),
            )
        return self._client

    def _mark_down(self, reason: str) -> None:
        self._ready = False
        self._next_probe = time.monotonic() + REPROBE_SECONDS
        if not self._warned:
            self._warned = True
            logger.warning(
                "Ollama kullanılamıyor ({}). Kurulum: https://ollama.com → `ollama pull {}`. "
                "{} sn'de bir yeniden denenecek.",
                reason, self.model, int(REPROBE_SECONDS),
            )

    async def warm_up(self) -> None:
        """Modeli belleğe yükle ve ``keep_alive`` süresince orada tut."""
        if not self.cfg.enabled:
            return
        try:
            response = await self._http().post(
                "/api/generate",
                json={"model": self.model, "keep_alive": self.cfg.keep_alive},
                timeout=httpx.Timeout(120.0, connect=1.0),  # ilk yükleme uzun sürebilir
            )
            if response.status_code == 404:
                self._mark_down(f"model '{self.model}' indirilmemiş")
                return
            response.raise_for_status()
            # ilk token gecikmesini de ısıt
            self._ready = True
            await self.translate("Hello.", [])
            self._warned = False
            logger.info("Ollama hazır ({})", self.model)
        except (httpx.HTTPError, TranslationError) as exc:
            self._mark_down(type(exc).__name__)

    def _messages(self, text: str, context: Context) -> list[dict[str, str]]:
        if "translategemma" in self.model.lower():
            # TranslateGemma kendi prompt formatını bekler; sistem mesajı/bağlam yok
            return [{"role": "user", "content": build_translategemma_prompt(text)}]
        return [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt(text, context, self.glossary)},
        ]

    async def translate(self, text: str, context: Context, on_partial: OnPartial | None = None) -> str:
        if not self.cfg.enabled:
            raise TranslationError("Ollama devre dışı")
        payload = {
            "model": self.model,
            "messages": self._messages(text, context),
            "stream": True,
            "keep_alive": self.cfg.keep_alive,
            "options": {"temperature": 0.0, "num_predict": self.cfg.num_predict},
        }
        acc = ""
        try:
            async with self._http().stream("POST", "/api/chat", json=payload) as response:
                if response.status_code == 404:
                    self._mark_down(f"model '{self.model}' indirilmemiş")
                    raise TranslationError(f"Ollama: model '{self.model}' bulunamadı")
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise TranslationError(f"Ollama: {data['error']}")
                    piece = (data.get("message") or {}).get("content") or ""
                    if piece:
                        acc += piece
                        if on_partial is not None:
                            partial = clean_output(acc)
                            if partial:
                                on_partial(partial)
                    if data.get("done"):
                        break
        except httpx.HTTPError as exc:
            self._mark_down(type(exc).__name__)
            raise TranslationError(f"Ollama: {type(exc).__name__}") from None
        except json.JSONDecodeError as exc:
            raise TranslationError(f"Ollama: bozuk yanıt ({exc})") from None
        result = clean_output(acc)
        if not result:
            raise TranslationError("Ollama boş yanıt döndürdü")
        if not self._ready:
            self._ready = True
            self._warned = False
            logger.info("Ollama yeniden erişilebilir ({})", self.model)
        return result

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
