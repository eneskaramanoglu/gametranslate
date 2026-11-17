"""Monitör yakalama yedeği (DXGI Desktop Duplication).

``windows-capture`` paketinin DXGI oturumunu kullanır (dxcam/bettercam ile aynı
mekanizma, ek bağımlılık yok). Hedef pencere verilirse yalnızca onun ekrandaki
alanı kırpılır. Overlay ``WDA_EXCLUDEFROMCAPTURE`` sayesinde bu yakalamaya da girmez.
"""

from __future__ import annotations

import threading
import time

from loguru import logger

from ..overlay import win32
from .base import Frame, FrameSource


class MonitorSource(FrameSource):
    def __init__(self, monitor_index: int = 1, fps: int = 30, hwnd: int | None = None) -> None:
        super().__init__()
        self.monitor_index = monitor_index
        self.fps = max(1, fps)
        self.hwnd = hwnd
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._origin = (0, 0)
        self._rect: tuple[int, int, int, int] | None = None

    @property
    def description(self) -> str:
        return f"DXGI monitör #{self.monitor_index}"

    def start(self) -> None:
        self._origin = _monitor_origin(self.monitor_index)
        self._thread = threading.Thread(target=self._run, name="monitor-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from windows_capture import DxgiDuplicationSession

        try:
            session = DxgiDuplicationSession(self.monitor_index)
        except Exception as exc:
            self.error = f"Monitör yakalama başlatılamadı: {exc}"
            logger.error(self.error)
            self.finished.set()
            return
        logger.info("Yakalama başladı: {}", self.description)
        interval = 1.0 / self.fps
        index = 0
        next_at = time.perf_counter()
        while not self._stop.is_set():
            try:
                native = session.acquire_frame(timeout_ms=int(interval * 1000))
            except Exception as exc:  # çözünürlük değişimi vb. → oturumu yenile
                logger.warning("DXGI oturumu yenileniyor: {}", exc)
                time.sleep(0.2)
                try:
                    session.recreate()
                except Exception as exc2:
                    logger.error("DXGI yenileme hatası: {}", exc2)
                    time.sleep(1.0)
                continue
            if native is None:
                continue
            now = time.perf_counter()
            full = native.to_bgr()
            ox, oy = self._origin
            if self.hwnd is not None and (bounds := win32.frame_bounds(self.hwnd)):
                x, y, w, h = bounds
                x0, y0 = max(x - ox, 0), max(y - oy, 0)
                x1, y1 = min(x - ox + w, full.shape[1]), min(y - oy + h, full.shape[0])
                if x1 - x0 < 16 or y1 - y0 < 16:
                    continue
                image = full[y0:y1, x0:x1].copy()
                self._rect = (x0 + ox, y0 + oy, x1 - x0, y1 - y0)
            else:
                image = full.copy()
                self._rect = (ox, oy, full.shape[1], full.shape[0])
            index += 1
            self.latest.put(Frame(image, now, index))
            next_at += interval
            delay = next_at - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_at = time.perf_counter()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def screen_rect(self) -> tuple[int, int, int, int] | None:
        return self._rect


def _monitor_origin(monitor_index: int) -> tuple[int, int]:
    """1 tabanlı monitör indeksinin sanal masaüstündeki sol-üst köşesi."""
    if not win32.IS_WINDOWS:
        return 0, 0
    import ctypes
    from ctypes import wintypes

    origins: list[tuple[int, int]] = []
    proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def callback(_mon, _dc, rect, _lparam) -> bool:  # noqa: ANN001
        origins.append((rect.contents.left, rect.contents.top))
        return True

    win32.user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
    if 1 <= monitor_index <= len(origins):
        return origins[monitor_index - 1]
    return 0, 0
