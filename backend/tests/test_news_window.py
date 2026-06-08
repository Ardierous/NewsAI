"""Окно дат публикации от даты выпуска (шаг 0)."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.services import digest_service as ds


class _Resp:
    def __init__(self, status_code: int, url: str, text: str):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


def _digest(date_iso: str = "2026-05-15", days: int = 3, kind: str = "calendar"):
    return SimpleNamespace(
        id=1,
        date=date.fromisoformat(date_iso),
        news_window_days=days,
        news_window_day_kind=kind,
    )


def test_digest_earliest_calendar_days():
    d = _digest("2026-05-15", days=3, kind="calendar")
    with patch.object(ds, "digest_news_anchor_date", return_value=date(2026, 5, 15)):
        assert ds.digest_earliest_news_date(d).isoformat() == "2026-05-12"


def test_digest_news_anchor_date_not_before_today_msk():
    """Верхняя граница окна — не раньше сегодняшней даты по МСК."""
    d = _digest("2026-05-10", days=3, kind="calendar")
    fixed_now = ds.datetime(2026, 5, 20, 12, 0, 0, tzinfo=ds.MSK_TZ)
    with patch("app.services.digest_service.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: ds.datetime(*a, **kw)
        anchor = ds.digest_news_anchor_date(d)
        earliest = ds.digest_earliest_news_date(d)
    assert anchor == date(2026, 5, 20)
    assert earliest == date(2026, 5, 17)


def test_stale_digest_rejects_article_before_today_window():
    """Материал 12.05 отсекается, если якорь окна — сегодня 20.05 (а не дата выпуска 10.05)."""
    d = _digest("2026-05-10", days=3, kind="calendar")
    with patch.object(ds, "digest_news_anchor_date", return_value=date(2026, 5, 20)):
        code = ds._published_at_window_reject_code(
            d,
            "2026-05-12T10:00:00+03:00",
            "https://example.com/news/2026/05/12/article",
        )
    assert code == "published_before_window"


def test_url_path_compact_ria_date_in_window():
    d = _digest("2026-05-19", days=3, kind="calendar")
    url = "https://ria.ru/20260519/ii-2093333250.html"
    assert ds._url_path_publication_day(url) == date(2026, 5, 19)
    with patch.object(ds, "digest_news_anchor_date", return_value=date(2026, 5, 19)):
        assert ds._url_path_date_before_digest_window(d, url) is False


def test_url_path_date_after_anchor_rejected():
    d = _digest("2026-06-08", days=7, kind="calendar")
    url = "https://example.com/news/2026/06/15/future-article"
    with patch.object(ds, "digest_news_anchor_date", return_value=date(2026, 6, 8)):
        with patch.object(ds, "digest_earliest_news_date", return_value=date(2026, 6, 1)):
            assert ds._url_path_date_before_digest_window(d, url) is True


def test_url_in_window_overrides_stale_meta():
    d = _digest("2026-05-19", days=3, kind="calendar")
    url = "https://ria.ru/20260519/ii-2093333250.html"
    with patch.object(ds, "digest_news_anchor_date", return_value=date(2026, 5, 19)):
        assert ds._published_at_before_digest_window(d, "2023-01-01T12:00:00+03:00", url) is False


def test_undefined_date_reject_code():
    d = _digest()
    assert (
        ds._published_at_window_reject_code(d, ds.PUBLISHED_AT_UNDEFINED, "https://example.com/article/slug")
        == "published_date_undefined"
    )


def test_verify_rejects_vedomosti_2023_in_default_window(monkeypatch):
    """Регрессия: статья 2023 в URL не должна проходить при выпуске 2026-05-15 и окне 3 дня."""
    url = (
        "https://www.vedomosti.ru/technologies/importsubstitution/articles/"
        "2023/05/31/977936-iskusstvennii-intellekt-v-deistvii"
    )
    html = """
    <html><head>
      <meta property="og:type" content="article">
      <meta property="og:title" content="Искусственный интеллект в действии">
      <meta property="article:published_time" content="2023-05-31T12:00:00+03:00">
    </head><body><h1>Искусственный интеллект в действии</h1>
    <p>Нейросети и машинное обучение в промышленности.</p></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, url, html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    item = {
        "original_number": 1,
        "title": "",
        "url": url,
        "verification_comment": "",
        "link_status": False,
    }
    svc = SimpleNamespace()
    svc._ensure_russian_candidate_title = lambda _d, _u, h: h
    ds.DigestService._verify_llm_candidate_dict(svc, _digest(), item)
    assert "published_before_window" in str(item.get("verification_comment") or "")
    assert item.get("headline_editorial_ok") is not True


def test_filter_verified_pool_removes_outside_window():
    d = _digest("2026-05-19", days=3, kind="calendar")
    with patch.object(ds, "digest_news_anchor_date", return_value=date(2026, 5, 19)):
        pool = [
            {
                "url": "https://ria.ru/20260519/ii-1.html",
                "published_at": "2026-05-19T10:00:00+03:00",
            },
            {
                "url": "https://ria.ru/20260510/ii-2.html",
                "published_at": "2026-05-10T10:00:00+03:00",
            },
        ]
        kept, removed = ds._filter_verified_pool_by_date_window(d, pool)
    assert removed == 1
    assert len(kept) == 1
    assert "20260519" in kept[0]["url"]


def test_reject_code_after_anchor_window():
    d = _digest("2026-05-19", days=3, kind="calendar")
    with patch.object(ds, "digest_news_anchor_date", return_value=date(2026, 5, 19)):
        code = ds._published_at_window_reject_code(
            d,
            "2026-05-25T10:00:00+03:00",
            "https://example.com/news/article",
        )
    assert code == "published_before_window"
