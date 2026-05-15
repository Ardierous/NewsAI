from types import SimpleNamespace

from app.services import digest_service as ds


class _Resp:
    def __init__(self, status_code: int, url: str, text: str):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


def test_bundle_marks_article_page(monkeypatch):
    html = """
    <html><head>
      <meta property="og:type" content="article">
      <meta property="og:url" content="https://example.com/news/abc">
      <meta property="og:title" content="Big AI release by Example Inc">
    </head><body><h1>Big AI release by Example Inc</h1></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, "https://example.com/news/abc", html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    bundle = ds._fetch_article_page_bundle("https://example.com/redirect")
    assert bundle["ok"] is True
    assert bundle["article_markers"] is True
    assert bundle["headline"]
    assert bundle["headline_strict"] is True
    assert "topic_corpus" in bundle
    assert ds._ai_digest_topic_matches(bundle["topic_corpus"], bundle["headline"])


def test_bundle_rejects_non_article_markup(monkeypatch):
    html = """
    <html><head><title>All AI news today</title></head>
    <body><h1>All AI news today</h1></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, "https://example.com/news", html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    bundle = ds._fetch_article_page_bundle("https://example.com/news")
    assert bundle["ok"] is True
    assert bundle["article_markers"] is False
    assert bundle["headline_source"] in {"h1_fallback", "html_title_fallback", "none"}


def test_bundle_handles_http_error(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return _Resp(404, "https://example.com/missing", "")

    monkeypatch.setattr(ds.requests, "get", fake_get)
    bundle = ds._fetch_article_page_bundle("https://example.com/missing")
    assert bundle["ok"] is False


def test_source_policy_flags_aggregator():
    tier, is_aggregator, status = ds._classify_source_policy("https://news.google.com/articles/abc")
    assert is_aggregator is True
    assert tier == "Tier-4"
    assert status == "❗ без подтверждения"


def test_editorial_headline_rejects_numeric_ids():
    assert ds._editorial_headline_rejected("77034003406") is True
    assert ds._editorial_headline_rejected("16230143264") is True
    assert ds._editorial_headline_rejected("77034003406 — Документы в суд") is True
    assert ds._editorial_headline_rejected("ИИ в образовании: новый план на 2026 год") is False
    assert ds._editorial_headline_rejected("OpenAI выпустила обновление API") is False


def test_ai_topic_rejects_bankruptcy_style_corpus():
    corpus = (
        "77034003406 объявления о несостоятельности Коммерсантъ "
        "Решением Арбитражного суда Республики Крым по делу о банкротстве общества"
    )
    assert ds._ai_digest_topic_matches(corpus, "77034003406") is False


def test_ai_topic_accepts_clear_ai_headline():
    assert ds._ai_digest_topic_matches("Главные новости дня", "Нейросеть научилась распознавать речь") is True


def test_ai_topic_accepts_ai_headline_even_if_corpus_too_short():
    assert ds._ai_digest_topic_matches("", "ChatGPT: обновление API для разработчиков") is True


def test_article_markers_detects_article_element():
    chunk = '<html><body><article><h1>Нейросеть и ИИ</h1><p>Текст</p></article></body></html>'
    assert ds._has_article_markers(chunk, []) is True


def test_pick_display_url_prefers_canonical_for_same_page():
    final_url = "https://example.com/news/abc?utm_source=x"
    canonical = "https://example.com/news/abc"
    assert ds._pick_display_url(final_url, canonical, None) == canonical


def test_pick_display_url_keeps_final_for_other_page():
    final_url = "https://example.com/news/abc"
    canonical = "https://example.com/news/other"
    assert ds._pick_display_url(final_url, canonical, None) == final_url


def test_verify_rejects_redirect_to_homepage(monkeypatch):
    html = """
    <html><head><title>Expert - news</title></head>
    <body><h1>Главная</h1><p>ИИ новости на сайте</p></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, "https://expert.ru/", html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    item = {
        "original_number": 1,
        "title": "Статья про ИИ",
        "url": "https://expert.ru/2026/05/17/strategiya-razvitiya-ai",
        "verification_comment": "",
    }
    from unittest.mock import MagicMock

    svc = MagicMock()
    ds.DigestService._verify_llm_candidate_dict(svc, 1, item)
    assert "REJECT_REASON:url_redirect_mismatch" in str(item.get("verification_comment") or "")
    assert item.get("headline_editorial_ok") is not True
