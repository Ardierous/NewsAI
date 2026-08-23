"""Редакционная политика выпуска: единый «Дайджест ИИ» (канонически serious)."""

from __future__ import annotations

DIGEST_TYPE_SERIOUS = "serious"
DIGEST_TYPE_CURIOUS = "curious"  # legacy: принимается в API/БД, маршрутизация → serious
DIGEST_TYPE_AI = DIGEST_TYPE_SERIOUS


def normalize_digest_type(digest_type: str | None) -> str:
    """Канонический тип для поиска, фильтров и шага 1 — всегда serious."""
    raw = str(digest_type or "").strip().lower()
    if raw == DIGEST_TYPE_CURIOUS:
        return DIGEST_TYPE_SERIOUS
    return DIGEST_TYPE_SERIOUS


def is_legacy_stored_curious(digest_type: str | None) -> bool:
    """Сырой тип в БД до объединения режимов (для тона финала старых выпусков)."""
    return str(digest_type or "").strip().lower() == DIGEST_TYPE_CURIOUS


def is_curious_digest(digest_type: str | None) -> bool:
    """Отдельный курьёзный контур отключён — единый режим «Дайджест ИИ»."""
    return False


def digest_type_display_ru(_digest_type: str | None = None) -> str:
    return "Дайджест ИИ"


# --- Веб-поиск шага 1 (дополняют tier-host seed) ---

_STEP1_TOPIC_TERMS_SERIOUS_EN = (
    "AI artificial intelligence neural networks machine learning news article "
    "regulation research breakthrough deployment partnership investment "
)

# Обязательный «развлекательный» якорь в поиске — без него выдача уходит в обычные tech-новости.
_STEP1_CURIOUS_ENTERTAINMENT_ANCHOR_RU = (
    '(курьёз OR "ИИ ляпы" OR "нейросеть ошиблась" OR "галлюцинации нейросети" OR '
    '"смешной ИИ-арт" OR "абсурд нейросети" OR "ИИ сочинил" OR '
    "забавн OR смешн OR ржач OR угар OR фейл OR провал OR глюк OR абсурд OR мем OR кринж OR "
    "вирусн OR пранк OR дипфейк OR нейровидео OR facepalm)"
)

_STEP1_CURIOUS_ENTERTAINMENT_ANCHOR_EN = (
    '("AI fails" OR "funny AI art" OR "AI hallucinations" OR "weird AI" OR "AI mistakes" OR '
    '"AI video" OR hilarious OR bizarre OR meme OR viral OR facepalm OR cringe OR prank OR roast OR '
    "deepfake OR plot OR twist OR users OR complained OR broke OR epic OR fail)"
)

_STEP1_TOPIC_TERMS_CURIOUS_RU = (
    f"{_STEP1_CURIOUS_ENTERTAINMENT_ANCHOR_RU} "
    "(нейросеть OR ИИ OR чат-бот OR ChatGPT OR Gemini) "
    "неожиданный нелепый пользователи пожаловались возмутились сломала удалила код странный кейс "
    "галлюцинация перепутал скандал на этой неделе сегодня вчера reddit "
    "заголовок на русском языке "
)

# Для зарубежных батчей curious_source_hosts — узкий EN-хвост, не деловая повестка.
_STEP1_TOPIC_TERMS_CURIOUS_EN = (
    f"{_STEP1_CURIOUS_ENTERTAINMENT_ANCHOR_EN} "
    "(AI OR chatbot OR neural OR LLM) sarcastic ironic unexpected users complained furious "
    "this week deepfake scandal "
)


def step1_curious_entertainment_anchor_ru() -> str:
    return _STEP1_CURIOUS_ENTERTAINMENT_ANCHOR_RU


def step1_curious_entertainment_anchor_en() -> str:
    return _STEP1_CURIOUS_ENTERTAINMENT_ANCHOR_EN

_STEP1_PRODUCT_EXCLUDES_COMMON = (
    "-pricing -demo -trial -signup -download -features -product -tool -chatbot -assistant "
    "-inurl:/product -inurl:/tools -inurl:/features -inurl:/pricing "
    "-vacancy -careers -webinar -podcast "
)

_STEP1_PRODUCT_EXCLUDES_SERIOUS_EXTRA = "-blog -opinion "

_STEP1_PRODUCT_EXCLUDES_CURIOUS_EXTRA = (
    "-opinion -regulation -investment -partnership -earnings -conference -summit -framework -guideline "
    "-регулирование -инвестиц -партнёрств -законопроект -прорыв -конференц -саммит -отчёт -выручк "
    "-представил -анонсировал -выпустил -релиз -новая версия -внедр -контрол "
)


def step1_topic_terms_for_digest_type(digest_type: str | None) -> str:
    """Основной tier-поиск — деловые EN-ключи; курьёзный добор — отдельными батчами."""
    return _STEP1_TOPIC_TERMS_SERIOUS_EN


def step1_curious_foreign_topic_terms() -> str:
    return _STEP1_TOPIC_TERMS_CURIOUS_EN


# Литералы для ранжирования сырых URL (slug/query) — те же якоря, что и в web_search.
_STEP1_CURIOUS_RAW_URL_LITERALS: tuple[str, ...] = (
    # RU якорь + topic_terms (латиница в slug и кириллица)
    "курьёз",
    "курьез",
    "kurioz",
    "kuryoz",
    "ляп",
    "lyap",
    "ошиб",
    "fail",
    "feil",
    "fails",
    "галлюцин",
    "hallucin",
    "смеш",
    "smesh",
    "funny",
    "забав",
    "zabav",
    "ржач",
    "rjach",
    "rofl",
    "угар",
    "ugar",
    "фейл",
    "провал",
    "глюк",
    "glitch",
    "glich",
    "абсурд",
    "absurd",
    "мем",
    "meme",
    "memes",
    "кринж",
    "cringe",
    "вирус",
    "viral",
    "пранк",
    "prank",
    "дипфейк",
    "deepfake",
    "нейровидео",
    "facepalm",
    "неожидан",
    "unexpected",
    "нелеп",
    "nelep",
    "пожаловал",
    "complain",
    "complained",
    "возмущ",
    "outrag",
    "furious",
    "сломал",
    "broke",
    "удалил",
    "deleted",
    "странн",
    "weird",
    "перепут",
    "messed",
    "скандал",
    "scandal",
    "reddit",
    "hilarious",
    "bizarre",
    "amusing",
    "ridiculous",
    "mishap",
    "blooper",
    "gaffe",
    "blunder",
    "wtf",
    "roast",
    "troll",
    "prank",
    "spoof",
    "plot-twist",
    "epic-fail",
    "epicfail",
    "side-splitting",
    "laughable",
    "comical",
    "quirky",
    "wacky",
    "odd",
    "wild",
    "backlash",
    "казус",
    "kazus",
    "конфуз",
    "konfuz",
    "умор",
    "umor",
    "хохм",
    "прикол",
    "байк",
    "анекдот",
    "сюр",
    "surreal",
    "парадокс",
    "бот-refus",
    "character-ai",
    "ai-girlfriend",
    "extra-fingers",
    "too-many-fingers",
    "robot-phone",
    "robotphone",
    "не-смог",
    "cannot-count",
    "counting-fail",
)

_STEP1_CURIOUS_RAW_URL_SERIOUS_LITERALS: tuple[str, ...] = (
    "press-release",
    "press_release",
    "представил",
    "predstavil",
    "анонс",
    "anons",
    "announced",
    "unveil",
    "changelog",
    "release-notes",
    "release_notes",
    "investment",
    "regulation",
    "earnings",
    "partnership",
    "framework",
    "guideline",
    "conference",
    "summit",
    "quarterly",
    "funding-round",
    "ipo",
    "breakthrough",
    "product-update",
    "release-notes",
    "новая-версия",
    "new-model",
    "model-update",
)


def step1_curious_raw_url_literals() -> tuple[str, ...]:
    return _STEP1_CURIOUS_RAW_URL_LITERALS


def step1_curious_raw_url_serious_literals() -> tuple[str, ...]:
    return _STEP1_CURIOUS_RAW_URL_SERIOUS_LITERALS


def curious_ru_share_bounds() -> tuple[float, float]:
    """Мин/макс доля RU-источников в rebalance курьёзного пула (legacy)."""
    return 0.55, 0.85


def step1_product_excludes_for_digest_type(digest_type: str | None) -> str:
    _ = normalize_digest_type(digest_type)
    return _STEP1_PRODUCT_EXCLUDES_COMMON + _STEP1_PRODUCT_EXCLUDES_SERIOUS_EXTRA


def step1_research_editorial_block(digest_type: str | None) -> str:
    _ = normalize_digest_type(digest_type)
    return (
        "РЕЖИМ ВЫПУСКА: единый дайджест ИИ (деловой + практичный + развлекательный). "
        "Приоритет — значимые новости ИИ: регулирование, исследования, внедрения, инвестиции, партнёрства, официальные заявления. "
        "Допустимы качественные практические обзоры инструментов и умеренно курьёзные/вирусные истории про ИИ без потери новостной ценности. "
        "Корпоративные пресс-релизы — 20-35% пула (2-3 из 10), только как новости (факты, планы), не промо инструментов. "
        "Доля российских источников — 30-50%. Из одного источника — не более 2 новостей. "
    )


def step1_scoring_editorial_block(digest_type: str | None) -> str:
    _ = normalize_digest_type(digest_type)
    return (
        "digest_type=ai: повышай баллы значимости, влияния и новизны для деловых новостей ИИ; "
        "умеренно повышай практичные обзоры инструментов и «человечные» курьёзные/вирусные сюжеты про ИИ; "
        "снижай баллы промо лендингов, чистых мемов без новостной ценности и сухого официоза без угла для читателя."
    )


def step2_order_system_prompt(digest_type: str | None) -> str:
    base = (
        "Ты выпускающий редактор дайджеста ExTellect про искусственный интеллект. "
        "Твоя задача — расставить ровно 5 уже отобранных новостей в порядке output_position от 1 до 5 "
        "так, чтобы максимизировать интерес читателя к выпуску: сильный заход в позиции 1, "
        "логичный ритм в середине, запоминающийся финал в позиции 5. "
        "Учитывай заголовки, описания, баллы total_score и tier источника. "
        "Нельзя добавлять или удалять candidate_id — только переставить. "
        "Ответ — только JSON-объект без markdown: "
        "overall_rationale (3–5 простых предложений на русском — почему именно этот порядок лучше удержит читателя выпуска), "
        "items — массив из 5 объектов с полями candidate_id, output_position, ordering_reason "
        "(ordering_reason: 1–2 предложения, почему новость на этой позиции)."
    )
    _ = normalize_digest_type(digest_type)
    return (
        base
        + " Выпуск объединяет деловые, практичные и умеренно развлекательные AI-новости: "
        "сильная важная или практичная новость в начале, разнообразный ритм в середине, "
        "запоминающийся финал — без перекоса только в мемы или только в официоз."
    )
