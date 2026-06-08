"""Поиск и приоритизация шага 1: serious и curious не смешивают контуры."""

from datetime import date
from types import SimpleNamespace

from app.config import get_settings
from app.services import digest_service as ds
from app.services.digest_type_policy import step1_topic_terms_for_digest_type
from app.services.step1_search_routing import resolve_step1_search_routing


def _digest(dtype: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        date=date(2026, 6, 7),
        digest_type=dtype,
        news_window_days=7,
        news_window_day_kind="calendar",
    )


def test_search_routing_separate_for_serious_and_curious() -> None:
    serious = resolve_step1_search_routing("serious", query_override=None, tier_strict_setting=True)
    curious = resolve_step1_search_routing("curious", query_override=None, tier_strict_setting=True)
    assert serious.route == "serious_tier"
    assert serious.curious_strict is False
    assert curious.route == "curious_hosts"
    assert curious.curious_strict is True


def test_topic_terms_do_not_cross_digest_types() -> None:
    serious_terms = step1_topic_terms_for_digest_type("serious")
    curious_terms = step1_topic_terms_for_digest_type("curious")
    assert "курьёз" not in serious_terms
    assert "regulation" in serious_terms
    assert "курьёз" in curious_terms
    assert "кринж" in curious_terms or "ржач" in curious_terms


def test_curious_prioritize_prefers_entertainment_over_tier_host() -> None:
    svc = ds.DigestService.__new__(ds.DigestService)
    digest = _digest("curious")
    urls = [
        "https://ria.ru/20260606/serious-ai.html",
        "https://www.popmech.ru/science/funny-ai-id1/",
    ]
    ordered = svc._step1_prioritize_curious_search_urls(urls, digest)
    assert ordered[0].startswith("https://www.popmech.ru/")


def test_serious_prioritize_still_prefers_tier1_over_entertainment() -> None:
    svc = ds.DigestService.__new__(ds.DigestService)
    svc.settings = SimpleNamespace(source_tiers_path=get_settings().source_tiers_path)
    digest = _digest("serious")
    urls = [
        "https://www.popmech.ru/science/funny-ai-id1/",
        "https://ria.ru/20260606/serious-ai.html",
    ]
    ordered = svc._step1_prioritize_search_urls(urls, digest)
    assert ordered[0].startswith("https://ria.ru/")


def test_curious_search_query_uses_curious_seed_hint_not_tiers() -> None:
    svc = ds.DigestService.__new__(ds.DigestService)
    svc.settings = SimpleNamespace(source_tiers_path=get_settings().source_tiers_path)
    curious_q = svc._step1_search_query(_digest("curious"))
    serious_q = svc._step1_search_query(_digest("serious"))
    assert "curious_source_hosts" in curious_q
    assert "tier-файла" in serious_q or "tier-2" in serious_q.lower()
    assert "curious_source_hosts" not in serious_q
