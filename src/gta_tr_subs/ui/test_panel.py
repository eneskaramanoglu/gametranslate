"""Test paneli: kaynağı seç, pipeline'ı başlat/durdur, okunan altyazıyı, çeviriyi ve
gecikmeleri canlı izle. ``uv run gta-tr-subs --ui`` ile açılır.

Oyun olmadan da kullanılabilir: kaynak olarak video dosyası veya sentetik test videosu
seçilebilir; bu durumda overlay, paneldeki önizlemenin üstüne bindirilir.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from loguru import logger
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..app import Bridge, PipelineThread
from ..capture.base import FrameSource
from ..config import Settings, save_values
from ..metrics import Metrics, Stat
from ..overlay import win32
from ..overlay.window import LAYER_LABELS, OverlayWindow
from ..pipeline import HideEvent, ManualEvent, Pipeline, ShowEvent, StatsEvent
from ..roi.calibrate import CalibrationDialog
from ..translate import MODE_LABELS, MODES

LAYER_HEX = {"cache": "#3d8fd1", "cloud": "#2e9e55", "local": "#c98a1b", "fallback": "#c9504a"}
HISTORY_COLUMNS = ["Saat", "İngilizce (OCR)", "Türkçe", "Kararlı ms", "İlk katman", "İlk çeviri ms", "Son katman", "Güncelleme ms"]
MAX_HISTORY = 500


@dataclass
class _Row:
    job_id: int
    clock: str
    source: str
    text: str = ""
    first_layer: str = ""
    final_layer: str = ""
    stable_ms: float = 0.0
    total_ms: float | None = None  # belirme → ilk TAM çeviri
    final_ms: float | None = None  # belirme → son katmanın sonucu (bulut yükseltmesi)
    table_row: int = -1


class FramePreview(QLabel):
    """Yakalanan karenin küçültülmüş önizlemesi + ROI çerçevesi."""

    def __init__(self) -> None:
        super().__init__("Kaynak seçip Başlat'a bas")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background:#000; color:#888;")
        self._image_rect: QRect | None = None  # görüntünün etiket içindeki yeri (mantıksal px)

    def show_frame(self, image: np.ndarray, roi: tuple[int, int, int, int], speaker: tuple[int, int, int, int] | None) -> None:
        h, w = image.shape[:2]
        scale = min(self.width() / w, self.height() / h)
        size = (max(1, int(w * scale)), max(1, int(h * scale)))
        small = cv2.resize(image[:, :, :3], size, interpolation=cv2.INTER_AREA)
        small = np.ascontiguousarray(small)
        qimage = QImage(small.data, size[0], size[1], size[0] * 3, QImage.Format.Format_BGR888)
        pixmap = QPixmap.fromImage(qimage)
        painter = QPainter(pixmap)
        for rect, color in ((roi, QColor(80, 220, 120)), (speaker, QColor(255, 190, 80))):
            if rect is None:
                continue
            x, y, rw, rh = rect
            painter.setPen(QPen(color, 2))
            painter.drawRect(int(x * scale), int(y * scale), int(rw * scale), int(rh * scale))
        painter.end()
        self.setPixmap(pixmap)
        self._image_rect = QRect((self.width() - size[0]) // 2, (self.height() - size[1]) // 2, size[0], size[1])

    def clear_frame(self) -> None:
        self._image_rect = None
        self.clear()
        self.setText("Kaynak seçip Başlat'a bas")

    def screen_rect(self) -> tuple[int, int, int, int] | None:
        """Görüntünün ekrandaki yeri (fiziksel piksel) — video kaynağında overlay bunu takip eder."""
        rect = self._image_rect
        if rect is None or not self.isVisible() or self.window().isMinimized():
            return None
        ratio = self.devicePixelRatioF()
        origin = self.mapToGlobal(rect.topLeft())
        return (int(origin.x() * ratio), int(origin.y() * ratio), int(rect.width() * ratio), int(rect.height() * ratio))


def _stat(stat: Stat | None) -> str:
    if stat is None:
        return "—"
    return f"son {stat.last:6.0f}   p50 {stat.p50:6.0f}   p95 {stat.p95:6.0f} ms   (n={stat.count})"


class TestPanel(QMainWindow):
    def __init__(self, settings: Settings, initial_video: str | None = None, fake: bool = False,
                 simulate_offline: bool = False) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle("gta-tr-subs — test paneli")
        self.resize(1320, 860)

        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.fatal.connect(self._on_fatal)
        self.metrics = Metrics()
        self.source: FrameSource | None = None
        self.hwnd: int | None = None
        self.thread: PipelineThread | None = None
        self.overlay: OverlayWindow | None = None
        self._video_path: str | None = initial_video
        self._rows: dict[int, _Row] = {}
        self._order: list[int] = []

        self._build_ui()
        self.fake_box.setChecked(fake)
        self.offline_box.setChecked(simulate_offline)
        self._reload_sources()
        if initial_video:
            self._select_video(initial_video)

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(66)
        self._frame_timer.timeout.connect(self._refresh_frame)
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(400)
        self._watch_timer.timeout.connect(self._watchdog)
        self._watch_timer.start()
        self._set_running(False)

    # ------------------------------------------------------------------ arayüz
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # ---- sol: önizlemeler + geçmiş/istatistik
        left = QVBoxLayout()
        self.frame_preview = FramePreview()
        left.addWidget(self.frame_preview, stretch=5)
        self.roi_preview = QLabel("Altyazı bölgesi (yeşil = OCR'a giden maske)")
        self.roi_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.roi_preview.setFixedHeight(110)
        self.roi_preview.setStyleSheet("background:#000; color:#888;")
        left.addWidget(self.roi_preview)

        tabs = QTabWidget()
        self.history = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history.verticalHeader().setVisible(False)
        header = self.history.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.addWidget(self.history)
        history_buttons = QHBoxLayout()
        self.summary = QLabel("")
        history_buttons.addWidget(self.summary, stretch=1)
        export = QPushButton("CSV olarak kaydet…")
        export.clicked.connect(self._export_csv)
        clear = QPushButton("Geçmişi temizle")
        clear.clicked.connect(self._clear_history)
        history_buttons.addWidget(export)
        history_buttons.addWidget(clear)
        history_layout.addLayout(history_buttons)
        tabs.addTab(history_page, "Geçmiş")

        self.stats = QLabel("Henüz ölçüm yok")
        mono = QFont("Consolas")
        mono.setPixelSize(13)
        self.stats.setFont(mono)
        self.stats.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.stats.setMargin(8)
        tabs.addTab(self.stats, "İstatistik")
        left.addWidget(tabs, stretch=4)
        root.addLayout(left, stretch=3)

        # ---- sağ: kontroller
        right = QVBoxLayout()

        source_box = QGroupBox("Kaynak")
        source_layout = QGridLayout(source_box)
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(300)
        source_layout.addWidget(self.source_combo, 0, 0, 1, 3)
        refresh = QPushButton("Pencereleri yenile")
        refresh.clicked.connect(self._reload_sources)
        pick_video = QPushButton("Video dosyası…")
        pick_video.clicked.connect(self._pick_video)
        synthetic = QPushButton("Sentetik test videosu")
        synthetic.setToolTip("Bilinen 16 satırlık altyazı içeren, programın ürettiği 43 sn'lik video")
        synthetic.clicked.connect(self._use_synthetic)
        source_layout.addWidget(refresh, 1, 0)
        source_layout.addWidget(pick_video, 1, 1)
        source_layout.addWidget(synthetic, 1, 2)
        self.start_button = QPushButton("▶  Başlat")
        self.start_button.setMinimumHeight(36)
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("■  Durdur")
        self.stop_button.setMinimumHeight(36)
        self.stop_button.clicked.connect(self.stop)
        self.calibrate_button = QPushButton("Altyazı bölgesini seç…")
        self.calibrate_button.clicked.connect(self._calibrate)
        source_layout.addWidget(self.start_button, 2, 0)
        source_layout.addWidget(self.stop_button, 2, 1)
        source_layout.addWidget(self.calibrate_button, 2, 2)
        self.status = QLabel("Durduruldu")
        self.status.setWordWrap(True)
        source_layout.addWidget(self.status, 3, 0, 1, 3)
        right.addWidget(source_box)

        translate_box = QGroupBox("Çeviri")
        translate_layout = QGridLayout(translate_box)
        translate_layout.addWidget(QLabel("Mod:"), 0, 0)
        self.mode_combo = QComboBox()
        for mode in MODES:
            self.mode_combo.addItem(MODE_LABELS[mode], mode)
        self.mode_combo.setCurrentIndex(MODES.index(self.settings.translate.mode))
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        translate_layout.addWidget(self.mode_combo, 0, 1)
        self.overlay_box = QCheckBox("Overlay'i göster")
        self.overlay_box.setChecked(True)
        self.overlay_box.toggled.connect(self._overlay_toggled)
        self.english_box = QCheckBox("Overlay'de İngilizceyi de göster")
        self.english_box.setChecked(self.settings.overlay.show_english)
        self.english_box.toggled.connect(self._english_toggled)
        self.offline_box = QCheckBox("İnternet kesintisi simülasyonu (bulut hata versin)")
        self.offline_box.toggled.connect(self._offline_toggled)
        self.fake_box = QCheckBox("Sahte çevirmen: çeviri YAPMAZ, İngilizceyi geri verir (yalnızca hız testi)")
        self.fake_box.setToolTip("Ağ ve model olmadan pipeline gecikmesini ölçmek içindir. Bir sonraki Başlat'ta geçerli olur.")
        translate_layout.addWidget(self.overlay_box, 1, 0, 1, 2)
        translate_layout.addWidget(self.english_box, 2, 0, 1, 2)
        translate_layout.addWidget(self.offline_box, 3, 0, 1, 2)
        translate_layout.addWidget(self.fake_box, 4, 0, 1, 2)
        right.addWidget(translate_box)

        current_box = QGroupBox("Şu anki satır")
        current_layout = QVBoxLayout(current_box)
        self.current_en = QLabel("—")
        self.current_en.setWordWrap(True)
        self.current_en.setStyleSheet("color:#666;")
        self.current_tr = QLabel("—")
        self.current_tr.setWordWrap(True)
        big = QFont()
        big.setPointSize(13)
        big.setBold(True)
        self.current_tr.setFont(big)
        self.current_info = QLabel("")
        self.current_info.setTextFormat(Qt.TextFormat.RichText)
        for widget in (self.current_en, self.current_tr, self.current_info):
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            current_layout.addWidget(widget)
        current_box.setMinimumHeight(150)
        right.addWidget(current_box)

        manual_box = QGroupBox("Elle çeviri testi (çalışırken)")
        manual_layout = QVBoxLayout(manual_box)
        row = QHBoxLayout()
        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText("İngilizce bir satır yaz, Enter'a bas")
        self.manual_input.returnPressed.connect(self._manual_translate)
        self.manual_button = QPushButton("Çevir")
        self.manual_button.clicked.connect(self._manual_translate)
        row.addWidget(self.manual_input)
        row.addWidget(self.manual_button)
        manual_layout.addLayout(row)
        self.manual_output = QPlainTextEdit()
        self.manual_output.setReadOnly(True)
        self.manual_output.setPlaceholderText("Her katmanın sonucu ve süresi burada görünür")
        manual_layout.addWidget(self.manual_output)
        right.addWidget(manual_box, stretch=1)

        root.addLayout(right, stretch=2)

    # ----------------------------------------------------------------- kaynak
    def _reload_sources(self) -> None:
        current = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        if self._video_path:
            self.source_combo.addItem(f"🎞  Video: {Path(self._video_path).name}", ("video", self._video_path))
        keywords = self.settings.capture.suggest_keywords
        windows = sorted(
            win32.list_windows(), key=lambda w: (win32.suggestion_rank(w.title, keywords), w.title.casefold())
        )
        saved = self.settings.capture.window_title.casefold()
        preferred = -1
        for window in windows:
            if window.title == self.windowTitle():
                continue
            suggested = win32.suggestion_rank(window.title, keywords) < len(keywords)
            self.source_combo.addItem(("★ " if suggested else "") + window.title, ("window", window.hwnd, window.title))
            index = self.source_combo.count() - 1
            if preferred < 0 and (suggested or (saved and saved in window.title.casefold())):
                preferred = index
        target = -1
        if current is not None:
            for i in range(self.source_combo.count()):
                if self.source_combo.itemData(i) == current:
                    target = i
                    break
        if target < 0:
            target = 0 if self._video_path else max(preferred, 0)
        if self.source_combo.count():
            self.source_combo.setCurrentIndex(target)
        self.source_combo.blockSignals(False)

    def _select_video(self, path: str) -> None:
        self._video_path = path
        self._reload_sources()
        self.source_combo.setCurrentIndex(0)

    def _pick_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Altyazılı oyun videosu seç", str(self.settings.base_dir), "Video (*.mp4 *.mkv *.mov *.avi *.webm);;Tümü (*)"
        )
        if path:
            self._select_video(path)

    def _use_synthetic(self) -> None:
        video = self.settings.base_dir / "out" / "synthetic.mp4"
        if not video.exists():
            script = self.settings.base_dir / "tools" / "make_test_video.py"
            if not script.exists():
                QMessageBox.warning(self, "gta-tr-subs", f"Üretici betik bulunamadı: {script}")
                return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                result = subprocess.run(
                    [sys.executable, str(script), "--out", str(video)], cwd=str(self.settings.base_dir),
                    capture_output=True, text=True, timeout=180,
                )
            finally:
                QApplication.restoreOverrideCursor()
            if result.returncode != 0 or not video.exists():
                QMessageBox.warning(self, "gta-tr-subs", f"Test videosu üretilemedi:\n{result.stderr[-600:]}")
                return
        self._select_video(str(video))

    # ----------------------------------------------------------- başlat/durdur
    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.source_combo.setEnabled(not running)
        self.fake_box.setEnabled(not running)
        self.calibrate_button.setEnabled(running)
        self.manual_button.setEnabled(running)
        self.manual_input.setEnabled(running)

    @property
    def pipeline(self) -> Pipeline | None:
        return self.thread.pipeline if self.thread is not None else None

    def start(self) -> None:
        if self.thread is not None:
            return
        data = self.source_combo.currentData()
        if data is None:
            QMessageBox.information(self, "gta-tr-subs", "Önce bir kaynak seç (pencere veya video).")
            return
        try:
            if data[0] == "video":
                from ..capture.video_file import VideoFileSource

                self.hwnd = None
                self.source = VideoFileSource(data[1], loop=True)
            else:
                _, hwnd, title = data
                if not win32.is_window(hwnd):
                    QMessageBox.warning(self, "gta-tr-subs", "Pencere artık yok. Listeyi yenile.")
                    self._reload_sources()
                    return
                self.hwnd = hwnd
                if title != self.settings.capture.window_title:
                    self.settings.capture.window_title = title
                    save_values(self.settings, {"capture.window_title": title})
                if self.settings.capture.backend == "monitor":
                    from ..capture.monitor import MonitorSource

                    self.source = MonitorSource(self.settings.capture.monitor_index, self.settings.capture.fps, hwnd=hwnd)
                else:
                    from ..capture.wgc import WgcWindowSource

                    self.source = WgcWindowSource(hwnd, self.settings.capture.fps)
            self.source.start()
        except Exception as exc:  # noqa: BLE001
            self.source = None
            QMessageBox.critical(self, "gta-tr-subs", f"Kaynak başlatılamadı:\n{exc}")
            return

        self.settings.translate.mode = self.mode_combo.currentData()
        self.metrics = Metrics()
        args = SimpleNamespace(simulate_offline=self.offline_box.isChecked(), fake_translate=self.fake_box.isChecked())
        self.thread = PipelineThread(self.settings, self.source, self.metrics, self.bridge, args)
        self.thread.start()

        self.overlay = OverlayWindow(self.settings.overlay, self._roi_screen_rect)
        self.overlay.start()
        self.overlay.set_enabled(self.overlay_box.isChecked())
        if self.english_box.isChecked() != self.settings.overlay.show_english:
            self.overlay.toggle_english()

        self._frame_timer.start()
        self._set_running(True)
        self.status.setText(f"Çalışıyor — {self.source.description}")
        QTimer.singleShot(600, self._sync_preview_flag)

    def _sync_preview_flag(self) -> None:
        pipeline = self.pipeline
        if pipeline is not None:
            pipeline.preview_enabled = True
        elif self.thread is not None:
            QTimer.singleShot(400, self._sync_preview_flag)

    def stop(self, message: str = "Durduruldu") -> None:
        self._frame_timer.stop()
        thread, self.thread = self.thread, None
        if thread is not None:
            if thread.pipeline is not None:
                thread.pipeline.stop()
            thread.join(timeout=4.0)
        source, self.source = self.source, None
        if source is not None:
            source.stop()
        overlay, self.overlay = self.overlay, None
        if overlay is not None:
            overlay.close()
            overlay.deleteLater()
        self.frame_preview.clear_frame()
        self._set_running(False)
        self.status.setText(message)

    def _watchdog(self) -> None:
        if self.thread is not None and not self.thread.is_alive():
            reason = "Kaynak sona erdi"
            if self.source is not None and self.source.error:
                reason = self.source.error
            elif self.hwnd is not None:
                reason = "Yakalanan pencere kapandı"
            self.stop(reason)

    def _on_fatal(self, message: str) -> None:
        self.stop("Hata")
        QMessageBox.critical(self, "gta-tr-subs", message)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.stop()
        super().closeEvent(event)

    # ---------------------------------------------------------------- önizleme
    def _roi_pixels(self, width: int, height: int):  # noqa: ANN202
        roi = self.settings.roi
        speaker = roi.speaker.to_pixels(width, height) if roi.speaker else None
        return roi.subtitle.to_pixels(width, height), speaker

    def _refresh_frame(self) -> None:
        if self.source is None:
            return
        frame = self.source.latest.peek_last()
        if frame is None:
            return
        height, width = frame.image.shape[:2]
        roi, speaker = self._roi_pixels(width, height)
        self.frame_preview.show_frame(frame.image, roi, speaker)

    def _roi_screen_rect(self) -> tuple[int, int, int, int] | None:
        """Overlay'in yeri: pencere kaynağında gerçek pencere, videoda paneldeki önizleme."""
        if self.source is None:
            return None
        if self.hwnd is not None:
            if win32.is_minimized(self.hwnd):
                return None
            rect = self.source.screen_rect()
        else:
            rect = self.frame_preview.screen_rect()
        if rect is None:
            return None
        x, y, w, h = rect
        rx, ry, rw, rh = self.settings.roi.subtitle.to_pixels(w, h)
        return x + rx, y + ry, rw, rh

    def _calibrate(self) -> None:
        if self.source is None:
            return
        frame = self.source.latest.peek_last()
        if frame is None:
            QMessageBox.information(self, "gta-tr-subs", "Henüz kare gelmedi.")
            return
        roi = self.settings.roi
        dialog = CalibrationDialog(frame.image, roi.subtitle, roi.speaker, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        subtitle, speaker = dialog.result_rects()
        roi.subtitle, roi.speaker, roi.calibrated = subtitle, speaker, True
        save_values(self.settings, {
            "roi.subtitle": subtitle.model_dump(),
            "roi.speaker": speaker.model_dump() if speaker else None,
            "roi.calibrated": True,
        })
        if self.pipeline is not None:
            self.pipeline.set_roi(subtitle, speaker)
        if self.overlay is not None:
            self.overlay.hide_translation()
            self.overlay.follow_target()

    # ----------------------------------------------------------------- ayarlar
    def _mode_changed(self) -> None:
        mode = self.mode_combo.currentData()
        self.settings.translate.mode = mode
        if self.pipeline is not None:
            self.pipeline.set_mode(mode)

    def _overlay_toggled(self, checked: bool) -> None:
        if self.overlay is not None:
            self.overlay.set_enabled(checked)

    def _english_toggled(self, _checked: bool) -> None:
        if self.overlay is not None:
            self.overlay.toggle_english()

    def _offline_toggled(self, checked: bool) -> None:
        if self.pipeline is not None:
            self.pipeline.router.simulate_offline = checked

    def _manual_translate(self) -> None:
        text = self.manual_input.text().strip()
        pipeline = self.pipeline
        if not text or pipeline is None:
            return
        self.manual_output.appendPlainText(f"EN  {text}")
        pipeline.translate_manual(text)

    # ----------------------------------------------------------------- olaylar
    def _on_event(self, event: object) -> None:
        if isinstance(event, ShowEvent):
            if self.overlay is not None:
                self.overlay.show_translation(event)
            self._show_current(event)
            self._record(event)
        elif isinstance(event, HideEvent):
            if self.overlay is not None:
                self.overlay.hide_translation()
            if event.reason in ("empty", "gone"):
                self.current_info.setText(self.current_info.text() + "  ·  <span style='color:#888'>ekrandan kalktı</span>")
        elif isinstance(event, StatsEvent):
            self._show_stats(event)
        elif isinstance(event, ManualEvent):
            self._show_manual(event)

    def _show_current(self, event: ShowEvent) -> None:
        self.current_en.setText(event.source)
        self.current_tr.setText(event.text + (" …" if event.partial else ""))
        color = LAYER_HEX.get(event.layer, "#888")
        label = LAYER_LABELS.get(event.layer, event.layer)
        self.current_info.setText(
            f"<span style='color:{color}; font-weight:bold'>{label}</span>"
            f"  ·  toplam <b>{event.latency_ms:.0f} ms</b>"
            f"  ·  kararlı {event.stable_ms:.0f} ms  ·  çeviri {event.translate_ms:.0f} ms  ·  OCR {event.ocr_ms:.0f} ms"
        )

    def _record(self, event: ShowEvent) -> None:
        row = self._rows.get(event.job_id)
        if row is None:
            row = _Row(event.job_id, time.strftime("%H:%M:%S"), event.source, first_layer=event.layer,
                       stable_ms=event.stable_ms)
            self._rows[event.job_id] = row
            self._order.append(event.job_id)
            row.table_row = self.history.rowCount()
            self.history.insertRow(row.table_row)
            if len(self._order) > MAX_HISTORY:
                self._drop_oldest()
        row.text = event.text
        if not event.partial:
            if row.total_ms is None:
                row.total_ms = event.latency_ms
            row.final_layer = event.layer
            row.final_ms = event.latency_ms
        self._render_row(row)
        self.history.scrollToBottom()
        self._update_summary()

    def _drop_oldest(self) -> None:
        oldest = self._order.pop(0)
        self._rows.pop(oldest, None)
        self.history.removeRow(0)
        for row in self._rows.values():
            row.table_row -= 1

    def _render_row(self, row: _Row) -> None:
        upgraded = bool(row.final_layer) and row.final_layer != row.first_layer
        values = [
            row.clock, row.source, row.text,
            f"{row.stable_ms:.0f}",
            LAYER_LABELS.get(row.first_layer, row.first_layer),
            f"{row.total_ms:.0f}" if row.total_ms is not None else "…",
            LAYER_LABELS.get(row.final_layer, row.final_layer) if upgraded else "",
            f"{row.final_ms:.0f}" if upgraded else "",
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in (3, 5, 7):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if column == 4:
                item.setForeground(QColor(LAYER_HEX.get(row.first_layer, "#888")))
            if column == 6 and upgraded:
                item.setForeground(QColor(LAYER_HEX.get(row.final_layer, "#888")))
            if column == 5 and row.total_ms is not None:
                limit = 150 if row.first_layer == "cache" else 600
                item.setForeground(QColor("#2e9e55" if row.total_ms <= limit else "#c9504a"))
            self.history.setItem(row.table_row, column, item)

    def _update_summary(self) -> None:
        from ..metrics import percentile

        done = [r for r in self._rows.values() if r.total_ms is not None]
        new = [r.total_ms for r in done if r.first_layer != "cache"]
        cached = [r.total_ms for r in done if r.first_layer == "cache"]
        parts = [f"{len(self._rows)} satır"]
        if new:
            parts.append(f"yeni: p50 {percentile(new, 50):.0f} / p95 {percentile(new, 95):.0f} ms")
        if cached:
            parts.append(f"cache: p50 {percentile(cached, 50):.0f} / p95 {percentile(cached, 95):.0f} ms")
        self.summary.setText("   ·   ".join(parts))

    def _clear_history(self) -> None:
        self._rows.clear()
        self._order.clear()
        self.history.setRowCount(0)
        self.summary.setText("")

    def _export_csv(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "gta-tr-subs", "Kaydedilecek satır yok.")
            return
        default = str(self.settings.base_dir / "out" / f"panel-{time.strftime('%Y%m%d-%H%M%S')}.csv")
        Path(default).parent.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Geçmişi kaydet", default, "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["saat", "ocr_metni", "ceviri", "kararli_ms", "ilk_katman", "ilk_ceviri_ms", "son_katman", "son_guncelleme_ms"])
            for job_id in self._order:
                r = self._rows[job_id]
                writer.writerow([r.clock, r.source, r.text, f"{r.stable_ms:.0f}", r.first_layer,
                                 "" if r.total_ms is None else f"{r.total_ms:.0f}", r.final_layer,
                                 "" if r.final_ms is None else f"{r.final_ms:.0f}"])
        self.status.setText(f"Kaydedildi: {path}")

    def _show_stats(self, event: StatsEvent) -> None:
        if event.preview is not None:
            image = np.ascontiguousarray(event.preview)
            h, w = image.shape[:2]
            qimage = QImage(image.data, w, h, w * 3, QImage.Format.Format_BGR888)
            self.roi_preview.setPixmap(QPixmap.fromImage(qimage).scaled(
                self.roi_preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        snap = event.snapshot
        stats: dict = snap.get("stats", {})
        rates: dict = snap.get("rates", {})
        counts: dict = snap.get("counts", {})
        values: dict = snap.get("values", {})
        lines = [
            f"yakalama        {rates.get('fps', 0.0):5.1f} fps     düşen kare {values.get('dropped', '0')}",
            f"OCR             {rates.get('ocr', 0.0):5.1f} Hz      {_stat(stats.get('ocr_ms'))}",
            f"değişim tespiti               {_stat(stats.get('detect_ms'))}",
            f"kararlı olma                  {_stat(stats.get('stable_ms'))}",
            f"bulut çeviri                  {_stat(stats.get('translate.cloud_ms'))}",
            f"yerel çeviri                  {_stat(stats.get('translate.local_ms'))}",
            "",
            f"TOPLAM  yeni satır            {_stat(stats.get('latency_new_ms'))}",
            f"TOPLAM  cache isabeti         {_stat(stats.get('latency_cache_ms'))}",
            "",
            f"mod: {MODE_LABELS.get(event.mode, event.mode)}     devre kesici: {values.get('breaker', 'closed')}"
            f"     son katman: {LAYER_LABELS.get(values.get('layer', ''), '—')}",
            f"satır {counts.get('lines', 0)}   cache {counts.get('layer.cache', 0)}   bulut {counts.get('layer.cloud', 0)}"
            f"   yerel {counts.get('layer.local', 0)}   yedek {counts.get('layer.fallback', 0)}"
            f"   çevrilemeyen {counts.get('translate.failed', 0)}",
            f"bulut: hata {counts.get('cloud.errors', 0)}   zaman aşımı {counts.get('cloud.timeouts', 0)}"
            f"   429 {counts.get('cloud.rate_limited', 0)}   kota nedeniyle atlanan {counts.get('cloud.skipped_rate_limit', 0)}",
            f"yerel hata {counts.get('local.errors', 0)}   gizleme {counts.get('hides', 0)}"
            f"   OCR'sız onay {counts.get('fast_confirms', 0)}",
        ]
        self.stats.setText("\n".join(lines))

    def _show_manual(self, event: ManualEvent) -> None:
        if event.done:
            if event.result is None:
                self.manual_output.appendPlainText("    ✗ hiçbir katman yanıt vermedi\n")
            else:
                self.manual_output.appendPlainText("")
            return
        result = event.result
        if result is None or result.partial:
            return
        label = LAYER_LABELS.get(result.layer, result.layer)
        self.manual_output.appendPlainText(f"    {label:<6} {result.ms:5.0f} ms   {result.text}")


def run_panel(settings: Settings, args) -> int:  # noqa: ANN001
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("gta-tr-subs")
    panel = TestPanel(settings, initial_video=args.source, fake=args.fake_translate,
                      simulate_offline=args.simulate_offline)
    panel.show()
    if args.source:
        QTimer.singleShot(200, panel.start)
    if getattr(args, "exit_after", 0):
        QTimer.singleShot(int(args.exit_after * 1000), panel.close)
        QTimer.singleShot(int(args.exit_after * 1000) + 200, app.quit)
    logger.info("Test paneli açıldı")
    return app.exec()
