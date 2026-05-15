"""Окно дат публикации от даты выпуска (шаг 0)."""
from datetime import date
from types import SimpleNamespace

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
    assert ds.digest_earliest_news_date(d).isoformat() == "2026-05-12"


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
