"""Курьёзный выпуск: агрегаторы в сыром пуле, первоисточники в кандидатах, fail-fast порядок."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from urllib.parse import urlparse

from app.services import digest_service as ds
from app.services import news_search
from app.services.step1_filter_settings import load_step1_filter_settings
from app.services.step1_filters import CURIOUS_PREFILTER_DEFAULT_ORDER


def _digest() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        date=date(2026, 6, 7),
        digest_type="curious",
        news_window_days=7,
        news_window_day_kind="calendar",
    )


def _curious_svc(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    svc = SimpleNamespace()
    svc.settings = SimpleNamespace(step1_curious_use_serious_tiers=False)
    svc._ensure_russian_candidate_title = lambda _d, _u, h: h
    svc._step1_log_curious_tone = lambda **_kwargs: None
    monkeypatch.setattr(ds, "_step1_curious_mode", True, raising=False)
    return svc


def test_aggregator_in_raw_pool_but_not_in_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    agg_url = "https://news.google.com/rss/articles/CBMiabc"
    source_url = "https://vc.ru/id123456/funny-ai-fail"

    def _fake_fetch(*_a, **_k):
        return [agg_url, source_url]

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_fetch)
    merged = news_search.fetch_curious_prioritized_raw_urls(
        SimpleNamespace(),
        window_prefix="after:2026-06-01 before:2026-06-08 ",
        topic_terms_ru="нейросеть курьёз",
        topic_terms_foreign="AI funny",
        product_excludes="",
        fetch_limit=20,
        proxy=None,
    )
    hosts = {urlparse(u).hostname for u in merged}
    assert any(h and "google.com" in h for h in hosts)
    assert any(h and "vc.ru" in h for h in hosts)


def test_aggregator_url_rejected_at_final_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    page_url = "https://news.google.com/rss/articles/CBMiabc"
    bundle = {
        "ok": True,
        "headline": "Смешной провал нейросети на конкурсе мемов",
        "topic_corpus": "ИИ нарисовал абсурдного кота — пользователи ржут в комментариях, вирусный кринж.",
        "final_url": page_url,
        "display_url": page_url,
        "is_listing_page": False,
    }
    svc = _curious_svc(monkeypatch)
    item = {
        "original_number": 1,
        "title": "",
        "url": page_url,
        "verification_comment": "",
        "link_status": False,
    }
    ds.DigestService._verify_curious_candidate_dict(
        svc, _digest(), item, prefetched_bundle=bundle, filter_enabled=lambda _fid: True
    )
    assert item.get("headline_editorial_ok") is not True
    assert "aggregator_source" in str(item.get("verification_comment") or "")


def test_first_source_link_from_aggregator_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    page_url = "https://vc.ru/id123456/funny-ai-fail"
    bundle = {
        "ok": True,
        "headline": "Нейросеть устроила курьёз на конкурсе мемов",
        "topic_corpus": (
            "ИИ нарисовал абсурдного кота — зрители ржут в комментариях, это вирусный кринж. "
            "Нейросеть смешно ошиблась с лапами и мордой, пользователи делятся скриншотами в соцсетях."
        ),
        "final_url": page_url,
        "display_url": page_url,
        "published_at": "2026-06-10T12:00:00+03:00",
        "is_listing_page": False,
        "article_markers": True,
    }
    svc = _curious_svc(monkeypatch)
    item = {
        "original_number": 2,
        "title": "",
        "url": page_url,
        "verification_comment": "",
        "link_status": False,
    }
    ds.DigestService._verify_curious_candidate_dict(
        svc, _digest(), item, prefetched_bundle=bundle, filter_enabled=lambda _fid: True
    )
    assert item.get("headline_editorial_ok") is True
    assert item.get("link_status") is True
    assert "aggregator_source" not in str(item.get("verification_comment") or "")


def test_non_policy_source_check_first() -> None:
    curious_filters = load_step1_filter_settings("curious")["filters"]
    pre_http = [f for f in curious_filters if f["id"] in CURIOUS_PREFILTER_DEFAULT_ORDER]
    pre_http.sort(key=lambda x: x["order"])
    assert pre_http[0]["id"] == "non_policy_source"
    assert (
        news_search.search_url_prefilter_reason(
            "https://openai.com/news/some-release",
            curious_strict=True,
            order=[f["id"] for f in pre_http],
        )
        == "non_policy_source"
    )


def test_off_topic_not_curious_check_first_post_http(monkeypatch: pytest.MonkeyPatch) -> None:
    page_url = "https://news.google.com/rss/articles/CBMiabc"
    bundle = {
        "ok": True,
        "headline": "Компания представила новую модель GPT-5",
        "topic_corpus": "Регулятор одобрил выпуск; инвесторы оценили сделку в миллиард долларов.",
        "final_url": page_url,
        "display_url": page_url,
        "is_listing_page": False,
    }
    svc = _curious_svc(monkeypatch)
    item = {
        "original_number": 3,
        "title": "",
        "url": page_url,
        "verification_comment": "",
        "link_status": False,
    }
    ds.DigestService._verify_curious_candidate_dict(
        svc, _digest(), item, prefetched_bundle=bundle, filter_enabled=lambda _fid: True
    )
    comment = str(item.get("verification_comment") or "")
    assert "off_topic_not_curious" in comment
    assert "aggregator_source" not in comment


def test_curious_prefilter_allows_aggregator_for_expansion() -> None:
    curious_filters = load_step1_filter_settings("curious")["filters"]
    order = [f["id"] for f in sorted(curious_filters, key=lambda x: x["order"])]
    enabled = {f["id"]: f["enabled"] for f in curious_filters}
    reason = news_search.search_url_prefilter_reason(
        "https://news.google.com/rss/articles/CBMiabc",
        curious_strict=True,
        order=order,
        is_enabled=lambda fid: bool(enabled.get(fid, True)),
    )
    assert reason != "aggregator_source"


def test_telegram_aggregator_expands_to_external_article(monkeypatch: pytest.MonkeyPatch) -> None:
    seed = "https://t.me/s/technokratos/42"
    external = "https://vc.ru/id123456/funny-ai-fail"

    def _fake_http(_url: str):
        return SimpleNamespace(
            text=(
                '<div class="tgme_widget_message_wrap">'
                '<div data-post="technokratos/42"></div>'
                '<time datetime="2026-06-10T12:00:00+00:00"></time>'
                f'<div class="tgme_widget_message_text"><a href="{external}">link</a></div>'
                "</div>"
            )
        )

    child_bundle = {
        "ok": True,
        "headline": "Нейросеть устроила курьёз",
        "topic_corpus": "Смешной фейл нейросети — пользователи ржут в комментариях.",
        "final_url": external,
        "display_url": external,
        "is_listing_page": False,
        "article_markers": True,
    }

    monkeypatch.setattr(ds, "_http_get_html_for_article", _fake_http)
    monkeypatch.setattr(ds, "_fetch_article_page_bundle", lambda url: child_bundle if url == external else {"ok": False})

    pairs = ds._expand_aggregator_seed_urls(seed, max_children=3)
    assert pairs
    assert pairs[0][0] == external


def test_curious_prefilter_rejects_aggregator_when_filter_enabled() -> None:
    reason = news_search.search_url_prefilter_reason(
        "https://news.google.com/rss/articles/CBMiabc",
        curious_strict=True,
        order=["aggregator_source"],
        is_enabled=lambda fid: fid == "aggregator_source",
    )
    assert reason == "aggregator_source"


def test_telemetr_listing_rejected_at_final_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    page_url = "https://telemetr.me/content/newsych/some-post"
    bundle = {
        "ok": True,
        "headline": "Смешной провал нейросети на конкурсе мемов",
        "topic_corpus": "ИИ нарисовал абсурдного кота — пользователи ржут в комментариях.",
        "final_url": page_url,
        "display_url": page_url,
        "is_listing_page": False,
    }
    svc = _curious_svc(monkeypatch)
    item = {
        "original_number": 4,
        "title": "",
        "url": page_url,
        "verification_comment": "",
        "link_status": False,
    }
    ds.DigestService._verify_curious_candidate_dict(
        svc, _digest(), item, prefetched_bundle=bundle, filter_enabled=lambda _fid: True
    )
    assert item.get("headline_editorial_ok") is not True
    assert "aggregator_source" in str(item.get("verification_comment") or "")
