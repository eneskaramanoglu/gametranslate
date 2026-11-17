"""Metin stabilizasyonu + dedupe.

Altyazılar belirirken/kaybolurken fade olur; yarım okunan metin çeviriye gitmemeli.
Kural: aynı (veya benzerlik ≥ eşik) metin ``required_hits`` ardışık OCR'da görülünce
"kararlı" sayılır. Son gösterilenle aynıysa yeniden çevrilmez.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from .config import StabilizeConfig

Kind = Literal["pending", "candidate", "stable", "same", "gone", "empty"]


@dataclass(frozen=True, slots=True)
class StabResult:
    """``feed`` sonucu.

    - ``pending``: henüz karar yok, OCR'a devam
    - ``candidate``: yeni bir aday metin ilk kez görüldü (spekülatif çeviri başlatılabilir)
    - ``stable``: yeni kararlı metin → çevir ve göster
    - ``same``: kararlı ama zaten gösterilenle aynı → hiçbir şey yapma
    - ``gone``: gösterilen altyazı kayboldu → overlay'i gizle
    - ``empty``: okunacak metin yok (zaten bir şey gösterilmiyordu)
    """

    kind: Kind
    text: str = ""
    extends_previous: bool = False

    @property
    def settled(self) -> bool:
        """True ise bir sonraki görüntü değişimine kadar OCR'a gerek yok."""
        return self.kind in ("stable", "same", "gone", "empty")


def similar(a: str, b: str, threshold: float) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    return fuzz.ratio(a.casefold(), b.casefold()) >= threshold


def is_extension(new: str, old: str, threshold: float = 92.0) -> bool:
    """``new``, ``old`` metninin devamı mı (karakter karakter yazılan altyazı)?"""
    if not old or len(new) <= len(old):
        return False
    return fuzz.ratio(new[: len(old)].casefold(), old.casefold()) >= threshold


class Stabilizer:
    def __init__(self, cfg: StabilizeConfig) -> None:
        self.cfg = cfg
        self._candidate: str | None = None
        self._hits = 0
        self._misses = 0
        self._shown: str | None = None

    @property
    def shown(self) -> str | None:
        return self._shown

    def reset(self) -> None:
        self._candidate = None
        self._hits = 0
        self._misses = 0
        self._shown = None

    def clear_shown(self) -> None:
        """Altyazı dışarıdan (ör. maske boşaldı) gizlendi."""
        self._shown = None
        self._candidate = None
        self._hits = 0
        self._misses = 0

    def feed(self, text: str | None) -> StabResult:
        cfg = self.cfg
        if not text:
            self._candidate = None
            self._hits = 0
            self._misses += 1
            if self._misses >= cfg.required_misses:
                if self._shown is not None:
                    self._shown = None
                    return StabResult("gone")
                return StabResult("empty")
            return StabResult("pending")

        self._misses = 0
        first_sight = False
        if self._candidate is not None and similar(text, self._candidate, cfg.similarity):
            self._hits += 1
            self._candidate = text  # en güncel okuma (fade bittikçe daha doğru)
        else:
            self._candidate = text
            self._hits = 1
            first_sight = True

        if self._hits >= cfg.required_hits:
            if self._shown is not None and similar(text, self._shown, cfg.similarity):
                return StabResult("same", text)
            previous = self._shown
            self._shown = text
            return StabResult("stable", text, extends_previous=is_extension(text, previous or "", cfg.similarity))

        if first_sight and not (self._shown is not None and similar(text, self._shown, cfg.similarity)):
            return StabResult("candidate", text)
        return StabResult("pending", text)
