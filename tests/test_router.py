import asyncio

import pytest

from gta_tr_subs.config import CacheConfig, TranslateConfig
from gta_tr_subs.translate import CircuitBreaker, Glossary, Router, TranslationCache
from gta_tr_subs.translate.base import TranslationResult
from gta_tr_subs.translate.fake import FakeTranslator


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_router(cloud=None, local=None, fallback=None, clock=None, **cfg) -> Router:
    config = TranslateConfig(**cfg)
    return Router(
        config, TranslationCache(None), cloud, local, fallback, Glossary(keep=["Lucia"]),
        cache_cfg=CacheConfig(), clock=clock or Clock(),
    )


async def run(router: Router, text: str) -> tuple[TranslationResult | None, list[TranslationResult]]:
    seen: list[TranslationResult] = []
    final = await router.translate(text, seen.append)
    return final, seen


# ------------------------------------------------------------- devre kesici
def test_breaker_opens_after_consecutive_failures_and_recovers():
    clock = Clock()
    breaker = CircuitBreaker(failures=2, cooldown=30, clock=clock)
    assert breaker.allow() and breaker.state == "closed"
    breaker.failure()
    assert breaker.allow()  # tek hata yetmez
    breaker.failure()
    assert breaker.state == "open" and not breaker.allow()
    clock.now += 29.9
    assert not breaker.allow()
    clock.now += 0.2
    assert breaker.state == "half_open"
    assert breaker.allow()  # tek deneme isteği
    assert not breaker.allow()  # ikinci istek beklemeli
    breaker.success()
    assert breaker.state == "closed" and breaker.allow()


def test_breaker_failed_probe_reopens_and_success_resets_count():
    clock = Clock()
    breaker = CircuitBreaker(failures=2, cooldown=30, clock=clock)
    breaker.failure()
    breaker.success()
    breaker.failure()
    assert breaker.state == "closed"  # ardışık değil
    breaker.failure()
    clock.now += 31
    assert breaker.allow()
    breaker.failure()  # deneme başarısız → yeniden 30 sn
    assert breaker.state == "open" and not breaker.allow()
    clock.now += 31
    assert breaker.allow()
    breaker.release()  # iptal edilen deneme hakkı geri verilir
    assert breaker.allow()


# -------------------------------------------------------------- yönlendirici
async def test_cache_hit_is_instant_and_skips_providers():
    cloud, local = FakeTranslator("cloud", delay=0.2), FakeTranslator("local", delay=0.2)
    router = make_router(cloud, local)
    router.cache.put("Get in the car!", "Arabaya bin!", "cloud")
    final, seen = await run(router, "get in the car!")
    assert final.layer == "cache" and final.text == "Arabaya bin!" and final.ms < 50
    assert cloud.calls == [] and local.calls == []


async def test_race_local_first_then_cloud_replaces_and_is_cached():
    cloud = FakeTranslator("cloud", delay=0.10, prefix="C:")
    local = FakeTranslator("local", delay=0.02, prefix="L:")
    router = make_router(cloud, local)
    final, seen = await run(router, "Hello")
    assert [(r.layer, r.text) for r in seen] == [("local", "L:Hello"), ("cloud", "C:Hello")]
    assert final.layer == "cloud"
    assert router.cache.get("Hello").text == "C:Hello"


async def test_race_cloud_first_cancels_local():
    cloud = FakeTranslator("cloud", delay=0.02, prefix="C:")
    local = FakeTranslator("local", delay=0.30, prefix="L:")
    router = make_router(cloud, local)
    final, seen = await run(router, "Hello")
    assert [r.layer for r in seen] == ["cloud"]
    await asyncio.sleep(0)
    assert local.cancelled == 1


async def test_cloud_failure_falls_back_to_local_and_caches_it():
    router = make_router(FakeTranslator("cloud", fail=True), FakeTranslator("local", delay=0.01, prefix="L:"))
    final, seen = await run(router, "Hello")
    assert final.layer == "local" and [r.layer for r in seen] == ["local"]
    entry = router.cache.get("Hello")
    assert entry.layer == "local"


async def test_breaker_skips_cloud_after_two_failures_then_probes_again():
    clock = Clock()
    cloud = FakeTranslator("cloud", fail=True)
    local = FakeTranslator("local", prefix="L:")
    router = make_router(cloud, local, clock=clock)
    await run(router, "one")
    await run(router, "two")
    assert router.breaker.state == "open" and len(cloud.calls) == 2
    await run(router, "three")
    assert len(cloud.calls) == 2  # bulut atlandı
    clock.now += 31  # 30 sn doldu → tek deneme
    cloud.fail = False
    final, _ = await run(router, "four")
    assert len(cloud.calls) == 3 and final.layer == "cloud"
    assert router.breaker.state == "closed"


async def test_cloud_timeout_counts_as_failure_in_cloud_first():
    cloud = FakeTranslator("cloud", delay=1.0)
    local = FakeTranslator("local", prefix="L:")
    router = make_router(cloud, local, mode="cloud_first", cloud_timeout_ms=50)
    final, seen = await run(router, "Hello")
    assert final.layer == "local" and final.ms < 500
    await run(router, "Again")
    assert router.breaker.state == "open"


async def test_local_only_never_calls_cloud():
    cloud, local = FakeTranslator("cloud"), FakeTranslator("local", prefix="L:")
    router = make_router(cloud, local, mode="local_only")
    final, _ = await run(router, "Hello")
    assert final.layer == "local" and cloud.calls == []


async def test_simulated_offline_switches_to_local_without_stalling():
    cloud, local = FakeTranslator("cloud"), FakeTranslator("local", delay=0.01, prefix="L:")
    router = make_router(cloud, local)
    router.simulate_offline = True
    for text in ("a1", "b2", "c3", "d4"):
        final, _ = await asyncio.wait_for(run(router, text), timeout=2.0)
        assert final.layer == "local"
    assert cloud.calls == [] and router.breaker.state == "open"


async def test_fallback_layer_when_cloud_and_local_fail():
    router = make_router(
        FakeTranslator("cloud", fail=True), FakeTranslator("local", fail=True),
        FakeTranslator("fallback", prefix="F:"),
    )
    final, _ = await run(router, "Hello")
    assert final.layer == "fallback" and final.text == "F:Hello"


async def test_all_layers_fail_returns_none():
    router = make_router(FakeTranslator("cloud", fail=True), FakeTranslator("local", fail=True))
    final, seen = await run(router, "Hello")
    assert final is None and seen == []


async def test_cancellation_cancels_pending_requests_without_tripping_breaker():
    cloud, local = FakeTranslator("cloud", delay=5), FakeTranslator("local", delay=5)
    router = make_router(cloud, local)
    task = asyncio.create_task(router.translate("old line", lambda r: None))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert cloud.cancelled == 1 and local.cancelled == 1
    assert router.breaker.state == "closed"


async def test_local_cache_entry_is_upgraded_by_cloud():
    cloud = FakeTranslator("cloud", delay=0.01, prefix="C:")
    router = make_router(cloud, FakeTranslator("local"))
    router.cache.put("Hello", "L:Hello", "local")
    final, seen = await run(router, "Hello")
    assert [r.layer for r in seen] == ["cache", "cloud"]
    assert router.cache.get("Hello").layer == "cloud"


async def test_context_keeps_last_three_lines():
    cloud = FakeTranslator("cloud", prefix="C:")
    router = make_router(cloud, None, mode="cloud_first")
    for text in ("l1", "l2", "l3", "l4"):
        await run(router, text)
    assert list(router._context) == [("l2", "C:l2"), ("l3", "C:l3"), ("l4", "C:l4")]


async def test_glossary_enforced_on_provider_output():
    class Transliterating(FakeTranslator):
        async def translate(self, text, context, on_partial=None):
            return "Sakin ol, Lusiya."

    router = make_router(Transliterating("cloud"), None, mode="cloud_first")
    final, _ = await run(router, "Relax, Lucia.")
    assert final.text == "Sakin ol, Lucia."


def test_cycle_mode():
    router = make_router(FakeTranslator("cloud"), FakeTranslator("local"))
    assert [router.cycle_mode() for _ in range(3)] == ["cloud_first", "local_only", "race"]
