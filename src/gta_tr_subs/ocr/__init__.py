from __future__ import annotations

from loguru import logger

from ..config import OcrConfig
from .base import OcrEngine, OcrLine, OcrResult, OcrUnavailable, read_roi

__all__ = ["OcrEngine", "OcrLine", "OcrResult", "OcrUnavailable", "create_engine", "read_roi"]


def create_engine(cfg: OcrConfig, name: str | None = None, allow_fallback: bool = True) -> OcrEngine:
    """Config'deki motoru oluşturur; kullanılamıyorsa (ve izin varsa) diğerine düşer."""
    name = name or cfg.engine
    order = [name] + ([n for n in ("windows", "rapid") if n != name] if allow_fallback else [])
    errors: list[str] = []
    for candidate in order:
        try:
            if candidate == "windows":
                from .windows_ocr import WindowsOcr

                engine: OcrEngine = WindowsOcr(cfg.language)
            elif candidate == "rapid":
                from .rapid_ocr import RapidOcr

                engine = RapidOcr(cfg.rapid_use_dml)
            else:
                raise OcrUnavailable(f"Bilinmeyen OCR motoru: {candidate}")
        except OcrUnavailable as exc:
            errors.append(str(exc))
            logger.warning("OCR motoru '{}' kullanılamıyor:\n{}", candidate, exc)
            continue
        if candidate != name:
            logger.warning("OCR motoru '{}' yerine '{}' kullanılıyor", name, candidate)
        return engine
    raise OcrUnavailable("\n\n".join(errors))
