"""Опциональные запросы к веб-поиску для якорных URL (SerpAPI / Tavily)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("app.news_search")


def fetch_article_urls_from_search(settings: "Settings", query: str, limit: int = 15) -> list[str]:
    """
    Возвращает список URL статей из выдачи поиска (без LLM).
    Порядок: SerpAPI (google_news) → Tavily search.
    """
    urls: list[str] = []
    if settings.serpapi_api_key:
        urls = _serpapi_google_news_urls(settings.serpapi_api_key, query, limit)
        if urls:
            logger.info("SerpAPI: получено URL | count=%s", len(urls))
            return urls
    if settings.tavily_api_key:
        urls = _tavily_search_urls(settings.tavily_api_key, query, limit)
        if urls:
            logger.info("Tavily: получено URL | count=%s", len(urls))
            return urls
    return []


def _serpapi_google_news_urls(api_key: str, query: str, limit: int) -> list[str]:
    try:
        r = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_news",
                "q": query,
                "api_key": api_key,
                "hl": "ru",
            },
            timeout=25,
        )
        if r.status_code >= 400:
            logger.warning("SerpAPI HTTP %s", r.status_code)
            return []
        data = r.json()
        out: list[str] = []
        for block in ("news_results", "organic_results"):
            for row in data.get(block) or []:
                link = row.get("link") if isinstance(row, dict) else None
                if isinstance(link, str) and link.startswith("http"):
                    out.append(link.split("#")[0])
                if len(out) >= limit:
                    return _uniq_urls(out)
        return _uniq_urls(out)
    except Exception:
        logger.warning("SerpAPI недоступен", exc_info=True)
        return []


def _tavily_search_urls(api_key: str, query: str, limit: int) -> list[str]:
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": min(limit, 20),
                "search_depth": "basic",
                "include_domains": [],
            },
            timeout=25,
        )
        if r.status_code >= 400:
            logger.warning("Tavily HTTP %s", r.status_code)
            return []
        data = r.json()
        out: list[str] = []
        for row in data.get("results") or []:
            if not isinstance(row, dict):
                continue
            u = row.get("url")
            if isinstance(u, str) and u.startswith("http"):
                out.append(u.split("#")[0])
        return _uniq_urls(out)
    except Exception:
        logger.warning("Tavily недоступен", exc_info=True)
        return []


def _uniq_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        key = u.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out
