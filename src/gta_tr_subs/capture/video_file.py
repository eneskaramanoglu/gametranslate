"""Video dosyasını gerçek zamanlı kare kaynağı olarak oynatır (test için)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
from loguru import logger

from .base import Frame, FrameSource


class VideoFileSource(FrameSource):
    def __init__(self, path: str | Path, speed: float = 1.0, loop: bool = False) -> None:
        super().__init__()
        self.path = Path(path)
        self.speed = max(0.05, speed)
        self.loop = loop
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.video_fps = 30.0
        self.frame_count = 0

    @property
    def description(self) -> str:
        return f"video: {self.path.name}"

    def start(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Video bulunamadı: {self.path}")
        self._thread = threading.Thread(target=self._run, name="video-source", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            self.error = f"Video açılamadı: {self.path}"
            logger.error(self.error)
            self.finished.set()
            return
        self.video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        interval = 1.0 / (self.video_fps * self.speed)
        logger.info("Video oynatılıyor: {} ({:.1f} fps, hız x{})", self.path.name, self.video_fps, self.speed)
        index = 0
        video_index = 0
        start = time.perf_counter()
        try:
            while not self._stop.is_set():
                ok, image = cap.read()
                if not ok:
                    if self.loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        video_index = 0
                        start = time.perf_counter()
                        continue
                    break
                due = start + video_index * interval
                delay = due - time.perf_counter()
                if delay > 0:
                    self._stop.wait(delay)
                index += 1
                self.latest.put(
                    Frame(image, time.perf_counter(), index, media_ts=video_index / self.video_fps)
                )
                video_index += 1
        finally:
            cap.release()
            self.finished.set()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
