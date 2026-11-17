"""İki OCR motorunu (Windows.Media.Ocr, RapidOCR) aynı görüntülerle ölçer: süre + karakter doğruluğu.

    uv run python tools/bench_ocr.py                       # sentetik altyazılar (1080p + 720p)
    uv run python tools/bench_ocr.py --images kareler/     # kendi ekran görüntülerin (tam kare)
        kareler/001.png + kareler/001.txt (beklenen metin, opsiyonel) ...
    uv run python tools/bench_ocr.py --images roi/ --cropped   # görüntüler zaten ROI kırpılmışsa

Ölçüm, uygulamanın kullandığı yolun aynısıdır (maske → ön işleme → OCR → satır birleştirme).
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz.distance import Levenshtein

from gta_tr_subs.config import load_settings
from gta_tr_subs.detect import bright_mask
from gta_tr_subs.metrics import percentile
from gta_tr_subs.ocr import OcrUnavailable, create_engine, read_roi
from gta_tr_subs.ocr.text import normalize_text
from synth import SAMPLE_LINES, background, draw_subtitle

Sample = tuple[str, np.ndarray, str | None]  # (ad, ROI görüntüsü BGR, beklenen metin)


def synthetic_samples(settings) -> list[Sample]:  # noqa: ANN001
    samples: list[Sample] = []
    texts = list(dict.fromkeys(text for _, _, text in SAMPLE_LINES))
    for width, height in ((1920, 1080), (1280, 720)):
        for bright in (0.0, 0.45):
            for i, text in enumerate(texts):
                frame = background(width, height, 1.0 + i * 0.7, bright)
                draw_subtitle(frame, text)
                x, y, w, h = settings.roi.subtitle.to_pixels(width, height)
                samples.append((f"{height}p-b{bright:.1f}-{i:02d}", frame[y : y + h, x : x + w].copy(),
                                normalize_text(text.replace("\n", " "))))
    return samples


def image_samples(folder: Path, settings, cropped: bool) -> list[Sample]:  # noqa: ANN001
    samples: list[Sample] = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            continue
        image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            continue
        if not cropped:
            x, y, w, h = settings.roi.subtitle.to_pixels(image.shape[1], image.shape[0])
            image = image[y : y + h, x : x + w]
        truth_file = path.with_suffix(".txt")
        truth = normalize_text(truth_file.read_text(encoding="utf-8").replace("\n", " ")) if truth_file.exists() else None
        samples.append((path.name, image, truth))
    return samples


async def bench(engine_name: str, samples: list[Sample], settings, repeat: int, show_errors: bool) -> None:  # noqa: ANN001
    try:
        engine = create_engine(settings.ocr, engine_name, allow_fallback=False)
    except OcrUnavailable as exc:
        print(f"\n[{engine_name}] kullanılamıyor:\n  " + str(exc).replace("\n", "\n  "))
        return
    await read_roi(engine, samples[0][1], bright_mask(samples[0][1], settings.detect), settings.ocr)  # ısınma
    times: list[float] = []
    distance = length = wrong = judged = 0
    errors: list[str] = []
    for name, roi, truth in samples:
        mask = bright_mask(roi, settings.detect)
        text = ""
        for _ in range(repeat):
            start = time.perf_counter()
            result = await read_roi(engine, roi, mask, settings.ocr)
            times.append((time.perf_counter() - start) * 1000.0)
            text = result.text
        if truth is not None:
            d = Levenshtein.distance(text, truth)
            judged += 1
            distance += min(d, len(truth))
            length += len(truth)
            if d:
                wrong += 1
                errors.append(f"    {name}: beklenen {truth!r}\n    {' ' * len(name)}  okunan   {text!r}")
    print(f"\n[{engine_name}]  {len(samples)} görüntü × {repeat} tekrar")
    print(f"  süre: ort {sum(times) / len(times):6.1f} ms   p50 {percentile(times, 50):6.1f} ms   "
          f"p95 {percentile(times, 95):6.1f} ms   max {max(times):6.1f} ms")
    if judged:
        print(f"  karakter doğruluğu: %{100 * (1 - distance / length):.2f}   hatalı satır: {wrong}/{judged}")
        if show_errors:
            print("\n".join(errors[:12]))
    else:
        print("  (beklenen metin dosyası yok → yalnızca süre ölçüldü)")


async def main_async(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    if args.upscale:
        settings.ocr.upscale = args.upscale
    if args.preprocess:
        settings.ocr.preprocess = args.preprocess
    samples = image_samples(Path(args.images), settings, args.cropped) if args.images else synthetic_samples(settings)
    if not samples:
        raise SystemExit("Görüntü bulunamadı")
    print(f"Örnek sayısı: {len(samples)}   ön işleme: {settings.ocr.preprocess}   büyütme: x{settings.ocr.upscale}")
    for engine_name in args.engines:
        await bench(engine_name, samples, settings, args.repeat, args.errors)
    print("\nMotor seçimi: config.toml → [ocr] engine = \"windows\" | \"rapid\"")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--images", default=None, help="test görüntüleri klasörü (yoksa sentetik)")
    parser.add_argument("--cropped", action="store_true", help="görüntüler zaten altyazı ROI'si")
    parser.add_argument("--engines", nargs="+", default=["windows", "rapid"], choices=["windows", "rapid"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--upscale", type=float, default=None, help="config'deki büyütmeyi geçersiz kıl")
    parser.add_argument("--preprocess", choices=["mask", "gray"], default=None)
    parser.add_argument("--errors", action="store_true", help="hatalı okunan satırları göster")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
