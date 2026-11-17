"""Birincil OCR: Windows.Media.Ocr (pywinrt). Çok hızlı; temiz oyun altyazısında yeterli."""

from __future__ import annotations

import cv2
import numpy as np

from .base import OcrLine, OcrUnavailable

INSTALL_HINT = (
    "Windows OCR için '{lang}' dil paketi kurulu değil.\n"
    "Yönetici PowerShell'de şunu çalıştır:\n"
    '    Add-WindowsCapability -Online -Name "Language.OCR~~~{lang}~0.0.1.0"\n'
    "veya Ayarlar → Saat ve dil → Dil ve bölge → Dil ekle → English (United States) "
    "(\"Optik karakter tanıma\" seçili olsun).\n"
    "Alternatif: config.toml içinde [ocr] engine = \"rapid\" (uv sync --extra rapid)."
)


class WindowsOcr:
    name = "windows"

    def __init__(self, language: str = "en-US") -> None:
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.storage.streams import Buffer
        except ImportError as exc:  # pragma: no cover - Windows dışı
            raise OcrUnavailable(f"Windows OCR (pywinrt) yüklenemedi: {exc}") from exc

        lang = Language(language)
        if not OcrEngine.is_language_supported(lang):
            installed = ", ".join(l.language_tag for l in OcrEngine.available_recognizer_languages) or "yok"
            raise OcrUnavailable(INSTALL_HINT.format(lang=language) + f"\n(Kurulu OCR dilleri: {installed})")
        engine = OcrEngine.try_create_from_language(lang)
        if engine is None:
            raise OcrUnavailable(INSTALL_HINT.format(lang=language))
        self._engine = engine
        self._Buffer = Buffer
        self._SoftwareBitmap = SoftwareBitmap
        self._format = BitmapPixelFormat.BGRA8
        self._max_dim = int(OcrEngine.max_image_dimension)

    async def recognize(self, gray: np.ndarray) -> list[OcrLine]:
        h, w = gray.shape[:2]
        scale = 1.0
        if max(h, w) > self._max_dim:
            scale = self._max_dim / max(h, w)
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            h, w = gray.shape[:2]
        bgra = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGRA) if gray.ndim == 2 else cv2.cvtColor(gray, cv2.COLOR_BGR2BGRA)
        buffer = self._Buffer(bgra.nbytes)
        buffer.length = bgra.nbytes
        with memoryview(buffer) as view:
            np.frombuffer(view, dtype=np.uint8)[:] = bgra.reshape(-1)
        bitmap = self._SoftwareBitmap.create_copy_from_buffer(buffer, self._format, w, h)
        try:
            result = await self._engine.recognize_async(bitmap)
        finally:
            bitmap.close()

        lines: list[OcrLine] = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            x0 = min(word.bounding_rect.x for word in words)
            y0 = min(word.bounding_rect.y for word in words)
            x1 = max(word.bounding_rect.x + word.bounding_rect.width for word in words)
            y1 = max(word.bounding_rect.y + word.bounding_rect.height for word in words)
            lines.append(
                OcrLine(
                    text=line.text,
                    x=int(x0 / scale),
                    y=int(y0 / scale),
                    w=int((x1 - x0) / scale),
                    h=int((y1 - y0) / scale),
                )
            )
        return lines
