"""Bellek sızıntısı (soak) testi: videoyu döngüde hızlı oynatıp pipeline'ı N dakika çalıştırır,
işlem belleğini (RSS) periyodik örnekler.

    uv run python tools/soak_test.py out/synthetic.mp4 --minutes 5 --speed 4

Sahte çevirmen + bellek içi cache kullanır (ağ gerekmez). Çıktıdaki eğilim (MB/saat) ~0 olmalı.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import gc
import sys
import time
from ctypes import wintypes

from loguru import logger

from gta_tr_subs.capture.video_file import VideoFileSource
from gta_tr_subs.config import load_settings
from gta_tr_subs.metrics import Metrics
from gta_tr_subs.ocr import create_engine
from gta_tr_subs.pipeline import Pipeline, ShowEvent
from gta_tr_subs.translate import build_router


class _MemCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def private_mb() -> float:
    counters = _MemCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.PrivateUsage / (1024 * 1024)


async def run(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    metrics = Metrics()
    engine = create_engine(settings.ocr)
    router = build_router(settings, metrics, fake=True, db_path=None)
    source = VideoFileSource(args.video, speed=args.speed, loop=True)
    lines = 0

    def on_event(event: object) -> None:
        nonlocal lines
        if isinstance(event, ShowEvent) and event.first:
            lines += 1

    pipeline = Pipeline(settings, source, engine, router, metrics, on_event)
    source.start()
    task = asyncio.create_task(pipeline.run(warm_up=False))
    samples: list[tuple[float, float]] = []
    start = time.monotonic()
    duration = args.minutes * 60
    interval = max(5.0, duration / 24)
    while time.monotonic() - start < duration:
        await asyncio.sleep(interval)
        gc.collect()
        elapsed = time.monotonic() - start
        memory = private_mb()
        samples.append((elapsed, memory))
        print(f"  {elapsed / 60:5.1f} dk   bellek {memory:7.1f} MB   kare {pipeline.frames_processed:>7}   "
              f"altyazı {lines:>5}   OCR {metrics.count('ocr_ms'):>6}", flush=True)
    pipeline.stop()
    await task
    source.stop()
    await router.close()

    # ilk %25 ısınma sayılır; kalan örneklere doğrusal eğilim
    tail = samples[len(samples) // 4:]
    if len(tail) >= 3:
        n = len(tail)
        mean_t = sum(t for t, _ in tail) / n
        mean_m = sum(m for _, m in tail) / n
        slope = sum((t - mean_t) * (m - mean_m) for t, m in tail) / sum((t - mean_t) ** 2 for t, _ in tail)
        print(f"\nEğilim (ısınma sonrası): {slope * 3600:+.1f} MB/saat   "
              f"min {min(m for _, m in tail):.1f} MB   max {max(m for _, m in tail):.1f} MB")
        print(f"Toplam: {pipeline.frames_processed} kare, {lines} altyazı, hız x{args.speed} "
              f"(≈ {args.minutes * args.speed:.0f} dk'lık oyun içeriği)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video")
    parser.add_argument("--config", default=None)
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--speed", type=float, default=4.0)
    args = parser.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
