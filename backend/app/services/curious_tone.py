"""Эвристики «курьёзного» тона для digest_type=curious (забавное/неожиданное про ИИ)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

from app.services.digest_type_policy import (
    step1_curious_raw_url_literals,
    step1_curious_raw_url_serious_literals,
)

# Забавный/неожиданный угол (RU + EN).
# Расширенные топ-маркеры курьёзной подборки (RU/EN) + контекстные фразы ниже.
_CURIOUS_POSITIVE_RU_MARKERS: tuple[str, ...] = (
    r"курь[её]з",
    r"казус",
    r"конфуз",
    r"нелеп\w*",
    r"\bляп\w*",
    r"прокол",
    r"фейл",
    r"облом",
    r"осечк",
    r"промах",
    r"умор",
    r"хохм",
    r"прикл",
    r"ржак",
    r"ст[её]б",
    r"треш",
    r"угар",
    r"байк",
    r"анекдот",
    r"небылиц",
    r"абсурд",
    r"\bсюр\b",
    r"сюрреал",
    r"парадокс",
    r"комичн",
    r"уморительн",
    r"забавн",
    r"потешн",
    r"смешн",
    r"эпичн",
)

_CURIOUS_POSITIVE_EN_MARKERS: tuple[str, ...] = (
    r"hilarious",
    r"funny",
    r"amusing",
    r"comical",
    r"laughable",
    r"side[- ]splitting",
    r"\bjoke\b",
    r"absurd",
    r"ridiculous",
    r"bizarre",
    r"quirky",
    r"wacky",
    r"\bfail\b",
    r"epic\s+fail",
    r"blooper",
    r"gaffe",
    r"blunder",
    r"mishap",
    r"\bmeme\b",
    r"spoof",
)

_CURIOUS_POSITIVE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        *_CURIOUS_POSITIVE_RU_MARKERS,
        *_CURIOUS_POSITIVE_EN_MARKERS,
        r"несураз",
        r"несураз",
        r"абсурд",
        r"провал",
        r"глюк",
        r"\bбаг\w*",
        r"сломал[аи]?",
        r"сломалась",
        r"удалил[аи]?",
        r"перестал\w*\s+работ",
        r"мем\w*",
        r"рофл",
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
        r"glitch",
        r"\bwtf\b",
        r"viral",
        r"went\s+wrong",
        r"messed\s+up",
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
        r"робот\w*.*(?:телефон|phone)",
        r"показал\w+.*(?:музе|выставк|multimedia)",
        r"необычн\w+\s+(?:прототип|гаджет|устройств|robot)",
        r"не\s+мож\w*\s+посчит\w*",
        r"cannot\s+count",
        r"counting\s+fail",
        r"отказал\w*\s+от\s+.*(?:ии|ai|нейросет|инструмент|агент)",
        r"не\s+помог\w*\s+(?:решить|справиться|устранить)",
        r"обогнал\w*\s+людей",
        r"бот\w*\s+.*обогнал\w*\s+людей",
        r"что\s+ид[ёе]т\s+не\s+так",
        r"refused\s+to\s+use\s+.*\bai\b",
        r"couldn['']t\s+help",
        r"bots?\s+.*(?:outpace|overtake|surpass).*(?:human|people)",
    )
)

# Подсказки из slug URL (theregister …/funny_…/, ferra …robot-phone…).
_CURIOUS_URL_SLUG_HINTS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"funny",
        r"hilarious",
        r"amusing",
        r"mishap",
        r"blooper",
        r"gaffe",
        r"fail",
        r"cringe",
        r"absurd",
        r"ridiculous",
        r"wtf",
        r"facepalm",
        r"prank",
        r"weird",
        r"robot[-_]?phone",
        r"robotic",
        r"meme",
        r"rofl",
        r"umor",
        r"smeshn",
        r"kurioz",
        r"kazus",
    )
)

# Слабый, но узнаваемый «человеческий» крючок — только реакции людей, не «промпт» в changelog продукта.
_CURIOUS_HUMAN_INTEREST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"пользовател\w+.*(?:рассказ|поделил|выложил|заметил|увидел|жалу|возмущ|недовол|шокир)",
        r"users?\s+(?:share|shared|post|posted|notice|noticed|complain|outraged|furious)",
        r"подал\w+\s+в\s+суд",
        r"sued\s+(?:chatgpt|character\s*ai|openai|google|meta)",
        r"character\s*ai",
        r"users\s+are\s+(?:furious|outraged|complaining)",
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
        r"представил[аи]?\s+(?:нов\w+\s+)?(?:ии|сервис|продукт|инструмент|платформ)",
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
        r"начали\s+(?:использовать|внедр|применять)\s+(?:ии|искусственн)",
        r"контрол\w+\s+(?:качеств|детал|лопаток|продукц)",
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
        r"модел\w*\s+по\s+умолчан",
        r"default\s+model",
        r"новост\w*\s+(?:midjourney|openai|google|anthropic|gemini|chatgpt|claude|sora|runway|stable\s*diffusion)",
        r"новост\w*\s+\S+\s+от\s+\d{1,2}\.\d{1,2}\.\d{2,4}",
        r"редизайн\s+(?:сайта|интерфейс|панел)",
        r"масштабн\w+\s+редизайн",
        r"панел\w*\s+(?:ввода\s+)?промпт",
        r"style\s+creator",
        r"release\s+notes",
        r"changelog",
        r"roadmap",
        r"локализац",
        r"crowdin",
        r"user\s+research",
        r"исследован\w+\s+пользовател",
        r"верси[яи]\s+v?\d+\.\d+",
        r"becomes\s+the\s+default\s+model",
        r"product\s+update",
        r"обновлен\w+\s+панел",
    )
)

# Для скоринга тона — не весь sidebar/комментарии страницы.
_CURIOUS_TONE_CORPUS_LIMIT = 900


def _merged_text(title: str, corpus: str, *, corpus_limit: int = _CURIOUS_TONE_CORPUS_LIMIT) -> str:
    return f"{title or ''}\n{(corpus or '')[:corpus_limit]}".strip()


def _title_text(title: str) -> str:
    return (title or "").strip()


def curious_url_match_text(url: str) -> str:
    """Нормализованный path/query URL для поиска курьёзных маркеров (как в web_search якоре)."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return ""
    try:
        parsed = urlparse(u)
        parts = [unquote(parsed.path or ""), unquote(parsed.query or "")]
    except Exception:
        return u.replace("-", " ").replace("_", " ")
    blob = " ".join(part for part in parts if part)
    return blob.replace("-", " ").replace("_", " ").replace(".", " ").replace("%20", " ")


def curious_url_positive_hints(url: str) -> str:
    """Ключевые слова из path/slug URL для tone gate, если заголовок страницы слабый."""
    blob = curious_url_match_text(url)
    if not blob.strip():
        return ""
    hits = [rx.pattern for rx in _CURIOUS_URL_SLUG_HINTS if rx.search(blob)]
    low = blob.lower()
    for lit in step1_curious_raw_url_literals():
        if lit in low:
            hits.append(lit)
    return " ".join(dict.fromkeys(hits))


def curious_raw_url_keyword_score(url: str) -> int:
    """Скор «курьёзности» сырого URL: те же маркеры, что в поиске и tone gate."""
    blob = curious_url_match_text(url)
    if not blob.strip():
        return 0
    score = 0
    if has_curious_positive_signal("", blob):
        score += 4
    low = blob.lower()
    for lit in step1_curious_raw_url_literals():
        if lit in low:
            score += 1
    for rx in _CURIOUS_URL_SLUG_HINTS:
        if rx.search(blob):
            score += 2
    return score


def curious_raw_url_serious_slug_penalty(url: str) -> int:
    """Штраф за slug пресс-релиза/официоза — такие URL проверяем позже."""
    low = curious_url_match_text(url).lower()
    if not low:
        return 0
    if any(lit in low for lit in step1_curious_raw_url_serious_literals()):
        return 2
    if has_curious_serious_blockers("", low):
        return 1
    return 0


def curious_raw_url_rank_key(
    url: str,
    *,
    fresh_rank: int,
    source_rank: int,
    day_ordinal: int | None = None,
) -> tuple[int, int, int, int, int]:
    """Кортеж для sort: меньше — выше приоритет HTTP-проверки."""
    kw = curious_raw_url_keyword_score(url)
    serious = curious_raw_url_serious_slug_penalty(url)
    day_pri = -(day_ordinal or 0)
    # Сначала отсекаем URL с датой в path вне окна, затем курьёзность slug, затем свежесть.
    return (fresh_rank, -kw, day_pri, serious, source_rank)


def _gate_input(title: str, corpus: str, url: str = "") -> tuple[str, str]:
    hints = curious_url_positive_hints(url)
    if hints:
        corpus = f"{corpus}\n{hints}".strip()
    return title, corpus


def has_curious_positive_signal(title: str, corpus: str = "") -> bool:
    text = _merged_text(title, corpus)
    return bool(text) and any(rx.search(text) for rx in _CURIOUS_POSITIVE)


def has_curious_serious_blockers(title: str, corpus: str = "") -> bool:
    text = _merged_text(title, corpus)
    return bool(text) and any(rx.search(text) for rx in _SERIOUS_BLOCKERS)


def has_curious_serious_blockers_in_title(title: str) -> bool:
    text = _title_text(title)
    return bool(text) and any(rx.search(text) for rx in _SERIOUS_BLOCKERS)


def has_curious_positive_in_title(title: str) -> bool:
    return has_curious_positive_signal(title, "")


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


def is_dry_serious_curious_news(title: str, corpus: str = "", *, url: str = "") -> bool:
    """Сухой официоз без развлекательного крючка — не для курьёзного пула."""
    title, corpus = _gate_input(title, corpus, url)
    url_hints = curious_url_positive_hints(url)
    if url_hints and not has_curious_serious_blockers_in_title(title):
        if has_curious_positive_signal(title, corpus) or has_curious_positive_in_title(title):
            return False
        if has_curious_positive_signal("", url_hints):
            return False
    text = _merged_text(title, corpus)
    if not text:
        return True
    title_only = _title_text(title)
    if title_only and re.search(
        r"^новост\w*\s+\S+",
        title_only,
        flags=re.IGNORECASE,
    ) and not has_curious_positive_in_title(title):
        return True
    if title_only and has_curious_serious_blockers_in_title(title) and not has_curious_positive_in_title(title):
        return True
    score = curious_tone_score(title, corpus)
    has_positive = has_curious_positive_signal(title, corpus)
    has_serious = has_curious_serious_blockers(title, corpus)
    if has_curious_human_interest(title, corpus) and not has_serious:
        if has_curious_positive_signal(title, corpus) or has_curious_positive_in_title(title):
            return False
        return True
    if has_serious and not has_positive:
        return True
    if (
        has_serious
        and has_positive
        and not has_curious_positive_in_title(title)
        and has_curious_serious_blockers_in_title(title)
    ):
        # Маркеры «курьёза» только в sidebar при официозном заголовке — не считаем.
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


def passes_curious_tone_gate(title: str, corpus: str = "", *, url: str = "") -> bool:
    """
    Курьёзный выпуск: пропуск только с явным развлекательным/человеческим крючком.
    Нейтральные tech-новости без фейла/юмора/скандала — отсекаются.
    """
    if is_dry_serious_curious_news(title, corpus, url=url):
        return False
    title, corpus = _gate_input(title, corpus, url)
    text = _merged_text(title, corpus)
    if not text:
        return False
    score = curious_tone_score(title, corpus)
    has_positive = has_curious_positive_signal(title, corpus)
    has_serious = has_curious_serious_blockers(title, corpus)
    if has_curious_serious_blockers_in_title(title) and not has_curious_positive_in_title(title):
        return False
    if has_serious and not has_positive:
        return False
    if (
        has_serious
        and has_positive
        and not has_curious_positive_in_title(title)
        and has_curious_serious_blockers_in_title(title)
    ):
        return False
    if has_curious_positive_in_title(title) and curious_tone_score(title, "") >= 2:
        return True
    if has_curious_human_interest(title, corpus) and not has_serious:
        if has_curious_positive_signal(title, corpus) or has_curious_positive_in_title(title):
            return True
        return False
    if has_positive and score >= 2 and not has_serious:
        return True
    if has_positive and score >= 1 and not has_serious:
        return True
    return False


def passes_curious_pool_gate(title: str, corpus: str = "", *, url: str = "") -> bool:
    """
    Допуск в пул шага 1: отсекаем сухой официоз и ложные срабатывания sidebar.
    Спорные, но с курьёзным крючком — в пул (возможен curious_tone_low).
    """
    if is_dry_serious_curious_news(title, corpus, url=url):
        return False
    title, corpus = _gate_input(title, corpus, url)
    if passes_curious_tone_gate(title, corpus, url=url):
        return True
    has_positive = has_curious_positive_signal(title, corpus)
    has_serious = has_curious_serious_blockers(title, corpus)
    if has_positive and not has_serious:
        return True
    if has_curious_human_interest(title, corpus) and not has_serious:
        if has_curious_positive_signal(title, corpus) or has_curious_positive_in_title(title):
            return True
    return False


def explain_curious_gates(title: str, corpus: str = "", *, url: str = "") -> dict[str, Any]:
    """Структурированное объяснение pool/tone gate — для логов и настройки фильтров."""
    dry = is_dry_serious_curious_news(title, corpus, url=url)
    tone_pass = passes_curious_tone_gate(title, corpus, url=url)
    pool_pass = passes_curious_pool_gate(title, corpus, url=url)
    tone_score = curious_tone_score(title, corpus)
    title_score = curious_tone_score(title, "")
    has_positive = has_curious_positive_signal(title, corpus)
    has_positive_title = has_curious_positive_in_title(title)
    has_serious = has_curious_serious_blockers(title, corpus)
    has_serious_title = has_curious_serious_blockers_in_title(title)
    has_human = has_curious_human_interest(title, corpus)

    dry_reason = _curious_dry_reject_reason(title, corpus) if dry else None
    tone_reason = (
        _curious_tone_pass_reason(title, corpus)
        if tone_pass
        else _curious_tone_reject_reason(title, corpus, dry_reason)
    )
    pool_reason = (
        _curious_pool_pass_reason(title, corpus, tone_pass) if pool_pass else (dry_reason or "no_hook")
    )

    return {
        "pool_pass": pool_pass,
        "tone_pass": tone_pass,
        "dry_serious": dry,
        "tone_score": tone_score,
        "title_score": title_score,
        "has_positive": has_positive,
        "has_positive_title": has_positive_title,
        "has_serious": has_serious,
        "has_serious_title": has_serious_title,
        "has_human_interest": has_human,
        "dry_reason": dry_reason,
        "pool_reason": pool_reason,
        "tone_reason": tone_reason,
    }


def _curious_dry_reject_reason(title: str, corpus: str) -> str:
    if not (title or corpus).strip():
        return "empty_text"
    if has_curious_serious_blockers_in_title(title) and not has_curious_positive_in_title(title):
        return "serious_title_no_positive"
    has_positive = has_curious_positive_signal(title, corpus)
    has_serious = has_curious_serious_blockers(title, corpus)
    if (
        has_serious
        and has_positive
        and not has_curious_positive_in_title(title)
        and has_curious_serious_blockers_in_title(title)
    ):
        return "sidebar_false_positive"
    if has_serious and not has_positive:
        return "serious_no_positive"
    if not has_positive and curious_tone_score(title, corpus) <= 2:
        return "no_positive_low_score"
    return "dry_serious"


def _curious_tone_pass_reason(title: str, corpus: str) -> str:
    if has_curious_positive_in_title(title) and curious_tone_score(title, "") >= 2:
        return "title_marker"
    if has_curious_human_interest(title, corpus) and not has_curious_serious_blockers(title, corpus):
        return "human_interest"
    score = curious_tone_score(title, corpus)
    if has_curious_positive_signal(title, corpus) and score >= 2:
        return "positive_score_ge2"
    if has_curious_positive_signal(title, corpus) and score >= 1:
        return "positive_score_ge1"
    return "tone_pass"


def _curious_tone_reject_reason(title: str, corpus: str, dry_reason: str | None) -> str:
    if dry_reason:
        return dry_reason
    if has_curious_serious_blockers_in_title(title) and not has_curious_positive_in_title(title):
        return "serious_title_no_positive"
    has_positive = has_curious_positive_signal(title, corpus)
    has_serious = has_curious_serious_blockers(title, corpus)
    if has_serious and not has_positive:
        return "serious_no_positive"
    if (
        has_serious
        and has_positive
        and not has_curious_positive_in_title(title)
        and has_curious_serious_blockers_in_title(title)
    ):
        return "sidebar_false_positive"
    return "tone_strict_fail"


def _curious_pool_pass_reason(title: str, corpus: str, tone_pass: bool) -> str:
    if tone_pass:
        return "tone_gate"
    if has_curious_positive_signal(title, corpus) and not has_curious_serious_blockers(title, corpus):
        return "soft_positive"
    if has_curious_human_interest(title, corpus) and not has_curious_serious_blockers(title, corpus):
        return "soft_human_interest"
    return "pool_pass"

