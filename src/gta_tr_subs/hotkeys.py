"""Global klavye kısayolları (pynput). Geri çağrılar pynput thread'inde çalışır."""

from __future__ import annotations

from typing import Callable

from loguru import logger

from .config import HotkeyConfig

ACTIONS = ("toggle_overlay", "recalibrate", "toggle_english", "cycle_mode", "debug_panel")


class Hotkeys:
    def __init__(self, cfg: HotkeyConfig, dispatch: Callable[[str], None]) -> None:
        self.cfg = cfg
        self._dispatch = dispatch
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception as exc:  # noqa: BLE001
            logger.error("Kısayollar başlatılamadı (pynput): {}", exc)
            return False
        mapping: dict[str, Callable[[], None]] = {}
        for action in ACTIONS:
            combo = getattr(self.cfg, action, "")
            if combo:
                mapping[combo] = (lambda a=action: self._dispatch(a))
        try:
            self._listener = keyboard.GlobalHotKeys(mapping)
        except ValueError as exc:
            logger.error("Geçersiz kısayol tanımı ({}). Örnek biçim: \"<f8>\", \"<ctrl>+<alt>+t\"", exc)
            return False
        self._listener.daemon = True
        self._listener.start()
        logger.info("Kısayollar: " + "  ".join(f"{getattr(self.cfg, a)}={a}" for a in ACTIONS))
        return True

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
