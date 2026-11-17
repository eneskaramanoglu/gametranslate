"""Katman 3 — ultra hızlı yedek: CTranslate2 + Helsinki-NLP/opus-mt-tc-big-en-tr.

Opsiyonel (``uv sync --extra ct2``). Model dizini dönüştürülmüş CT2 modelini ve
``source.spm`` / ``target.spm`` dosyalarını içermelidir (README'ye bak).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from loguru import logger

from ..config import Ct2Config
from .base import OnPartial, TranslationError
from .prompts import Context


class Ct2Translator:
    layer = "fallback"

    def __init__(self, cfg: Ct2Config, model_dir: Path) -> None:
        self.cfg = cfg
        self.model = "opus-mt-tc-big-en-tr"
        self.model_dir = model_dir
        self._translator = None
        self._sp_source = None
        self._sp_target = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ct2")
        self._ok = False
        if cfg.enabled:
            self._load()

    def _load(self) -> None:
        try:
            import ctranslate2
            import sentencepiece as spm
        except ImportError:
            logger.warning("CT2 yedeği etkin ama paketler yok: uv sync --extra ct2")
            return
        source_spm = self.model_dir / "source.spm"
        target_spm = self.model_dir / "target.spm"
        if not (self.model_dir / "model.bin").exists() or not source_spm.exists() or not target_spm.exists():
            logger.warning("CT2 modeli bulunamadı: {} (dönüştürme adımları README'de)", self.model_dir)
            return
        self._translator = ctranslate2.Translator(
            str(self.model_dir), device="cpu", compute_type=self.cfg.compute_type,
            inter_threads=1, intra_threads=self.cfg.threads,
        )
        self._sp_source = spm.SentencePieceProcessor(model_file=str(source_spm))
        self._sp_target = spm.SentencePieceProcessor(model_file=str(target_spm))
        self._ok = True
        logger.info("CT2 yedeği hazır ({})", self.model_dir.name)

    @property
    def available(self) -> bool:
        return self._ok

    async def warm_up(self) -> None:
        if self._ok:
            try:
                await self.translate("Hello.", [])
            except TranslationError as exc:
                logger.warning("CT2 ısınma hatası: {}", exc)

    def _run(self, text: str) -> str:
        tokens = self._sp_source.encode(text, out_type=str) + ["</s>"]
        results = self._translator.translate_batch([tokens], beam_size=2, max_decoding_length=128)
        output = [t for t in results[0].hypotheses[0] if t != "</s>"]
        return self._sp_target.decode(output)

    async def translate(self, text: str, context: Context, on_partial: OnPartial | None = None) -> str:
        if not self._ok:
            raise TranslationError("CT2 yedeği kullanılamıyor")
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(self._pool, self._run, text)
        except Exception as exc:  # noqa: BLE001
            raise TranslationError(f"CT2: {type(exc).__name__}: {exc}") from None
        if not result.strip():
            raise TranslationError("CT2 boş yanıt döndürdü")
        return result.strip()

    async def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
