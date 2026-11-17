"""Qt uygulaması: pencere seçimi → yakalama → kalibrasyon → pipeline thread'i → overlay.

Thread modeli:
  - Ana thread: yalnızca Qt UI (overlay, debug paneli, diyaloglar). Asla bloklanmaz.
  - Yakalama thread'i: WGC / DXGI / video → ``LatestFrame`` (tek elemanlı yuva).
  - Pipeline thread'i: kendi asyncio döngüsü (değişim tespiti, OCR, çeviri ağ istekleri).
  - pynput thread'i: global kısayollar.
Thread'ler arası iletişim Qt sinyalleri (otomatik kuyruklu) ile yapılır.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import threading
import time

from loguru import logger
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QMessageBox, QSystemTrayIcon

from .capture.base import FrameSource
from .capture.picker import WindowPickerDialog
from .config import Settings, save_values
from .hotkeys import Hotkeys
from .metrics import Metrics
from .overlay import win32
from .overlay.window import DebugPanel, OverlayWindow
from .pipeline import HideEvent, Pipeline, ShowEvent, StatsEvent
from .roi.calibrate import CalibrationDialog
from .translate import MODE_LABELS, MODES


class Bridge(QObject):
    event = Signal(object)  # pipeline olayları
    hotkey = Signal(str)
    fatal = Signal(str)


class PipelineThread(threading.Thread):
    def __init__(self, settings: Settings, source: FrameSource, metrics: Metrics, bridge: Bridge,
                 args: argparse.Namespace) -> None:
        super().__init__(name="pipeline", daemon=True)
        self.settings = settings
        self.source = source
        self.metrics = metrics
        self.bridge = bridge
        self.args = args
        self.pipeline: Pipeline | None = None

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Pipeline thread'i çöktü")
            self.bridge.fatal.emit(f"Beklenmeyen hata: {type(exc).__name__}: {exc}")

    async def _main(self) -> None:
        # OCR motoru (WinRT) ve ağ istemcileri bu thread'de oluşturulur ve kullanılır
        from .ocr import OcrUnavailable, create_engine
        from .translate import build_router

        try:
            engine = create_engine(self.settings.ocr)
        except OcrUnavailable as exc:
            self.bridge.fatal.emit(str(exc))
            return
        router = build_router(
            self.settings, self.metrics,
            simulate_offline=self.args.simulate_offline, fake=self.args.fake_translate,
        )
        logger.info("OCR: {}   çeviri modu: {}   cache: {} kayıt", engine.name, MODE_LABELS[router.mode],
                    len(router.cache))
        self.pipeline = Pipeline(self.settings, self.source, engine, router, self.metrics, self.bridge.event.emit)
        try:
            await self.pipeline.run()
        finally:
            await router.close()


class VideoPreview(QLabel):
    """``--source video.mp4`` modunda videoyu gösteren pencere (overlay bunu takip eder)."""

    def __init__(self, source: FrameSource) -> None:
        super().__init__(None)
        self._source = source
        self.setWindowTitle("gta-tr-subs — video kaynağı (test)")
        self.setFixedSize(1280, 720)
        self.setStyleSheet("background:#000;")
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._last_index = -1

    def _refresh(self) -> None:
        frame = self._source.latest.peek_last()
        if frame is None or frame.index == self._last_index:
            return
        self._last_index = frame.index
        image = frame.image
        h, w = image.shape[:2]
        qimage = QImage(image.data, w, h, image.strides[0], QImage.Format.Format_BGR888)
        self.setPixmap(QPixmap.fromImage(qimage).scaled(
            self.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation))

    def screen_rect(self) -> tuple[int, int, int, int] | None:
        if not self.isVisible() or self.isMinimized():
            return None
        ratio = self.devicePixelRatioF()
        origin = self.mapToGlobal(self.rect().topLeft())
        return (int(origin.x() * ratio), int(origin.y() * ratio), int(self.width() * ratio), int(self.height() * ratio))


class App:
    def __init__(self, settings: Settings, args: argparse.Namespace) -> None:
        self.settings = settings
        self.args = args
        self.metrics = Metrics()
        self.bridge = Bridge()
        self.source: FrameSource | None = None
        self.hwnd: int | None = None
        self.preview_window: VideoPreview | None = None
        self.thread: PipelineThread | None = None
        self.overlay: OverlayWindow | None = None
        self.debug: DebugPanel | None = None
        self.hotkeys: Hotkeys | None = None
        self.tray: QSystemTrayIcon | None = None
        self._calibrating = False
        self._exit_code = 0

    # ---------------------------------------------------------------- kaynak
    def _choose_window(self, force_picker: bool) -> win32.WindowInfo | None:
        cfg = self.settings.capture
        wanted = self.args.window or cfg.window_title
        if wanted and not force_picker:
            found = win32.find_window(wanted)
            if found is not None:
                return found
            logger.warning("Kayıtlı pencere bulunamadı: {!r} → seçim penceresi açılıyor", wanted)
        dialog = WindowPickerDialog(cfg.suggest_keywords, wanted)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected is None:
            return None
        return dialog.selected

    def _make_source(self) -> FrameSource | None:
        cfg = self.settings.capture
        if self.args.source:
            from .capture.video_file import VideoFileSource

            source = VideoFileSource(self.args.source, speed=self.args.speed, loop=True)
            self.preview_window = VideoPreview(source)
            self.preview_window.show()
            return source
        window = self._choose_window(self.args.pick_window)
        if window is None:
            return None
        self.hwnd = window.hwnd
        if window.title != cfg.window_title:
            cfg.window_title = window.title
            save_values(self.settings, {"capture.window_title": window.title})
        if cfg.backend == "monitor":
            from .capture.monitor import MonitorSource

            return MonitorSource(cfg.monitor_index, cfg.fps, hwnd=window.hwnd)
        from .capture.wgc import WgcWindowSource

        return WgcWindowSource(window.hwnd, cfg.fps)

    def _wait_first_frame(self, timeout: float = 5.0):  # noqa: ANN202
        assert self.source is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.source.latest.peek_last()
            if frame is not None:
                return frame
            QApplication.processEvents()
            time.sleep(0.02)
        return None

    def _roi_screen_rect(self) -> tuple[int, int, int, int] | None:
        """Altyazı ROI'sinin ekrandaki yeri (fiziksel piksel)."""
        if self.preview_window is not None:
            rect = self.preview_window.screen_rect()
        elif self.source is not None:
            if self.hwnd is not None and win32.is_minimized(self.hwnd):
                return None
            rect = self.source.screen_rect()
        else:
            rect = None
        if rect is None:
            return None
        x, y, w, h = rect
        rx, ry, rw, rh = self.settings.roi.subtitle.to_pixels(w, h)
        return x + rx, y + ry, rw, rh

    # ---------------------------------------------------------- kalibrasyon
    def calibrate(self) -> None:
        if self._calibrating or self.source is None:
            return
        frame = self.source.latest.peek_last()
        if frame is None:
            QMessageBox.warning(None, "gta-tr-subs", "Henüz kare yakalanamadı; pencere simge durumunda olabilir.")
            return
        self._calibrating = True
        try:
            roi = self.settings.roi
            dialog = CalibrationDialog(frame.image, roi.subtitle, roi.speaker)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                if not roi.calibrated:
                    roi.calibrated = True  # varsayılanla devam; bir daha sorma
                    save_values(self.settings, {"roi.calibrated": True})
                return
            subtitle, speaker = dialog.result_rects()
            roi.subtitle, roi.speaker, roi.calibrated = subtitle, speaker, True
            save_values(self.settings, {
                "roi.subtitle": subtitle.model_dump(),
                "roi.speaker": speaker.model_dump() if speaker else None,
                "roi.calibrated": True,
            })
            logger.info("ROI kaydedildi: altyazı={} konuşmacı={}", subtitle, speaker)
            pipeline = self.thread.pipeline if self.thread else None
            if pipeline is not None:
                pipeline.set_roi(subtitle, speaker)
            if self.overlay is not None:
                self.overlay.hide_translation()
                self.overlay.follow_target()
        finally:
            self._calibrating = False

    # ----------------------------------------------------------------- olaylar
    def _on_event(self, event: object) -> None:
        if isinstance(event, ShowEvent):
            if self.overlay is not None:
                self.overlay.show_translation(event)
            if self.debug is not None:
                self.debug.note_translation(event)
            if not event.partial:
                logger.debug("[{} {:.0f} ms] {!r} → {!r}", event.layer, event.latency_ms, event.source, event.text)
        elif isinstance(event, HideEvent):
            if self.overlay is not None:
                self.overlay.hide_translation()
        elif isinstance(event, StatsEvent):
            if self.debug is not None:
                self.debug.update_stats(event)

    def _on_hotkey(self, action: str) -> None:
        pipeline = self.thread.pipeline if self.thread else None
        overlay = self.overlay
        if action == "toggle_overlay" and overlay is not None:
            enabled = not overlay.enabled
            overlay.set_enabled(enabled)
            if pipeline is not None:
                pipeline.set_paused(not enabled)
            overlay.flash("Çeviri: AÇIK" if enabled else "Çeviri: KAPALI")
        elif action == "recalibrate":
            self.calibrate()
        elif action == "toggle_english" and overlay is not None:
            shown = overlay.toggle_english()
            overlay.flash("İngilizce: göster" if shown else "İngilizce: gizle")
        elif action == "cycle_mode" and pipeline is not None:
            next_mode = MODES[(MODES.index(pipeline.router.mode) + 1) % len(MODES)]
            pipeline.cycle_mode()
            if overlay is not None:
                overlay.flash(f"Çeviri modu: {MODE_LABELS[next_mode]}")
        elif action == "debug_panel":
            self.toggle_debug()

    def toggle_debug(self) -> None:
        if self.debug is None:
            return
        visible = not self.debug.isVisible()
        self.debug.setVisible(visible)
        pipeline = self.thread.pipeline if self.thread else None
        if pipeline is not None:
            pipeline.preview_enabled = visible

    def _on_fatal(self, message: str) -> None:
        logger.error(message)
        QMessageBox.critical(None, "gta-tr-subs", message)
        self._exit_code = 1
        QApplication.quit()

    def _watchdog(self) -> None:
        """Kaynak bitti (pencere kapandı) veya pipeline durduysa çık; debug önizlemesini eşitle."""
        if self.thread is not None:
            pipeline = self.thread.pipeline
            if pipeline is not None and self.debug is not None:
                pipeline.preview_enabled = self.debug.isVisible()
            if not self.thread.is_alive():
                if self.source is not None and self.source.finished.is_set():
                    logger.warning("Kaynak sona erdi: {}", self.source.error or "yakalanan pencere kapandı")
                QApplication.quit()

    def _make_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(20, 22, 28))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 60, 60, 12, 12)
        font = QFont("Segoe UI")
        font.setPixelSize(30)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "TR")
        painter.end()
        tray = QSystemTrayIcon(QIcon(pixmap))
        tray.setToolTip("gta-tr-subs — Türkçe altyazı overlay")
        menu = QMenu()
        hk = self.settings.hotkeys
        for label, action in (
            (f"Çeviriyi aç/kapa  ({hk.toggle_overlay})", "toggle_overlay"),
            (f"Altyazı bölgesini kalibre et  ({hk.recalibrate})", "recalibrate"),
            (f"İngilizceyi göster/gizle  ({hk.toggle_english})", "toggle_english"),
            (f"Çeviri modunu değiştir  ({hk.cycle_mode})", "cycle_mode"),
            (f"Debug paneli  ({hk.debug_panel})", "debug_panel"),
        ):
            item = QAction(label, menu)
            item.triggered.connect(lambda _checked=False, a=action: self._on_hotkey(a))
            menu.addAction(item)
        menu.addSeparator()
        quit_action = QAction("Çıkış", menu)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.show()
        self.tray = tray
        self._tray_menu = menu

    # ------------------------------------------------------------------- run
    def run(self) -> int:
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName("gta-tr-subs")

        self.source = self._make_source()
        if self.source is None:
            logger.info("Pencere seçilmedi, çıkılıyor")
            return 0
        try:
            self.source.start()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(None, "gta-tr-subs", f"Yakalama başlatılamadı:\n{exc}")
            return 1

        frame = self._wait_first_frame()
        if frame is None:
            logger.warning("İlk kare 5 sn içinde gelmedi (pencere simge durumunda mı?)")
        elif not self.settings.roi.calibrated or self.args.calibrate:
            self.calibrate()

        self.bridge.event.connect(self._on_event)
        self.bridge.hotkey.connect(self._on_hotkey)
        self.bridge.fatal.connect(self._on_fatal)

        self.overlay = OverlayWindow(self.settings.overlay, self._roi_screen_rect)
        self.overlay.start()
        self.debug = DebugPanel(on_quit=QApplication.quit)
        self._make_tray()

        self.thread = PipelineThread(self.settings, self.source, self.metrics, self.bridge, self.args)
        self.thread.start()
        if self.args.debug:
            self.toggle_debug()

        self.hotkeys = Hotkeys(self.settings.hotkeys, self.bridge.hotkey.emit)
        self.hotkeys.start()

        watchdog = QTimer()
        watchdog.setInterval(300)
        watchdog.timeout.connect(self._watchdog)
        watchdog.start()
        # Ctrl+C: Python sinyal işleyicisi Qt döngüsü içinde de çalışsın
        signal.signal(signal.SIGINT, lambda *_: QApplication.quit())

        if self.args.exit_after:
            QTimer.singleShot(int(self.args.exit_after * 1000), QApplication.quit)

        logger.info("Hazır. Çıkış: tepsi simgesi → Çıkış, debug panelindeki düğme veya konsolda Ctrl+C")
        try:
            app.exec()
        finally:
            watchdog.stop()
            if self.hotkeys is not None:
                self.hotkeys.stop()
            if self.thread.pipeline is not None:
                self.thread.pipeline.stop()
            self.thread.join(timeout=4.0)
            self.source.stop()
            if self.tray is not None:
                self.tray.hide()
        return self._exit_code
