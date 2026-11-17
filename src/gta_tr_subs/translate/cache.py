"""Katman 0 — önbellek: bellek içi LRU + kalıcı SQLite.

Anahtar = normalize edilmiş İngilizce metin. GTA'da tekrar eden satır çoktur.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ..ocr.text import normalize_text

_KEY_WS = re.compile(r"\s+")
_KEY_TRAILING = re.compile(r"[\s.!?…]+$")


def cache_key(text: str) -> str:
    """Büyük/küçük harf, boşluk, tırnak ve sondaki noktalama farklarını yok sayar."""
    key = normalize_text(text).casefold()
    key = _KEY_WS.sub(" ", key)
    trimmed = _KEY_TRAILING.sub("", key)
    # "What?" ile "What." aynı çeviriyi almasın: soru/ünlem bilgisini koru
    marker = "?" if "?" in key[len(trimmed):] else ("!" if "!" in key[len(trimmed):] else "")
    return (trimmed + marker).strip()


@dataclass(frozen=True, slots=True)
class CacheEntry:
    text: str
    layer: str
    model: str


class TranslationCache:
    def __init__(self, db_path: str | Path | None, lru_size: int = 4096) -> None:
        self._lru: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lru_size = max(1, lru_size)
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self.hits = 0
        self.misses = 0
        if db_path is not None:
            try:
                path = Path(db_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=2.0)
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
                self._db.execute(
                    """CREATE TABLE IF NOT EXISTS translations (
                        key TEXT PRIMARY KEY,
                        src TEXT NOT NULL,
                        dst TEXT NOT NULL,
                        layer TEXT NOT NULL,
                        model TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL,
                        hits INTEGER NOT NULL DEFAULT 0
                    )"""
                )
                self._db.commit()
            except sqlite3.Error as exc:
                logger.error("Önbellek veritabanı açılamadı ({}): {} — yalnızca bellek içi çalışılacak", db_path, exc)
                self._db = None

    def _remember(self, key: str, entry: CacheEntry) -> None:
        self._lru[key] = entry
        self._lru.move_to_end(key)
        while len(self._lru) > self._lru_size:
            self._lru.popitem(last=False)

    def get(self, text: str) -> CacheEntry | None:
        key = cache_key(text)
        if not key:
            return None
        with self._lock:
            entry = self._lru.get(key)
            if entry is not None:
                self._lru.move_to_end(key)
                self.hits += 1
                return entry
            if self._db is not None:
                try:
                    row = self._db.execute(
                        "SELECT dst, layer, model FROM translations WHERE key = ?", (key,)
                    ).fetchone()
                except sqlite3.Error as exc:
                    logger.warning("Önbellek okuma hatası: {}", exc)
                    row = None
                if row is not None:
                    entry = CacheEntry(row[0], row[1], row[2])
                    self._remember(key, entry)
                    self.hits += 1
                    return entry
            self.misses += 1
            return None

    def put(self, text: str, translation: str, layer: str, model: str = "") -> None:
        key = cache_key(text)
        if not key or not translation:
            return
        entry = CacheEntry(translation, layer, model)
        with self._lock:
            existing = self._lru.get(key)
            # bulut sonucu, daha düşük kaliteli katmanın sonucuyla ezilmesin
            if existing is not None and existing.layer == "cloud" and layer != "cloud":
                return
            self._remember(key, entry)
            if self._db is not None:
                try:
                    self._db.execute(
                        """INSERT INTO translations (key, src, dst, layer, model, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(key) DO UPDATE SET
                               dst = excluded.dst, layer = excluded.layer,
                               model = excluded.model, created_at = excluded.created_at
                           WHERE translations.layer != 'cloud' OR excluded.layer = 'cloud'""",
                        (key, text, translation, layer, model, time.time()),
                    )
                    self._db.commit()
                except sqlite3.Error as exc:
                    logger.warning("Önbellek yazma hatası: {}", exc)

    def __len__(self) -> int:
        with self._lock:
            if self._db is not None:
                try:
                    return int(self._db.execute("SELECT COUNT(*) FROM translations").fetchone()[0])
                except sqlite3.Error:
                    pass
            return len(self._lru)

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
