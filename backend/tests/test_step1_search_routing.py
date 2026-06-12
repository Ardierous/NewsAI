"""Изоляция контуров поиска: serious не трогает curious_hosts и наоборот."""

from app.services.digest_type_policy import step1_product_excludes_for_digest_type, step1_topic_terms_for_digest_type
from app.services.step1_search_routing import resolve_step1_search_routing


def test_serious_default_uses_source_tiers_only() -> None:
    r = resolve_step1_search_routing("serious", query_override=None, tier_strict_setting=True)
    assert r.route == "serious_tier"
    assert r.uses_source_tiers
    assert not r.uses_curious_hosts
    assert r.tier_strict is True
    assert r.curious_strict is False
    assert r.curious_verify is False


def test_curious_default_uses_curious_hosts() -> None:
    r = resolve_step1_search_routing(
        "curious",
        query_override=None,
        tier_strict_setting=True,
    )
    assert r.route == "curious_hosts"
    assert r.uses_curious_hosts
    assert not r.uses_source_tiers
    assert r.tier_strict is False
    assert r.curious_strict is True
    assert r.curious_verify is True


def test_curious_optional_serious_tiers_hybrid() -> None:
    r = resolve_step1_search_routing(
        "curious",
        query_override=None,
        tier_strict_setting=True,
        curious_use_serious_tiers=True,
    )
    assert r.route == "serious_tier"
    assert r.uses_source_tiers
    assert not r.uses_curious_hosts
    assert r.tier_strict is True
    assert r.curious_strict is False
    assert r.curious_verify is True


def test_curious_with_override_disables_host_batches_but_keeps_verify() -> None:
    r = resolve_step1_search_routing("curious", query_override="custom query", tier_strict_setting=True)
    assert r.route == "query_override"
    assert not r.uses_source_tiers
    assert not r.uses_curious_hosts
    assert r.curious_verify is True


def test_serious_topic_terms_not_curious_keywords() -> None:
    serious = step1_topic_terms_for_digest_type("serious")
    curious = step1_topic_terms_for_digest_type("curious")
    assert "курьёз" not in serious
    assert "курьёз" in curious
    assert "regulation" in serious


def test_serious_product_excludes_not_curious_regulation_minus() -> None:
    serious = step1_product_excludes_for_digest_type("serious")
    curious = step1_product_excludes_for_digest_type("curious")
    assert "-регулирование" not in serious
    assert "-регулирование" in curious
