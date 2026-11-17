"""Uygulama yapılandırması: ``config.toml`` → pydantic modelleri.

Çalışma anında değişen değerler (pencere başlığı, ROI, mod...) ``save_values`` ile
yorumlar korunarak (tomlkit) dosyaya geri yazılır.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import tomlkit
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_NAME = "config.toml"


class Rect(BaseModel):
    """Yakalanan kareye oransal (0..1) dikdörtgen."""

    x: float = 0.10
    y: float = 0.78
    w: float = 0.80
    h: float = 0.22

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        x0 = min(max(int(round(self.x * width)), 0), max(width - 2, 0))
        y0 = min(max(int(round(self.y * height)), 0), max(height - 2, 0))
        x1 = min(max(int(round((self.x + self.w) * width)), x0 + 2), width)
        y1 = min(max(int(round((self.y + self.h) * height)), y0 + 2), height)
        return x0, y0, x1 - x0, y1 - y0


class CaptureConfig(BaseModel):
    window_title: str = ""  # boşsa açılışta sorulur
    backend: Literal["wgc", "monitor"] = "wgc"
    monitor_index: int = 1  # backend = "monitor" için (1 tabanlı)
    fps: int = 30
    suggest_keywords: list[str] = [
        "PS Remote Play",
        "Remote Play",
        "chiaki",
        "Projector",
        "Projektör",
    ]


class RoiConfig(BaseModel):
    subtitle: Rect = Rect()
    speaker: Rect | None = None  # opsiyonel ikinci ROI (konuşmacı adı)
    calibrated: bool = False


class DetectConfig(BaseModel):
    bright_threshold: int = 170  # V (max kanal) eşiği: altyazı pikseli
    downscale: int = 4
    change_ratio: float = 0.06  # XOR / birleşim oranı; üstü "değişti"
    empty_ratio: float = 0.0015  # maske doluluk oranı; altı "altyazı yok"
    hide_delay_ms: int = 250
    require_outline: bool = False  # parlak piksel yanında koyu kontur şartı
    dark_threshold: int = 90


class OcrConfig(BaseModel):
    engine: Literal["windows", "rapid"] = "windows"
    language: str = "en-US"
    max_hz: float = 15.0
    upscale: float = 2.0
    preprocess: Literal["mask", "gray"] = "mask"  # OCR'a giden görüntü (bkz. ocr/preprocess.py)
    min_confidence: float = 0.5  # yalnızca güven skoru veren motorlar (rapid)
    min_chars: int = 2
    min_alpha_ratio: float = 0.55
    rapid_use_dml: bool = False


class StabilizeConfig(BaseModel):
    required_hits: int = 2
    required_misses: int = 2
    similarity: float = 92.0
    # İlk okumada çeviriyi önden başlat, kararlı olunca göster. ~1 kare kazandırır ama
    # fade sırasındaki yarım okumalar için fazladan API çağrısı yapar → varsayılan kapalı.
    speculative: bool = False
    # Sonraki karede görüntü (maske) değişmediyse ikinci OCR'ı beklemeden onayla
    fast_confirm: bool = True
    fast_confirm_diff: float = 0.02


class GeminiConfig(BaseModel):
    enabled: bool = True
    model: str = "gemini-3.5-flash-lite"
    temperature: float = 0.2
    # "auto": modele göre en düşük seviye; "off": thinking_budget=0;
    # "minimal"/"low": thinking_level; "default": hiç gönderme
    thinking: Literal["auto", "off", "minimal", "low", "default"] = "auto"
    max_output_tokens: int = 200
    stream_partials: bool = True


class OllamaConfig(BaseModel):
    enabled: bool = True
    url: str = "http://localhost:11434"
    model: str = "translategemma:4b"
    keep_alive: str = "2h"
    timeout_ms: int = 4000
    num_predict: int = 160


class Ct2Config(BaseModel):
    enabled: bool = False
    model_dir: str = "models/opus-mt-tc-big-en-tr-ct2"
    compute_type: str = "int8"
    threads: int = 4


class TranslateConfig(BaseModel):
    mode: Literal["race", "cloud_first", "local_only"] = "race"
    cloud_timeout_ms: int = 1200
    context_lines: int = 3
    breaker_failures: int = 2
    breaker_cooldown_s: float = 30.0
    glossary_path: str = "glossary.yaml"
    gemini: GeminiConfig = GeminiConfig()
    ollama: OllamaConfig = OllamaConfig()
    ct2: Ct2Config = Ct2Config()


class CacheConfig(BaseModel):
    db_path: str = "translations.db"
    lru_size: int = 4096
    cache_local_results: bool = True  # yerel sonuç da yazılır; bulut gelince üzerine yazılır


class OverlayConfig(BaseModel):
    enabled: bool = True
    opacity: float = 0.82  # arka plan kutusu opaklığı
    font_family: str = "Segoe UI"
    font_bold: bool = True
    max_lines: int = 3
    min_font_px: int = 14
    max_font_px: int = 64
    show_english: bool = False
    show_badge: bool = True
    exclude_from_capture: bool = True


class HotkeyConfig(BaseModel):
    toggle_overlay: str = "<f8>"
    recalibrate: str = "<f9>"
    toggle_english: str = "<f10>"
    cycle_mode: str = "<f11>"
    debug_panel: str = "<f12>"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    capture: CaptureConfig = CaptureConfig()
    roi: RoiConfig = RoiConfig()
    detect: DetectConfig = DetectConfig()
    ocr: OcrConfig = OcrConfig()
    stabilize: StabilizeConfig = StabilizeConfig()
    translate: TranslateConfig = TranslateConfig()
    cache: CacheConfig = CacheConfig()
    overlay: OverlayConfig = OverlayConfig()
    hotkeys: HotkeyConfig = HotkeyConfig()
    log_level: str = "INFO"

    # yüklenen dosyanın yolu (kaydetmek ve göreli yolları çözmek için)
    config_path: Path | None = Field(default=None, exclude=True)

    @property
    def base_dir(self) -> Path:
        return self.config_path.parent if self.config_path else Path.cwd()

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.base_dir / p


def find_config(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("GTA_TR_SUBS_CONFIG")
    if env:
        return Path(env).resolve()
    cwd = Path.cwd() / DEFAULT_CONFIG_NAME
    if cwd.exists():
        return cwd
    # depo kökü: src/gta_tr_subs/config.py → ../../..
    repo = Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_NAME
    return repo if repo.exists() else cwd


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    cfg_path = find_config(path)
    data: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            # utf-8-sig: Not Defteri'nin eklediği BOM'u yok say
            data = tomlkit.parse(cfg_path.read_text(encoding="utf-8-sig")).unwrap()
        except Exception as exc:
            raise SystemExit(f"config okunamadı ({cfg_path}): {exc}") from exc
    settings = Settings(**data)
    settings.config_path = cfg_path
    return settings


def save_values(settings: Settings, values: dict[str, Any]) -> None:
    """``{"roi.subtitle": {...}, "capture.window_title": "..."}`` biçimindeki
    değerleri config dosyasına (yorumları koruyarak) yazar."""
    path = settings.config_path or (Path.cwd() / DEFAULT_CONFIG_NAME)
    doc = tomlkit.parse(path.read_text(encoding="utf-8-sig")) if path.exists() else tomlkit.document()
    for dotted, value in values.items():
        keys = dotted.split(".")
        node: Any = doc
        for key in keys[:-1]:
            if key not in node or not isinstance(node[key], dict):
                node[key] = tomlkit.table()
            node = node[key]
        if value is None:
            if keys[-1] in node:
                del node[keys[-1]]
        elif isinstance(value, dict):
            table = tomlkit.inline_table()
            table.update(value)
            node[keys[-1]] = table
        else:
            node[keys[-1]] = value
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)
