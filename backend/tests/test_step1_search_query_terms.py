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
    assert "курьёз" in terms
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
