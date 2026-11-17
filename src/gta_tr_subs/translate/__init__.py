from __future__ import annotations

from ..config import Settings
from ..metrics import Metrics
from .base import TranslationError, TranslationResult, Translator
from .cache import TranslationCache, cache_key
from .prompts import Glossary
from .router import MODE_LABELS, MODES, CircuitBreaker, Router

__all__ = [
    "CircuitBreaker", "Glossary", "MODES", "MODE_LABELS", "Router", "TranslationCache",
    "TranslationError", "TranslationResult", "Translator", "build_router", "cache_key",
]


def build_router(
    settings: Settings,
    metrics: Metrics | None = None,
    *,
    simulate_offline: bool = False,
    fake: bool = False,
    db_path: str | None = "",
) -> Router:
    """Config'e göre tüm katmanları kurar.

    ``fake=True``: ağ/model gerektirmeyen sahte çevirmenler (pipeline testi için).
    ``db_path``: "" → config'deki yol, None → yalnızca bellek içi.
    """
    cfg = settings.translate
    glossary = Glossary.load(settings.resolve(cfg.glossary_path))
    if fake and db_path == "":
        db_path = None  # sahte çeviriler kalıcı önbelleği kirletmesin
    cache_path = settings.resolve(settings.cache.db_path) if db_path == "" else db_path
    cache = TranslationCache(cache_path, settings.cache.lru_size)

    cloud: Translator | None
    local: Translator | None
    fallback: Translator | None = None
    if fake:
        from .fake import FakeTranslator

        cloud = FakeTranslator("cloud", delay=0.35, prefix="[bulut] ")
        local = FakeTranslator("local", delay=0.12, prefix="[yerel] ")
    else:
        from .gemini import GeminiTranslator
        from .ollama_local import OllamaTranslator

        cloud = GeminiTranslator(cfg.gemini, glossary)
        local = OllamaTranslator(cfg.ollama, glossary) if cfg.ollama.enabled else None
        if cfg.ct2.enabled:
            from .ct2_fallback import Ct2Translator

            fallback = Ct2Translator(cfg.ct2, settings.resolve(cfg.ct2.model_dir))

    router = Router(cfg, cache, cloud, local, fallback, glossary, metrics, settings.cache)
    router.simulate_offline = simulate_offline
    return router
