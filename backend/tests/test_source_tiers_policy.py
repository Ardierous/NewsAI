from pathlib import Path

from app.config import get_settings
from app.source_tiers_policy import (
    batched_site_host_groups,
    classify_source_policy,
    get_source_tiers_policy,
    is_aggregator_source,
    is_blocked_search_host,
    is_policy_tier_source,
    is_tier5_forbidden_source,
    load_source_tiers,
    policy_tier_host_groups,
    tier2_search_host_markers,
)


def test_load_source_tiers_splits_prompt_and_host_rules():
    policy = load_source_tiers(get_settings().source_tiers_path)
    assert "Система приоритетов источников" in policy.prompt_text
    assert "--- HOST_RULES ---" not in policy.prompt_text
    assert "news.google." in policy.aggregator_hosts
    assert "ai-manual.ru" in policy.tier1_hosts
    assert "techcrunch.com" in policy.tier2_hosts
    assert "digital.gov.ru" in policy.tier3_hosts
    assert "openai.com" in policy.tier4_hosts
    assert "meduza.io" in policy.banned_media_hosts
    assert "arxiv.org" in policy.blocked_search_hosts
    assert "https://ria.ru/product_iskusstvennyy-intellekt/" in policy.search_seed_urls
    assert "investing.com" in policy.tier2_hosts
    investing_seeds = (
        "https://ru.investing.com/news/stock-market-news",
        "https://ru.investing.com/news/economy",
        "https://ru.investing.com/analysis/stock-markets",
        "https://ru.investing.com/analysis/market-overview",
        "https://ru.investing.com/analysis/bonds",
    )
    for seed in investing_seeds:
        assert seed in policy.search_seed_urls


def test_classify_source_policy_aggregator_as_tier2():
    tier, is_agg, status = classify_source_policy("https://news.google.com/articles/abc")
    assert tier == "Tier-2"
    assert is_agg is True
    assert "сомнительный" in status or "подтверждено" in status


def test_classify_source_policy_forbidden_media():
    tier, _, _ = classify_source_policy("https://meduza.io/feature/ai-case")
    assert tier == "Tier-5"
    assert is_tier5_forbidden_source("https://meduza.io/feature/ai-case")


def test_classify_source_policy_tier2():
    tier, is_agg, status = classify_source_policy("https://techcrunch.com/2024/ai-story")
    assert tier == "Tier-2"
    assert is_agg is False
    assert "подтверждено" in status


def test_classify_source_policy_tier1_and_tier4():
    tier1, _, _ = classify_source_policy("https://ria.ru/20260519/ai-123.html")
    tier4, _, _ = classify_source_policy("https://openai.com/news/ai-release")
    assert tier1 == "Tier-1"
    assert tier4 == "Tier-4"


def test_blocked_and_aggregator_helpers():
    assert is_blocked_search_host("https://arxiv.org/abs/1234.5678")
    assert is_aggregator_source("https://yandex.ru/news/story/1")
    assert not is_blocked_search_host("https://tass.ru/ai/1")


def test_is_policy_tier_source_and_host_groups():
    assert is_policy_tier_source("https://ria.ru/20260519/ai.html")
    assert not is_policy_tier_source("https://meduza.io/feature/ai")
    assert is_policy_tier_source("https://news.google.com/articles/x")
    assert is_policy_tier_source("https://yandex.ru/news/story/1")
    groups = policy_tier_host_groups()
    assert groups[0][0] == "Tier-1"
    assert "ria.ru" in groups[0][1]
    t2 = tier2_search_host_markers()
    assert "techcrunch.com" in t2
    assert "news.google." in t2
    assert groups[1][0] == "Tier-2"
    assert "news.google." in groups[1][1]
    batches = batched_site_host_groups(("a.ru", "b.ru", "c.ru", "d.ru"), batch_size=3)
    assert batches == [("a.ru", "b.ru", "c.ru"), ("d.ru",)]


def test_policy_cache_follows_file_mtime(tmp_path: Path):
    path = tmp_path / "tiers.txt"
    path.write_text(
        "Prompt only\n\n--- HOST_RULES ---\n\n[aggregator_hosts]\nfoo.example\n[search_seed_urls]\nhttps://foo.example/ai\n",
        encoding="utf-8",
    )
    p1 = get_source_tiers_policy(path)
    assert p1.aggregator_hosts == ("foo.example",)
    assert p1.search_seed_urls == ("https://foo.example/ai",)
    path.write_text(
        "Prompt only\n\n--- HOST_RULES ---\n\n[aggregator_hosts]\nbar.example\n",
        encoding="utf-8",
    )
    from app.source_tiers_policy import invalidate_policy_cache

    invalidate_policy_cache()
    p2 = get_source_tiers_policy(path)
    assert p2.aggregator_hosts == ("bar.example",)
