"""Ucuz değişim tespiti: her karede çalışır, OCR'ı yalnızca gerektiğinde tetikler.

ROI → parlak piksel maskesi (altyazılar parlak + koyu konturlu) → küçült →
referans maskeyle XOR oranı. Referans yalnızca "değişti" dendiğinde güncellenir;
böylece yavaş fade'ler de birikerek eşiği aşar.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..config import DetectConfig


@dataclass(slots=True)
class Detection:
    mask: np.ndarray  # tam çözünürlük, uint8 0/255
    fill: float  # küçültülmüş maskenin doluluk oranı
    diff: float  # referansa göre XOR / birleşim
    changed: bool
    empty: bool


def bright_mask(roi: np.ndarray, cfg: DetectConfig) -> np.ndarray:
    """Altyazı adayı pikseller: en parlak kanal eşiğin üstünde (renkli metin de yakalanır)."""
    if roi.ndim == 2:
        value = roi
    else:
        b, g, r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
        value = cv2.max(cv2.max(b, g), r)
    _, mask = cv2.threshold(value, cfg.bright_threshold, 255, cv2.THRESH_BINARY)
    if cfg.require_outline:
        _, dark = cv2.threshold(value, cfg.dark_threshold, 255, cv2.THRESH_BINARY_INV)
        near_dark = cv2.dilate(dark, np.ones((5, 5), np.uint8))
        mask = cv2.bitwise_and(mask, near_dark)
        # konturun içinde kalan gövdeyi geri kazan
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return mask


class ChangeDetector:
    def __init__(self, cfg: DetectConfig) -> None:
        self.cfg = cfg
        self._ref: np.ndarray | None = None
        self._current: np.ndarray | None = None

    def reset(self) -> None:
        self._ref = None
        self._current = None

    def snapshot(self) -> np.ndarray | None:
        """Son karenin küçültülmüş maskesi (``distance`` ile karşılaştırmak için)."""
        return self._current

    def distance(self, snapshot: np.ndarray | None) -> float:
        """Son karenin maskesi ile ``snapshot`` arasındaki XOR / birleşim oranı (0 = aynı)."""
        current = self._current
        if snapshot is None or current is None or snapshot.shape != current.shape:
            return 1.0
        union = int(np.count_nonzero(np.logical_or(current, snapshot)))
        if union == 0:
            return 0.0
        return int(np.count_nonzero(np.logical_xor(current, snapshot))) / union

    def update(self, roi: np.ndarray) -> Detection:
        cfg = self.cfg
        mask = bright_mask(roi, cfg)
        h, w = mask.shape[:2]
        ds = max(1, cfg.downscale)
        small_size = (max(1, w // ds), max(1, h // ds))
        small = cv2.resize(mask, small_size, interpolation=cv2.INTER_AREA) > 48
        self._current = small
        total = small.size
        filled = int(np.count_nonzero(small))
        fill = filled / total
        empty = fill < cfg.empty_ratio

        ref = self._ref
        if ref is None or ref.shape != small.shape:
            ref_filled = 0
            xor = filled
            union = filled
        else:
            ref_filled = int(np.count_nonzero(ref))
            xor = int(np.count_nonzero(np.logical_xor(small, ref)))
            union = int(np.count_nonzero(np.logical_or(small, ref)))
        floor = max(1.0, cfg.empty_ratio * total)
        diff = xor / max(float(union), floor)
        changed = diff > cfg.change_ratio and (xor >= floor or (empty and ref_filled >= floor))
        if empty and ref_filled < floor:
            changed = False  # boştan boşa: gürültü
        if changed or ref is None or ref.shape != small.shape:
            self._ref = small
        return Detection(mask=mask, fill=fill, diff=diff, changed=changed, empty=empty)
