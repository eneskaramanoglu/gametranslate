"""OCR motorları için ortak tipler ve yardımcılar."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..config import OcrConfig
from .preprocess import prepare
from .text import is_meaningful, is_noise_line, join_lines


class OcrUnavailable(RuntimeError):
    """Motor kullanılamıyor (dil paketi / paket eksik). Mesaj kullanıcıya gösterilir."""


@dataclass(slots=True)
class OcrLine:
    text: str
    x: int
    y: int
    w: int
    h: int
    confidence: float | None = None  # Windows OCR güven skoru vermez


@dataclass(slots=True)
class OcrResult:
    text: str  # birleştirilmiş, normalize edilmiş; geçersizse ""
    lines: list[OcrLine] = field(default_factory=list)
    ms: float = 0.0
    bbox: tuple[int, int, int, int] | None = None  # ROI pikseli (x, y, w, h)

    @property
    def line_height(self) -> float:
        return sum(l.h for l in self.lines) / len(self.lines) if self.lines else 0.0


class OcrEngine(Protocol):
    name: str

    async def recognize(self, gray: np.ndarray) -> list[OcrLine]:
        """Gri görüntüdeki satırları (görüntü koordinatlarında) döndürür."""
        ...


def order_lines(lines: list[OcrLine]) -> list[OcrLine]:
    """Yukarıdan aşağı, aynı satırdakileri soldan sağa sırala."""
    if not lines:
        return []
    avg_h = sum(l.h for l in lines) / len(lines)
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda l: l.y + l.h / 2):
        center = line.y + line.h / 2
        if rows and abs(center - (rows[-1][0].y + rows[-1][0].h / 2)) < avg_h * 0.6:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda l: l.x)]


async def read_roi(engine: OcrEngine, roi: np.ndarray, mask: np.ndarray | None, cfg: OcrConfig) -> OcrResult:
    """ROI'yi ön işle, OCR'la, satırları birleştir ve doğrula."""
    start = time.perf_counter()
    prepared = prepare(roi, mask, cfg.upscale, cfg.preprocess)
    if prepared is None:
        return OcrResult(text="", ms=(time.perf_counter() - start) * 1000.0)
    raw_lines = await engine.recognize(prepared.image)
    lines: list[OcrLine] = []
    for line in order_lines(raw_lines):
        if line.confidence is not None and line.confidence < cfg.min_confidence:
            continue
        if is_noise_line(line.text):
            continue
        x, y, w, h = prepared.to_roi(line.x, line.y, line.w, line.h)
        lines.append(OcrLine(line.text, x, y, w, h, line.confidence))
    text = join_lines([l.text for l in lines])
    if not is_meaningful(text, cfg.min_chars, cfg.min_alpha_ratio):
        text = ""
    bbox = None
    if lines and text:
        x0 = min(l.x for l in lines)
        y0 = min(l.y for l in lines)
        x1 = max(l.x + l.w for l in lines)
        y1 = max(l.y + l.h for l in lines)
        bbox = (x0, y0, x1 - x0, y1 - y0)
    return OcrResult(text=text, lines=lines, ms=(time.perf_counter() - start) * 1000.0, bbox=bbox)
