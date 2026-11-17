"""OCR çıktısını temizleme: satır birleştirme, normalize etme, geçerlilik kontrolü."""

from __future__ import annotations

import re
import unicodedata

_QUOTES = str.maketrans(
    {
        "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
        "‘": "'", "’": "'", "‚": "'", "`": "'", "´": "'",
        "–": "-", "—": "-", "−": "-",
        " ": " ",
    }
)
_WS = re.compile(r"\s+")
_DOTS = re.compile(r"(?:\.\s*){3,}|…")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:])")
_EDGE_JUNK = re.compile(r"^[\s|_~^*=\\/<>\[\]{}•·]+|[\s|_~^*=\\/<>\[\]{}•·]+$")
_STANDALONE_PIPE = re.compile(r"(?<![A-Za-z])[|](?![A-Za-z])")
_WORD = re.compile(r"[A-Za-z]{2,}")
_I_CONTRACTION = re.compile(r"(?<![\w'])[1l|]'(m|ll|ve|d)\b")
_I_DOUBLED = re.compile(r"(?<![\w'])11(m|ll|ve)\b")
_LONE_L = re.compile(r"(?<![\w'])l(?![\w'])")


def clean_line(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    text = _DOTS.sub("...", text)
    text = _STANDALONE_PIPE.sub("I", text)  # tek başına "|" → "I"
    # Sık görülen "I" karışıklıkları: l'm / 1'm / 11m → I'm, tek başına "l" → "I"
    text = _I_CONTRACTION.sub(lambda m: "I'" + m.group(1), text)
    text = _I_DOUBLED.sub(lambda m: "I'" + m.group(1), text)
    text = _LONE_L.sub("I", text)
    text = _EDGE_JUNK.sub("", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    return _WS.sub(" ", text).strip()


def join_lines(lines: list[str]) -> str:
    """Satırları tek cümlede birleştirir; satır sonu tirelemesini düzeltir."""
    out = ""
    for raw in lines:
        line = clean_line(raw)
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-") and len(out) >= 2 and out[-2].isalpha() and line[0].islower():
            out = out[:-1] + line  # "some-" + "thing" → "something"
        else:
            out = f"{out} {line}"
    return normalize_text(out)


def normalize_text(text: str) -> str:
    text = clean_line(text)
    # tırnak içi baş/son boşluk: " hello " → "hello"
    text = re.sub(r'"\s+([^"]*?)\s+"', r'"\1"', text)
    return text


def alpha_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(c.isalpha() for c in chars) / len(chars)


def is_meaningful(text: str, min_chars: int = 2, min_alpha_ratio: float = 0.55) -> bool:
    """Çok kısa, harf oranı düşük veya kelime içermeyen metinleri eler."""
    if len(text) < min_chars:
        return False
    if alpha_ratio(text) < min_alpha_ratio:
        return False
    if not _WORD.search(text) and text.strip(" .!?,'\"-").upper() not in {"I", "A", "O"}:
        return False
    return True


def is_noise_line(text: str) -> bool:
    """Arka plandan gelen tek tük karakter satırları ("l", "—", ". ,")."""
    stripped = text.strip()
    if not stripped:
        return True
    letters = sum(c.isalpha() for c in stripped)
    if letters == 0:
        return True
    if letters == 1 and stripped.strip(" .!?,'\"-").upper() not in {"I", "A", "O"}:
        return True
    return alpha_ratio(stripped) < 0.4
