from app.source_tiers_policy import (
    SourceTiersPolicy,
    policy_tier_host_groups_ru_first,
)


def _policy() -> SourceTiersPolicy:
    return SourceTiersPolicy(
        prompt_text="test",
        aggregator_hosts=("news.google.",),
        tier1_hosts=("ria.ru", "vedomosti.ru", "thenextweb.com"),
        tier2_hosts=("technologyreview.com", "habr.com", "techcrunch.com"),
        tier3_hosts=(),
        tier4_hosts=(),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=("ria.ru", "vedomosti.ru", "habr.com"),
        blocked_search_hosts=(),
        search_seed_urls=(),
    )


def test_policy_tier_host_groups_ru_first_puts_ru_before_foreign():
    groups = policy_tier_host_groups_ru_first(_policy())
    labels = [g[0] for g in groups]
    assert labels.index("Tier-1") < labels.index("Tier-2")
    ru_tier1 = next(hosts for label, hosts in groups if label == "Tier-1")
    assert "ria.ru" in ru_tier1
    assert "thenextweb.com" not in ru_tier1
    defer = [hosts for label, hosts in groups if label.endswith("-defer")]
    assert defer
    assert any("technologyreview.com" in h for batch in defer for h in batch)

