"""Поиск шага 1: единый «Дайджест ИИ» (serious_tier + добор curious/practical)."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.config import get_settings
from app.services import digest_service as ds
from app.services.digest_type_policy import (
    is_curious_digest,
    normalize_digest_type,
    step1_topic_terms_for_digest_type,
)
from app.services.step1_search_routing import resolve_step1_search_routing


def _digest(dtype: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        date=date(2026, 6, 7),
        digest_type=dtype,
        news_window_days=7,
        news_window_day_kind="calendar",
    )


def test_search_routing_unified_for_serious_and_legacy_curious() -> None:
    for dtype in ("serious", "curious"):
        r = resolve_step1_search_routing(dtype, query_override=None, tier_strict_setting=True)
        assert r.route == "serious_tier"
        assert r.curious_strict is False
        assert is_curious_digest(dtype) is False


def test_topic_terms_unified_serious_en() -> None:
    for dtype in ("serious", "curious"):
        terms = step1_topic_terms_for_digest_type(dtype)
        assert "курьёз" not in terms
        assert "regulation" in terms


def test_curious_prioritize_still_works_for_legacy_helper() -> None:
    svc = ds.DigestService.__new__(ds.DigestService)
    digest = _digest("curious")
    urls = [
        "https://ria.ru/20260606/serious-ai.html",
        "https://www.popmech.ru/science/funny-ai-id1/",
    ]
    ordered = svc._step1_prioritize_curious_search_urls(urls, digest)
    assert ordered[0].startswith("https://www.popmech.ru/")


def test_curious_prioritize_prefers_keyword_slug_over_neutral_same_host() -> None:
    from app.services.curious_tone import curious_raw_url_keyword_score

    svc = ds.DigestService.__new__(ds.DigestService)
    digest = _digest("curious")
    funny = "https://vc.ru/ai/2953773-neiroset-smeshno-oshiblasya"
    dry = "https://vc.ru/ai/2953773-rastushchie-zatraty-na-ii-agentov"
    assert curious_raw_url_keyword_score(funny) > curious_raw_url_keyword_score(dry)
    ordered = svc._step1_prioritize_curious_search_urls([dry, funny], digest)
    assert ordered[0] == funny


def test_serious_prioritize_still_prefers_tier1_over_entertainment() -> None:
    svc = ds.DigestService.__new__(ds.DigestService)
    svc.settings = SimpleNamespace(source_tiers_path=get_settings().source_tiers_path)
    digest = _digest("serious")
    urls = [
        "https://www.popmech.ru/science/funny-ai-id1/",
        "https://ria.ru/20260606/serious-ai.html",
    ]
    with patch.object(ds, "digest_news_anchor_date", return_value=digest.date):
        ordered = svc._step1_prioritize_search_urls(urls, digest)
    assert ordered[0].startswith("https://ria.ru/")


def test_unified_search_query_uses_tiers_not_curious_hosts() -> None:
    svc = ds.DigestService.__new__(ds.DigestService)
    svc.settings = SimpleNamespace(source_tiers_path=get_settings().source_tiers_path)
    for dtype in ("serious", "curious"):
        q = svc._step1_search_query(_digest(dtype))
        assert "curious_source_hosts" not in q
        assert "tier-файла" in q or "tier-2" in q.lower()


def test_legacy_curious_normalizes_on_read() -> None:
    assert normalize_digest_type("curious") == "serious"


def test_search_query_includes_publication_year() -> None:
    svc = ds.DigestService.__new__(ds.DigestService)
    svc.settings = SimpleNamespace(source_tiers_path=get_settings().source_tiers_path)
    digest = _digest("serious")
    digest.date = date(2026, 6, 16)
    q = svc._step1_search_query(digest)
    assert "Год публикации: 2026" in q
