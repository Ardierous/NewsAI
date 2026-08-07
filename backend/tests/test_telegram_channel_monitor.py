"""Парсинг t.me/s/ и сбор внешних URL для шага 1."""
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import telegram_channel_monitor as tcm

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "technokratos_tme_sample.html"


@pytest.fixture
def sample_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_normalize_channel_username():
    assert tcm.normalize_channel_username("@technokratos") == "technokratos"
    assert tcm.normalize_channel_username("https://t.me/s/technokratos") == "technokratos"
    assert tcm.normalize_channel_username("") is None


def test_is_telegram_internal_url():
    assert tcm.is_telegram_internal_url("https://t.me/technokratos/1")
    assert tcm.is_telegram_internal_url("http://www.technokratos.com/")
    assert not tcm.is_telegram_internal_url("https://habr.com/ru/news/1/")


def test_parse_channel_posts_extracts_external_only(sample_html: str):
    posts = tcm.parse_channel_posts_html(sample_html, "technokratos")
    assert len(posts) == 2
    newest = posts[0]
    assert newest.post_id == 3377
    assert "Дайджест" in tcm.message_plain_text(newest.text_html)
    assert "vc.ru" in newest.urls[0]
    assert "habr.com" in newest.urls[1]
    assert all("t.me" not in u for u in newest.urls)


def test_post_matches_digest_filter(sample_html: str):
    posts = tcm.parse_channel_posts_html(sample_html, "technokratos")
    digest_post = posts[0]
    other_post = posts[1]
    assert tcm.post_matches_text_filter(digest_post, "Дайджест")
    assert not tcm.post_matches_text_filter(other_post, "Дайджест")


def test_collect_only_digest_posts(sample_html: str):
    with patch.object(tcm, "fetch_channel_html", return_value=sample_html):
        urls, _markers = tcm.collect_external_links_from_channels(
            ("technokratos",),
            earliest_date=date(2026, 4, 1),
            max_pages=1,
            max_links=20,
            max_digest_posts=3,
            post_text_filter="Дайджест",
        )
    assert any("vc.ru" in u for u in urls)
    assert not any("ria.ru" in u for u in urls)


def test_collect_respects_earliest_date(sample_html: str):
    with patch.object(tcm, "fetch_channel_html", return_value=sample_html):
        urls, _markers = tcm.collect_external_links_from_channels(
            ("technokratos",),
            earliest_date=date(2026, 5, 5),
            max_pages=1,
            max_links=20,
            post_text_filter="Дайджест",
        )
    assert "vc.ru" in urls[0] or any("vc.ru" in u for u in urls)
    assert not any("ria.ru" in u for u in urls)


def test_collect_skips_old_posts_without_blocking_newer_digest_links():
    """t.me/s/ отдаёт посты от старых к новым — нельзя break на первом посте вне окна."""
    from datetime import datetime, timezone

    posts = [
        tcm.TelegramPostLinks(
            channel="technokratos",
            post_id=1,
            published_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
            text_html="<b>Утро-Дайджест</b> старый",
            urls=("https://old.example.com/a",),
        ),
        tcm.TelegramPostLinks(
            channel="technokratos",
            post_id=2,
            published_at=datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc),
            text_html="<b>Утро-Дайджест</b> свежий",
            urls=(
                "https://habr.com/ru/news/1049262/",
                "https://vc.ru/ai/2984485-anthropic-uluchshila-claude-design-obnovlenie-dlya-polzovateley",
            ),
        ),
    ]
    urls = tcm.collect_urls_from_digest_posts(
        posts,
        earliest_date=date(2026, 6, 14),
        max_digest_posts=2,
        post_text_filter="Дайджест",
    )
    assert "https://habr.com/ru/news/1049262/" in urls
    assert any("2984485" in u for u in urls)
    assert not any("old.example.com" in u for u in urls)


def test_telegram_seed_markers_from_proxyapi(monkeypatch: pytest.MonkeyPatch, sample_html: str):
    from types import SimpleNamespace

    settings = SimpleNamespace(
        step1_telegram_monitor_enabled=True,
        step1_telegram_monitor_channels="technokratos",
        step1_telegram_max_pages=1,
        step1_telegram_max_links=20,
        step1_telegram_max_digest_posts=3,
        step1_telegram_post_text_filter="Дайджест",
        step1_telegram_timeout_sec=10.0,
        step1_telegram_via_proxyapi=True,
        step1_telegram_direct_fallback=False,
        proxyapi_web_search_enabled=True,
        proxyapi_web_search_context_size="high",
        step1_telegram_proxyapi_context_size="high",
    )

    class FakeProxy:
        def fetch_telegram_digest_seed_urls(self, channel, **kwargs):
            assert channel == "technokratos"
            return (
                ["https://vc.ru/chatgpt/2913363-openai-chatgpt", "https://habr.com/ru/news/1032110/"],
                [sample_html],
            )

    markers = tcm.collect_telegram_seed_url_markers_for_digest(
        settings,
        earliest_date=date(2026, 5, 1),
        proxy=FakeProxy(),
    )
    assert markers
    assert all(v == "https://t.me/s/technokratos" for v in markers.values())


def test_collect_telegram_seed_prefers_proxyapi(monkeypatch: pytest.MonkeyPatch, sample_html: str):
    from types import SimpleNamespace

    settings = SimpleNamespace(
        step1_telegram_monitor_enabled=True,
        step1_telegram_monitor_channels="technokratos",
        step1_telegram_max_pages=1,
        step1_telegram_max_links=20,
        step1_telegram_max_digest_posts=3,
        step1_telegram_post_text_filter="Дайджест",
        step1_telegram_timeout_sec=10.0,
        step1_telegram_via_proxyapi=True,
        step1_telegram_direct_fallback=False,
        proxyapi_web_search_enabled=True,
        proxyapi_web_search_context_size="high",
        step1_telegram_proxyapi_context_size="high",
    )

    class FakeProxy:
        def fetch_telegram_digest_seed_urls(self, channel, **kwargs):
            assert channel == "technokratos"
            return (
                ["https://vc.ru/chatgpt/2913363-openai-chatgpt", "https://habr.com/ru/news/1032110/"],
                [sample_html],
            )

    urls = tcm.collect_telegram_seed_urls_for_digest(
        settings,
        earliest_date=date(2026, 5, 1),
        proxy=FakeProxy(),
    )
    assert any("vc.ru" in u for u in urls)
    assert any("habr.com" in u for u in urls)


def test_parse_monitor_channels_default():
    assert tcm.parse_monitor_channels("technokratos, @technokratos") == ("technokratos",)
