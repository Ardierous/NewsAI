"""Rebalance: лимит на домен — на шаге 2; пул шага 1 без урезания по домену."""

from app.services import digest_service as ds


def _item(url: str, *, score: int = 6) -> dict:
    host = url.split("/")[2] if "/" in url else "example.com"
    return {
        "url": url,
        "title": f"Забавная история про ИИ на {host}",
        "source": host,
        "tier": "Tier-2",
        "total_score": score,
        "headline_editorial_ok": True,
        "link_status": True,
        "is_aggregator": False,
        "category": "technology",
        "description": "нейросеть чат-бот смешной фейл",
        "curious_tone_score": 3,
    }


def test_step1_pool_rebalance_allows_up_to_three_per_host() -> None:
    pool = [_item(f"https://thenextweb.com/a{i}", score=9 - (i % 3)) for i in range(6)]
    out = ds._rebalance_verified_pool(
        pool,
        target=6,
        digest_type="curious",
        per_host_cap=ds.STEP1_POOL_PER_HOST_CAP,
    )
    counts = ds._pool_host_counts(out)
    assert counts.get("thenextweb.com", 0) == 3


def test_step2_rebalance_caps_per_host() -> None:
    pool = [
        _item(f"https://thenextweb.com/a{i}", score=9 - (i % 3))
        for i in range(10)
    ] + [_item(f"https://vc.ru/ai/story{i}", score=8) for i in range(5)]
    out = ds._rebalance_verified_pool(
        pool,
        target=15,
        digest_type="curious",
        per_host_cap=ds.STEP2_MAX_PER_SOURCE,
    )
    counts = ds._pool_host_counts(out)
    assert counts
    assert max(counts.values()) <= ds.STEP2_MAX_PER_SOURCE


def test_pinned_items_respect_step2_host_cap() -> None:
    pool = [_item(f"https://vedomosti.ru/a{i}", score=10 - i) for i in range(5)]
    pinned = {ds._url_fingerprint(row["url"]) for row in pool}
    out = ds._rebalance_verified_pool(
        pool,
        target=5,
        pinned_fps=pinned,
        digest_type="serious",
        per_host_cap=ds.STEP2_MAX_PER_SOURCE,
    )
    assert max(ds._pool_host_counts(out).values()) <= ds.STEP2_MAX_PER_SOURCE


def test_social_status_url_detected() -> None:
    assert ds._is_social_embed_status_url("https://x.com/user/status/1234567890")
    assert ds._headline_unusable_for_digest("Функции JavaScript недоступны.")
