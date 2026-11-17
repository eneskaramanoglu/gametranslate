"""ROI kalibrasyonu: donmuş kare üzerinde fareyle altyazı (ve opsiyonel konuşmacı) bölgesi seçimi."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..config import Rect

MIN_FRACTION = 0.01


class _Canvas(QWidget):
    def __init__(self, pixmap: QPixmap, subtitle: Rect, speaker: Rect | None) -> None:
        super().__init__()
        self._pixmap = pixmap
        self.setFixedSize(pixmap.size())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.rects: dict[str, Rect | None] = {"subtitle": subtitle, "speaker": speaker}
        self.active = "subtitle"
        self._drag_start: QPointF | None = None
        self._drag_now: QPointF | None = None

    def _to_qrect(self, rect: Rect) -> QRectF:
        w, h = self.width(), self.height()
        return QRectF(rect.x * w, rect.y * h, rect.w * w, rect.h * h)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = self._drag_now = event.position()
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drag_start is not None:
            self._drag_now = event.position()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() != Qt.MouseButton.LeftButton or self._drag_start is None:
            return
        rect = self._drag_rect()
        self._drag_start = self._drag_now = None
        if rect is not None:
            w, h = self.width(), self.height()
            fraction = Rect(x=rect.left() / w, y=rect.top() / h, w=rect.width() / w, h=rect.height() / h)
            if fraction.w >= MIN_FRACTION and fraction.h >= MIN_FRACTION:
                self.rects[self.active] = fraction
        self.update()

    def _drag_rect(self) -> QRectF | None:
        if self._drag_start is None or self._drag_now is None:
            return None
        bounds = QRectF(0, 0, self.width(), self.height())
        return QRectF(self._drag_start, self._drag_now).normalized().intersected(bounds)

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))
        styles = {"subtitle": (QColor(80, 220, 120), "Altyazı"), "speaker": (QColor(255, 190, 80), "Konuşmacı")}
        for name, rect in self.rects.items():
            if rect is None:
                continue
            color, label = styles[name]
            qrect = self._to_qrect(rect)
            painter.drawPixmap(qrect, self._pixmap, qrect)  # seçili bölge karartılmasın
            painter.setPen(QPen(color, 3 if name == self.active else 1.5))
            painter.drawRect(qrect)
            painter.drawText(qrect.topLeft() + QPointF(4, -6), label)
        drag = self._drag_rect()
        if drag is not None:
            painter.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
            painter.drawRect(drag)


class CalibrationDialog(QDialog):
    """Kullanım: ``dialog.exec()`` → kabul edildiyse ``dialog.result_rects()``."""

    def __init__(self, frame: np.ndarray, subtitle: Rect, speaker: Rect | None, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Altyazı bölgesini seç — gta-tr-subs")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)

        image = np.ascontiguousarray(frame[:, :, :3])
        h, w = image.shape[:2]
        qimage = QImage(image.data, w, h, w * 3, QImage.Format.Format_BGR888).copy()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        scale = min(1.0, screen.width() * 0.88 / w, (screen.height() * 0.88 - 110) / h)
        pixmap = QPixmap.fromImage(qimage).scaled(
            int(w * scale), int(h * scale), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.canvas = _Canvas(pixmap, subtitle, speaker)

        hint = QLabel(
            "Fareyle sürükleyerek altyazıların çıktığı bölgeyi seç (en uzun 2–3 satırlık altyazı sığacak kadar; "
            "gereğinden büyük seçme). Oran olarak kaydedilir, pencere boyutu değişse de bozulmaz."
        )
        hint.setWordWrap(True)

        self.mode_button = QPushButton()
        self.mode_button.clicked.connect(self._toggle_mode)
        clear_speaker = QPushButton("Konuşmacı bölgesini sil")
        clear_speaker.clicked.connect(self._clear_speaker)
        default = QPushButton("Varsayılan (alt %22, orta %80)")
        default.clicked.connect(self._reset_default)
        save = QPushButton("Kaydet")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        cancel = QPushButton("İptal")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        for button in (self.mode_button, clear_speaker, default):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.canvas, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(buttons)
        self._refresh_mode()

    def _refresh_mode(self) -> None:
        if self.canvas.active == "subtitle":
            self.mode_button.setText("Şu an: ALTYAZI bölgesi → konuşmacı bölgesine geç (opsiyonel)")
        else:
            self.mode_button.setText("Şu an: KONUŞMACI bölgesi → altyazı bölgesine dön")
        self.canvas.update()

    def _toggle_mode(self) -> None:
        self.canvas.active = "speaker" if self.canvas.active == "subtitle" else "subtitle"
        self._refresh_mode()

    def _clear_speaker(self) -> None:
        self.canvas.rects["speaker"] = None
        self.canvas.update()

    def _reset_default(self) -> None:
        self.canvas.rects["subtitle"] = Rect()
        self.canvas.update()

    def result_rects(self) -> tuple[Rect, Rect | None]:
        subtitle = self.canvas.rects["subtitle"] or Rect()
        return _rounded(subtitle), (_rounded(self.canvas.rects["speaker"]) if self.canvas.rects["speaker"] else None)


def _rounded(rect: Rect) -> Rect:
    return Rect(x=round(rect.x, 4), y=round(rect.y, 4), w=round(rect.w, 4), h=round(rect.h, 4))
