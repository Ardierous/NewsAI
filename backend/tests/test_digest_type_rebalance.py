from app.services import digest_service as ds


def _item(url: str, *, press: bool = False, score: int = 5) -> dict:
    host = url.split("/")[2] if "/" in url else "x.com"
    return {
        "url": url,
        "title": f"Title {host}",
        "source": host,
        "tier": "Tier-2",
        "total_score": score,
        "headline_editorial_ok": True,
        "link_status": True,
        "is_aggregator": False,
        "category": "press_release" if press else "technology",
        "description": "press" if press else "news",
    }


def test_rebalance_curious_skips_press_quota() -> None:
    pool = [
        _item("https://ria.ru/a1", press=False, score=9),
        _item("https://ria.ru/a2", press=False, score=8),
        _item("https://openai.com/news/pr1", press=True, score=9),
        _item("https://openai.com/news/pr2", press=True, score=8),
        _item("https://vc.ru/ai/funny", press=False, score=7),
    ]
    out = ds._rebalance_verified_pool(pool, target=4, digest_type="curious")
    press_in = sum(1 for x in out if ds._is_substantive_press_for_pool(x))
    assert press_in == 0
    assert len(out) == 4
