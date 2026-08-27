from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.services import digest_service as ds
from app.services.digest_type_policy import (
    _STEP1_TOPIC_TERMS_SERIOUS_EN,
    is_curious_digest,
    is_legacy_stored_curious,
    normalize_digest_type,
    step1_product_excludes_for_digest_type,
    step1_research_editorial_block,
    step1_topic_terms_for_digest_type,
)


def test_unified_search_uses_english_topic_terms_only() -> None:
    for dtype in ("serious", "curious", None):
        terms = step1_topic_terms_for_digest_type(dtype)
        assert _STEP1_TOPIC_TERMS_SERIOUS_EN in terms
        assert "искусственный интеллект" in terms
        assert "курьёз" not in terms


def test_legacy_curious_maps_to_serious() -> None:
    assert normalize_digest_type("curious") == "serious"
    assert is_curious_digest("curious") is False
    assert is_legacy_stored_curious("curious") is True


def test_unified_product_excludes_serious_profile() -> None:
    serious = step1_product_excludes_for_digest_type("serious")
    curious = step1_product_excludes_for_digest_type("curious")
    assert "-blog" in serious
    assert "-blog" in curious
    assert "-регулирование" not in curious


def test_unified_research_block_describes_ai_digest() -> None:
    block = step1_research_editorial_block("serious")
    assert "единый дайджест ИИ" in block
    assert "практич" in block


def test_curious_human_stories_query_targets_entertainment_sources() -> None:
    digest = SimpleNamespace(
        id=1,
        date=date(2026, 6, 7),
        digest_type="serious",
        news_window_days=7,
        news_window_day_kind="calendar",
    )
    svc = ds.DigestService.__new__(ds.DigestService)
    with patch.object(ds, "digest_news_anchor_date", return_value=digest.date):
        q = svc._step1_curious_human_stories_query(digest)
    assert "курьёз" in q or "ИИ ляпы" in q
