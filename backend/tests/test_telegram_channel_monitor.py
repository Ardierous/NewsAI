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
    assert "vc.ru" in newest.urls[0]
    assert "habr.com" in newest.urls[1]
    assert all("t.me" not in u for u in newest.urls)


def test_collect_respects_earliest_date(sample_html: str):
    with patch.object(tcm, "fetch_channel_html", return_value=sample_html):
        urls = tcm.collect_external_links_from_channels(
            ("technokratos",),
            earliest_date=date(2026, 5, 5),
            max_pages=1,
            max_links=20,
        )
    assert "vc.ru" in urls[0] or any("vc.ru" in u for u in urls)
    assert not any("ria.ru" in u for u in urls)


def test_parse_monitor_channels_default():
    assert tcm.parse_monitor_channels("technokratos, @technokratos") == ("technokratos",)
