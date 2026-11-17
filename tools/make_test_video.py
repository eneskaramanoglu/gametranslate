"""Sentetik altyazılı test videosu + ground_truth.srt üretir (oyun kaydı yokken test için).

    uv run python tools/make_test_video.py --out out/synthetic.mp4
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from synth import SAMPLE_LINES, background, draw_subtitle, write_srt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="out/synthetic.mp4")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--fade-frames", type=int, default=4, help="altyazı fade-in/out süresi (kare)")
    parser.add_argument("--bright", type=float, default=0.0, help="0..1 parlak sahne miktarı")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = max(start + length for start, length, _ in SAMPLE_LINES) + 1.5
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (args.width, args.height))
    if not writer.isOpened():
        raise SystemExit(f"Video yazılamadı: {out}")
    fade = max(1, args.fade_frames) / args.fps
    total = int(duration * args.fps)
    for index in range(total):
        t = index / args.fps
        frame = background(args.width, args.height, t, args.bright)
        for start, length, text in SAMPLE_LINES:
            if start <= t < start + length:
                alpha = min(1.0, (t - start + 1 / args.fps) / fade, (start + length - t) / fade)
                draw_subtitle(frame, text, alpha)
                break
        writer.write(frame)
    writer.release()
    srt = out.with_name("ground_truth.srt")
    write_srt(srt, SAMPLE_LINES)
    print(f"Yazıldı: {out} ({total} kare, {duration:.1f} sn) ve {srt}")


if __name__ == "__main__":
    main()
