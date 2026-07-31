"""Тематика выпуска: AI (по умолчанию) vs Style (мода и стиль)."""

from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path

DIGEST_TOPIC_AI = "ai"
DIGEST_TOPIC_STYLE = "style"

_active_digest_topic: ContextVar[str] = ContextVar("digest_topic", default=DIGEST_TOPIC_AI)
_active_source_tiers_path: ContextVar[Path | None] = ContextVar("source_tiers_path", default=None)


def normalize_digest_topic(digest_topic: str | None) -> str:
    if str(digest_topic or "").strip().lower() == DIGEST_TOPIC_STYLE:
        return DIGEST_TOPIC_STYLE
    return DIGEST_TOPIC_AI


def is_style_digest(digest_topic: str | None) -> bool:
    return normalize_digest_topic(digest_topic) == DIGEST_TOPIC_STYLE


def is_ai_digest(digest_topic: str | None) -> bool:
    return not is_style_digest(digest_topic)


def set_active_digest_topic(digest_topic: str | None) -> None:
    _active_digest_topic.set(normalize_digest_topic(digest_topic))


def get_active_digest_topic() -> str:
    return _active_digest_topic.get()


def set_active_source_tiers_path(path: Path | None) -> None:
    _active_source_tiers_path.set(path)


def resolve_source_tiers_path(digest_topic: str | None = None) -> Path:
    override = _active_source_tiers_path.get()
    if override is not None:
        return override
    from app.config import get_settings

    settings = get_settings()
    if is_style_digest(digest_topic or get_active_digest_topic()):
        return settings.style_source_tiers_path
    return settings.source_tiers_path


def get_tiers_policy_for_topic(digest_topic: str | None = None):
    from app.source_tiers_policy import get_source_tiers_policy

    return get_source_tiers_policy(resolve_source_tiers_path(digest_topic))


# --- Поиск шага 1: ключевые фразы и исключения ---

_STEP1_TOPIC_TERMS_STYLE_RU = (
    "мода fashion стиль одежда неделя моды fashion week показ мод дефиле "
    "фэшн-индустрия fashion industry коллекция одежды lookbook лукбук "
    "стрит-стайл street style haute couture высокая мода "
    "устойчивая мода sustainable fashion eco-fashion "
    "тренды сезона антитренды модный образ стильный образ "
    "коллаборация брендов капсульный гардероб дроп коллекции "
    "дизайнер одежды luxury люкс "
)

_STEP1_PRODUCT_EXCLUDES_STYLE = (
    "-pricing -demo -trial -signup -download -features -product -tool "
    "-inurl:/product -inurl:/tools -inurl:/pricing "
    "-vacancy -careers -webinar -podcast "
    "-blog -opinion "
    "-\"стиль кода\" -\"стиль управления\" -\"бизнес-модель\" -\"business model\" "
    "-нейросет -chatgpt -искусственный -machine\\ learning "
)


def step1_topic_terms_for_topic(digest_topic: str | None) -> str:
    if is_style_digest(digest_topic):
        return _STEP1_TOPIC_TERMS_STYLE_RU
    from app.services.digest_type_policy import step1_topic_terms_for_digest_type

    return step1_topic_terms_for_digest_type("serious")


def step1_product_excludes_for_topic(digest_topic: str | None, digest_type: str | None = None) -> str:
    if is_style_digest(digest_topic):
        return _STEP1_PRODUCT_EXCLUDES_STYLE
    from app.services.digest_type_policy import step1_product_excludes_for_digest_type

    return step1_product_excludes_for_digest_type(digest_type)


def step1_research_editorial_block_for_topic(digest_topic: str | None, digest_type: str | None = None) -> str:
    if is_style_digest(digest_topic):
        return (
            "РЕЖИМ ВЫПУСКА: digest_topic=style (дайджест моды и стиля). "
            "Подбирай новости о моде, одежде, стиле, fashion-индустрии, показах, коллекциях, "
            "стрит-стайле, люксе, beauty в контексте моды. "
            "ИСКЛЮЧАЙ IT/ИИ, бизнес-модели ПО, «стиль кода/управления», спорт без модного контекста. "
            "Доля российских источников — 30-50%. Из одного источника — не более 2 новостей. "
        )
    from app.services.digest_type_policy import step1_research_editorial_block

    return step1_research_editorial_block(digest_type)


def step1_scoring_editorial_block_for_topic(digest_topic: str | None, digest_type: str | None = None) -> str:
    if is_style_digest(digest_topic):
        return (
            "digest_topic=style: повышай total_score новостям о моде, коллекциях, показах, "
            "трендах сезона, fashion-бизнесе, стрит-стайле, коллаборациях брендов; "
            "снижай баллы материалам про IT, ИИ, программирование и «стиль» в переносном смысле."
        )
    from app.services.digest_type_policy import step1_scoring_editorial_block

    return step1_scoring_editorial_block(digest_type)


def step2_order_system_prompt_for_topic(digest_topic: str | None, digest_type: str | None = None) -> str:
    if is_style_digest(digest_topic):
        return (
            "Ты выпускающий редактор дайджеста ExTellect про моду и стиль. "
            "Расставь ровно 5 отобранных новостей в порядке output_position от 1 до 5 "
            "для максимального интереса читателя: сильный модный заход в позиции 1, "
            "логичный ритм в середине, запоминающийся финал в позиции 5. "
            "Ответ — только JSON-объект без markdown: overall_rationale, items "
            "(candidate_id, output_position, ordering_reason)."
        )
    from app.services.digest_type_policy import step2_order_system_prompt

    return step2_order_system_prompt(digest_type)


# --- Тематический матчинг Style (15 ключевых фраз + защита от омонимов) ---

_STYLE_STRONG_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"недел[ьяи]\s+мод",
        r"fashion\s+week",
        r"показ\s+мод",
        r"дефиле",
        r"фэшн[-\s]?индустр",
        r"fashion\s+industry",
        r"fashion\s+market",
        r"дизайнер\s+одежд",
        r"нов(ая|ой|ую|ые)\s+коллекци",
        r"круизн(ая|ой|ую)\s+коллекци",
        r"pre[-\s]?collection",
        r"лукбук",
        r"lookbook",
        r"капсульн(ый|ого|ая|ое)\s+гардероб",
        r"коллабораци\w*\s+бренд",
        r"brand\s+collaboration",
        r"тренд\w*\s+сезон",
        r"антитренд",
        r"стрит[-\s]?стайл",
        r"street\s+style",
        r"модн(ый|ого|ая|ое)\s+образ",
        r"стильн(ый|ого|ая|ое)\s+образ",
        r"высок(ая|ой|ую)\s+мод",
        r"haute\s+couture",
        r"устойчив(ая|ой|ую)\s+мод",
        r"sustainable\s+fashion",
        r"eco[-\s]?fashion",
        r"дроп\s+коллекци",
        r"limited\s+drop",
        r"\bfashion\b",
        r"\bмод[аыуе]\b",
        r"одежд",
        r"гардероб",
        r"коллекци\w*\s+одежд",
    )
)

_STYLE_HOMONYM_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"стиль\s+кода",
        r"стиль\s+управлен",
        r"стиль\s+жизни\s+предприним",
        r"бизнес[-\s]?модел",
        r"business\s+model",
        r"модел\w*\s+(gpt|llm|нейросет|machine\s+learning|ml\b|ai\b|искусствен)",
        r"искусственн\w+\s+интеллект",
        r"\bchatgpt\b",
        r"\bнейросет",
        r"machine\s+learning",
        r"\bapi\b.*\b(релиз|release|update)\b",
    )
)


def _normalize_topic_corpus(corpus: str, extra: str = "") -> str:
    merged = f"{corpus or ''} {extra or ''}"
    merged = re.sub(r"\s+", " ", merged).strip()
    return merged


def style_digest_topic_matches(corpus: str, extra: str = "") -> bool:
    """True, если материал про моду/стиль, а не омоним «стиль» в IT/бизнесе."""
    extra_s = _normalize_topic_corpus("", extra)
    if extra_s and any(rx.search(extra_s) for rx in _STYLE_STRONG_RES):
        if not any(rx.search(extra_s) for rx in _STYLE_HOMONYM_RES):
            return True
    merged = _normalize_topic_corpus(corpus, extra)
    if len(merged) < 12:
        return False
    if any(rx.search(merged) for rx in _STYLE_HOMONYM_RES):
        if not any(rx.search(merged) for rx in _STYLE_STRONG_RES):
            return False
    return any(rx.search(merged) for rx in _STYLE_STRONG_RES)
