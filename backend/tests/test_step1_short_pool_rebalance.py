"""Rebalance при неполном пуле: не выкидывать лишние материалы с одного домена."""

from app.services import digest_service as ds


def _verified_item(url: str, *, score: int = 8) -> dict:
    host = url.split("/")[2] if "/" in url else "example.com"
    return {
        "url": url,
        "title": f"Новость ИИ на {host}",
        "source": host,
        "tier": "Tier-1",
        "total_score": score,
        "headline_editorial_ok": True,
        "link_status": True,
        "is_aggregator": False,
        "category": "technology",
        "description": "искусственный интеллект нейросеть",
    }


def test_short_pool_keeps_third_item_from_same_host() -> None:
    pool = [
        _verified_item("https://vedomosti.ru/tech/articles/2026/06/01/a", score=9),
        _verified_item("https://vedomosti.ru/tech/articles/2026/06/02/b", score=8),
        _verified_item("https://vedomosti.ru/tech/articles/2026/06/03/c", score=7),
        _verified_item("https://ria.ru/20260601/story", score=6),
    ]
    strict = ds._rebalance_verified_pool(pool, target=4, digest_type="serious")
    relaxed = ds._rebalance_verified_pool_host_cap_only(
        pool,
        target=4,
        per_host_cap=ds.STEP1_MAX_PER_SOURCE_SHORT_POOL,
    )
    assert len(strict) <= 3
    assert len(relaxed) == 4
