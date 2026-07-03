"""Пул шага 1: до 3 статей с домена; на шаге 2 — не более 2 в топ‑5."""

from app.services import digest_service as ds


def _item(url: str, *, score: int = 8) -> dict:
    host = url.split("/")[2] if "/" in url else "example.com"
    return {
        "url": url,
        "title": f"ИИ и рынок на {host}",
        "source": host,
        "tier": "Tier-2",
        "total_score": score,
        "headline_editorial_ok": True,
        "link_status": True,
        "is_aggregator": False,
        "category": "technology",
        "description": "искусственный интеллект нейросеть",
    }


def test_listing_max_children_investing():
    assert ds._listing_max_children("https://ru.investing.com/analysis/stock-markets") == 16
    assert ds._listing_max_children("https://ria.ru/news") == ds.STEP1_LISTING_EXPAND_CHILDREN


def test_step1_pool_caps_at_three_per_host():
    pool = [_item(f"https://investing.com/news/ai-{i}", score=10 - i) for i in range(6)]
    out = ds._rebalance_verified_pool_host_cap_only(
        pool,
        len(pool),
        per_host_cap=ds.STEP1_POOL_PER_HOST_CAP,
        digest_type="serious",
    )
    assert len(out) == 3
    assert ds._pool_host_counts(out).get("investing.com", 0) == 3
