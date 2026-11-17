"""LLM prompt'ları + sözlük (glossary) uygulaması."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger
from rapidfuzz import fuzz

Context = list[tuple[str, str]]  # (İngilizce, Türkçe) — eskiden yeniye

SYSTEM_PROMPT = """GTA 6 oyun altyazısını doğal, konuşma dilinde Türkçeye çevir. Sadece çeviriyi yaz, açıklama ekleme.

Kurallar:
- Kısa ve okunabilir tut; altyazı uzunluğunu aşırı uzatma.
- Ton birebir korunsun. Kaynakta küfür varsa (fuck, shit, bitch, asshole...) sansürlemeden, yumuşatmadan aynı
  sertlikte çevir. Kaynakta küfür YOKSA çeviriye küfür EKLEME. "hell", "damn", "crap" gibi hafif ifadeleri
  hafif karşıla (lan, be, kahretsin, ne halt); bunları ağır küfre çevirme.
- Özel isimleri (kişi, yer, marka, araç, radyo istasyonu) çevirme, aynen bırak.
- Verilen bağlam satırlarını çevirme; yalnızca zamir, hitap (sen/siz) ve ton tutarlılığı için kullan.
- Çıktı tek satır olsun: tırnak, etiket ("TR:"), not veya alternatif ekleme.
- Metin OCR ile okunmuştur; bariz okuma hatalarını (l/I, rn/m, 0/O) anlamına göre düzelterek çevir."""

# TranslateGemma model kartındaki resmi kullanıcı prompt'u (iki boş satırdan sonra metin).
TRANSLATEGEMMA_TEMPLATE = (
    "You are a professional {src_lang} ({src_code}) to {tgt_lang} ({tgt_code}) translator. "
    "Your goal is to accurately convey the meaning and nuances of the original {src_lang} text "
    "while adhering to {tgt_lang} grammar, vocabulary, and cultural sensitivities.\n"
    "Produce only the {tgt_lang} translation, without any additional explanations or commentary. "
    "Please translate the following {src_lang} text into {tgt_lang}:\n\n\n{text}"
)


@dataclass
class Glossary:
    keep: list[str] = field(default_factory=list)  # çevrilmeyecek özel isimler
    terms: dict[str, str] = field(default_factory=dict)  # İngilizce → Türkçe sabit terimler

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        path = Path(path)
        if not path.exists():
            logger.warning("Sözlük dosyası bulunamadı: {}", path)
            return cls()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error("Sözlük okunamadı ({}): {}", path, exc)
            return cls()
        keep = [str(k).strip() for k in (data.get("keep") or []) if str(k).strip()]
        terms = {str(k).strip(): str(v).strip() for k, v in (data.get("terms") or {}).items()}
        # uzun isimler önce: "Leonida Keys", "Leonida"dan önce eşleşsin
        keep.sort(key=len, reverse=True)
        return cls(keep=keep, terms=terms)

    def names_in(self, text: str) -> list[str]:
        return [name for name in self.keep if _contains_word(text, name)]

    def terms_in(self, text: str) -> dict[str, str]:
        return {en: tr for en, tr in self.terms.items() if _contains_word(text, en)}

    def prompt_block(self, text: str) -> str:
        """Bu satırla ilgili sözlük talimatları (yoksa boş)."""
        parts: list[str] = []
        names = self.names_in(text)
        if names:
            parts.append("Çevrilmeyecek özel isimler: " + ", ".join(names))
        terms = self.terms_in(text)
        if terms:
            parts.append("Sabit terimler: " + "; ".join(f"{en} = {tr}" for en, tr in terms.items()))
        return "\n".join(parts)

    def enforce(self, source: str, translation: str) -> tuple[str, list[str]]:
        """Çeviri sonrası özel isim kontrolü.

        Kaynakta geçen ama çeviride olmayan isimleri, çevirideki benzer yazımlı
        kelimeyi düzelterek geri koymaya çalışır ("Lusiya'yı" → "Lucia'yı").
        Düzeltilemeyenler ``issues`` listesinde döner.
        """
        issues: list[str] = []
        for name in self.names_in(source):
            if name.casefold() in translation.casefold():
                continue
            fixed = _repair_name(translation, name)
            if fixed is None:
                issues.append(name)
            else:
                translation = fixed
        return translation, issues


def _contains_word(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, flags=re.IGNORECASE) is not None


_TOKEN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)


def _repair_name(translation: str, name: str) -> str | None:
    """Tek kelimelik isimlerde, çevirideki en benzer kelimeyi isimle değiştirir."""
    if " " in name:
        return None
    best: tuple[float, re.Match[str]] | None = None
    for match in _TOKEN.finditer(translation):
        stem = re.split(r"['’]", match.group(0))[0]
        score = fuzz.ratio(stem.casefold(), name.casefold())
        # Türkçe ek almış olabilir: "Lusiyayı" → baş kısmı karşılaştır
        score = max(score, fuzz.ratio(stem[: len(name)].casefold(), name.casefold()) - 5)
        if score >= 72 and (best is None or score > best[0]):
            best = (score, match)
    if best is None:
        return None
    match = best[1]
    word = match.group(0)
    suffix = ""
    apostrophe = re.search(r"['’]", word)
    if apostrophe:
        suffix = word[apostrophe.start():]
    return translation[: match.start()] + name + suffix + translation[match.end():]


def build_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_user_prompt(text: str, context: Context, glossary: Glossary | None) -> str:
    parts: list[str] = []
    if glossary is not None:
        block = glossary.prompt_block(text)
        if block:
            parts.append(block)
    if context:
        lines = ["Önceki satırlar (bağlam, çevirme):"]
        for en, tr in context:
            lines.append(f"EN: {en}")
            lines.append(f"TR: {tr}")
        parts.append("\n".join(lines))
    parts.append(f"Çevrilecek satır:\nEN: {text}\nTR:")
    return "\n\n".join(parts)


def build_translategemma_prompt(text: str) -> str:
    return TRANSLATEGEMMA_TEMPLATE.format(
        src_lang="English", src_code="en", tgt_lang="Turkish", tgt_code="tr", text=text
    )


_LABEL = re.compile(r"^\s*(?:TR|Türkçe|Çeviri|Translation)\s*[:：]\s*", re.IGNORECASE)


def clean_output(text: str) -> str:
    """Model çıktısından etiket, sarmalayan tırnak ve fazladan satırları temizler."""
    text = text.strip()
    if not text:
        return ""
    # ilk dolu satır (modeller bazen açıklama ekler)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    text = " ".join(lines) if len(lines) <= 3 and not any(_LABEL.match(l) for l in lines[1:]) else lines[0]
    text = _LABEL.sub("", text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”" and text.count(text[0]) == 2:
        text = text[1:-1].strip()
    elif len(text) >= 2 and text[0] == "“" and text[-1] == "”":
        text = text[1:-1].strip()
    return re.sub(r"\s+", " ", text)
