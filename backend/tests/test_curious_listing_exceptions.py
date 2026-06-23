from unittest.mock import patch

from app.services.digest_service import _expand_listing_url_candidates
from app.services.news_search import (
    is_curated_list_url,
    is_listing_page_url,
    is_step1_listing_seed_url,
    search_url_prefilter_reason,
)


def test_curated_list_url_passes_filter() -> None:
    url = "https://vc.ru/top/funny-ai-fails"
    assert is_curated_list_url(url)
    assert not is_listing_page_url(url)
    assert search_url_prefilter_reason(url, curious_strict=True) is None


def test_funny_path_not_rejected() -> None:
    url = "https://dtf.ru/memes/funny-ai-moments"
    assert is_curated_list_url(url)
    assert not is_listing_page_url(url)
    assert is_step1_listing_seed_url(url)


def test_top_best_roundup_exceptions() -> None:
    samples = [
        "https://vc.ru/best-ai-fails-2026",
        "https://dtf.ru/roundup/viral-memes",
        "https://habr.com/ru/companies/example/lists/crazy-neural-network-bugs",
        "https://pikabu.ru/tag/funny-ai-stories",
    ]
    for url in samples:
        assert is_curated_list_url(url), url
        assert not is_listing_page_url(url), url


def test_plain_tag_still_listing() -> None:
    url = "https://vc.ru/tag/artificial-intelligence"
    assert not is_curated_list_url(url)
    assert search_url_prefilter_reason(url, curious_strict=True) == "news_listing_page"


def test_extract_articles_from_curated_list() -> None:
    listing_url = "https://vc.ru/top/funny-ai-fails"
    bundle = {
        "ok": True,
        "final_url": listing_url,
        "is_listing_page": True,
        "listing_article_urls": [
            "https://vc.ru/ai/123-cat-ai-story",
            "https://vc.ru/ai/456-robot-fail",
        ],
    }
    child_bundle = {
        "ok": True,
        "final_url": "https://vc.ru/ai/123-cat-ai-story",
        "headline": "Коты и нейросеть",
        "topic_corpus": "x" * 200,
        "is_listing_page": False,
    }
    with patch("app.services.digest_service._fetch_article_page_bundle") as fetch:
        fetch.side_effect = lambda url: bundle if url == listing_url else child_bundle
        pairs = _expand_listing_url_candidates(listing_url, max_children=2)
    assert pairs
    assert "123-cat-ai-story" in pairs[0][0]
