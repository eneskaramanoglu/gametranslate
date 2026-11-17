"""Komut satırı girişi.

    uv run gta-tr-subs                      # overlay uygulaması
    uv run gta-tr-subs --source video.mp4   # kare kaynağı olarak video (test)
    uv run gta-tr-subs --console            # overlay yok; kararlı altyazıları konsola yaz
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger

from .config import Settings, load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gta-tr-subs", description="PS5 oyun altyazısı → anlık Türkçe overlay")
    parser.add_argument("--config", default=None, help="config.toml yolu")
    parser.add_argument("--source", default=None, metavar="VIDEO", help="kare kaynağı olarak video dosyası")
    parser.add_argument("--speed", type=float, default=1.0, help="video oynatma hızı")
    parser.add_argument("--window", default=None, metavar="BAŞLIK", help="yakalanacak pencere başlığı (alt dize)")
    parser.add_argument("--pick-window", action="store_true", help="pencere seçim diyaloğunu zorla")
    parser.add_argument("--list-windows", action="store_true", help="yakalanabilir pencereleri listele ve çık")
    parser.add_argument("--calibrate", action="store_true", help="açılışta ROI kalibrasyonunu aç")
    parser.add_argument("--mode", choices=["race", "cloud_first", "local_only"], default=None)
    parser.add_argument("--engine", choices=["windows", "rapid"], default=None, help="OCR motoru")
    parser.add_argument("--simulate-offline", action="store_true", help="bulut istemcisini hataya zorla")
    parser.add_argument("--fake-translate", action="store_true", help="sahte çevirmenler (ağ/model gerekmez)")
    parser.add_argument("--console", action="store_true", help="overlay olmadan konsol modu")
    parser.add_argument("--ui", action="store_true", help="test paneli: kaynak seç, canlı izle, gecikmeleri ölç")
    parser.add_argument("--debug", action="store_true", help="debug panelini aç + ayrıntılı log")
    parser.add_argument("--exit-after", type=float, default=0.0, metavar="SN", help="N saniye sonra çık (duman testi)")
    return parser


def setup_logging(settings: Settings, debug: bool) -> None:
    level = "DEBUG" if debug else settings.log_level
    logger.remove()
    if sys.stderr is not None:
        logger.add(sys.stderr, level=level, format="<green>{time:HH:mm:ss.SSS}</green> <level>{level:<7}</level> {message}")
    log_dir = settings.base_dir / "logs"
    try:
        log_dir.mkdir(exist_ok=True)
        logger.add(log_dir / "gta-tr-subs.log", level=level, rotation="5 MB", retention=3, encoding="utf-8")
    except OSError:
        pass


async def run_console(settings: Settings, args: argparse.Namespace) -> int:
    """Overlay olmadan: kararlı altyazıları ve çevirilerini konsola yazar."""
    from .capture.base import FrameSource
    from .metrics import Metrics
    from .ocr import OcrUnavailable, create_engine
    from .overlay import win32
    from .pipeline import Pipeline, ShowEvent
    from .translate import build_router

    source: FrameSource
    if args.source:
        from .capture.video_file import VideoFileSource

        source = VideoFileSource(args.source, speed=args.speed)
    else:
        title = args.window or settings.capture.window_title
        window = win32.find_window(title) if title else None
        if window is None:
            logger.error("Pencere bulunamadı: {!r}. --window BAŞLIK ver veya --list-windows ile bak.", title)
            return 1
        if settings.capture.backend == "monitor":
            from .capture.monitor import MonitorSource

            source = MonitorSource(settings.capture.monitor_index, settings.capture.fps, hwnd=window.hwnd)
        else:
            from .capture.wgc import WgcWindowSource

            source = WgcWindowSource(window.hwnd, settings.capture.fps)
    try:
        engine = create_engine(settings.ocr)
    except OcrUnavailable as exc:
        logger.error("{}", exc)
        return 1
    metrics = Metrics()
    router = build_router(settings, metrics, simulate_offline=args.simulate_offline, fake=args.fake_translate)

    def on_event(event: object) -> None:
        if isinstance(event, ShowEvent) and not event.partial:
            tag = "" if event.first else " (güncellendi)"
            print(f"[{event.layer:<8} {event.latency_ms:5.0f} ms]{tag}\n   EN: {event.source}\n   TR: {event.text}", flush=True)

    pipeline = Pipeline(settings, source, engine, router, metrics, on_event)
    source.start()
    logger.info("Konsol modu: {} — çıkmak için Ctrl+C", source.description)
    if args.exit_after:
        asyncio.get_running_loop().call_later(args.exit_after, pipeline.stop)
    try:
        await pipeline.run()
        await pipeline.drain()
    finally:
        source.stop()
        await router.close()
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    if args.mode:
        settings.translate.mode = args.mode
    if args.engine:
        settings.ocr.engine = args.engine
    setup_logging(settings, args.debug)
    logger.info("Config: {}", settings.config_path)

    if args.list_windows:
        from .overlay import win32

        keywords = settings.capture.suggest_keywords
        for window in sorted(win32.list_windows(), key=lambda w: win32.suggestion_rank(w.title, keywords)):
            star = "★" if win32.suggestion_rank(window.title, keywords) < len(keywords) else " "
            print(f"{star} {window.title}")
        return

    if args.source and not Path(args.source).exists():
        logger.error("Video bulunamadı: {}", args.source)
        sys.exit(1)

    if args.console:
        try:
            sys.exit(asyncio.run(run_console(settings, args)))
        except KeyboardInterrupt:
            sys.exit(0)

    if args.ui:
        from .ui.test_panel import run_panel

        sys.exit(run_panel(settings, args))

    from .app import App

    sys.exit(App(settings, args).run())


if __name__ == "__main__":
    main()
