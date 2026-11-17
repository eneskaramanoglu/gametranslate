"""Pipeline davranışı: sahte kare kaynağı + sahte OCR + sahte çevirmenlerle."""

import asyncio

import numpy as np

from gta_tr_subs.capture.base import Frame, FrameSource
from gta_tr_subs.config import CacheConfig, DetectConfig, Settings, TranslateConfig
from gta_tr_subs.detect import ChangeDetector
from gta_tr_subs.metrics import Metrics
from gta_tr_subs.ocr.base import OcrLine
from gta_tr_subs.pipeline import HideEvent, Pipeline, ShowEvent
from gta_tr_subs.translate import Glossary, Router, TranslationCache
from gta_tr_subs.translate.fake import FakeTranslator

W, H = 640, 360


def frame_with(pattern: int, ts: float, index: int = 0) -> Frame:
    """pattern=0: altyazı yok; diğerleri: ROI içinde farklı parlak bloklar."""
    image = np.full((H, W, 3), 40, np.uint8)
    if pattern:
        x0 = 150 + 12 * pattern
        image[310:330, x0 : x0 + 200 + 30 * pattern] = 255
    return Frame(image, ts, index)


class ScriptedOcr:
    """Görüntüye bakmaz; test hangi metni "ekranda" istiyorsa onu döndürür."""

    name = "scripted"

    def __init__(self) -> None:
        self.text = ""
        self.calls = 0

    async def recognize(self, gray: np.ndarray) -> list[OcrLine]:
        self.calls += 1
        return [OcrLine(self.text, 10, 10, 300, 30)] if self.text else []


class Harness:
    def __init__(self, **stabilize) -> None:
        self.settings = Settings()
        self.settings.ocr.max_hz = 100000  # testte hız sınırı devre dışı
        self.settings.stabilize.speculative = stabilize.get("speculative", False)
        self.settings.stabilize.fast_confirm = stabilize.get("fast_confirm", False)
        self.ocr = ScriptedOcr()
        # Windows zamanlayıcı çözünürlüğü ~16 ms: gecikmeleri belirgin ayrı tut
        self.cloud = FakeTranslator("cloud", delay=0.12, prefix="C:")
        self.local = FakeTranslator("local", delay=0.02, prefix="L:")
        self.router = Router(
            TranslateConfig(), TranslationCache(None), self.cloud, self.local, None, Glossary(),
            cache_cfg=CacheConfig(),
        )
        self.events: list[object] = []
        self.pipeline = Pipeline(self.settings, FrameSource(), self.ocr, self.router, Metrics(), self.events.append)
        self.t = 100.0
        self.index = 0

    async def feed(self, pattern: int, text: str, frames: int = 1) -> None:
        self.ocr.text = text
        for _ in range(frames):
            self.t += 1 / 30
            self.index += 1
            await self.pipeline._process(frame_with(pattern, self.t, self.index))

    def shows(self) -> list[ShowEvent]:
        return [e for e in self.events if isinstance(e, ShowEvent)]

    def hides(self, *reasons: str) -> list[HideEvent]:
        return [e for e in self.events if isinstance(e, HideEvent) and (not reasons or e.reason in reasons)]


async def test_stable_line_is_translated_local_then_cloud():
    h = Harness()
    await h.feed(1, "Get in the car!", frames=3)
    await asyncio.sleep(0.3)
    shows = h.shows()
    assert [s.layer for s in shows] == ["local", "cloud"]
    assert shows[0].source == "Get in the car!" and shows[0].first and not shows[1].first
    assert shows[1].text == "C:Get in the car!"
    assert shows[0].bbox is not None and shows[0].roi_size == (512, 79)


async def test_static_subtitle_runs_ocr_only_until_stable():
    h = Harness()
    await h.feed(1, "Shut up and cover me.", frames=30)
    assert h.ocr.calls == 2  # 2 ardışık okuma → kararlı → değişim yokken OCR yok
    await asyncio.sleep(0.3)
    assert len({s.job_id for s in h.shows()}) == 1


async def test_same_text_after_background_change_is_not_retranslated():
    h = Harness()
    await h.feed(1, "Relax, Lucia.", frames=3)
    await asyncio.sleep(0.3)
    await h.feed(2, "Relax, Lucia.", frames=4)  # maske değişti ama metin aynı
    await asyncio.sleep(0.3)
    assert h.cloud.calls == ["Relax, Lucia."]
    assert len({s.job_id for s in h.shows()}) == 1


async def test_hide_within_delay_after_subtitle_disappears():
    h = Harness()
    await h.feed(1, "Then drive like you mean it.", frames=3)
    await asyncio.sleep(0.3)
    start = h.t
    await h.feed(0, "", frames=7)  # 7 kare ≈ 233 ms < 250 ms
    assert not h.hides("empty")
    await h.feed(0, "", frames=2)  # ≈ 300 ms
    assert len(h.hides("empty")) == 1 and h.t - start <= 0.31
    assert h.pipeline.stabilizer.shown is None


async def test_new_line_cancels_pending_requests_of_old_line():
    h = Harness()
    h.cloud.delay = h.local.delay = 5.0
    await h.feed(1, "Old line here.", frames=3)
    await asyncio.sleep(0.03)  # istekler yola çıksın
    await h.feed(2, "Brand new line.", frames=3)
    await asyncio.sleep(0.03)
    assert h.cloud.cancelled == 1 and h.local.cancelled == 1
    assert h.cloud.calls == ["Old line here.", "Brand new line."]
    assert not h.shows()
    h.pipeline._hide("test")


async def test_repeat_line_served_from_cache():
    h = Harness()
    await h.feed(1, "Get in the car!", frames=3)
    await asyncio.sleep(0.3)
    await h.feed(0, "", frames=10)
    await h.feed(1, "Get in the car!", frames=3)
    await asyncio.sleep(0.1)
    last = h.shows()[-1]
    assert last.layer == "cache" and last.text == "C:Get in the car!"
    assert h.cloud.calls == ["Get in the car!"]


async def test_unreadable_text_hides_overlay():
    h = Harness()
    await h.feed(1, "Shut up and cover me.", frames=3)
    await asyncio.sleep(0.3)
    await h.feed(2, "", frames=4)  # parlak arka plan: maske dolu ama okunacak metin yok
    assert len(h.hides("gone")) == 1


async def test_fast_confirm_needs_single_ocr_when_image_is_unchanged():
    h = Harness(fast_confirm=True)
    await h.feed(1, "Wouldn't have it any other way.", frames=3)
    await asyncio.sleep(0.3)
    assert h.ocr.calls == 1 and [s.layer for s in h.shows()] == ["local", "cloud"]


async def test_speculative_job_is_adopted_not_duplicated():
    h = Harness(speculative=True)
    await h.feed(1, "Trust me.", frames=3)
    await asyncio.sleep(0.3)
    assert h.cloud.calls == ["Trust me."]  # tek istek: aday → kararlı devralındı
    assert h.shows()[-1].layer == "cloud"


# ------------------------------------------------------------ değişim tespiti
def test_change_detector_empty_change_and_stability():
    detector = ChangeDetector(DetectConfig())
    roi = lambda p: frame_with(p, 0).image[281:360, 64:576]  # noqa: E731
    first = detector.update(roi(0))
    assert first.empty and not first.changed
    appeared = detector.update(roi(1))
    assert appeared.changed and not appeared.empty
    assert not detector.update(roi(1)).changed  # aynı kare
    assert detector.update(roi(3)).changed  # farklı metin
    gone = detector.update(roi(0))
    assert gone.empty and gone.changed
    assert not detector.update(roi(0)).changed  # boştan boşa


def test_change_detector_slow_fade_accumulates():
    detector = ChangeDetector(DetectConfig())
    base = np.full((80, 512, 3), 40, np.uint8)
    detector.update(base)
    fired = 0
    for width in range(4, 204, 4):  # her karede küçük büyüme
        image = base.copy()
        image[30:50, 100 : 100 + width] = 255
        fired += detector.update(image).changed
    assert fired >= 3  # tek tek küçük farklar birikerek eşiği aşar
