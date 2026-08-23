"""Маршрутизация поиска: единый «Дайджест ИИ» через serious_tier."""

from app.services.digest_type_policy import (
    is_curious_digest,
    is_legacy_stored_curious,
    normalize_digest_type,
    step1_product_excludes_for_digest_type,
    step1_topic_terms_for_digest_type,
)
from app.services.step1_search_routing import resolve_step1_search_routing


def test_unified_ai_uses_source_tiers() -> None:
    for dtype in ("serious", "curious", None):
        r = resolve_step1_search_routing(dtype, query_override=None, tier_strict_setting=True)
        assert r.route == "serious_tier"
        assert r.uses_source_tiers
        assert not r.uses_curious_hosts
        assert r.tier_strict is True
        assert r.curious_strict is False
        assert r.curious_verify is False


def test_legacy_curious_input_normalizes_to_serious() -> None:
    assert normalize_digest_type("curious") == "serious"
    assert is_curious_digest("curious") is False
    assert is_legacy_stored_curious("curious") is True


def test_query_override_route() -> None:
    r = resolve_step1_search_routing("serious", query_override="custom query", tier_strict_setting=True)
    assert r.route == "query_override"
    assert not r.uses_source_tiers
    assert not r.uses_curious_hosts


def test_style_topic_uses_style_tier_route() -> None:
    r = resolve_step1_search_routing(
        "serious",
        digest_topic="style",
        query_override=None,
        tier_strict_setting=True,
    )
    assert r.route == "style_tier"
    assert r.tier_strict is True
    assert r.uses_source_tiers


def test_unified_topic_terms_use_serious_en() -> None:
    for dtype in ("serious", "curious", None):
        terms = step1_topic_terms_for_digest_type(dtype)
        assert "regulation" in terms
        assert "курьёз" not in terms


def test_unified_product_excludes_use_serious_profile() -> None:
    serious = step1_product_excludes_for_digest_type("serious")
    curious = step1_product_excludes_for_digest_type("curious")
    assert "-blog" in serious
    assert "-blog" in curious
    assert "-регулирование" not in serious
    assert "-регулирование" not in curious
