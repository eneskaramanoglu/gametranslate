from gta_tr_subs.config import Rect, load_settings, save_values
from gta_tr_subs.translate.cache import TranslationCache, cache_key
from gta_tr_subs.translate.prompts import (
    Glossary,
    build_translategemma_prompt,
    build_user_prompt,
    clean_output,
)


# --------------------------------------------------------------------- cache
def test_cache_key_normalization():
    assert cache_key("Get in the car!") == cache_key("  get in  the CAR! ")
    assert cache_key("It’s fine.") == cache_key("It's fine")
    assert cache_key("What?") != cache_key("What.")  # soru bilgisi korunur
    assert cache_key("Wait...") == cache_key("Wait")


def test_memory_cache_and_stats():
    cache = TranslationCache(None, lru_size=8)
    assert cache.get("Hello.") is None
    cache.put("Hello.", "Merhaba.", "cloud", "m")
    entry = cache.get("hello")
    assert entry is not None and entry.text == "Merhaba." and entry.layer == "cloud"
    assert (cache.hits, cache.misses) == (1, 1)


def test_lru_eviction():
    cache = TranslationCache(None, lru_size=2)
    cache.put("one", "bir", "cloud")
    cache.put("two", "iki", "cloud")
    cache.get("one")  # "one" artık en yeni
    cache.put("three", "üç", "cloud")
    assert cache.get("two") is None
    assert cache.get("one") is not None and cache.get("three") is not None


def test_sqlite_persistence(tmp_path):
    db = tmp_path / "t.db"
    first = TranslationCache(db)
    first.put("Shut up and cover me.", "Kes sesini de beni koru.", "cloud", "gemini")
    first.close()
    second = TranslationCache(db)
    entry = second.get("shut up and cover me")
    assert entry is not None and entry.text == "Kes sesini de beni koru."
    assert len(second) == 1
    second.close()


def test_cloud_result_not_overwritten_by_local(tmp_path):
    db = tmp_path / "t.db"
    cache = TranslationCache(db)
    cache.put("Line", "yerel çeviri", "local")
    cache.put("Line", "bulut çeviri", "cloud")
    cache.put("Line", "yine yerel", "local")
    assert cache.get("Line").text == "bulut çeviri"
    cache.close()
    reopened = TranslationCache(db)
    assert reopened.get("Line").text == "bulut çeviri"
    reopened.close()


# ------------------------------------------------------------------- sözlük
def glossary() -> Glossary:
    g = Glossary(keep=["Lucia", "Jason", "Vice City", "Leonida"], terms={"wanted level": "aranma seviyesi"})
    g.keep.sort(key=len, reverse=True)
    return g


def test_glossary_load(tmp_path):
    path = tmp_path / "g.yaml"
    path.write_text("keep:\n  - Lucia\n  - Vice City\nterms:\n  safehouse: güvenli ev\n", encoding="utf-8")
    g = Glossary.load(path)
    assert g.keep == ["Vice City", "Lucia"] and g.terms == {"safehouse": "güvenli ev"}
    assert Glossary.load(tmp_path / "yok.yaml").keep == []


def test_names_and_terms_matching():
    g = glossary()
    assert g.names_in("Relax, lucia. We're leaving Vice City.") == ["Vice City", "Lucia"]
    assert g.names_in("That was a jasonic move") == []  # kelime sınırı
    assert g.terms_in("Your Wanted Level is rising") == {"wanted level": "aranma seviyesi"}


def test_prompt_contains_glossary_and_context():
    prompt = build_user_prompt(
        "Lucia, check your wanted level.", [("Hey.", "Selam."), ("What?", "Ne?")], glossary()
    )
    assert "Çevrilmeyecek özel isimler: Lucia" in prompt
    assert "wanted level = aranma seviyesi" in prompt
    assert "EN: Hey.\nTR: Selam." in prompt
    assert prompt.rstrip().endswith("EN: Lucia, check your wanted level.\nTR:")
    bare = build_user_prompt("Run!", [], glossary())
    assert "Önceki satırlar" not in bare and "özel isimler" not in bare


def test_enforce_repairs_transliterated_name():
    g = glossary()
    fixed, issues = g.enforce("I saw Lucia yesterday.", "Dün Lusiya'yı gördüm.")
    assert fixed == "Dün Lucia'yı gördüm." and issues == []
    same, issues = g.enforce("Relax, Lucia.", "Sakin ol, Lucia.")
    assert same == "Sakin ol, Lucia." and issues == []


def test_enforce_reports_untranslatable_fix():
    g = glossary()
    text, issues = g.enforce("Welcome to Vice City.", "Günah Şehri'ne hoş geldin.")
    assert issues == ["Vice City"] and text == "Günah Şehri'ne hoş geldin."


def test_clean_output():
    assert clean_output('TR: "Arabaya bin!"') == "Arabaya bin!"
    assert clean_output("  Arabaya   bin!\n") == "Arabaya bin!"
    assert clean_output("Çeviri: Kes sesini.") == "Kes sesini."
    assert clean_output('"Dur" dedi, "bekle."') == '"Dur" dedi, "bekle."'
    assert clean_output("") == ""


def test_translategemma_prompt_format():
    prompt = build_translategemma_prompt("Get in the car!")
    assert prompt.startswith("You are a professional English (en) to Turkish (tr) translator.")
    assert prompt.endswith("into Turkish:\n\n\nGet in the car!")


# -------------------------------------------------------------------- config
def test_rect_to_pixels_scales_with_window():
    rect = Rect()
    assert rect.to_pixels(1920, 1080) == (192, 842, 1536, 238)
    x, y, w, h = rect.to_pixels(1280, 720)
    assert (x, y, w, h) == (128, 562, 1024, 158)
    assert y + h <= 720 and x + w <= 1280


def test_save_values_roundtrip_keeps_comments(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '# üst yorum\n[capture]\nwindow_title = ""  # satır yorumu\n\n[roi]\ncalibrated = false\n', encoding="utf-8"
    )
    settings = load_settings(path)
    assert settings.capture.window_title == "" and not settings.roi.calibrated
    save_values(settings, {
        "capture.window_title": "PS Remote Play",
        "roi.subtitle": {"x": 0.2, "y": 0.7, "w": 0.6, "h": 0.2},
        "roi.speaker": None,
        "roi.calibrated": True,
    })
    text = path.read_text(encoding="utf-8")
    assert "# üst yorum" in text and "# satır yorumu" in text
    reloaded = load_settings(path)
    assert reloaded.capture.window_title == "PS Remote Play"
    assert reloaded.roi.calibrated and reloaded.roi.subtitle.x == 0.2 and reloaded.roi.speaker is None
