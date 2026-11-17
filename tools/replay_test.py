"""Bir videoyu kare kaynağı olarak oynatıp tüm pipeline'ı başsız çalıştırır; her altyazı
için ölçümleri CSV'ye yazar ve p50/p95 gecikme, OCR doğruluğu, cache isabet özetini basar.

    uv run python tools/replay_test.py out/synthetic.mp4 --ground-truth out/ground_truth.srt
    uv run python tools/replay_test.py kayit.mp4 --simulate-offline
    uv run python tools/replay_test.py kayit.mp4 --fake-translate     # ağ/model olmadan
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from rapidfuzz.distance import Levenshtein

from gta_tr_subs.capture.video_file import VideoFileSource
from gta_tr_subs.config import load_settings
from gta_tr_subs.metrics import Metrics, percentile
from gta_tr_subs.ocr import create_engine
from gta_tr_subs.ocr.text import normalize_text
from gta_tr_subs.pipeline import HideEvent, Pipeline, ShowEvent
from gta_tr_subs.translate import build_router


@dataclass
class Row:
    job_id: int
    media_ts: float | None
    wall_s: float
    ocr_text: str
    stable_ms: float
    first_layer: str
    first_text_ms: float
    final_layer: str = ""
    translate_ms: float = 0.0
    total_ms: float = 0.0  # belirme → ilk TAM çeviri
    translation: str = ""
    complete: bool = False


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    def seconds(stamp: str) -> float:
        h, m, rest = stamp.strip().split(":")
        s, ms = rest.replace(".", ",").split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    entries = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        timing = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing is None:
            continue
        start, end = lines[timing].split("-->")
        text = normalize_text(" ".join(lines[timing + 1:]))
        text = re.sub(r"<[^>]+>", "", text)
        if text:
            entries.append((seconds(start), seconds(end), text))
    return entries


def compare(rows: list[Row], truth: list[tuple[float, float, str]]) -> tuple[float, int, list[str]]:
    """Karakter doğruluğu, kaçırılan satır sayısı ve hatalı okuma örnekleri."""
    distance = 0
    length = 0
    missed = 0
    examples: list[str] = []
    for start, end, text in truth:
        candidates = [r for r in rows if r.media_ts is not None and start - 0.35 <= r.media_ts <= end]
        length += len(text)
        if not candidates:
            missed += 1
            distance += len(text)
            examples.append(f"KAÇTI  {text!r}")
            continue
        best = min(candidates, key=lambda r: Levenshtein.distance(r.ocr_text, text))
        d = Levenshtein.distance(best.ocr_text, text)
        distance += min(d, len(text))
        if d:
            examples.append(f"d={d}  beklenen {text!r}\n        okunan   {best.ocr_text!r}")
    accuracy = 1.0 - distance / length if length else 0.0
    return accuracy, missed, examples


async def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if args.engine:
        settings.ocr.engine = args.engine
    if args.mode:
        settings.translate.mode = args.mode
    metrics = Metrics(window=100_000)
    engine = create_engine(settings.ocr)
    router = build_router(
        settings, metrics,
        simulate_offline=args.simulate_offline,
        fake=args.fake_translate,
        db_path=args.cache_db,  # varsayılan None: bellek içi → koşular karşılaştırılabilir
    )
    source = VideoFileSource(args.video, speed=args.speed)

    rows: dict[int, Row] = {}
    hides: list[float] = []
    started = time.perf_counter()
    media_offset: list[float] = []

    def on_event(event: object) -> None:
        now = time.perf_counter()
        if isinstance(event, ShowEvent):
            row = rows.get(event.job_id)
            if row is None:
                row = rows[event.job_id] = Row(
                    job_id=event.job_id, media_ts=event.media_ts, wall_s=now - started,
                    ocr_text=event.source, stable_ms=event.stable_ms,
                    first_layer=event.layer, first_text_ms=event.latency_ms,
                )
                if event.media_ts is not None and not media_offset:
                    # yaklaşık: belirme anının duvar saati = now - latency
                    media_offset.append((now - event.latency_ms / 1000.0) - event.media_ts)
            if not event.partial:
                if not row.complete:
                    row.complete = True
                    row.total_ms = event.latency_ms
                row.final_layer = event.layer
                row.translate_ms = event.translate_ms
                row.translation = event.text
        elif isinstance(event, HideEvent) and event.reason in ("empty", "gone"):
            hides.append(now)

    pipeline = Pipeline(settings, source, engine, router, metrics, on_event)
    await router.warm_up()
    source.start()
    await pipeline.run(warm_up=False)
    source.stop()
    await router.close()

    ordered = [rows[k] for k in sorted(rows)]
    out = Path(args.csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "job", "video_zamani_s", "duvar_zamani_s", "ocr_metni", "kararli_olma_ms", "ilk_katman",
            "ilk_metin_ms", "son_katman", "ceviri_ms", "toplam_gecikme_ms", "ceviri",
        ])
        for r in ordered:
            writer.writerow([
                r.job_id, "" if r.media_ts is None else f"{r.media_ts:.3f}", f"{r.wall_s:.3f}", r.ocr_text,
                f"{r.stable_ms:.0f}", r.first_layer, f"{r.first_text_ms:.0f}", r.final_layer,
                f"{r.translate_ms:.0f}", f"{r.total_ms:.0f}" if r.complete else "", r.translation,
            ])

    def summary(name: str, values: list[float]) -> str:
        if not values:
            return f"  {name:<34} —"
        return (f"  {name:<34} n={len(values):<4} p50={percentile(values, 50):6.0f} ms  "
                f"p95={percentile(values, 95):6.0f} ms  max={max(values):6.0f} ms")

    done = [r for r in ordered if r.complete]
    new = [r.total_ms for r in done if r.first_layer != "cache"]
    cached = [r.total_ms for r in done if r.first_layer == "cache"]
    layers: dict[str, int] = {}
    for r in done:
        layers[r.final_layer] = layers.get(r.final_layer, 0) + 1

    print("\n================ REPLAY ÖZETİ ================")
    print(f"  video: {args.video}   kare: {pipeline.frames_processed}   düşen kare: {source.latest.dropped}")
    print(f"  OCR motoru: {engine.name}   mod: {router.mode}   offline simülasyonu: {args.simulate_offline}")
    print(f"  altyazı: {len(ordered)}   çevrilen: {len(done)}   çevrilemeyen: {len(ordered) - len(done)}")
    print(summary("toplam gecikme — yeni satır", new))
    print(summary("toplam gecikme — cache isabeti", cached))
    print(summary("ilk metin (kısmi dahil) — yeni", [r.first_text_ms for r in ordered if r.first_layer != "cache"]))
    print(summary("kararlı olma süresi", [r.stable_ms for r in ordered]))
    for name, label in (("ocr_ms", "OCR süresi"), ("detect_ms", "değişim tespiti"),
                        ("translate.cloud_ms", "bulut çeviri"), ("translate.local_ms", "yerel çeviri")):
        stat = metrics.stat(name)
        if stat:
            print(f"  {label:<34} n={stat.count:<4} p50={stat.p50:6.1f} ms  p95={stat.p95:6.1f} ms")
    if ordered:
        hits = sum(r.first_layer == "cache" for r in ordered)
        print(f"  cache isabet oranı (satır bazında)  {hits}/{len(ordered)} = %{100 * hits / len(ordered):.0f}")
    print(f"  son katman dağılımı                {layers}")
    print(f"  bulut hata sayısı: {metrics.count('cloud.errors')}   devre kesici: {router.breaker.state}")

    truth_path = Path(args.ground_truth) if args.ground_truth else None
    if truth_path and truth_path.exists():
        truth = parse_srt(truth_path)
        accuracy, missed, examples = compare(ordered, truth)
        print(f"  OCR karakter doğruluğu             %{accuracy * 100:.2f}  ({len(truth)} satır, kaçan {missed})")
        for example in examples[:8]:
            print("    " + example)
        if media_offset and hides:
            delays = []
            for _start, end, _text in truth:
                wall_end = media_offset[0] + end
                after = [h - wall_end for h in hides if h >= wall_end - 0.05]
                if after and min(after) < 1.0:
                    delays.append(min(after) * 1000.0)
            print(summary("kaybolma → overlay gizleme", delays))
    print(f"  CSV: {out}")
    print("==============================================")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video")
    parser.add_argument("--config", default=None)
    parser.add_argument("--csv", default="out/replay.csv")
    parser.add_argument("--ground-truth", default=None, help="elle hazırlanmış .srt (OCR doğruluğu için)")
    parser.add_argument("--simulate-offline", action="store_true", help="bulut istemcisini hataya zorla")
    parser.add_argument("--fake-translate", action="store_true", help="sahte çevirmenler (ağ/model gerekmez)")
    parser.add_argument("--engine", choices=["windows", "rapid"], default=None)
    parser.add_argument("--mode", choices=["race", "cloud_first", "local_only"], default=None)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--cache-db", default=None, help="kalıcı cache dosyası (varsayılan: bellek içi)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.verbose else "INFO", format="<level>{level:<7}</level> {message}")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
