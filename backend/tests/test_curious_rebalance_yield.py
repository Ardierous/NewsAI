"""Rebalance: строго не более 2 материалов с одного домена."""

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


def test_rebalance_never_more_than_two_per_host() -> None:
    pool = [
        _item(f"https://thenextweb.com/a{i}", score=9 - (i % 3))
        for i in range(10)
    ] + [_item(f"https://vc.ru/ai/story{i}", score=8) for i in range(5)]
    out = ds._rebalance_verified_pool(pool, target=15, digest_type="curious")
    counts = ds._pool_host_counts(out)
    assert counts
    assert max(counts.values()) <= ds.STEP1_MAX_PER_SOURCE


def test_social_status_url_detected() -> None:
    assert ds._is_social_embed_status_url("https://x.com/user/status/1234567890")
    assert ds._headline_unusable_for_digest("Функции JavaScript недоступны.")
