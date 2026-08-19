import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import news_search
from app.source_tiers_policy import is_policy_tier_source


def test_extract_http_urls_from_json_array():
    raw = '["https://example.com/a", "https://techcrunch.com/b"]'
    urls = news_search.extract_http_urls_from_text(raw, limit=10)
    assert urls == ["https://example.com/a", "https://techcrunch.com/b"]


def test_bad_search_url_rejects_listing_sections():
    assert news_search._is_bad_search_url("https://www.unian.net/techno/neiroseti") is True
    assert news_search._is_bad_search_url("https://www.content-review.com/articles/artificial_intelligence/") is True
    assert news_search._is_bad_search_url("https://vc.ru/ai/12345-some-article-title") is False


def test_hallucinated_urls_rejected():
    assert news_search.url_suspected_hallucinated(
        "https://www.wsj.com/articles/openai-launches-gpt-5-with-enhanced-reasoning-abilities-2026-"
    )
    assert news_search.url_suspected_hallucinated(
        "https://tass.ru/ekonomika/15052026/mincifry-zapuskaet-programmu"
    )
    assert news_search._is_bad_search_url("https://www.kommersant.ru/doc/5678901")
    assert not news_search.url_suspected_hallucinated(
        "https://www.cnews.ru/news/top/2026-05-06_sozdateli_yandeksa_potratyat"
    )
    assert not news_search.url_suspected_hallucinated(
        "https://tass.ru/ekonomika/15543211/rosatom-zapuskaet-proekt-po-sozdaniyu-ii-dlya-upravleniya-energosistemami"
    )
    assert news_search.url_suspected_hallucinated(
        "https://www.technologyreview.com/2026/06/05/1051212/"
        "ai-generated-artwork-raises-questions-about-authorship-and-copyright-law/"
    )
    assert news_search.url_suspected_hallucinated(
        "https://www.technologyreview.com/2026/06/05/ai-in-healthcare-2026/"
    )
    assert news_search.url_suspected_hallucinated(
        "https://www.technologyreview.com/2026/06/07/1234567890/ai-breakthroughs-2026/"
    )


def test_digest35_hallucinated_techcrunch_slug_only_rejected():
    """URL из прогона digest #35 — шаблонные slug-only пути TechCrunch."""
    fake_urls = (
        "https://techcrunch.com/2026/06/15/ai-company-announces-partnership-with-major-retailer/",
        "https://techcrunch.com/2026/06/16/ai-startup-launches-new-product-for-automating-marketing-campaigns/",
        "https://www.wired.com/2026/06/14/ai-ethics-debate-continues/",
    )
    for u in fake_urls:
        assert news_search.url_suspected_hallucinated(u), u
        assert news_search.search_url_prefilter_reason(u) == "llm_hallucinated_url", u
    real = "https://techcrunch.com/2026/06/08/following-anthropic-openai-files-confidentially-for-ipo/"
    assert not news_search.url_suspected_hallucinated(real)


def test_support_documentation_prefilter():
    from app.services.step1_candidate_policy import is_support_documentation_url

    assert is_support_documentation_url("https://browser.yandex.ru/help/en/security/virus-protection")
    assert is_support_documentation_url(
        "https://m.yandex.ru/support/yandex-360/business/purchase/en/troubleshooting/faq-plans-changes"
    )
    assert news_search.search_url_prefilter_reason(
        "https://browser.yandex.ru/help/en/security/virus-protection"
    ) == "support_documentation_page"
    assert not is_support_documentation_url("https://habr.com/ru/news/996360/")


def test_parse_search_window_dates():
    earliest, anchor = news_search.parse_search_window_dates(
        "after:2026-06-01 before:2026-06-08 нейросети "
    )
    assert earliest == date(2026, 6, 1)
    assert anchor == date(2026, 6, 8)


def test_url_path_publication_day_formats():
    assert news_search.url_path_publication_day(
        "https://techcrunch.com/2026/06/08/following-anthropic-openai-files-confidentially-for-ipo/"
    ) == date(2026, 6, 8)
    assert news_search.url_path_publication_day(
        "https://ria.ru/20260519/ii-2093333250.html"
    ) == date(2026, 5, 19)
    assert news_search.url_path_publication_day(
        "https://www.vedomosti.ru/technologies/articles/2023/05/31/977936-test"
    ) == date(2023, 5, 31)
    assert news_search.url_path_publication_day(
        "https://example.com/news/31.05.2026/story"
    ) == date(2026, 5, 31)
    assert news_search.url_path_publication_day(
        "https://example.com/archive/31/05/2026/story"
    ) == date(2026, 5, 31)
    assert news_search.url_path_publication_day(
        "https://example.com/us/06/08/2026/story"
    ) == date(2026, 8, 6)
    assert news_search.url_path_publication_day(
        "https://example.com/articles/2026-06-08-openai-ipo.html"
    ) == date(2026, 6, 8)


def test_publication_day_from_url_candidates_prefers_seed():
    seed = "https://techcrunch.com/2026/06/08/following-anthropic-openai-files-confidentially-for-ipo/"
    stored = "https://techcrunch.com/following-anthropic-openai-files-confidentially-for-ipo/"
    assert news_search.publication_day_from_url_candidates(seed, stored) == date(2026, 6, 8)


def test_search_url_path_date_outside_window():
    earliest = date(2026, 6, 1)
    anchor = date(2026, 6, 8)
    stale_ria = "https://ria.ru/20250621/meditsina-2024549851.html"
    fresh_ria = "https://ria.ru/20260602/meditsina-2096118654.html"
    future = "https://techcrunch.com/2026/06/15/ai-roundup/"
    assert news_search.search_url_path_date_outside_window(
        stale_ria, earliest=earliest, anchor=anchor
    )
    assert not news_search.search_url_path_date_outside_window(
        fresh_ria, earliest=earliest, anchor=anchor
    )
    assert news_search.search_url_path_date_outside_window(
        future, earliest=earliest, anchor=anchor
    )
    assert (
        news_search.search_url_raw_reject_reason(
            stale_ria, earliest=earliest, anchor=anchor
        )
        == "published_before_window"
    )


def test_fetch_tier_rejects_path_dates_outside_search_window(monkeypatch: pytest.MonkeyPatch):
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="test",
        aggregator_hosts=(),
        tier1_hosts=("ria.ru", "tass.ru"),
        tier2_hosts=("technologyreview.com",),
        tier3_hosts=(),
        tier4_hosts=(),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=(),
        blocked_search_hosts=(),
        search_seed_urls=(),
    )

    def _fake_fetch(settings, query, limit, *, proxy=None, search_context_size=None, include_domains=None, allowed_hosts=None):
        hosts = allowed_hosts or []
        out: list[str] = []
        if "ria.ru" in hosts:
            out.append("https://ria.ru/20250621/meditsina-2024549851.html")
            out.append("https://ria.ru/20260605/meditsina-2096118654.html")
        if "technologyreview.com" in hosts:
            out.append(
                "https://www.technologyreview.com/2026/06/05/1051212/"
                "ai-generated-artwork-raises-questions-about-authorship-and-copyright-law/"
            )
        return out

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_fetch)
    settings = SimpleNamespace(enable_web_fetch=True, proxyapi_web_search_enabled=True)
    urls = news_search.fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="after:2026-06-01 before:2026-06-08 ",
        topic_terms="ИИ нейросети",
        product_excludes="-продукт",
        fetch_limit=20,
        proxy=MagicMock(),
        policy=policy,
    )
    assert "https://ria.ru/20260605/meditsina-2096118654.html" in urls
    assert all("20250621" not in u for u in urls)
    assert all("technologyreview.com" not in u for u in urls)


def test_listing_page_urls_rejected():
    assert news_search.is_listing_page_url("https://shtruzel.ru/news") is True
    assert news_search.is_listing_page_url("https://arxiv.org/list/cs.CL/2024-03") is True
    assert news_search.is_listing_page_url("https://www.aiweekly.co/ai-news-today") is True
    assert news_search.is_listing_page_url("https://vc.ru/ai") is True
    assert news_search.is_listing_page_url("https://www.ferra.ru/life/humor/") is True
    assert news_search.is_listing_page_url("https://dtf.ru/id193446") is True
    assert news_search._is_bad_search_url("https://shtruzel.ru/news") is True
    assert news_search._is_bad_search_url("https://arxiv.org/list/cs.CL/2024-03") is True
    assert news_search.is_listing_page_url("https://www.1tv.ru/news/2026-04-26/540448") is False
    assert news_search.is_listing_page_url("https://arxiv.org/abs/2403.08295") is False
    assert news_search._is_bad_search_url("https://arxiv.org/abs/2403.08295") is True
    assert (
        news_search.is_listing_page_url(
            "https://habr.com/ru/hubs/artificial_intelligence/articles/top/yearly/page114/"
        )
        is True
    )
    assert news_search._is_bad_search_url("https://www.networkworld.com/artificial-intelligence/") is True


def test_step1_listing_seed_url_covers_section_paths():
    assert news_search.is_step1_listing_seed_url(
        "https://www.content-review.com/articles/artificial_intelligence/"
    ) is True
    assert news_search.is_step1_listing_seed_url("https://vc.ru/ai") is True
    assert news_search.is_step1_listing_seed_url(
        "https://ria.ru/product_iskusstvennyy-intellekt/"
    ) is True
    assert news_search.is_listing_page_url(
        "https://ria.ru/product_iskusstvennyy-intellekt/"
    ) is True
    assert news_search.search_url_prefilter_reason(
        "https://ria.ru/product_iskusstvennyy-intellekt/",
        tier_strict=True,
    ) == "news_listing_page"
    assert news_search.is_listing_page_url("https://ria.ru/20260519/ii-2093333250.html") is False
    assert news_search.is_step1_listing_seed_url(
        "https://www.1tv.ru/news/2026-04-26/540448"
    ) is False


def test_investing_com_noise_paths():
    assert news_search.is_search_noise_url("https://ru.investing.com/certificates") is True
    assert news_search.is_search_noise_url("https://ru.investing.com/certificates/foo-bar") is True
    assert news_search.is_search_noise_url(
        "https://www.investing.com/economic-calendar/ecb-president-lagarde-speaks-1965"
    ) is True
    assert news_search.is_search_noise_url(
        "https://ru.investing.com/news/stock-market-news/nvidia-ai-rally-1234567"
    ) is False


def test_investing_com_listing_urls():
    listings = (
        "https://ru.investing.com/analysis/stock-markets",
        "https://ru.investing.com/news/stock-market-news",
        "https://ru.investing.com/news/economy",
        "https://ru.investing.com/analysis/market-overview",
        "https://ru.investing.com/analysis/bonds",
    )
    for listing in listings:
        assert news_search.is_investing_com_listing_url(listing) is True
        assert news_search.is_step1_listing_seed_url(listing) is True
        assert news_search.is_listing_page_url(listing) is True
        assert news_search.search_url_prefilter_reason(listing, tier_strict=True) == "news_listing_page"
    assert is_policy_tier_source(
        "https://ru.investing.com/news/stock-market-news/nvidia-ai-rally-1234567"
    ) is True
    assert news_search.is_investing_com_listing_url(
        "https://ru.investing.com/analysis/ai-chips-market-outlook-1234567"
    ) is False
    assert news_search.is_investing_com_article_url(
        "https://ru.investing.com/analysis/article-200323069"
    ) is True
    assert news_search.is_investing_com_listing_url(
        "https://ru.investing.com/analysis/article-200323069"
    ) is False
    assert news_search.is_investing_com_article_url(
        "https://ru.investing.com/news/stock-market-news/nvidia-ai-rally-1234567"
    ) is True
    assert news_search.is_investing_com_url("https://ru.investing.com/analysis/bonds") is True


def test_editorial_listing_titles():
    assert news_search.is_editorial_listing_title("Юмор — все статьи и новости — Ferra.ru") is True
    assert news_search.is_editorial_listing_title("Новости , 14 июня") is True
    assert news_search.is_editorial_listing_title("Иван (id193446)") is True
    assert news_search.is_editorial_listing_title("Google Gemini удалил 30 000 строк кода") is False


def test_topic_pool_urls_rejected():
    assert news_search.is_topic_pool_page_url("https://www.cnews.ru/book/mutual/8757/251081") is True
    assert news_search.is_topic_pool_page_url("https://www.cnews.ru/book/mutual/95/6095") is True
    assert news_search._is_bad_search_url("https://www.cnews.ru/book/mutual/95/6095") is True
    assert (
        news_search._is_bad_search_url(
            "https://www.cnews.ru/news/top/2026-05-06_sozdateli_yandeksa_potratyat"
        )
        is False
    )


def test_extract_http_urls_filters_aggregators():
    raw = '["https://news.google.com/articles/abc", "https://news.tek.fm/news/306335", "https://vc.ru/ai/123"]'
    urls = news_search.extract_http_urls_from_text(raw, limit=10)
    assert urls == ["https://vc.ru/ai/123"]


def test_fetch_article_urls_raw_merges_providers(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
        serpapi_api_key="serp",
        tavily_api_key="tav",
    )
    proxy = MagicMock()
    proxy.search_news_article_urls.return_value = ["https://ria.ru/20260519/a.html"]
    monkeypatch.setattr(
        news_search,
        "_serpapi_google_news_urls",
        lambda key, query, limit: ["https://www.interfax.ru/ai/b"],
    )
    monkeypatch.setattr(
        news_search,
        "_tavily_search_urls",
        lambda key, query, limit, include_domains=None: [
            "https://ria.ru/20260519/a.html",
            "https://habr.com/c",
        ],
    )
    raw = news_search.fetch_article_urls_raw_merged(settings, "AI", limit=10, proxy=proxy)
    assert "https://ria.ru/20260519/a.html" in raw
    assert "https://www.interfax.ru/ai/b" in raw
    assert "https://habr.com/c" in raw
    assert len(raw) == 3


def test_fetch_article_urls_proxyapi_first(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
        serpapi_api_key="serp",
        tavily_api_key=None,
    )
    proxy = MagicMock()
    proxy.search_news_article_urls.return_value = ["https://example.com/real"]

    urls = news_search.fetch_article_urls_from_search(
        settings, "AI news", limit=5, proxy=proxy
    )
    assert urls == ["https://example.com/real"]
    proxy.search_news_article_urls.assert_called_once()


def test_fetch_article_urls_falls_back_to_serpapi(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
        serpapi_api_key="serp",
        tavily_api_key=None,
    )
    proxy = MagicMock()
    proxy.search_news_article_urls.return_value = []
    serp_url = "https://www.vedomosti.ru/technologies/article/2026/05/19/ii-test"
    monkeypatch.setattr(
        news_search,
        "_serpapi_google_news_urls",
        lambda key, query, limit: [serp_url],
    )

    urls = news_search.fetch_article_urls_from_search(
        settings, "AI news", limit=5, proxy=proxy
    )
    assert urls == [serp_url]


def test_search_url_prefilter_non_policy_source_when_tier_strict():
    assert news_search.search_url_prefilter_reason(
        "https://random-blog.example.com/ai-news",
        tier_strict=True,
    ) == "non_policy_source"
    assert news_search.search_url_prefilter_reason(
        "https://random-blog.example.com/ai-news",
        tier_strict=False,
    ) is None
    assert news_search.search_url_prefilter_reason(
        "https://ria.ru/20260519/ai-story.html",
        tier_strict=True,
    ) is None


def test_fetch_tier_prioritized_raw_urls_batches_by_tier(monkeypatch: pytest.MonkeyPatch):
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="test",
        aggregator_hosts=("news.google.",),
        tier1_hosts=("ria.ru", "tass.ru"),
        tier2_hosts=("techcrunch.com",),
        tier3_hosts=(),
        tier4_hosts=(),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=(),
        blocked_search_hosts=(),
        search_seed_urls=("https://ria.ru/product_iskusstvennyy-intellekt/",),
    )
    calls: list[dict] = []

    def _fake_fetch(settings, query, limit, *, proxy=None, search_context_size=None, include_domains=None, allowed_hosts=None):
        calls.append(
            {
                "query": query,
                "limit": limit,
                "include_domains": include_domains,
                "allowed_hosts": allowed_hosts,
            }
        )
        hosts = allowed_hosts or []
        out: list[str] = []
        if "ria.ru" in hosts:
            out.extend(["https://ria.ru/20260519/a.html"])
        if "tass.ru" in hosts:
            out.append("https://tass.ru/ekonomika/123")
        if "techcrunch.com" in hosts:
            out.append("https://techcrunch.com/2026/ai-story")
        return out

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_fetch)
    settings = SimpleNamespace(enable_web_fetch=True, proxyapi_web_search_enabled=True)
    urls = news_search.fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="за неделю ",
        topic_terms="ИИ нейросети",
        product_excludes="-продукт",
        fetch_limit=20,
        proxy=MagicMock(),
        policy=policy,
    )
    assert "https://ria.ru/20260519/a.html" in urls
    assert "https://tass.ru/ekonomika/123" in urls
    assert "https://techcrunch.com/2026/ai-story" in urls
    assert all("news.google." not in u for u in urls)
    assert calls
    assert all(call["allowed_hosts"] for call in calls)
    assert "site:ria.ru" in calls[0]["query"] or "site:tass.ru" in calls[0]["query"]


def test_fetch_tier_prioritized_raw_urls_enforces_host_diversity(monkeypatch: pytest.MonkeyPatch):
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="test",
        aggregator_hosts=(),
        tier1_hosts=("ria.ru", "tass.ru", "interfax.ru"),
        tier2_hosts=("vedomosti.ru", "kommersant.ru", "forbes.ru"),
        tier3_hosts=("habr.com", "vc.ru", "cnews.ru"),
        tier4_hosts=(),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=(),
        blocked_search_hosts=(),
        search_seed_urls=(),
    )

    def _fake_fetch(settings, query, limit, *, proxy=None, search_context_size=None, include_domains=None, allowed_hosts=None):
        hosts = allowed_hosts or []
        out: list[str] = []
        for h in hosts:
            out.append(f"https://{h}/news/2026/06/01/main-story")
            out.append(f"https://{h}/news/2026/06/01/secondary-story")
            out.append(f"https://{h}/news/2026/06/01/third-story")
        return out

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_fetch)
    settings = SimpleNamespace(enable_web_fetch=True, proxyapi_web_search_enabled=True)
    urls = news_search.fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="after:2026-05-25 ",
        topic_terms="ИИ нейросети",
        product_excludes="-вакансии",
        fetch_limit=24,
        proxy=MagicMock(),
        policy=policy,
    )
    hosts = {(news_search.urlparse(u).hostname or "").lower() for u in urls}
    assert len(hosts) >= 6
    assert len(urls) <= 24


def test_fetch_curious_prioritized_reaches_tech_hosts_with_small_cap(monkeypatch: pytest.MonkeyPatch):
    from app.curious_source_policy import CuriousSourcePolicy

    policy = CuriousSourcePolicy(
        curious_tier1_hosts=("popmech.ru", "dzen.ru", "reddit.com", "theverge.com"),
        curious_tier2_hosts=("habr.com", "vc.ru", "neurohive.io"),
        curious_ru_entertainment_hosts=("popmech.ru", "dzen.ru"),
        curious_ru_tech_hosts=("habr.com", "vc.ru", "neurohive.io"),
        curious_foreign_hosts=("reddit.com", "theverge.com"),
        aggregator_hosts=(),
        banned_media_hosts=(),
        blocked_search_hosts=(),
        russian_host_markers=("popmech.ru", "dzen.ru", "habr.com", "vc.ru", "neurohive.io"),
        search_seed_urls=("https://vc.ru/ai", "https://neurohive.io/"),
    )
    calls: list[list[str]] = []

    def _fake_fetch(
        settings,
        query,
        limit,
        *,
        proxy=None,
        search_context_size=None,
        include_domains=None,
        allowed_hosts=None,
        curious_search=False,
    ):
        hosts = list(allowed_hosts or [])
        calls.append(hosts)
        if "habr.com" in hosts:
            return ["https://habr.com/ru/news/2026/06/06/funny-ai"]
        if "vc.ru" in hosts:
            return ["https://vc.ru/ai/2950909-polzovateli-nedovolny-novymi-limitami-google-gemini"]
        if "popmech.ru" in hosts:
            return ["https://www.popmech.ru/science/funny-ai-art-id6541839/"]
        if "reddit.com" in hosts:
            return ["https://www.reddit.com/r/ChatGPT/comments/funny_ai_fail/"]
        return []

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_fetch)
    settings = SimpleNamespace(enable_web_fetch=True, proxyapi_web_search_enabled=True)
    urls = news_search.fetch_curious_prioritized_raw_urls(
        settings,
        window_prefix="after:2026-05-07 before:2026-06-06 ",
        topic_terms_ru="нейросеть ИИ курьёз",
        topic_terms_foreign="AI fail funny",
        product_excludes="-pricing",
        fetch_limit=5,
        proxy=MagicMock(),
        policy=policy,
    )

    assert calls
    assert any("habr.com" in hosts or "vc.ru" in hosts for hosts in calls)
    assert any("reddit.com" in hosts for hosts in calls)
    # После sort: оба T1 без даты в path; reddit slug «funny_ai_fail» выше по keyword score.
    assert urls[0] == "https://www.reddit.com/r/ChatGPT/comments/funny_ai_fail/"
    assert any("habr.com" in u or "vc.ru" in u for u in urls)
    assert any("reddit.com" in hosts for hosts in calls)


def test_search_url_prefilter_rejects_aggregator_listing_not_article():
    listing = "https://news.google.com/search?q=artificial+intelligence"
    # aggregator_source по умолчанию выключен — ленту режет news_listing_page, не blanket-агрегатор
    assert news_search.search_url_prefilter_reason(
        listing,
        is_enabled=lambda fid: fid != "aggregator_source",
        tier_strict=True,
    ) == "news_listing_page"


def test_search_url_prefilter_skips_aggregator_when_filter_disabled():
    assert news_search.search_url_prefilter_reason(
        "https://news.ycombinator.com/item?id=12345678",
        is_enabled=lambda fid: fid != "aggregator_source",
        tier_strict=True,
    ) != "aggregator_source"


def test_fetch_tier_prioritized_skips_tier34_when_pool_near_full(monkeypatch: pytest.MonkeyPatch):
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="test",
        aggregator_hosts=(),
        tier1_hosts=("ria.ru",),
        tier2_hosts=("tass.ru",),
        tier3_hosts=("habr.com",),
        tier4_hosts=("vc.ru",),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=(),
        blocked_search_hosts=(),
        search_seed_urls=(),
    )
    calls: list[list[str]] = []

    def _fake_fetch(settings, query, limit, *, proxy=None, search_context_size=None, include_domains=None, allowed_hosts=None):
        calls.append(list(allowed_hosts or []))
        return [f"https://{(allowed_hosts or ['example.com'])[0]}/a.html"]

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_fetch)
    settings = SimpleNamespace(enable_web_fetch=True, proxyapi_web_search_enabled=True)
    news_search.fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="",
        topic_terms="ИИ",
        product_excludes="",
        fetch_limit=20,
        proxy=MagicMock(),
        policy=policy,
        current_verified=10,
    )
    all_hosts = {h for batch in calls for h in batch}
    assert "habr.com" not in all_hosts
    assert "vc.ru" not in all_hosts
    assert "ria.ru" in all_hosts or "tass.ru" in all_hosts


def test_fetch_tier_prioritized_tier2_batch_includes_aggregator_hosts(monkeypatch: pytest.MonkeyPatch):
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="test",
        aggregator_hosts=("news.ycombinator.com",),
        tier1_hosts=(),
        tier2_hosts=("techcrunch.com",),
        tier3_hosts=(),
        tier4_hosts=(),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=(),
        blocked_search_hosts=(),
        search_seed_urls=(),
    )
    calls: list[list[str]] = []

    def _fake_fetch(settings, query, limit, *, proxy=None, search_context_size=None, include_domains=None, allowed_hosts=None):
        calls.append(list(allowed_hosts or []))
        return [f"https://{(allowed_hosts or ['example.com'])[0]}/2026/06/01/story.html"]

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_fetch)
    settings = SimpleNamespace(enable_web_fetch=True, proxyapi_web_search_enabled=True)
    news_search.fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="",
        topic_terms="ИИ",
        product_excludes="",
        fetch_limit=12,
        proxy=MagicMock(),
        policy=policy,
    )
    assert calls
    tier2_batches = [c for c in calls if "news.ycombinator.com" in c or "techcrunch.com" in c]
    assert tier2_batches
    assert any("news.ycombinator.com" in batch for batch in tier2_batches)

