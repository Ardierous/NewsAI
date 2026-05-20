"""Правила отбора кандидатов шага 1: новости vs промо инструментов/функционала."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

PRESS_RELEASE_HOST_MARKERS = (
    "businesswire.com",
    "prnewswire.com",
    "globenewswire.com",
)

PRESS_RELEASE_PATH_MARKERS = (
    "/press",
    "/press-release",
    "/pressrelease",
    "/newsroom",
    "/media-center",
    "/media/",
    "/investor/news",
)

_PRODUCT_TOOL_URL_PATH_RE = re.compile(
    r"(?:^|/)(?:product|products|tool|tools|feature|features|functionality|platform|"
    r"solutions?|pricing|price|demo|trial|signup|sign-up|register|download|"
    r"chatbot|assistant|copilot|widget|plugin|extension|integrations?|"
    r"use-cases?|usecases?|get-started|getstarted|try-now|freetrial)(?:/|$)",
    re.IGNORECASE,
)

_PRODUCT_TOOL_PROMO_RES = (
    re.compile(
        r"\b(?:попробуйте|попробовать|зарегистрируйтесь|скачайте|бесплатн(?:о|ый|ая)|"
        r"новый инструмент|новая функци[яю]|функционал|возможност(?:ь|и) (?:сервиса|платформы)|"
        r"наш(?:а|и)? (?:сервис|платформа|бот|ассистент|инструмент)|"
        r"как пользоваться|инструкция по использованию)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:try (?:our|the)|sign up|free trial|new feature|product update|"
        r"ai assistant|chatbot|copilot|tool(?:s)? for|how to use|user guide|"
        r"now available in|rolling out to users)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:launches?|introduces?|rolls? out|unveils?)\b.{0,80}\b(?:tool|feature|app|assistant|"
        r"copilot|platform|product|beta|plugin|widget)\b",
        re.IGNORECASE,
    ),
)

_NEWS_EVENT_SIGNAL_RES = (
    re.compile(
        r"\b(?:прорыв|breakthrough|открыти[ея]|исследовани[ея]|study|published|paper|"
        r"регулирован|закон|постановлен|decree|министерств|правительств|"
        r"инвестици|финансирован|raised \$|series [a-d]|партнёрств|partnership|"
        r"соглашен|договор|acquisition|слияни|merger|"
        r"внедрени[ея]|развертыван|deployment|пилотн(?:ая|ый) программ|"
        r"план(?:ы)? (?:развития|внедрения|цифровизации)|strategy|roadmap|"
        r"отчёт|report|результат|достиг|benchmark|"
        r"пресс-релиз|press release|official(?:ly)? announced|"
        r"лаборатор|university|openai|deepmind|"
        r"\d+\s*(?:млн|млрд|million|billion)|%\s+(?:рост|снижен))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:запустил(?:и)? (?:национальн|федеральн|государственн)|"
        r"объявил(?:и)? (?:о|план|программ)|announces? (?:plan|program|partnership|investment))\b",
        re.IGNORECASE,
    ),
)


def _text_blob(item: dict[str, Any], extra: str = "") -> str:
    parts = [
        str(item.get("title") or ""),
        str(item.get("description") or ""),
        str(extra or ""),
    ]
    return " ".join(parts).strip()


def is_product_tool_landing_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    try:
        path = (urlparse(u).path or "").lower().rstrip("/") or "/"
    except Exception:
        return False
    if _PRODUCT_TOOL_URL_PATH_RE.search(path):
        return True
    if re.search(r"/(?:ai-)?tools?(?:/|$)", path, re.IGNORECASE):
        return True
    return False


def has_substantive_news_event_signal(item: dict[str, Any], extra: str = "") -> bool:
    text = _text_blob(item, extra)
    if len(text) < 12:
        return False
    return any(rx.search(text) for rx in _NEWS_EVENT_SIGNAL_RES)


def looks_like_product_tool_promo(item: dict[str, Any], extra: str = "") -> bool:
    url = str(item.get("url") or "")
    if is_product_tool_landing_url(url):
        return True
    text = _text_blob(item, extra)
    if not text:
        return False
    promo_hits = sum(1 for rx in _PRODUCT_TOOL_PROMO_RES if rx.search(text))
    if promo_hits >= 2:
        return True
    if promo_hits >= 1 and not has_substantive_news_event_signal(item, extra):
        return True
    return False


def is_press_release_candidate_dict(item: dict[str, Any]) -> bool:
    """Широкая метка «похоже на пресс/официальное» — для статистики и эвристик."""
    url = str(item.get("url") or "").lower()
    source = str(item.get("source") or "").lower()
    title = str(item.get("title") or "").lower()
    desc = str(item.get("description") or "").lower()
    if any(m in source for m in PRESS_RELEASE_HOST_MARKERS):
        return True
    if any(m in url for m in PRESS_RELEASE_HOST_MARKERS):
        return True
    if any(m in url for m in PRESS_RELEASE_PATH_MARKERS):
        return True
    keywords = (
        "press release",
        "news release",
        "пресс-релиз",
        "официально объявил",
        "публично объявила",
        "официальное заявление",
    )
    return any(k in title or k in desc for k in keywords)


def is_substantive_press_for_pool(item: dict[str, Any], extra: str = "") -> bool:
    """
    Пресс/официальное для квоты пула: факты, планы, прорывы, регулирование, крупные сделки —
    не страницы инструментов и не «запуск функции».
    """
    if looks_like_product_tool_promo(item, extra):
        return False
    if not is_press_release_candidate_dict(item):
        return False
    return has_substantive_news_event_signal(item, extra)


def is_editorial_news_candidate(item: dict[str, Any], extra: str = "") -> bool:
    """Обычная новостная статья СМИ (не промо продукта)."""
    return not looks_like_product_tool_promo(item, extra)
