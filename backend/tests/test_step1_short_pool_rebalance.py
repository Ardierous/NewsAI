"""Rebalance при неполном пуле: не выкидывать лишние материалы с одного домена."""

from app.services import digest_service as ds
from app.services.step1_candidate_policy import SERIOUS_POOL_THEME_QUOTAS


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


def test_rebalance_prefers_research_when_available() -> None:
    pool = [
        {
            **_verified_item("https://skillbox.ru/course/ai-1", score=9),
            "material_form": "training",
            "verification_comment": "MATERIAL_FORM:training",
        },
        {
            **_verified_item("https://nplus1.ru/material/ai-breakthrough", score=8),
            "material_form": "research",
            "verification_comment": "MATERIAL_FORM:research",
        },
        {
            **_verified_item("https://indicator.ru/ai/2026/study", score=7),
            "material_form": "research",
            "verification_comment": "MATERIAL_FORM:research",
        },
        _verified_item("https://ria.ru/20260601/general-news", score=6),
    ]
    out = ds._rebalance_verified_pool(pool, target=4, digest_type="serious")
    research = [x for x in out if x.get("material_form") == "research"]
    assert len(research) >= 2


def test_rebalance_caps_training_links_at_one() -> None:
    pool = [
        _verified_item(f"https://skillbox.ru/course/ai-{i}", score=10 - i) for i in range(4)
    ] + [_verified_item("https://ria.ru/20260601/story", score=5)]
    for row in pool:
        row["material_form"] = "training" if "skillbox" in row["url"] else "article"
        row["verification_comment"] = (
            "MATERIAL_FORM:training" if row["material_form"] == "training" else "MATERIAL_FORM:article"
        )
    out = ds._rebalance_verified_pool(pool, target=5, digest_type="serious")
    training = [x for x in out if x.get("material_form") == "training"]
    assert len(training) <= SERIOUS_POOL_THEME_QUOTAS["training"]


def test_rebalance_enforces_serious_theme_quotas() -> None:
    themes = list(SERIOUS_POOL_THEME_QUOTAS.keys())
    pool: list[dict] = []
    idx = 0
    for theme in themes:
        for j in range(SERIOUS_POOL_THEME_QUOTAS[theme] + 2):
            pool.append(
                {
                    **_verified_item(f"https://publisher{idx}.com/news/{theme}/{j}", score=10 - j),
                    "material_form": theme,
                    "verification_comment": f"MATERIAL_FORM:{theme}",
                }
            )
            idx += 1
    target = sum(SERIOUS_POOL_THEME_QUOTAS.values())
    out = ds._rebalance_verified_pool(pool, target=target, digest_type="serious")
    for theme, quota in SERIOUS_POOL_THEME_QUOTAS.items():
        count = sum(1 for x in out if x.get("material_form") == theme)
        assert count <= quota, f"{theme}: {count} > {quota}"
        assert count == quota, f"{theme}: expected {quota}, got {count}"


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
