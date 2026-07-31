"""Тематика выпуска: AI vs Style."""

from app.services.digest_topic_policy import (
    is_style_digest,
    normalize_digest_topic,
    step1_topic_terms_for_topic,
    style_digest_topic_matches,
)
from app.services.platform_assembly import (
    DEFAULT_LEAD_STYLE,
    HEADER_TITLE_STYLE,
    resolve_default_lead,
    resolve_header_title,
)
from app.services.step1_search_routing import resolve_step1_search_routing


def test_normalize_digest_topic_defaults_to_ai() -> None:
    assert normalize_digest_topic(None) == "ai"
    assert normalize_digest_topic("ai") == "ai"
    assert normalize_digest_topic("style") == "style"
    assert normalize_digest_topic("STYLE") == "style"


def test_style_topic_matches_fashion_material() -> None:
    assert style_digest_topic_matches("", "Неделя моды в Милане: главные тренды сезона") is True
    assert style_digest_topic_matches("новая коллекция одежды", "Дизайнер представил lookbook") is True


def test_style_topic_rejects_homonyms() -> None:
    corpus = "Компания представила новую бизнес-модель и стиль управления командой"
    assert style_digest_topic_matches(corpus, "Стиль управления в IT") is False


def test_style_search_routing_uses_style_tiers() -> None:
    r = resolve_step1_search_routing(
        "serious",
        digest_topic="style",
        query_override=None,
        tier_strict_setting=True,
    )
    assert r.route == "style_tier"
    assert r.uses_source_tiers
    assert not r.uses_curious_hosts


def test_ai_search_routing_unchanged() -> None:
    r = resolve_step1_search_routing(
        "serious",
        digest_topic="ai",
        query_override=None,
        tier_strict_setting=True,
    )
    assert r.route == "serious_tier"


def test_style_topic_terms_differ_from_ai() -> None:
    ai_terms = step1_topic_terms_for_topic("ai")
    style_terms = step1_topic_terms_for_topic("style")
    assert "fashion" in style_terms.lower()
    assert "нейросет" not in style_terms.lower()
    assert "artificial intelligence" in ai_terms.lower() or "AI" in ai_terms


def test_platform_assembly_style_headers() -> None:
    payload = {"digest_type": "serious", "digest_topic": "style"}
    assert resolve_header_title(payload) == HEADER_TITLE_STYLE
    assert resolve_default_lead(payload) == DEFAULT_LEAD_STYLE
    assert is_style_digest("style") is True
