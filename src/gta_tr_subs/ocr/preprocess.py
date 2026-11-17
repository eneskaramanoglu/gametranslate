"""OCR ön işleme: metnin bounding box'ını bul, kırp, büyüt, kontrastı artır."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MAX_WIDTH = 2800
MAX_HEIGHT = 700


@dataclass(slots=True)
class Prepared:
    image: np.ndarray  # gri, uint8
    scale: float
    offset_x: int
    offset_y: int

    def to_roi(self, x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
        """OCR görüntüsündeki kutuyu ROI piksel koordinatlarına çevirir."""
        return (
            int(round(x / self.scale)) + self.offset_x,
            int(round(y / self.scale)) + self.offset_y,
            max(1, int(round(w / self.scale))),
            max(1, int(round(h / self.scale))),
        )


def text_bbox(mask: np.ndarray, pad: int = 14) -> tuple[int, int, int, int] | None:
    """Maskedeki parlak piksellerin sınırlayıcı kutusu (x, y, w, h), dolgu eklenmiş."""
    points = cv2.findNonZero(mask)
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    height, width = mask.shape[:2]
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + w + pad, width), min(y + h + pad, height)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return x0, y0, x1 - x0, y1 - y0


def prepare(
    roi: np.ndarray, mask: np.ndarray | None, upscale: float = 2.0, mode: str = "mask"
) -> Prepared | None:
    """ROI'den OCR'a verilecek görüntüyü üretir. Metin yoksa None.

    ``mode``:
      - "mask": parlak piksel maskesinden beyaz zemin üstüne siyah metin (arka plan
        tamamen atılır). Ölçümlerde Windows OCR için açık ara en doğru ve en hızlısı.
      - "gray": kırpılmış gri görüntü + kontrast germe (maske güvenilmezse).
    """
    if mode == "mask" and mask is not None:
        box = text_bbox(mask)
        if box is None:
            return None
        x, y, w, h = box
        scale = min(max(1.0, float(upscale)), MAX_WIDTH / w, MAX_HEIGHT / h)
        binary = mask[y : y + h, x : x + w]
        if abs(scale - 1.0) > 1e-3:
            interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
            binary = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=interp)
        return Prepared(image=np.ascontiguousarray(255 - binary), scale=scale, offset_x=x, offset_y=y)

    if mask is not None:
        box = text_bbox(mask)
        if box is None:
            return None
        x, y, w, h = box
    else:
        x, y = 0, 0
        h, w = roi.shape[:2]
    crop = roi[y : y + h, x : x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop

    scale = max(1.0, float(upscale))
    scale = min(scale, MAX_WIDTH / w, MAX_HEIGHT / h)
    if scale < 1.0:
        interp = cv2.INTER_AREA
    else:
        interp = cv2.INTER_CUBIC
    if abs(scale - 1.0) > 1e-3:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)

    # kontrast germe: %1–%99 aralığını 0–255'e yay
    lo, hi = np.percentile(gray, (1, 99))
    if hi - lo > 24:
        gray = cv2.convertScaleAbs(gray, alpha=255.0 / (hi - lo), beta=-lo * 255.0 / (hi - lo))
    return Prepared(image=np.ascontiguousarray(gray), scale=scale, offset_x=x, offset_y=y)
