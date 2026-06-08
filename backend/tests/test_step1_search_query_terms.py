from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.services import digest_service as ds
from app.services.digest_type_policy import (
    _STEP1_TOPIC_TERMS_CURIOUS_RU,
    _STEP1_TOPIC_TERMS_SERIOUS_EN,
    is_curious_digest,
    step1_product_excludes_for_digest_type,
    step1_research_editorial_block,
    step1_topic_terms_for_digest_type,
)


def test_serious_search_uses_english_topic_terms_only() -> None:
    terms = step1_topic_terms_for_digest_type("serious")
    assert terms == _STEP1_TOPIC_TERMS_SERIOUS_EN
    assert "курьёз" not in terms


def test_curious_search_uses_russian_quirky_terms_only() -> None:
    terms = step1_topic_terms_for_digest_type("curious")
    assert terms == _STEP1_TOPIC_TERMS_CURIOUS_RU
    assert "ИИ ляпы" in terms
    assert "галлюцинации нейросети" in terms
    assert "funny" not in terms


def test_default_digest_type_is_serious_for_search_terms() -> None:
    assert step1_topic_terms_for_digest_type(None) == _STEP1_TOPIC_TERMS_SERIOUS_EN


def test_curious_product_excludes_allow_blogs_block_regulation() -> None:
    serious = step1_product_excludes_for_digest_type("serious")
    curious = step1_product_excludes_for_digest_type("curious")
    assert "-blog" in serious
    assert "-blog" not in curious
    assert "-регулирование" in curious


def test_curious_research_block_excludes_press_releases() -> None:
    block = step1_research_editorial_block("curious")
    assert "digest_type=curious" in block
    assert "0% пресс" in block or "0%" in block
    assert is_curious_digest("curious")


def test_curious_human_stories_query_targets_entertainment_sources() -> None:
    digest = SimpleNamespace(
        id=1,
        date=date(2026, 6, 6),
        digest_type="curious",
        news_window_days=7,
        news_window_day_kind="calendar",
    )
    with patch.object(ds, "digest_news_anchor_date", return_value=date(2026, 6, 6)):
        query = ds.DigestService._step1_curious_human_stories_query(SimpleNamespace(), digest)
    assert "пользователи жалуются" in query
    assert "AI agent deleted code" in query
    assert "site:vc.ru" in query or "site:habr.com" in query
    assert "site:reddit.com" in query or "site:theverge.com" in query
