"""Çeviri katmanlarını ölçer: ilk token süresi (TTFT), toplam süre ve örnek çıktılar.

    uv run python tools/bench_translate.py                 # tüm kullanılabilir katmanlar
    uv run python tools/bench_translate.py --layers cloud  # yalnızca Gemini
    uv run python tools/bench_translate.py --router        # yönlendirici (cache + yarış) uçtan uca

Gemini için GEMINI_API_KEY, yerel için çalışan Ollama + indirilmiş model gerekir;
olmayan katman atlanır.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from gta_tr_subs.config import load_settings
from gta_tr_subs.metrics import Metrics, percentile
from gta_tr_subs.translate import Glossary, build_router
from gta_tr_subs.translate.base import TranslationError

LINES = [
    "Jason, we need to get out of here right now.",
    "Relax, Lucia. Nobody saw us.",
    "You said that last time, and we ended up running from half of Vice City.",
    "Get in the car!",
    "I'm not going back to prison. Not for you, not for anybody.",
    "Who the hell taught you how to shoot? Your grandmother?",
    "Shut up and cover me.",
    "The cops are all over Leonida looking for us.",
    "Trust me, this is the easiest money we'll ever make.",
    "If this goes sideways, I'm blaming you.",
    "What the fuck was that?",
    "Easy, man. I'm just messing with you.",
]


def line_stats(label: str, values: list[float]) -> str:
    if not values:
        return f"  {label:<12} —"
    return (f"  {label:<12} ort {sum(values) / len(values):6.0f} ms   p50 {percentile(values, 50):6.0f} ms   "
            f"p95 {percentile(values, 95):6.0f} ms   max {max(values):6.0f} ms")


async def bench_layer(name: str, translator, lines: list[str], show: int) -> None:  # noqa: ANN001
    if translator is None or not translator.available:
        print(f"\n[{name}] kullanılamıyor → atlandı")
        return
    warm_start = time.perf_counter()
    await translator.warm_up()
    print(f"\n[{name}] model: {translator.model}   ısınma: {(time.perf_counter() - warm_start) * 1000:.0f} ms")
    if not translator.available:
        print("  ısınma sonrası kullanılamıyor → atlandı")
        return
    ttfts: list[float] = []
    totals: list[float] = []
    context: list[tuple[str, str]] = []
    failures = 0
    for i, line in enumerate(lines):
        first: list[float] = []
        start = time.perf_counter()

        def on_partial(_text: str) -> None:
            if not first:
                first.append((time.perf_counter() - start) * 1000.0)

        try:
            out = await translator.translate(line, context[-3:], on_partial)
        except TranslationError as exc:
            failures += 1
            print(f"  HATA: {exc}")
            continue
        total = (time.perf_counter() - start) * 1000.0
        totals.append(total)
        ttfts.append(first[0] if first else total)
        context.append((line, out))
        if i < show:
            print(f"  {total:5.0f} ms  {line}\n            → {out}")
    print(line_stats("ilk token", ttfts))
    print(line_stats("toplam", totals))
    if failures:
        print(f"  başarısız: {failures}/{len(lines)}")


async def bench_router(settings, lines: list[str], simulate_offline: bool) -> None:  # noqa: ANN001
    metrics = Metrics()
    router = build_router(settings, metrics, simulate_offline=simulate_offline, db_path=None)
    await router.warm_up()
    print(f"\n[yönlendirici] mod: {router.mode}   offline simülasyonu: {simulate_offline}")
    first_ms: list[float] = []
    layers: dict[str, int] = {}
    for round_name, batch in (("yeni satırlar", lines), ("tekrar (cache)", lines)):
        first_ms.clear()
        for line in batch:
            seen = []
            final = await router.translate(line, seen.append)
            complete = [r for r in seen if not r.partial]
            if complete:
                first_ms.append(complete[0].ms)
            if final is not None:
                layers[final.layer] = layers.get(final.layer, 0) + 1
        print(line_stats(round_name, list(first_ms)))
    print(f"  katman dağılımı: {layers}   devre kesici: {router.breaker.state}   "
          f"bulut hatası: {metrics.count('cloud.errors')}")
    await router.close()


async def main_async(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    lines = LINES * args.repeat
    if args.router:
        await bench_router(settings, lines, args.simulate_offline)
        return
    router = build_router(settings, db_path=None)
    glossary: Glossary = router.glossary
    print(f"Satır sayısı: {len(lines)}   sözlük: {len(glossary.keep)} isim, {len(glossary.terms)} terim")
    layers = {"cloud": router.cloud, "local": router.local, "fallback": router.fallback}
    for name in args.layers:
        await bench_layer(name, layers[name], lines, args.show)
    await router.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--layers", nargs="+", default=["cloud", "local", "fallback"],
                        choices=["cloud", "local", "fallback"])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--show", type=int, default=6, help="gösterilecek örnek çeviri sayısı")
    parser.add_argument("--router", action="store_true", help="yönlendiriciyi uçtan uca ölç")
    parser.add_argument("--simulate-offline", action="store_true")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
