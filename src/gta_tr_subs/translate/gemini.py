"""Katman 1 — bulut LLM: Google Gemini (resmi ``google-genai`` SDK, streaming).

API anahtarı yalnızca ``GEMINI_API_KEY`` ortam değişkeninden okunur; koda/log'a yazılmaz.
"""

from __future__ import annotations

import os
import re

from loguru import logger

from ..config import GeminiConfig
from .base import OnPartial, RateLimited, TranslationError
from .prompts import Context, Glossary, build_system_prompt, build_user_prompt, clean_output

ENV_KEY = "GEMINI_API_KEY"


class GeminiTranslator:
    layer = "cloud"

    def __init__(self, cfg: GeminiConfig, glossary: Glossary | None = None) -> None:
        self.cfg = cfg
        self.model = cfg.model
        self.glossary = glossary
        self._client = None
        mode = cfg.thinking
        if mode == "auto":
            # Gemini 3+ "thinking_level", 2.x serisi "thinking_budget" kullanır
            name = cfg.model.lower()
            mode = "off" if ("2.5" in name or "2.0" in name) else "minimal"
        self._thinking_mode = mode  # reddedilirse: minimal/low → off → default
        self._has_key = bool(os.environ.get(ENV_KEY))
        if cfg.enabled and not self._has_key:
            logger.warning("{} tanımlı değil → bulut çeviri devre dışı (yalnızca yerel katmanlar)", ENV_KEY)

    @property
    def available(self) -> bool:
        return self.cfg.enabled and self._has_key

    def _ensure_client(self):  # noqa: ANN202
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=os.environ[ENV_KEY])
        return self._client

    def _thinking_config(self):  # noqa: ANN202
        """Thinking'i en düşük seviyeye çek / kapat (modele göre)."""
        from google.genai import types

        mode = self._thinking_mode
        if mode == "off":
            return types.ThinkingConfig(thinking_budget=0)
        if mode == "minimal":
            return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)
        if mode == "low":
            return types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
        return None

    def _config(self):  # noqa: ANN202
        from google.genai import types

        categories = (
            types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        )
        return types.GenerateContentConfig(
            system_instruction=build_system_prompt(),
            temperature=self.cfg.temperature,
            max_output_tokens=self.cfg.max_output_tokens,
            thinking_config=self._thinking_config(),
            # +18 oyun diyaloğu: küfür/argo çevirisi engellenmesin
            safety_settings=[
                types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE) for c in categories
            ],
        )

    async def warm_up(self) -> None:
        """TLS bağlantısını ve modeli önden ısıt (ilk satırın gecikmesini düşürür)."""
        if not self.available:
            return
        try:
            await self.translate("Hello.", [])
            logger.info("Gemini hazır ({})", self.model)
        except TranslationError as exc:
            logger.warning("Gemini ısınma isteği başarısız: {}", exc)

    async def translate(self, text: str, context: Context, on_partial: OnPartial | None = None) -> str:
        if not self.available:
            raise TranslationError("Gemini kullanılamıyor (anahtar yok veya devre dışı)")
        try:
            return await self._stream(text, context, on_partial)
        except TranslationError:
            raise
        except Exception as exc:
            message = _safe_message(exc, limit=4000)
            if "429" in message or "RESOURCE_EXHAUSTED" in message:
                found = re.search(r"retry in ([\d.]+)\s*s", message, flags=re.IGNORECASE)
                retry_after = float(found.group(1)) if found else 60.0
                raise RateLimited(f"Gemini kota sınırı (429), {retry_after:.0f} sn sonra", retry_after) from None
            message = message[:300]
            # Model bu thinking ayarını desteklemiyorsa bir kez ayarı düşürüp tekrar dene
            if "thinking" in message.lower() and self._thinking_mode != "default":
                next_mode = "default" if self._thinking_mode == "off" else "off"
                logger.warning("Thinking ayarı '{}' reddedildi → '{}' deneniyor", self._thinking_mode, next_mode)
                self._thinking_mode = next_mode
                try:
                    return await self._stream(text, context, on_partial)
                except TranslationError:
                    raise
                except Exception as exc2:
                    raise TranslationError(f"Gemini: {_safe_message(exc2)}") from None
            raise TranslationError(f"Gemini: {message}") from None

    async def _stream(self, text: str, context: Context, on_partial: OnPartial | None) -> str:
        client = self._ensure_client()
        prompt = build_user_prompt(text, context, self.glossary)
        stream = await client.aio.models.generate_content_stream(
            model=self.model, contents=prompt, config=self._config()
        )
        acc = ""
        async for chunk in stream:
            piece = chunk.text
            if piece:
                acc += piece
                if on_partial is not None and self.cfg.stream_partials:
                    partial = clean_output(acc)
                    if partial:
                        on_partial(partial)
        result = clean_output(acc)
        if not result:
            raise TranslationError("Gemini boş yanıt döndürdü (güvenlik filtresi olabilir)")
        return result

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aio.aclose()
            except Exception:  # noqa: BLE001 - kapanışta hata önemsiz
                pass


def _safe_message(exc: Exception, limit: int = 300) -> str:
    """Hata mesajını kısalt; anahtar sızmasın."""
    message = f"{type(exc).__name__}: {exc}"
    key = os.environ.get(ENV_KEY)
    if key:
        message = message.replace(key, "***")
    return message[:limit]
