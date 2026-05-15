from datetime import date
from types import SimpleNamespace

from app.services import digest_service as ds


def _digest(date_iso: str = "2026-05-15", days: int = 3, kind: str = "calendar"):
    return SimpleNamespace(
        id=1,
        date=date.fromisoformat(date_iso),
        news_window_days=days,
        news_window_day_kind=kind,
    )


class _Resp:
    def __init__(self, status_code: int, url: str, text: str):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


def test_extract_published_at_from_meta_article_published_time():
    chunk = """
    <html><head>
      <meta property="article:published_time" content="2026-05-10T14:30:00+03:00">
    </head><body></body></html>
    """
    got = ds._extract_published_at_from_chunk(chunk)
    assert got is not None
    assert got.startswith("2026-05-10")
    assert "+03:00" in got or "T14:30:00" in got


def test_extract_published_at_from_json_ld_date_published():
    chunk = """
    <html><head>
      <script type="application/ld+json">
      {"@type":"NewsArticle","headline":"ИИ в медицине","datePublished":"2026-04-22T09:15:00+03:00"}
      </script>
    </head></html>
    """
    got = ds._extract_published_at_from_chunk(chunk)
    assert got is not None
    assert "2026-04-22" in got


def test_published_at_from_url_path_dash_format():
    got = ds._published_at_from_url_path("https://www.1tv.ru/news/2026-04-26/540448")
    assert got is not None
    assert got.date().isoformat() == "2026-04-26"


def test_parse_russian_date_with_time():
    got = ds._parse_published_at_raw("26 апреля 2026, 22:40")
    assert got is not None
    assert got.year == 2026 and got.month == 4 and got.day == 26
    assert got.hour == 22 and got.minute == 40


def test_extract_published_at_from_1tv_like_html():
    chunk = '<span class="PlayerBlockHeading_date__5Man1">26 апреля 2026, 22:40</span>'
    got = ds._extract_published_at_from_page(chunk, "https://www.1tv.ru/news/2026-04-26/540448")
    assert got is not None
    assert "2026-04-26" in got
    assert "22:40" in got


def test_extract_published_at_from_time_datetime():
    chunk = """
    <html><body><article>
      <time datetime="2026-03-01T08:00:00+03:00">1 марта</time>
    </article></body></html>
    """
    got = ds._extract_published_at_from_chunk(chunk)
    assert got is not None
    assert "2026-03-01" in got


def test_bundle_includes_published_at(monkeypatch):
    html = """
    <html><head>
      <meta property="og:type" content="article">
      <meta property="article:published_time" content="2026-05-12T10:00:00+03:00">
      <meta property="og:title" content="Нейросеть OpenAI: новый релиз API">
    </head><body><h1>Нейросеть OpenAI: новый релиз API</h1></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, "https://example.com/ai-news", html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    bundle = ds._fetch_article_page_bundle("https://example.com/ai-news")
    assert bundle["ok"] is True
    assert bundle.get("published_at")
    assert "2026-05-12" in bundle["published_at"]


def test_verify_sets_published_at_from_bundle(monkeypatch):
    html = """
    <html><head>
      <meta property="og:type" content="article">
      <meta property="article:published_time" content="2026-05-08T16:45:00+03:00">
      <meta property="og:title" content="ИИ помогает в диагностике">
    </head><body><h1>ИИ помогает в диагностике</h1>
    <p>Нейросеть и машинное обучение в клинической практике.</p></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, "https://news.example.com/post/1", html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    item = {
        "original_number": 1,
        "title": "placeholder",
        "url": "https://news.example.com/post/1",
        "published_at": "2026-05-15T11:56:15+03:00",
        "verification_comment": "",
        "link_status": False,
    }
    svc = SimpleNamespace()
    svc._ensure_russian_candidate_title = lambda _d, _u, h: h
    ds.DigestService._verify_llm_candidate_dict(svc, _digest(days=10), item)
    assert item.get("headline_editorial_ok") is True
    assert item.get("published_at", "").startswith("2026-05-08")
    assert "11:56:15" not in item.get("published_at", "")


def test_extract_riastrela_like_article_date_not_sidebar():
    """itemprop=datePublished и дата у h1, не «14 мая» из блока популярного."""
    chunk = """
    <html><body>
    <div class="header">15.05.2026 17:52</div>
    <h1>Брянские врачи работают при поддержке ИИ</h1>
    <time class="b-meta-item" datetime="2025-11-09" itemprop="datePublished">09.11.2025 13:18:19</time>
    <p>Текст статьи про искусственный интеллект в медицине.</p>
    <aside>
      <a>14 мая 2026</a>
      <a>13 мая 2026</a>
    </aside>
    </body></html>
    """
    got = ds._extract_published_at_from_page(chunk, "https://riastrela.ru/p/207069/")
    assert got is not None
    assert got.startswith("2025-11-09")
    assert "13:18:19" in got
    assert "2026-05-14" not in got


def test_parse_dot_date_with_seconds():
    got = ds._parse_published_at_raw("09.11.2025 13:18:19")
    assert got is not None
    assert got.year == 2025 and got.month == 11 and got.day == 9
    assert got.hour == 13 and got.minute == 18 and got.second == 19


def test_verify_clears_published_at_when_page_has_no_date(monkeypatch):
    html = """
    <html><head>
      <meta property="og:type" content="article">
      <meta property="og:title" content="ИИ в образовании">
    </head><body><article><h1>ИИ в образовании</h1>
    <p>Нейросети и искусственный интеллект в школах и вузах.</p></article></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, "https://news.example.com/post/2", html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    item = {
        "original_number": 2,
        "title": "x",
        "url": "https://news.example.com/post/2",
        "published_at": "2026-05-15T11:56:15+03:00",
        "verification_comment": "",
    }
    svc = SimpleNamespace()
    svc._ensure_russian_candidate_title = lambda _d, _u, h: h
    ds.DigestService._verify_llm_candidate_dict(svc, _digest(), item)
    assert item.get("published_at") == ds.PUBLISHED_AT_UNDEFINED
