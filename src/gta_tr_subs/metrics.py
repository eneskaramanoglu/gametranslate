"""Hafif, thread-güvenli ölçüm kaydı (ms). Sabit boyutlu pencereler → sızıntı yok."""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


@dataclass(frozen=True)
class Stat:
    count: int
    last: float
    avg: float
    p50: float
    p95: float


class Metrics:
    def __init__(self, window: int = 512) -> None:
        self._window = window
        self._lock = threading.Lock()
        self._series: dict[str, deque[float]] = {}
        self._counts: dict[str, int] = {}
        self._ticks: dict[str, deque[float]] = {}
        self._values: dict[str, str] = {}

    def record(self, name: str, ms: float) -> None:
        with self._lock:
            series = self._series.get(name)
            if series is None:
                series = self._series[name] = deque(maxlen=self._window)
            series.append(ms)
            self._counts[name] = self._counts.get(name, 0) + 1

    @contextmanager
    def timer(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - start) * 1000.0)

    def tick(self, name: str) -> None:
        """Hız (Hz) ölçümü için olay işaretle."""
        now = time.perf_counter()
        with self._lock:
            ticks = self._ticks.get(name)
            if ticks is None:
                ticks = self._ticks[name] = deque(maxlen=240)
            ticks.append(now)

    def incr(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + by

    def set_value(self, name: str, value: str) -> None:
        with self._lock:
            self._values[name] = value

    def rate(self, name: str, horizon_s: float = 2.0) -> float:
        now = time.perf_counter()
        with self._lock:
            ticks = self._ticks.get(name)
            if not ticks:
                return 0.0
            recent = [t for t in ticks if now - t <= horizon_s]
        return len(recent) / horizon_s

    def count(self, name: str) -> int:
        with self._lock:
            return self._counts.get(name, 0)

    def stat(self, name: str) -> Stat | None:
        with self._lock:
            series = self._series.get(name)
            if not series:
                return None
            data = list(series)
            count = self._counts.get(name, len(data))
        return Stat(count, data[-1], sum(data) / len(data), percentile(data, 50), percentile(data, 95))

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            names = list(self._series)
            rates = list(self._ticks)
            counts = dict(self._counts)
            values = dict(self._values)
        return {
            "stats": {n: self.stat(n) for n in names},
            "rates": {n: self.rate(n) for n in rates},
            "counts": counts,
            "values": values,
        }
