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
        r"сломал[аи]?",
        r"сломалась",
        r"удалил[аи]?",
        r"перестал\w*\s+работ",
        r"мем\w*",
        r"анекдот",
        r"рофл",
        r"угар",
        r"скандал",
        r"возмутил",
        r"возмущ",
        r"пожаловал",
        r"неожидан",
        r"удивител",
        r"шокир",
        r"сюрприз",
        r"странн\w*\s+(?:истори|случа|ситуаци)",
        r"странн\w+",
        r"дикий",
        r"безумн",
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
        r"пользовател\w+.*(?:недовол|жалу|пожаловал)",
        r"удалил\w*\s+\d+[\s\xa0]*(?:тысяч|тыс\.?)\s+строк",
        r"сайт\w*.*сломал",
        r"код\w*.*сломал",
        r"реклам\w+.*ии[-\s]?режим",
        r"\bodd\b",
        r"\bweird\b",
        r"\bwild\b",
        r"\bcomplain",
        r"\bbacklash\b",
        r"\bdeleted\b.*\blines\b",
        r"\bbroke\b.*\bsite\b",
        r"кринж",
        r"ржач",
        r"угарн",
        r"истерик",
        r"пранк",
        r"тролл",
        r"обманул",
        r"напугал",
        r"испугал",
        r"попал\w*\s+в\s+просак",
        r"не\s+так\s+понял",
        r"перепутал\w*\s+.*(?:кот|собак|борщ|рецепт)",
        r"нейросет\w*.*(?:нарисовал|сгенерировал).*(?:кот|собак|президент|знаменит)",
        r"чат-?бот\w*.*(?:влюбил|флирт|романт|свидан)",
        r"ии[-\s]?подруга",
        r"ai\s+girlfriend",
        r"deepfake",
        r"дипфейк",
        r"подделк\w*\s+голос",
        r"суд\w*.*(?:чат-?бот|нейросет|character\s*ai)",
        r"\broast\b",
        r"\bcringe\b",
        r"\bprank\b",
        r"\btrolled\b",
        r"plot\s+twist",
        r"users\s+are\s+(?:furious|outraged|complaining)",
        r"нейровидео",
        r"ai\s+video",
        r"generated\s+video",
        r"сгенерировал\w*\s+видео",
        r"видео\s+.*(?:нейросет|chatgpt|ии)",
        r"попросил\w*.*(?:chatgpt|нейросет|gemini|claude|ии|бот)",
        r"asked\s+(?:chatgpt|gemini|claude|ai)",
        r"too\s+many\s+fingers",
        r"extra\s+fingers",
        r"лишн\w+\s+(?:пал|рук|ног)",
        r"reddit",
        r"/r/\w+",
        r"facepalm",
        r"bot\s+refus",
    )
)

# Слабый, но узнаваемый «человеческий» крючок — в пул с curious_tone_low, не в топ.
_CURIOUS_HUMAN_INTEREST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"пользовател\w+.*(?:рассказ|поделил|выложил|заметил|увидел)",
        r"users?\s+(?:share|shared|post|posted|notice|noticed)",
        r"experiment",
        r"эксперимент",
        r"prompt",
        r"промпт",
        r"нарисовал\w*",
        r"сгенерировал\w*",
        r"generated",
        r"hallucinat",
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
        r"обновил[аи]?\s+(?:модел|верси)",
        r"выпустил[аи]?\s+(?:модел|верси)",
        r"релиз\w*\s+(?:модел|верси)",
        r"новая\s+верси[яи]",
        r"новый\s+релиз",
        r"конференци",
        r"саммит",
        r"форум\b",
        r"стратеги\w+\s+развит",
        r"дорожн\w+\s+карт",
        r"грант\w*\s+(?:на|для)",
        r"субсиди",
        r"госпрограмм",
        r"министр\w*\s+.*(?:ии|искусственн)",
        r"\bCEO\b",
        r"\bCTO\b",
        r"квартальн\w*\s+отч",
        r"выручк\w+",
        r"прибыл",
        r"\bunveil",
        r"\bannounc",
        r"\blaunch(?:ed|es)?\b",
        r"\bnew\s+model\b",
        r"\bmodel\s+update\b",
        r"\bpolicy\b",
        r"\bsummit\b",
        r"\bconference\b",
        r"\bframework\b",
        r"\bguideline",
        r"как\s+работает",
        r"обзор\s",
        r"разбор\s",
        r"анализ\s",
        r"новые\s+возможност",
        r"перспектив\w+",
        r"влияни[ея]\s+.*(?:ии|искусственн)",
        r"тренд\w*\s+.*(?:ии|искусственн)",
        r"\bfeatures\b",
        r"\boverview\b",
        r"\bexplainer\b",
        r"\bdeep\s+dive\b",
        r"улучшен\w+\s+(?:модел|верси)",
    )
)


def _merged_text(title: str, corpus: str, *, corpus_limit: int = 2500) -> str:
    return f"{title or ''}\n{(corpus or '')[:corpus_limit]}".strip()


def has_curious_positive_signal(title: str, corpus: str = "") -> bool:
    text = _merged_text(title, corpus)
    return bool(text) and any(rx.search(text) for rx in _CURIOUS_POSITIVE)


def has_curious_serious_blockers(title: str, corpus: str = "") -> bool:
    text = _merged_text(title, corpus)
    return bool(text) and any(rx.search(text) for rx in _SERIOUS_BLOCKERS)


def has_curious_human_interest(title: str, corpus: str = "") -> bool:
    text = _merged_text(title, corpus)
    return bool(text) and any(rx.search(text) for rx in _CURIOUS_HUMAN_INTEREST)


def curious_tone_score(title: str, corpus: str = "") -> int:
    """0..8: выше — больше признаков курьёза, ниже — сухая деловая новость."""
    text = _merged_text(title, corpus)
    if not text:
        return 0
    pos = sum(1 for rx in _CURIOUS_POSITIVE if rx.search(text))
    neg = sum(1 for rx in _SERIOUS_BLOCKERS if rx.search(text))
    return max(0, min(8, pos * 2 - neg * 2))


def is_dry_serious_curious_news(title: str, corpus: str = "") -> bool:
    """Сухой официоз без развлекательного крючка — не для курьёзного пула."""
    text = _merged_text(title, corpus)
    if not text:
        return True
    score = curious_tone_score(title, corpus)
    has_positive = has_curious_positive_signal(title, corpus)
    has_serious = has_curious_serious_blockers(title, corpus)
    if has_curious_human_interest(title, corpus) and not has_serious:
        return False
    if has_serious and not has_positive:
        return True
    if not has_positive and score <= 2:
        return True
    return False


def curious_total_score_from_tone(tone: int, *, low: bool = False) -> int:
    """Балл кандидата в курьёзном пуле: развлекательные сюжеты выше, сухие — ниже."""
    base = max(1, min(9, 1 + int(tone or 0) * 2))
    if low:
        return min(base, 3)
    return base


def passes_curious_tone_gate(title: str, corpus: str = "") -> bool:
    """
    Курьёзный выпуск: пропуск только с явным развлекательным/человеческим крючком.
    Нейтральные tech-новости без фейла/юмора/скандала — отсекаются.
    """
    if is_dry_serious_curious_news(title, corpus):
        return False
    text = _merged_text(title, corpus)
    if not text:
        return False
    score = curious_tone_score(title, corpus)
    has_positive = has_curious_positive_signal(title, corpus)
    has_serious = has_curious_serious_blockers(title, corpus)
    if has_serious and not has_positive:
        return False
    if has_positive and score >= 2:
        return True
    if has_positive and score >= 1 and not has_serious:
        return True
    return False


def passes_curious_pool_gate(title: str, corpus: str = "") -> bool:
    """
    Допуск в пул шага 1: отсекаем только сухой официоз.
    Спорные, но с курьёзным крючком — в пул с curious_tone_low (не в топ).
    """
    if is_dry_serious_curious_news(title, corpus):
        return False
    if passes_curious_tone_gate(title, corpus):
        return True
    has_positive = has_curious_positive_signal(title, corpus)
    has_serious = has_curious_serious_blockers(title, corpus)
    if has_positive and not has_serious:
        return True
    if has_curious_human_interest(title, corpus) and not has_serious:
        return True
    return False
