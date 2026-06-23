from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.curious_source_policy import (
    get_curious_gray_zone_hosts,
    is_curious_allowed_source,
    is_curious_gray_zone_source,
    is_curious_policy_source,
)
from app.services.digest_service import DigestService
from app.services.news_search import search_url_prefilter_reason


def test_gray_zone_domain_passes_pre_http() -> None:
    url = "https://www.lenta.ru/news/2026/06/16/ai-funny/"
    assert is_curious_gray_zone_source(url)
    assert is_curious_allowed_source(url)
    assert not is_curious_policy_source(url)
    assert search_url_prefilter_reason(url, curious_strict=True) is None


def test_white_vs_gray_vs_black_list() -> None:
    white = "https://vc.ru/ai/123-test"
    gray = "https://snob.ru/technologies/ai-weird-story"
    black = "https://openai.com/news/some-release"
    blocked = "https://meduza.io/feature/ai"

    assert is_curious_policy_source(white)
    assert not is_curious_gray_zone_source(white)
    assert is_curious_allowed_source(white)

    assert is_curious_gray_zone_source(gray)
    assert not is_curious_policy_source(gray)
    assert is_curious_allowed_source(gray)

    assert not is_curious_policy_source(black)
    assert not is_curious_gray_zone_source(black)
    assert not is_curious_allowed_source(black)

    assert not is_curious_allowed_source(blocked)
    reason = search_url_prefilter_reason(blocked, curious_strict=True)
    assert reason in ("forbidden_media_source", "non_policy_source")


def test_gray_zone_hosts_file_exists() -> None:
    path = Path(__file__).resolve().parents[1] / "app" / "prompts" / "curious_gray_zone_hosts.txt"
    hosts = get_curious_gray_zone_hosts(path)
    assert "lenta.ru" in hosts
    assert "snob.ru" in hosts
    assert "vc.ru" not in hosts


def _curious_digest() -> object:
    return type(
        "D",
        (),
        {
            "id": 1,
            "digest_type": "curious",
            "date": date(2026, 6, 16),
            "news_window_days": 7,
            "news_window_day_kind": "calendar",
        },
    )()


def _run_gray_verify(item: dict, bundle: dict, digest: object) -> None:
    svc = DigestService.__new__(DigestService)
    svc.settings = type("S", (), {})()
    svc._step1_curious_mode = True
    svc._step1_curious_tone_audit = None
    svc._active_recent_top5_fps = set()

    with patch("app.services.digest_service._fetch_article_page_bundle", return_value=bundle):
        with patch.object(svc, "_ensure_russian_candidate_title", side_effect=lambda _d, _u, h: h):
            with patch("app.services.digest_service._page_is_article_like", return_value=True):
                with patch("app.services.digest_service._ai_digest_topic_matches", return_value=True):
                    with patch("app.services.digest_service._apply_source_policy_from_url"):
                        with patch("app.services.digest_service._apply_material_form_to_candidate"):
                            with patch("app.services.digest_service._normalize_candidate_source"):
                                svc._verify_curious_candidate_dict(
                                    digest,
                                    item,
                                    prefetched_bundle=bundle,
                                    filter_enabled=lambda fid: fid
                                    not in ("published_date_undefined", "recent_top5_repeat"),
                                )


def test_gray_zone_domain_strict_post_http() -> None:
    digest = _curious_digest()
    item = {"url": "https://lenta.ru/news/2026/06/16/corporate-ai/", "title": ""}
    bundle = {
        "ok": True,
        "final_url": item["url"],
        "headline": "Компания представила новую нейросеть для документооборота",
        "topic_corpus": "Корпоративный пресс-релиз о внедрении LLM в банковский сектор без юмора.",
        "published_at": "2026-06-15T12:00:00+03:00",
        "article_markers": True,
        "headline_strict": True,
    }
    _run_gray_verify(item, bundle, digest)
    assert item.get("is_gray_zone_source") is True
    assert item.get("link_status") is not True
    assert "off_topic_not_curious" in str(item.get("verification_comment") or "")


def test_gray_zone_curios_passes_post_http() -> None:
    digest = _curious_digest()
    item = {"url": "https://lenta.ru/news/2026/06/16/ai-cats/", "title": ""}
    bundle = {
        "ok": True,
        "final_url": item["url"],
        "headline": "Нейросеть перепутала кошек с такси — смешные кадры из соцсетей",
        "topic_corpus": (
            "Пользователи делятся абсурдными генерациями ИИ: модель нарисовала смешных котиков "
            "вместо машин. Вирусный тред собрал тысячи реакций."
        ),
        "published_at": "2026-06-15T12:00:00+03:00",
        "article_markers": True,
        "headline_strict": True,
    }
    _run_gray_verify(item, bundle, digest)
    assert item.get("is_gray_zone_source") is True
    assert item.get("link_status") is True
    assert item.get("headline_editorial_ok") is True
