"""Test/replay için sahte çevirmen: ağ gerektirmez, gecikmesi ve hatası ayarlanabilir."""

from __future__ import annotations

import asyncio

from .base import OnPartial, TranslationError
from .prompts import Context


class FakeTranslator:
    def __init__(
        self,
        layer: str = "local",
        delay: float = 0.0,
        fail: bool = False,
        prefix: str = "[TR] ",
        available: bool = True,
        model: str = "fake",
    ) -> None:
        self.layer = layer
        self.model = model
        self.delay = delay
        self.fail = fail
        self.prefix = prefix
        self._available = available
        self.calls: list[str] = []
        self.cancelled = 0

    @property
    def available(self) -> bool:
        return self._available

    async def warm_up(self) -> None:
        return None

    async def translate(self, text: str, context: Context, on_partial: OnPartial | None = None) -> str:
        self.calls.append(text)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.fail:
            raise TranslationError(f"{self.layer}: sahte hata")
        return f"{self.prefix}{text}"

    async def close(self) -> None:
        return None
