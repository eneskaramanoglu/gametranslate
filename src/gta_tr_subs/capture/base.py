"""Kare kaynakları için ortak tipler.

Her kaynak kendi thread'inde çalışır ve *yalnızca son kareyi* tek elemanlı bir
yuvaya (``LatestFrame``) koyar; tüketici yavaşsa eski kareler düşer, birikme olmaz.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(slots=True)
class Frame:
    image: np.ndarray  # HxWx3 (BGR) veya HxWx4 (BGRA), uint8
    ts: float  # time.perf_counter() — karenin elimize ulaştığı an
    index: int
    media_ts: float | None = None  # video dosyası için kaynak zaman damgası (sn)


class LatestFrame:
    """Tek elemanlı, thread-güvenli "son kare" yuvası."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._frame: Frame | None = None
        self._last: Frame | None = None
        self._notify: Callable[[], None] | None = None
        self.dropped = 0

    def set_notifier(self, notify: Callable[[], None] | None) -> None:
        """Yeni kare geldiğinde (yakalama thread'inden) çağrılacak fonksiyon."""
        self._notify = notify

    def put(self, frame: Frame) -> None:
        with self._cond:
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame
            self._last = frame
            self._cond.notify_all()
        notify = self._notify
        if notify is not None:
            notify()

    def take(self) -> Frame | None:
        """Bekleyen kareyi alır (yoksa None); yuva boşalır."""
        with self._lock:
            frame, self._frame = self._frame, None
            return frame

    def wait(self, timeout: float | None = None) -> Frame | None:
        with self._cond:
            if self._frame is None:
                self._cond.wait(timeout)
            frame, self._frame = self._frame, None
            return frame

    def peek_last(self) -> Frame | None:
        """Tüketilmiş olsa bile en son gelen kare (kalibrasyon/önizleme için)."""
        with self._lock:
            return self._last


class FrameSource:
    """Kare kaynağı arayüzü."""

    def __init__(self) -> None:
        self.latest = LatestFrame()
        self.finished = threading.Event()  # kaynak bitti (video sonu / pencere kapandı)
        self.error: str | None = None

    def start(self) -> None:  # pragma: no cover - arayüz
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - arayüz
        raise NotImplementedError

    def screen_rect(self) -> tuple[int, int, int, int] | None:
        """Yakalanan içeriğin ekrandaki yeri (x, y, w, h), fiziksel piksel. Yoksa None."""
        return None

    @property
    def description(self) -> str:
        return type(self).__name__
