"""Эвристики «курьёзного» тона для digest_type=curious (забавное/неожиданное про ИИ)."""

from __future__ import annotations

import re

# Забавный/неожиданный угол (RU + EN).
_CURIOUS_POSITIVE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"курь[её]з",
        r"забавн",
        r"смешн",
        r"несураз",
        r"абсурд",
        r"нелеп",
        r"фейл",
        r"провал",
        r"глюк",
        r"\bбаг\w*",
        r"мем\w*",
        r"анекдот",
        r"рофл",
        r"угар",
        r"неожидан",
        r"удивител",
        r"странн\w*\s+(?:истори|случа|ситуаци)",
        r"галлюцин",
        r"перепутал",
        r"ошибся",
        r"не\s+смог",
        r"сломал",
        r"облажал",
        r"чат-?бот\w*.*(туп|глуп|бред|обид)",
        r"нейросет\w*.*(нарисовал|нарисовала|придумал|придумала)",
        r"ии\s+.*(ошиб|перепут|галлюцин)",
        r"funny",
        r"hilarious",
        r"bizarre",
        r"absurd",
        r"\bfail\b",
        r"glitch",
        r"\bmeme",
        r"\bwtf\b",
        r"viral",
        r"went\s+wrong",
        r"messed\s+up",
        r"epic\s+fail",
        r"заставил\w*\s+извин",
        r"пожаловал\w+",
        r"обвинил\w+",
        r"подал\w+\s+в\s+суд",
        r"character\s*ai",
        r"chatgpt.*(?:refus|weird|wrong|insist)",
        r"нейросет\w*.*(?:ошиб|перепут|написал\w*\s+не\s+то)",
        r"чат-?бот\w*.*(?:настаив|отказал|обидел|оскорб)",
        r"робот\w*.*(?:упал|сломал|испугал|обидел)",
        r"пользовател\w+.*(?:возмущ|шокир|удивил)",
        r"\bodd\b",
        r"\bweird\b",
        r"\bwild\b",
    )
)

# Деловой/официозный угол без развлекательного крючка.
_SERIOUS_BLOCKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"регулирован",
        r"законопроект",
        r"законодатель",
        r"инвестици",
        r"финансирован",
        r"партн[её]рств",
        r"пресс-релиз",
        r"квартальн",
        r"\bмлрд\b",
        r"миллиард",
        r"\bIPO\b",
        r"санкци",
        r"капитализац",
        r"отч[её]тност",
        r"официально\s+(?:заявил|объявил|сообщил)",
        r"анонсировал\s+(?:новую\s+)?верси",
        r"представил[аи]?\s+(?:новую\s+)?модел",
        r"запуск\s+(?:новой\s+)?модел",
        r"\bregulation\b",
        r"\binvestment\b",
        r"\bpartnership\b",
        r"\bearnings\b",
        r"funding\s+round",
        r"\bbillion\b",
        r"breakthrough",  # «прорыв» в EN-заголовках
        r"прорыв",
        r"внедрени[ея]\s+(?:технолог|ии)",
    )
)


def _merged_text(title: str, corpus: str, *, corpus_limit: int = 2500) -> str:
    return f"{title or ''}\n{(corpus or '')[:corpus_limit]}".strip()


def curious_tone_score(title: str, corpus: str = "") -> int:
    """0..6: выше — больше признаков курьёза, ниже — сухая деловая новость."""
    text = _merged_text(title, corpus)
    if not text:
        return 0
    pos = sum(1 for rx in _CURIOUS_POSITIVE if rx.search(text))
    neg = sum(1 for rx in _SERIOUS_BLOCKERS if rx.search(text))
    return max(0, min(6, pos * 2 - neg))


def passes_curious_tone_gate(title: str, corpus: str = "") -> bool:
    """
    Курьёзный выпуск: отсекаем сухой официоз (регулирование, инвестиции, «представила модель»).
    Пропускаем явный курьёз, score≥2, либо нейтральные «человеческие» сюжеты про ИИ (score≥1 без blockers).
    """
    text = _merged_text(title, corpus)
    if not text:
        return False
    score = curious_tone_score(title, corpus)
    has_positive = any(rx.search(text) for rx in _CURIOUS_POSITIVE)
    has_serious = any(rx.search(text) for rx in _SERIOUS_BLOCKERS)
    if has_serious and not has_positive and score < 2:
        return False
    if has_positive or score >= 2:
        return True
    if not has_serious and score >= 1:
        return True
    return False
