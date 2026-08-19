from types import SimpleNamespace

from app.services import digest_service as ds
from app.services.article_reader_fallback import (
    extract_reader_markdown_urls,
    fetch_article_bundle_via_reader_proxy,
    looks_like_antibot_shell,
)


class _Resp:
    def __init__(self, status_code: int, url: str, text: str):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


def test_looks_like_antibot_shell_cloudflare():
    html = """
    <html><head><title>Just a moment...</title></head>
    <body>Checking your browser before accessing example.com. Ray ID: abc. cloudflare challenge-platform.</body>
    </html>
    """ + (" " * 30_000)
    assert looks_like_antibot_shell(html) is True


def test_looks_like_antibot_shell_skips_real_article():
    html = """
    <html><head><meta property="og:type" content="article"></head>
    <body><article><h1>Нейросеть научилась писать код</h1>
    <p>""" + ("Текст статьи про искусственный интеллект. " * 40) + """</p></article></body></html>
    """
    assert looks_like_antibot_shell(html) is False


def test_fetch_bundle_uses_reader_on_cloudflare_title(monkeypatch):
    html = """
    <html><head><title>Just a moment...</title>
    <meta property="og:title" content="Just a moment..."></head>
    <body>Checking your browser. cloudflare ray id challenge-platform.</body></html>
    """
    reader_md = """
Source URL: https://tass.ru/ekonomika/123
Title: Компания представила новую нейросеть

Подробный текст статьи про искусственный интеллект и машинное обучение в экономике.
Ещё несколько предложений с фактами и контекстом для проверки корпуса.
"""

    def fake_http_get(_url: str):
        return _Resp(200, "https://tass.ru/ekonomika/123", html)

    def fake_requests_get(url: str, *args, **kwargs):
        assert "r.jina.ai/https://" in url or "r.jina.ai/http://" in url
        return _Resp(200, url, reader_md)

    monkeypatch.setattr(ds, "_http_get_html_for_article", fake_http_get)
    monkeypatch.setattr(ds.requests, "get", fake_requests_get)
    bundle = ds._fetch_article_page_bundle("https://tass.ru/ekonomika/123")
    assert bundle["ok"] is True
    assert bundle["headline_source"] == "reader_proxy"
    assert "нейросеть" in bundle["headline"].lower()


def test_reader_proxy_tries_https_first(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str, *args, **kwargs):
        calls.append(url)
        if url.endswith("https://example.com/news/1"):
            return _Resp(
                200,
                url,
                "Title: AI breakthrough\n\n"
                + ("Long article body about neural networks and chips in industry. " * 8),
            )
        return _Resp(404, url, "")

    monkeypatch.setattr("app.services.article_reader_fallback.requests.get", fake_get)
    bundle = fetch_article_bundle_via_reader_proxy("https://example.com/news/1")
    assert bundle is not None
    assert bundle["ok"] is True
    assert calls[0].startswith("https://r.jina.ai/https://")


def test_extract_reader_markdown_urls_keeps_investing_articles():
    listing = "https://ru.investing.com/analysis/bonds"
    md = """
Title: Аналитика облигаций — Investing.com

[Цена нефти и газа](https://ru.investing.com/analysis/article-200323069)
[Ключевая ставка](https://ru.investing.com/analysis/article-200322287)
[Акции](https://ru.investing.com/analysis/stock-markets)
[](https://r.jina.ai/https://ru.investing.com/analysis/article-200323708)
"""
    hrefs = extract_reader_markdown_urls(md, listing)
    assert "https://ru.investing.com/analysis/article-200323069" in hrefs
    assert "https://ru.investing.com/analysis/article-200322287" in hrefs
    assert "https://ru.investing.com/analysis/article-200323708" in hrefs
    assert "https://ru.investing.com/analysis/stock-markets" in hrefs


def test_reader_proxy_listing_keeps_hrefs_without_long_corpus(monkeypatch):
    listing = "https://ru.investing.com/analysis/bonds"
    md = """
Title: Bonds

[One](https://ru.investing.com/analysis/article-200323069)
[Two](https://ru.investing.com/analysis/article-200322287)
"""

    def fake_get(url: str, *args, **kwargs):
        assert kwargs.get("headers", {}).get("X-With-Links-Summary") == "all"
        return _Resp(200, url, md)

    monkeypatch.setattr("app.services.article_reader_fallback.requests.get", fake_get)
    bundle = fetch_article_bundle_via_reader_proxy(listing)
    assert bundle is not None
    assert bundle["ok"] is True
    assert bundle["is_listing_page"] is True
    assert bundle["fetch_via"] == "reader_proxy"
    assert "https://ru.investing.com/analysis/article-200323069" in bundle["reader_hrefs"]
