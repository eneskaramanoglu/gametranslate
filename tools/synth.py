"""Sentetik oyun altyazısı üretimi (test videosu ve OCR benchmark'ı için ortak)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# GTA tarzı örnek satırlar (zaman: başlangıç sn, süre sn). Bazıları cache testi için tekrar eder.
SAMPLE_LINES: list[tuple[float, float, str]] = [
    (1.0, 2.2, "Jason, we need to get out of here right now."),
    (3.6, 2.0, "Relax, Lucia. Nobody saw us."),
    (6.0, 2.6, "You said that last time, and we ended up\nrunning from half of Vice City."),
    (9.2, 1.6, "Get in the car!"),
    (11.2, 2.4, "I'm not going back to prison. Not for you, not for anybody."),
    (14.0, 2.0, "Then drive like you mean it."),
    (16.4, 1.6, "Get in the car!"),
    (18.4, 2.6, "Who the hell taught you how to shoot?\nYour grandmother?"),
    (21.4, 2.0, "Shut up and cover me."),
    (23.8, 2.4, "The cops are all over Leonida looking for us."),
    (26.6, 1.8, "Relax, Lucia. Nobody saw us."),
    (28.8, 2.4, "Trust me, this is the easiest money we'll ever make."),
    (31.6, 2.2, "That's exactly what worries me."),
    (34.2, 1.6, "Get in the car!"),
    (36.2, 2.6, "If this goes sideways, I'm blaming you."),
    (39.2, 2.0, "Wouldn't have it any other way."),
]

_FONT_CANDIDATES = ("arialbd.ttf", "segoeuib.ttf", "arial.ttf", "DejaVuSans-Bold.ttf")


@lru_cache(maxsize=16)
def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise RuntimeError("Uygun TrueType font bulunamadı (arialbd.ttf / segoeuib.ttf)")


def background(width: int, height: int, t: float, bright: float = 0.0) -> np.ndarray:
    """Hareketli, dokulu oyun benzeri arka plan (BGR). ``bright`` 0..1 parlak sahne miktarı."""
    ys, xs = np.mgrid[0:height:8, 0:width:8].astype(np.float32)
    base = 70 + 40 * np.sin(xs / 160.0 + t * 0.9) + 30 * np.cos(ys / 110.0 - t * 0.6)
    base += bright * 110
    b = base + 25 * np.sin((xs + ys) / 90.0 + t)
    g = base + 10 * np.cos(xs / 60.0 - t * 1.3)
    r = base + 20 * np.sin(ys / 70.0 + t * 0.4)
    small = np.clip(np.stack([b, g, r], axis=-1), 0, 255).astype(np.uint8)
    frame = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    # hareketli parlak "ışık" lekesi (yanlış tetikleme testi) — altyazı bölgesinin üstünde
    cx = int((0.5 + 0.35 * np.sin(t * 0.7)) * width)
    cv2.circle(frame, (cx, int(height * 0.35)), int(height * 0.06), (235, 240, 250), -1, cv2.LINE_AA)
    return frame


def draw_subtitle(
    frame: np.ndarray,
    text: str,
    alpha: float = 1.0,
    font_px: int | None = None,
    bottom_margin: float = 0.08,
) -> np.ndarray:
    """Kareye beyaz + siyah konturlu, ortalanmış altyazı çizer (yerinde değiştirir)."""
    if alpha <= 0 or not text:
        return frame
    height, width = frame.shape[:2]
    size = font_px or max(14, round(height * 0.034))
    font = load_font(size)
    stroke = max(1, round(size * 0.09))
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    lines = text.split("\n")
    line_h = int(size * 1.3)
    y = int(height * (1 - bottom_margin)) - line_h * len(lines)
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(
            ((width - w) / 2, y), line, font=font, fill=(255, 255, 255, 255),
            stroke_width=stroke, stroke_fill=(0, 0, 0, 255),
        )
        y += line_h
    rgba = np.asarray(layer, dtype=np.float32)
    a = rgba[:, :, 3:4] / 255.0 * float(alpha)
    rgb = rgba[:, :, 2::-1]  # RGBA → BGR
    frame[:] = (frame.astype(np.float32) * (1 - a) + rgb * a).astype(np.uint8)
    return frame


def write_srt(path: str | Path, lines: list[tuple[float, float, str]]) -> None:
    def stamp(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    blocks = []
    for i, (start, duration, text) in enumerate(lines, 1):
        blocks.append(f"{i}\n{stamp(start)} --> {stamp(start + duration)}\n{text}\n")
    Path(path).write_text("\n".join(blocks), encoding="utf-8")
