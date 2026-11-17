"""Şeffaf, her zaman üstte, tıklamayı geçiren overlay penceresi + debug paneli (PySide6)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ..config import OverlayConfig
from ..metrics import Stat
from ..pipeline import ShowEvent, StatsEvent
from . import win32

LAYER_LABELS = {"cache": "cache", "cloud": "bulut", "local": "yerel", "fallback": "yedek"}
LAYER_COLORS = {
    "cache": QColor(120, 200, 255),
    "cloud": QColor(130, 230, 150),
    "local": QColor(255, 200, 110),
    "fallback": QColor(255, 140, 140),
}

ScreenRect = tuple[int, int, int, int]


def wrap_text(text: str, metrics: QFontMetricsF, max_width: float) -> list[str]:
    """Kelime bazlı açgözlü satır kırma."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}" if current else word
        if not current or metrics.horizontalAdvance(trial) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@dataclass(slots=True)
class _Layout:
    font: QFont
    lines: list[str]
    line_height: float
    ascent: float
    text_width: float
    box: QRectF
    small_font: QFont
    header: str  # konuşmacı / İngilizce satırı (opsiyonel)
    header_height: float


class OverlayWindow(QWidget):
    """Orijinal altyazının üstüne koyu kutu + konturlu Türkçe metin çizer."""

    def __init__(self, cfg: OverlayConfig, rect_provider: Callable[[], ScreenRect | None]) -> None:
        super().__init__(None)
        self.cfg = cfg
        self._rect_provider = rect_provider
        self._event: ShowEvent | None = None
        self._layout_cache: _Layout | None = None
        self._enabled = cfg.enabled
        self._show_english = cfg.show_english
        self._toast = ""
        self._toast_until = 0.0
        self._last_rect: ScreenRect | None = None
        self._hwnd = 0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle("gta-tr-subs overlay")

        self._follow = QTimer(self)
        self._follow.setInterval(100)
        self._follow.timeout.connect(self.follow_target)

    # ------------------------------------------------------------- yaşam döngüsü
    def start(self) -> None:
        self.resize(800, 200)
        self.show()
        self._hwnd = int(self.winId())
        win32.make_click_through(self._hwnd)
        if self.cfg.exclude_from_capture:
            # Overlay hiçbir ekran yakalamaya girmesin (kendi çevirimizi OCR'lamayalım)
            win32.exclude_from_capture(self._hwnd)
        self.follow_target()
        self._follow.start()

    def follow_target(self) -> None:
        """Yakalanan pencere hareket ederse/boyut değiştirirse onu takip et."""
        rect = self._rect_provider()
        if rect is None:
            if self._last_rect is not None:
                self._last_rect = None
                self.update()
            return
        if rect != self._last_rect:
            self._last_rect = rect
            x, y, w, h = rect
            win32.place_topmost(self._hwnd, x, y, max(w, 8), max(h, 8))
            self._layout_cache = None
            self.update()
        elif self._event is not None:
            win32.raise_topmost(self._hwnd)  # başka bir topmost pencerenin altında kalma
        if self._toast and time.monotonic() >= self._toast_until:
            self._toast = ""
            self.update()

    # ------------------------------------------------------------------ durum
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._event = None
        self.update()

    def toggle_english(self) -> bool:
        self._show_english = not self._show_english
        self._layout_cache = None
        self.update()
        return self._show_english

    def flash(self, message: str, seconds: float = 1.6) -> None:
        """Kısa süreli bilgi mesajı (mod değişimi vb.)."""
        self._toast = message
        self._toast_until = time.monotonic() + seconds
        self.update()

    def show_translation(self, event: ShowEvent) -> None:
        if not self._enabled:
            return
        self._event = event
        self._layout_cache = None
        self.update()

    def hide_translation(self) -> None:
        if self._event is not None:
            self._event = None
            self._layout_cache = None
            self.update()

    # ------------------------------------------------------------------ çizim
    def _compute_layout(self, event: ShowEvent) -> _Layout:
        cfg = self.cfg
        width, height = float(self.width()), float(self.height())
        roi_w, roi_h = event.roi_size
        sx, sy = width / max(roi_w, 1), height / max(roi_h, 1)
        if event.bbox is not None:
            bx, by, bw, bh = event.bbox
            orig = QRectF(bx * sx, by * sy, bw * sx, bh * sy)
        else:
            orig = QRectF(width * 0.2, height * 0.55, width * 0.6, height * 0.3)
        orig_line = event.line_height * sy if event.line_height > 0 else height * 0.16

        header_parts = []
        if event.speaker:
            header_parts.append(event.speaker)
        if self._show_english:
            header_parts.append(event.source)
        header = " — ".join(header_parts)

        size = min(float(cfg.max_font_px), max(float(cfg.min_font_px), orig_line * 0.92))
        pad_x = max(10.0, size * 0.6)
        pad_y = max(5.0, size * 0.28)
        max_text_width = width * 0.97 - 2 * pad_x
        while True:
            font = QFont(cfg.font_family)
            font.setPixelSize(int(round(size)))
            font.setBold(cfg.font_bold)
            metrics = QFontMetricsF(font)
            small_font = QFont(cfg.font_family)
            small_font.setPixelSize(max(10, int(round(size * 0.52))))
            header_height = QFontMetricsF(small_font).height() if header else 0.0

            # önce orijinal metnin genişliğine yakın kır, sığmazsa tüm genişliği kullan
            preferred = min(max_text_width, max(orig.width() * 1.08, width * 0.45))
            lines = wrap_text(event.text, metrics, preferred)
            if len(lines) > cfg.max_lines and preferred < max_text_width:
                lines = wrap_text(event.text, metrics, max_text_width)
            line_height = metrics.height() * 0.98
            block_height = line_height * len(lines) + header_height
            widest = max((metrics.horizontalAdvance(l) for l in lines), default=0.0)
            fits = (
                len(lines) <= cfg.max_lines
                and block_height + 2 * pad_y <= height
                and widest <= max_text_width
            )
            if fits or size <= cfg.min_font_px:
                break
            size = max(float(cfg.min_font_px), size * 0.93)

        # Kutu: orijinal metni tamamen kapatacak kadar büyük, onun üstüne ortalı
        box_w = min(width, max(widest, orig.width()) + 2 * pad_x)
        box_h = min(height, max(block_height, orig.height()) + 2 * pad_y)
        center = orig.center()
        left = min(max(center.x() - box_w / 2, 0.0), width - box_w)
        top = min(max(center.y() - box_h / 2, 0.0), height - box_h)
        return _Layout(
            font=font, lines=lines, line_height=line_height, ascent=metrics.ascent(), text_width=widest,
            box=QRectF(left, top, box_w, box_h), small_font=small_font, header=header,
            header_height=header_height,
        )

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self._toast:
            self._paint_toast(painter)
        event = self._event
        if event is None or not self._enabled or self._last_rect is None:
            return
        layout = self._layout_cache
        if layout is None:
            layout = self._layout_cache = self._compute_layout(event)

        box = layout.box
        radius = min(12.0, box.height() * 0.2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 8, 10, int(255 * min(max(self.cfg.opacity, 0.0), 1.0))))
        painter.drawRoundedRect(box, radius, radius)

        block_height = layout.line_height * len(layout.lines) + layout.header_height
        y = box.top() + (box.height() - block_height) / 2
        if layout.header:
            painter.setFont(layout.small_font)
            painter.setPen(QColor(200, 205, 215))
            header_rect = QRectF(box.left() + 8, y, box.width() - 16, layout.header_height)
            metrics = QFontMetricsF(layout.small_font)
            text = metrics.elidedText(layout.header, Qt.TextElideMode.ElideRight, header_rect.width())
            painter.drawText(header_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, text)
            y += layout.header_height

        metrics = QFontMetricsF(layout.font)
        outline = QPen(QColor(0, 0, 0), max(2.0, layout.font.pixelSize() * 0.14))
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        fill = QColor(255, 255, 255) if not event.partial else QColor(235, 235, 235)
        for line in layout.lines:
            line_width = metrics.horizontalAdvance(line)
            path = QPainterPath()
            path.addText(QPointF(box.center().x() - line_width / 2, y + layout.ascent), layout.font, line)
            painter.strokePath(path, outline)
            painter.fillPath(path, fill)
            y += layout.line_height

        if self.cfg.show_badge:
            self._paint_badge(painter, box, event.layer)

    def _paint_badge(self, painter: QPainter, box: QRectF, layer: str) -> None:
        label = LAYER_LABELS.get(layer, layer)
        font = QFont(self.cfg.font_family)
        font.setPixelSize(10)
        font.setBold(True)
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(label) + 10
        h = metrics.height() + 2
        rect = QRectF(box.right() - w - 4, box.top() + 3, w, h)
        if rect.left() < box.left():
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setFont(font)
        painter.setPen(LAYER_COLORS.get(layer, QColor(220, 220, 220)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_toast(self, painter: QPainter) -> None:
        font = QFont(self.cfg.font_family)
        font.setPixelSize(16)
        font.setBold(True)
        metrics = QFontMetricsF(font)
        w = metrics.horizontalAdvance(self._toast) + 24
        h = metrics.height() + 10
        rect = QRectF((self.width() - w) / 2, 4, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24, 225))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._toast)


def _fmt(stat: Stat | None) -> str:
    if stat is None:
        return "      —"
    return f"son {stat.last:6.0f}  p50 {stat.p50:6.0f}  p95 {stat.p95:6.0f} ms"


class DebugPanel(QWidget):
    """F12: canlı ROI önizlemesi + fps, OCR süresi, çeviri süresi, aktif katman."""

    def __init__(self, on_quit: Callable[[], None] | None = None) -> None:
        super().__init__(None)
        self.setWindowTitle("gta-tr-subs — debug")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("background:#15171c; color:#e6e8ee;")
        layout = QVBoxLayout(self)
        self.preview = QLabel("ROI önizlemesi bekleniyor…")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480, 80)
        self.preview.setStyleSheet("background:#000; border:1px solid #333;")
        layout.addWidget(self.preview)
        self.stats = QLabel("")
        mono = QFont("Consolas")
        mono.setPixelSize(13)
        self.stats.setFont(mono)
        self.stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.stats)
        if on_quit is not None:
            button = QPushButton("Uygulamadan çık")
            button.setStyleSheet("background:#2a2d36; padding:6px;")
            button.clicked.connect(on_quit)
            layout.addWidget(button)
        self._last_translation = ""
        self._last_layer = ""

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        # Debug paneli de yakalamaya girmesin (monitör yakalama modunda OCR'ı bozmasın)
        win32.exclude_from_capture(int(self.winId()))

    def note_translation(self, event: ShowEvent) -> None:
        self._last_translation = event.text
        self._last_layer = event.layer

    def update_stats(self, event: StatsEvent) -> None:
        if not self.isVisible():
            return
        if event.preview is not None:
            self.preview.setPixmap(_to_pixmap(event.preview))
        snap = event.snapshot
        stats: dict[str, Stat | None] = snap.get("stats", {})  # type: ignore[assignment]
        rates: dict[str, float] = snap.get("rates", {})  # type: ignore[assignment]
        counts: dict[str, int] = snap.get("counts", {})  # type: ignore[assignment]
        values: dict[str, str] = snap.get("values", {})  # type: ignore[assignment]
        from ..translate import MODE_LABELS

        lines = [
            f"yakalama   {rates.get('fps', 0.0):5.1f} fps   düşen kare {values.get('dropped', '0')}",
            f"OCR        {rates.get('ocr', 0.0):5.1f} Hz    {_fmt(stats.get('ocr_ms'))}",
            f"değişim    {_fmt(stats.get('detect_ms'))}",
            f"kararlılık {_fmt(stats.get('stable_ms'))}",
            f"bulut      {_fmt(stats.get('translate.cloud_ms'))}",
            f"yerel      {_fmt(stats.get('translate.local_ms'))}",
            f"GECİKME yeni   {_fmt(stats.get('latency_new_ms'))}",
            f"GECİKME cache  {_fmt(stats.get('latency_cache_ms'))}",
            "",
            f"mod: {MODE_LABELS.get(event.mode, event.mode)}   aktif katman: "
            f"{LAYER_LABELS.get(values.get('layer', ''), '—')}   devre kesici: {values.get('breaker', 'closed')}",
            f"satır {counts.get('lines', 0)}   cache {counts.get('layer.cache', 0)}   "
            f"bulut {counts.get('layer.cloud', 0)}   yerel {counts.get('layer.local', 0)}   "
            f"yedek {counts.get('layer.fallback', 0)}   hata {counts.get('translate.failed', 0)}",
            "",
            f"EN: {event.last_source[:90]}",
            f"TR: {self._last_translation[:90]}",
        ]
        self.stats.setText("\n".join(lines))


def _to_pixmap(image: np.ndarray) -> QPixmap:
    image = np.ascontiguousarray(image)
    h, w = image.shape[:2]
    qimage = QImage(image.data, w, h, w * 3, QImage.Format.Format_BGR888)
    return QPixmap.fromImage(qimage.copy())
