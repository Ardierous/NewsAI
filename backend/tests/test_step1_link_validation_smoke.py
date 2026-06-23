from types import SimpleNamespace

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
from app.services import news_search


def test_bot_challenge_html_detected():
    html = """<!DOCTYPE html><html><head>
    <noscript><meta http-equiv="refresh" content="0; url=/exhkqyad"></noscript>
    </head><body><div class="container"></div></body></html>"""
    assert ds._is_bot_challenge_html(html) is True
    assert ds._is_bot_challenge_html('<html><article><h1>Нейросеть</h1></article></html>') is False


def test_search_noise_url_filters_indexes_and_authors():
    assert news_search.is_search_noise_url("https://ria.ru/20011020/2026.html")
    assert news_search.is_search_noise_url("https://news.tek.fm/authors/10058")
    assert news_search.is_search_noise_url("https://arxiv.org/pdf/1706.01040")
    assert not news_search.is_search_noise_url("https://vz.ru/news/2026/4/9/1409407.html")
    assert not news_search.is_search_noise_url("https://ria.ru/20260519/ii-2093333250.html")
    assert news_search.search_url_prefilter_reason("https://ria.ru/20260519/ii-2093333250.html") is None


class _Resp:
    def __init__(self, status_code: int, url: str, text: str):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


def test_reader_proxy_bundle_extracts_headline_and_corpus():
    markdown_text = """
Source URL: https://www.reuters.com/technology/example
Title: Anthropic briefs watchdog on cyber risks

Reuters reports that Anthropic warned about cyber flaws exposed by adversarial prompting.
The note references model safeguards and incidents in financial systems.
"""
    # Сеть не трогаем: проверяем только локальные парсеры markdown.
    assert ds._reader_extract_headline(markdown_text) == "Anthropic briefs watchdog on cyber risks"
    assert len(ds._reader_topic_corpus(markdown_text)) > 120


def test_fetch_bundle_uses_reader_fallback_on_bot_challenge(monkeypatch):
    html_challenge = """
    <html><head><noscript><meta http-equiv="refresh" content="0; url=/exhkqyad"></noscript></head>
    <body>Loading...</body></html>
    """
    reader_md = """
Source URL: https://www.reuters.com/technology/example
Title: Anthropic briefs watchdog on cyber risks

Reuters reports that Anthropic warned about cyber flaws exposed by adversarial prompting.
The note references model safeguards and incidents in financial systems.
"""

    def fake_http_get(_url: str):
        return _Resp(200, "https://www.reuters.com/technology/example", html_challenge)

    def fake_requests_get(url: str, *args, **kwargs):
        assert "r.jina.ai/" in url
        return _Resp(200, url, reader_md)

    monkeypatch.setattr(ds, "_http_get_html_for_article", fake_http_get)
    monkeypatch.setattr(ds.requests, "get", fake_requests_get)
    bundle = ds._fetch_article_page_bundle("https://www.reuters.com/technology/example")
    assert bundle["ok"] is True
    assert bundle["headline_source"] == "reader_proxy"
    assert bundle["headline"]


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
    assert tier == "Tier-2"
    assert "сомнительный" in status or "подтверждено" in status


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


def test_cnews_topic_index_detected_and_extracts_news_links():
    html = """
    <html><head><meta property="og:type" content="website"></head>
    <body>
    <strong>Индексная книга (каталог) CNews</strong>
    <table>
    <tr><td>06.05.2026</td><td><a href="/news/top/2026-05-06_sozdateli_yandeksa_potratyat">A</a></td></tr>
    <tr><td>13.04.2026</td><td><a href="/news/top/2026-04-13_samye_umnye_nejronki">B</a></td></tr>
    <tr><td>29.01.2026</td><td><a href="/news/line/2026-01-29_top-7_professij">C</a></td></tr>
    <tr><td>24.10.2024</td><td><a href="/articles/2024-10-11_kakie_kosmicheskie_tehnologiiservisy">D</a></td></tr>
    </table>
    </body></html>
    """
    page_url = "https://www.cnews.ru/book/mutual/8757/251081"
    assert news_search.is_topic_pool_page_url(page_url) is True
    bundle = {
        "ok": True,
        "article_markers": False,
        "soft_article_signals": True,
        "headline_strict": False,
        "headline": "Искусственный интеллект - Deep Learning",
    }
    assert ds._is_news_listing_page(page_url, html, bundle) is True
    urls = ds._extract_listing_article_urls(html, page_url, limit=6)
    assert len(urls) >= 3
    assert all("/news/" in u or "/articles/" in u for u in urls)
    assert all("/book/mutual/" not in u for u in urls)


def test_verify_rejects_listing_page_url_without_fetch():
    item = {
        "original_number": 1,
        "title": "Лента",
        "url": "https://shtruzel.ru/news",
        "verification_comment": "",
    }
    from unittest.mock import MagicMock

    svc = MagicMock()
    ds.DigestService._verify_llm_candidate_dict(svc, _digest(), item)
    assert "news_listing_page" in str(item.get("verification_comment") or "")
    assert item.get("link_status") is not True


def test_sostav_blog_post_not_treated_as_listing_page():
    """Блоговая вёрстка sostav с заголовком и текстом — не лента."""
    page_url = "https://www.sostav.ru/blogs/290069/87070"
    html = "<html><body>" + "<a href='/blogs/1/2'>x</a>" * 35 + "<p>текст</p>" * 2 + "</body></html>"
    corpus = "x" * 220
    bundle = {
        "ok": True,
        "article_markers": False,
        "soft_article_signals": False,
        "headline_strict": True,
        "headline": "ИИ научился шутить",
        "topic_corpus": corpus,
    }
    assert ds._is_news_listing_page(page_url, html, bundle) is False


def test_listing_page_detected_and_extracts_article_links():
    html = """
    <html><head><meta property="og:type" content="website"></head>
    <body>
    <h1>Нейросети</h1>
    <h2><a href="https://www.unian.net/techno/neiroseti/article-one-12345.html">One</a></h2>
    <h2><a href="https://www.unian.net/techno/neiroseti/article-two-67890.html">Two</a></h2>
    <h2><a href="https://www.unian.net/techno/neiroseti/article-three-11111.html">Three</a></h2>
    <h2><a href="https://www.unian.net/techno/neiroseti/article-four-22222.html">Four</a></h2>
    </body></html>
    """
    page_url = "https://www.unian.net/techno/neiroseti"
    bundle = {
        "ok": True,
        "article_markers": False,
        "soft_article_signals": False,
        "headline_strict": False,
        "headline": "Нейросети",
    }
    assert ds._is_news_listing_page(page_url, html, bundle) is True
    urls = ds._extract_listing_article_urls(html, page_url, limit=6)
    assert len(urls) >= 3
    assert all("/techno/neiroseti/" in u and u != page_url for u in urls)


def test_expand_listing_url_candidates_returns_child_articles(monkeypatch):
    listing_html = """
    <html><body><h1>Искусственный интеллект</h1>
    <h2><a href="https://www.content-review.com/articles/ai-samsung-gemini.html">Samsung</a></h2>
    <h2><a href="https://www.content-review.com/articles/ai-doctor-lawsuit.html">Doctor</a></h2>
    <h2><a href="https://www.content-review.com/articles/ai-alibaba-qwen.html">Alibaba</a></h2>
    <h2><a href="https://www.content-review.com/articles/ai-brain-interface.html">Brain</a></h2>
    </body></html>
    """
    article_html = """
    <html><head>
      <meta property="og:type" content="article">
      <meta property="og:title" content="Samsung отдал зрение холодильникам Gemini">
    </head><body><h1>Samsung отдал зрение холодильникам Gemini</h1>
    <p>Нейросеть Gemini интегрирована в умные холодильники Samsung Family Hub для распознавания продуктов.</p>
    </body></html>
    """

    def fake_get(url, *args, **kwargs):
        u = str(url)
        if "artificial_intelligence" in u:
            return _Resp(200, u, listing_html)
        return _Resp(200, u, article_html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    pairs = ds._expand_listing_url_candidates(
        "https://www.content-review.com/articles/artificial_intelligence/", max_children=4
    )
    assert len(pairs) >= 1
    assert all(not b.get("is_listing_page") for _, b in pairs)


def test_verify_resets_skeleton_link_status_before_checks(monkeypatch):
    """Скелет поиска ставил link_status=True — verify не должен сохранять это до успеха."""
    html = """
    <html><head><meta property="og:type" content="article">
    <meta property="og:title" content="Нейросеть OpenAI: релиз API"></head>
    <body><h1>Нейросеть OpenAI: релиз API</h1>
    <p>Искусственный интеллект и машинное обучение в облаке для разработчиков.</p></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, "https://example.com/news/ai-api", html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    svc = ds.DigestService.__new__(ds.DigestService)
    svc._ensure_russian_candidate_title = lambda _d, _u, h: h
    item = ds.DigestService._skeleton_dict_from_search_url(svc, "https://example.com/news/ai-api", "2026-05-15", 1)
    assert item["link_status"] is True
    ds.DigestService._verify_llm_candidate_dict(svc, _digest(), item)
    assert item["link_status"] is True
    assert item["headline_editorial_ok"] is True


def test_listing_seed_section_expands_not_single_candidate(monkeypatch):
    """URL рубрики /articles/… — seed для разворота, не кандидат сам по себе."""
    import app.services.news_search as news_search

    listing_url = "https://www.content-review.com/articles/artificial_intelligence/"
    assert news_search.is_step1_listing_seed_url(listing_url)
    assert not news_search.is_listing_page_url(listing_url)

    child = "https://www.content-review.com/articles/ai-samsung-gemini.html"
    listing_bundle = {
        "ok": True,
        "is_listing_page": True,
        "listing_article_urls": [child],
        "final_url": listing_url,
        "display_url": listing_url,
    }
    child_bundle = {
        "ok": True,
        "is_listing_page": False,
        "final_url": child,
        "display_url": child,
        "headline": "Samsung и Gemini",
        "topic_corpus": "нейросеть искусственный интеллект " * 20,
        "article_markers": True,
    }

    def fake_fetch(url):
        u = str(url).rstrip("/")
        if u.endswith("/articles/artificial_intelligence"):
            return listing_bundle
        return child_bundle

    monkeypatch.setattr(ds, "_fetch_article_page_bundle", fake_fetch)
    pairs = ds._expand_listing_url_candidates(listing_url, max_children=3)
    assert pairs
    assert all("artificial_intelligence" not in p[0] for p in pairs)


def test_verify_rejects_listing_seed_section_without_expansion(monkeypatch):
    listing_url = "https://www.content-review.com/articles/artificial_intelligence/"

    def fake_get(url, *args, **kwargs):
        html = """
        <html><head><meta property="og:type" content="website"></head>
        <body><h1>Искусственный интеллект</h1>
        <h2><a href="https://www.content-review.com/articles/ai-one.html">One</a></h2>
        </body></html>
        """
        return _Resp(200, listing_url, html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    svc = ds.DigestService.__new__(ds.DigestService)
    svc._ensure_russian_candidate_title = lambda _d, _u, h: h
    svc._is_step1_filter_enabled = lambda _fid: True
    svc._active_recent_top5_fps = set()
    svc._step1_curious_mode = False
    svc.settings = type("S", (), {"step1_curious_use_serious_tiers": False})()
    item = ds.DigestService._skeleton_dict_from_search_url(svc, listing_url, "2026-06-15", 1)
    ds.DigestService._verify_llm_candidate_dict(svc, _digest(), item)
    assert item["link_status"] is False
    assert "news_listing_page" in str(item.get("verification_comment") or "")


def test_ria_product_listing_expands_to_articles(monkeypatch):
    listing_url = "https://ria.ru/product_iskusstvennyy-intellekt/"
    child = "https://ria.ru/20260618/robot-mfpt-1998765432.html"
    listing_html = f"""
    <html><body>
    <h1>Искусственный интеллект (ИИ)</h1>
    <a href="{child}">Гуманоидный робот</a>
    <a href="https://ria.ru/20260617/medvedev-ii-1998765431.html">Медведев</a>
    <a href="https://ria.ru/20260616/stroitelstvo-ii-1998765430.html">Строительство</a>
    </body></html>
    """
    article_html = """
    <html><head><meta property="og:type" content="article">
    <meta property="og:title" content="Гуманоидный робот в МФТИ"></head>
    <body><h1>Гуманоидный робот в МФТИ</h1>
    <p>Искусственный интеллект и нейросети в образовании.</p></body></html>
    """

    def fake_get(url, *args, **kwargs):
        u = str(url).rstrip("/")
        if u.endswith("/product_iskusstvennyy-intellekt"):
            return _Resp(200, listing_url, listing_html)
        return _Resp(200, u, article_html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    pairs = ds._expand_listing_url_candidates(listing_url, max_children=3)
    assert pairs
    assert listing_url.rstrip("/") not in {p[0].rstrip("/") for p in pairs}
    assert any("20260618" in p[0] for p in pairs)


def test_expand_listing_fallback_treats_article_when_no_children(monkeypatch):
    article_html = """
    <html><head>
      <meta property="og:type" content="article">
      <meta property="og:title" content="Нейросеть: прорыв в распознавании речи">
    </head><body><h1>Нейросеть: прорыв в распознавании речи</h1>
    <p>Искусственный интеллект и нейросети в промышленности.</p></body></html>
    """

    def fake_get(url, *args, **kwargs):
        return _Resp(200, str(url), article_html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    monkeypatch.setattr(
        ds,
        "_fetch_article_page_bundle",
        lambda url: {
            "ok": True,
            "is_listing_page": True,
            "listing_article_urls": [],
            "final_url": url,
            "display_url": url,
            "headline": "Нейросеть: прорыв в распознавании речи",
            "article_markers": True,
            "soft_article_signals": True,
            "headline_strict": True,
            "topic_corpus": "искусственный интеллект нейросети",
        },
    )
    pairs = ds._expand_listing_url_candidates("https://example.com/news/ai-speech", max_children=4)
    assert len(pairs) == 1
    assert pairs[0][1].get("is_listing_page") is False


def test_redirect_should_reject_homepage_but_not_article_canonical():
    bundle_article = {
        "headline": "Нейросеть OpenAI: обновление для разработчиков",
        "article_markers": True,
        "soft_article_signals": True,
    }
    assert (
        ds._redirect_should_reject(
            "https://example.com/r/abc?utm=1",
            "https://example.com/news/ai-release-2026",
            bundle_article,
        )
        is False
    )
    assert ds._redirect_should_reject("https://example.com/news/x", "https://example.com/", bundle_article) is True


def test_redirect_allowed_when_headline_extracted_without_article_markers():
    bundle = {
        "headline": "ChatGPT получил обновление API для разработчиков",
        "article_markers": False,
        "soft_article_signals": False,
    }
    assert (
        ds._redirect_should_reject(
            "https://news.site/click?id=1",
            "https://news.site/2026/05/ai-api-update",
            bundle,
        )
        is False
    )


def test_page_is_article_like_with_headline_and_corpus():
    bundle = {
        "is_listing_page": False,
        "article_markers": False,
        "soft_article_signals": False,
        "headline": "Нейросети в медицине: новый протокол",
        "topic_corpus": "искусственный интеллект " * 40,
    }
    assert ds._page_is_article_like(bundle) is True


def test_verify_accepts_redirected_article_with_headline(monkeypatch):
    html = """
    <html><head>
      <meta property="og:title" content="Нейросеть OpenAI: релиз API">
    </head><body><h1>Нейросеть OpenAI: релиз API</h1>
    <p>Искусственный интеллект и машинное обучение для разработчиков нейросетей.</p></body></html>
    """

    def fake_get(*_args, **_kwargs):
        return _Resp(200, "https://example.com/news/ai-api-2026", html)

    monkeypatch.setattr(ds.requests, "get", fake_get)
    item = {
        "original_number": 1,
        "title": "",
        "url": "https://example.com/redirect?utm=track",
        "verification_comment": "",
    }
    from unittest.mock import MagicMock

    svc = MagicMock()
    svc._ensure_russian_candidate_title = lambda _d, _u, h: h
    ds.DigestService._verify_llm_candidate_dict(svc, _digest(), item)
    assert item.get("link_status") is True
    assert item.get("headline_editorial_ok") is True
    assert "example.com/news/ai-api" in item.get("url", "")


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
    ds.DigestService._verify_llm_candidate_dict(svc, _digest(), item)
    assert "REJECT_REASON:url_redirect_mismatch" in str(item.get("verification_comment") or "")
    assert item.get("headline_editorial_ok") is not True
