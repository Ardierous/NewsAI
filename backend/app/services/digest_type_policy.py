"""Редакционная политика типов выпуска: serious (деловой) vs curious (курьёзный)."""

from __future__ import annotations

DIGEST_TYPE_SERIOUS = "serious"
DIGEST_TYPE_CURIOUS = "curious"


def normalize_digest_type(digest_type: str | None) -> str:
    if digest_type == DIGEST_TYPE_CURIOUS:
        return DIGEST_TYPE_CURIOUS
    return DIGEST_TYPE_SERIOUS


def is_curious_digest(digest_type: str | None) -> bool:
    return normalize_digest_type(digest_type) == DIGEST_TYPE_CURIOUS


# --- Веб-поиск шага 1 (дополняют tier-host seed) ---

_STEP1_TOPIC_TERMS_SERIOUS_EN = (
    "AI artificial intelligence neural networks machine learning news article "
    "regulation research breakthrough deployment partnership investment "
)

_STEP1_TOPIC_TERMS_CURIOUS_RU = (
    "нейросеть ИИ чат-бот курьёз забавный смешной неожиданный нелепый абсурдный "
    "фейл провал глюк ошибка мем анекдот история галлюцинация перепутал "
    "заголовок на русском языке "
)

# Для зарубежных батчей curious_source_hosts — узкий EN-хвост, не деловая повестка.
_STEP1_TOPIC_TERMS_CURIOUS_EN = (
    "AI fail funny bizarre unexpected meme chatbot "
)

_STEP1_PRODUCT_EXCLUDES_COMMON = (
    "-pricing -demo -trial -signup -download -features -product -tool -chatbot -assistant "
    "-inurl:/product -inurl:/tools -inurl:/features -inurl:/pricing "
    "-vacancy -careers -webinar -podcast "
)

_STEP1_PRODUCT_EXCLUDES_SERIOUS_EXTRA = "-blog -opinion "

_STEP1_PRODUCT_EXCLUDES_CURIOUS_EXTRA = (
    "-opinion -regulation -investment -partnership -earnings "
    "-регулирование -инвестиц -партнёрств -законопроект -прорыв "
)


def step1_topic_terms_for_digest_type(digest_type: str | None) -> str:
    if is_curious_digest(digest_type):
        return _STEP1_TOPIC_TERMS_CURIOUS_RU
    return _STEP1_TOPIC_TERMS_SERIOUS_EN


def step1_curious_foreign_topic_terms() -> str:
    return _STEP1_TOPIC_TERMS_CURIOUS_EN


def curious_ru_share_bounds() -> tuple[float, float]:
    """Мин/макс доля RU-источников в rebalance курьёзного пула."""
    return 0.55, 0.85


def step1_product_excludes_for_digest_type(digest_type: str | None) -> str:
    extra = (
        _STEP1_PRODUCT_EXCLUDES_CURIOUS_EXTRA
        if is_curious_digest(digest_type)
        else _STEP1_PRODUCT_EXCLUDES_SERIOUS_EXTRA
    )
    return _STEP1_PRODUCT_EXCLUDES_COMMON + extra


def step1_research_editorial_block(digest_type: str | None) -> str:
    if is_curious_digest(digest_type):
        return (
            "РЕЖИМ ВЫПУСКА: digest_type=curious (курьёзный дайджест на выходные). "
            "Подбирай только забавные, смешные, удивительные или неожиданные истории про ИИ/нейросети/чат-ботов/роботов: "
            "курьёзы, фейлы, глюки, абсурдные кейсы, вирусные мемы, странные эксперименты, нелепые заявления — "
            "то, что развлекает без напряжения. "
            "ИСКЛЮЧАЙ сухую регуляторику, отчёты инвесторов, корпоративные пресс-релизы, «прорывы» и официоз — "
            "их не должно быть в пуле (0% пресс-релизов). "
            "При равной пригодности отдавай предпочтение более «человечным» и эмоциональным заголовкам, а не Tier СМИ. "
            "Доля российских источников — 30-50%. Из одного источника — не более 2 новостей. "
        )
    return (
        "РЕЖИМ ВЫПУСКА: digest_type=serious (деловой дайджест). "
        "Приоритет — значимые новости ИИ: регулирование, исследования, внедрения, инвестиции, партнёрства, официальные заявления. "
        "Корпоративные пресс-релизы и официальные материалы — 20-35% пула (2-3 из 10), только как новости (факты, планы), не промо инструментов. "
        "Доля российских источников — 30-50%. Из одного источника — не более 2 новостей. "
    )


def step1_scoring_editorial_block(digest_type: str | None) -> str:
    if is_curious_digest(digest_type):
        return (
            "digest_type=curious: повышай total_score забавным, неожиданным, вирусным и «человечным» материалам про ИИ; "
            "снижай баллы сухим пресс-релизам, регуляторике и корпоративному официозу. "
            "В пуле не должно остаться «серьёзных» новостей без курьёзного/удивительного угла."
        )
    return (
        "digest_type=serious: повышай баллы значимости, влияния и новизны для деловых новостей ИИ; "
        "снижай баллы промо инструментов и чисто развлекательных мемов без новостной ценности."
    )


def step2_order_system_prompt(digest_type: str | None) -> str:
    base = (
        "Ты выпускающий редактор дайджеста ExTellect про искусственный интеллект. "
        "Твоя задача — расставить ровно 5 уже отобранных новостей в порядке output_position от 1 до 5 "
        "так, чтобы максимизировать интерес читателя к выпуску: сильный заход в позиции 1, "
        "логичный ритм в середине, запоминающийся финал в позиции 5. "
        "Учитывай заголовки, описания, баллы total_score и tier источника. "
        "Нельзя добавлять или удалять candidate_id — только переставить. "
        "Ответ — только JSON-массив из 5 объектов без markdown."
    )
    if is_curious_digest(digest_type):
        return (
            base
            + " Выпуск курьёзный (выходной): ставь в начало самую смешную/удивительную новость, "
            "чередуй лёгкий тон, избегай «тяжёлого» финала — лучше яркий позитивный или ироничный аккорд."
        )
    return base + " Выпуск деловой: сильная важная новость в начале, сбалансированный ритм без развлекательного перекоса."
