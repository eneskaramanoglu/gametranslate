"""Windows Graphics Capture ile pencere yakalama (``windows-capture``).

Pencere yakalama, pencerenin *kendi* içeriğini verir; üstündeki overlay'i görmez.
"""

from __future__ import annotations

import time

from loguru import logger

from ..overlay import win32
from .base import Frame, FrameSource


class WgcWindowSource(FrameSource):
    def __init__(self, hwnd: int, fps: int = 30) -> None:
        super().__init__()
        self.hwnd = hwnd
        self.fps = max(1, fps)
        self._control = None
        self._index = 0
        self._title = win32.window_title(hwnd)

    @property
    def description(self) -> str:
        return f"WGC pencere: {self._title!r}"

    def start(self) -> None:
        from windows_capture import WindowsCapture

        interval_ms = max(1, int(1000 / self.fps) - 2)
        kwargs = dict(cursor_capture=False, draw_border=False, window_hwnd=self.hwnd)
        try:
            capture = WindowsCapture(minimum_update_interval=interval_ms, **kwargs)
        except Exception as exc:  # eski Windows sürümü: sınır/aralık ayarı desteklenmeyebilir
            logger.warning("WGC gelişmiş ayarlar desteklenmiyor ({}), varsayılanlarla deneniyor", exc)
            capture = WindowsCapture(cursor_capture=False, window_hwnd=self.hwnd)

        min_gap = 1.0 / self.fps * 0.75
        last = 0.0

        @capture.event
        def on_frame_arrived(frame, capture_control) -> None:  # noqa: ANN001
            nonlocal last
            now = time.perf_counter()
            if now - last < min_gap:
                return
            last = now
            # frame_buffer yalnızca callback süresince geçerli → kopyala (BGRA→BGR)
            image = frame.frame_buffer[:, :, :3].copy()
            self._index += 1
            self.latest.put(Frame(image, now, self._index))

        @capture.event
        def on_closed() -> None:
            logger.warning("Yakalanan pencere kapandı")
            self.finished.set()

        self._control = capture.start_free_threaded()
        logger.info("Yakalama başladı: {}", self.description)

    def stop(self) -> None:
        control, self._control = self._control, None
        if control is not None:
            try:
                control.stop()
            except Exception as exc:  # zaten bitmiş olabilir
                logger.debug("WGC durdurma: {}", exc)

    def screen_rect(self) -> tuple[int, int, int, int] | None:
        return win32.frame_bounds(self.hwnd)
