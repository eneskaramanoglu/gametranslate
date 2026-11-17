from gta_tr_subs.config import StabilizeConfig
from gta_tr_subs.ocr.text import is_meaningful, is_noise_line, join_lines, normalize_text
from gta_tr_subs.stabilize import Stabilizer, is_extension, similar


def make(**kw) -> Stabilizer:
    return Stabilizer(StabilizeConfig(**kw))


# ---------------------------------------------------------------- stabilizasyon
def test_needs_two_consecutive_hits():
    s = make()
    first = s.feed("Get in the car!")
    assert first.kind == "candidate" and not first.settled
    second = s.feed("Get in the car!")
    assert second.kind == "stable" and second.text == "Get in the car!" and second.settled


def test_half_read_text_is_not_translated():
    s = make()
    assert s.feed("Get in").kind == "candidate"  # fade sırasında yarım okuma
    assert s.feed("Get in the car!").kind == "candidate"  # farklı → sayaç sıfırlanır
    assert s.feed("Get in the car!").kind == "stable"


def test_fuzzy_match_counts_as_same():
    s = make()
    s.feed("Relax, Lucia. Nobody saw us.")
    result = s.feed("Relax, Lucia. Nobody saw us")  # tek karakter fark (≥ 92)
    assert result.kind == "stable"


def test_dedupe_same_as_shown():
    s = make()
    s.feed("Shut up and cover me.")
    s.feed("Shut up and cover me.")
    # arka plan değişti, OCR tekrar çalıştı, metin aynı → yeniden çevirme
    assert s.feed("Shut up and cover me.").kind in ("same", "pending")
    again = s.feed("Shut up and cover me.")
    assert again.kind == "same" and again.settled


def test_no_candidate_event_for_text_already_shown():
    s = make()
    s.feed("Hello there.")
    s.feed("Hello there.")
    s.feed("")  # tek kaçırma: aday sıfırlanır ama altyazı hâlâ "gösteriliyor"
    assert s.feed("Hello there.").kind == "pending"  # spekülatif çeviri başlatma


def test_gone_after_required_misses():
    s = make()
    s.feed("Then drive like you mean it.")
    s.feed("Then drive like you mean it.")
    assert s.feed(None).kind == "pending"
    gone = s.feed(None)
    assert gone.kind == "gone" and s.shown is None


def test_empty_when_nothing_was_shown():
    s = make()
    assert s.feed("").kind == "pending"
    assert s.feed("").kind == "empty"


def test_new_line_replaces_old_and_extension_flag():
    s = make()
    s.feed("Who the hell")
    s.feed("Who the hell")
    s.feed("Who the hell taught you how to shoot?")
    result = s.feed("Who the hell taught you how to shoot?")
    assert result.kind == "stable" and result.extends_previous
    s.feed("Your grandmother?")
    other = s.feed("Your grandmother?")
    assert other.kind == "stable" and not other.extends_previous


def test_required_hits_configurable():
    s = make(required_hits=1)
    assert s.feed("Go!").kind == "stable"
    s3 = make(required_hits=3)
    assert s3.feed("Go now!").kind == "candidate"
    assert s3.feed("Go now!").kind == "pending"
    assert s3.feed("Go now!").kind == "stable"


def test_similar_and_extension_helpers():
    assert similar("Get in the car!", "get in the car!", 92)
    assert not similar("Get in the car!", "Get in the van!", 92)
    assert is_extension("Hey Jason, wait up", "Hey Jason")
    assert not is_extension("Hey", "Hey Jason")


# ------------------------------------------------------------------ OCR metni
def test_join_lines_and_hyphenation():
    assert join_lines(["You said that last time, and we ended up", "running from half of Vice City."]) == (
        "You said that last time, and we ended up running from half of Vice City."
    )
    assert join_lines(["This is some-", "thing else"]) == "This is something else"
    assert join_lines(["Wait -", "What?"]) == "Wait - What?"


def test_normalize_quotes_dots_spaces():
    assert normalize_text("  “Hey”   you ,  wait … ") == '"Hey" you, wait...'
    assert normalize_text("It’s  fine . . .") == "It's fine..."
    assert normalize_text("| don't know") == "I don't know"


def test_common_ocr_confusions_of_capital_i():
    assert normalize_text("11m not going back.") == "I'm not going back."
    assert normalize_text("l'm fine, l'll wait") == "I'm fine, I'll wait"
    assert normalize_text("1've seen it") == "I've seen it"
    assert normalize_text("What do l do?") == "What do I do?"
    assert normalize_text("All 11 of them, 1 by 1") == "All 11 of them, 1 by 1"  # sayılar bozulmaz
    assert normalize_text("He'll call") == "He'll call"


def test_meaningful_filter():
    assert is_meaningful("Get in the car!")
    assert is_meaningful("No.")
    assert not is_meaningful("l")
    assert not is_meaningful("—— .. ,")
    assert not is_meaningful("1 2 3 4 %")


def test_noise_lines():
    assert is_noise_line("—")
    assert is_noise_line("l")
    assert is_noise_line(". ,")
    assert not is_noise_line("it.")
    assert not is_noise_line("I")
    assert not is_noise_line("Your grandmother?")
