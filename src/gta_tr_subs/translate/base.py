"""Çeviri sağlayıcıları için ortak tipler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .prompts import Context

OnPartial = Callable[[str], None]


class TranslationError(RuntimeError):
    """Sağlayıcı hatası (ağ, zaman aşımı, boş yanıt...)."""


class RateLimited(TranslationError):
    """Sağlayıcı kota sınırı (HTTP 429). `retry_after` saniye sonra yeniden denenebilir."""

    def __init__(self, message: str, retry_after: float = 60.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class TranslationResult:
    text: str
    layer: str  # "cache" | "cloud" | "local" | "fallback"
    ms: float  # istek başlangıcından bu sonuca kadar geçen süre
    partial: bool = False  # akış hâlâ sürüyor
    model: str = ""


class Translator(Protocol):
    layer: str
    model: str

    @property
    def available(self) -> bool:
        ...

    async def warm_up(self) -> None:
        ...

    async def translate(self, text: str, context: Context, on_partial: OnPartial | None = None) -> str:
        """Çeviriyi döndürür; başarısızlıkta ``TranslationError`` fırlatır."""
        ...

    async def close(self) -> None:
        ...
