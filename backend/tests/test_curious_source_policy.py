from pathlib import Path

from app.curious_source_policy import (
    classify_curious_source,
    curious_host_search_groups,
    curious_tier_priority,
    get_curious_source_policy,
    is_curious_policy_source,
    is_curious_russian_host,
)
from app.services.news_search import search_url_prefilter_reason


def test_curious_policy_accepts_ru_and_foreign_hosts() -> None:
    assert is_curious_policy_source("https://vc.ru/ai/123-test")
    assert is_curious_policy_source("https://www.popmech.ru/science/ai-fail")
    assert is_curious_policy_source("https://www.reddit.com/r/MachineLearning/comments/abc/")


def test_curious_tier_labels_are_independent_from_serious() -> None:
    t1, _, _ = classify_curious_source("https://gizmodo.com/funny-ai-fail")
    t2, _, _ = classify_curious_source("https://habr.com/ru/news/123/")
    blocked, _, _ = classify_curious_source("https://openai.com/news/some-release")
    assert t1 == "Curious-T1"
    assert t2 == "Curious-T2"
    assert blocked == "Curious-T5"
    assert curious_tier_priority("Curious-T1") < curious_tier_priority("Curious-T2")


def test_curious_aggregator_allowed_as_tier2_search() -> None:
    assert is_curious_policy_source("https://news.google.com/articles/abc")
    tier, is_agg, _ = classify_curious_source("https://news.google.com/articles/abc")
    assert tier == "Curious-T2"
    assert is_agg is True
    groups = curious_host_search_groups()
    assert any(label == "Curious-T1" for label, _ in groups)
    assert any(label == "Curious-T2" for label, _ in groups)
    assert any(label == "Curious-T2-Aggregators" for label, _ in groups)


def test_curious_policy_rejects_tier_only_corporate_without_listing() -> None:
    assert not is_curious_policy_source("https://openai.com/news/some-release")


def test_curious_strict_prefilter_uses_curious_hosts() -> None:
    assert search_url_prefilter_reason(
        "https://habr.com/ru/news/123/",
        curious_strict=True,
    ) is None
    assert search_url_prefilter_reason(
        "https://openai.com/news/x",
        curious_strict=True,
    ) == "non_policy_source"


def test_curious_russian_host_markers() -> None:
    assert is_curious_russian_host("https://dtf.ru/games/ai")
    assert not is_curious_russian_host("https://www.reddit.com/r/ai/")


def test_curious_hosts_file_exists() -> None:
    path = Path(__file__).resolve().parents[1] / "app" / "prompts" / "curious_source_hosts.txt"
    policy = get_curious_source_policy(path)
    assert len(policy.curious_tier1_hosts) >= 8
    assert len(policy.curious_tier2_hosts) >= 4
    assert len(policy.curious_ru_entertainment_hosts) >= 5
    assert len(policy.curious_ru_tech_hosts) >= 3
    assert "ria.ru" not in policy.all_search_hosts()
