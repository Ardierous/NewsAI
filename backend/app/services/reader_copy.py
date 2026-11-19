"""Редакторские тексты для читателя: без служебки, без штампов ИИ."""

from __future__ import annotations

import re

# Служебные маркеры и типичный «ИИ-канцелярит»
_BANNED_PREFIXES = (
    "ключевая суть:",
    "суть:",
    "итог:",
    "вывод:",
    "резюме:",
    "инсайт:",
    "insight:",
)

_BANNED_PHRASES = (
    "это важно для рынка ии",
    "меняет расстановку сил",
    "ускорение внедрения технологий",
    "в контексте рынка ии",
    "игроки экосистемы",
    "явная монетизация",
    "перераспределение ресурсов",
    "команды, которые быстрее адаптируют",
)

_SERVICE_RE = re.compile(
    r"\b(?:Tier[- ]?[1-5]|TIER[- ]?[1-5]|total_score|балл(?:ов)?\s*\d|"
    r"✅|⚠️|❗|verification_comment|link_status|headline_editorial|"
    r"кандидат(?:а|ов)?\s*№|original_number|digest_id|ProxyAPI|CrewAI)\b",
    re.IGNORECASE,
)

# Шаблон аналитики шага 3 (не для читателя на площадках).
_ANALYTICS_SCAFFOLD_RE = re.compile(
    r"\s+(?:Аудитория|Почему важно|Польза|Вред|Возможные последствия)\s*:",
    re.IGNORECASE,
)


def strip_analytics_scaffold(text: str) -> str:
    """Обрезает служебные блоки «Аудитория: … Почему важно: …» из текста для читателя."""
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return ""
    match = _ANALYTICS_SCAFFOLD_RE.search(s)
    if match:
        s = s[: match.start()].strip()
    return s


def sanitize_reader_description(text: str, *, keep_analytics_labels: bool = False) -> str:
    """Убирает служебную информацию и штампы из текста для площадок."""
    s = str(text or "").strip()
    if not s:
        return ""
    s = _SERVICE_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    for pref in _BANNED_PREFIXES:
        if s.lower().startswith(pref):
            s = s[len(pref) :].strip()
    for phrase in _BANNED_PHRASES:
        s = re.sub(re.escape(phrase), "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;.")
    if not keep_analytics_labels:
        s = strip_analytics_scaffold(s)
    return s


_TECHNICAL_DESCRIPTION_MARKERS = (
    "кандидат из веб-поиска",
    "заголовок подтянут со страницы",
    "заголовок извлечён со страницы",
    "заголовок извлечен со страницы",
    "вставлено в поле url",
    "собрано telegram-монитором",
    "материал обязателен к использованию",
    "краткое описание не заполнено",
    "источник из веб-поиска",
    "проверка страницы обязательна",
    "сайт блокирует автоматическую проверку",
    "текст и заголовок подтянуты через веб-поиск",
)


def is_technical_candidate_description(text: str) -> bool:
    """True для служебных заглушек шага 1, не для читателя."""
    s = " ".join(str(text or "").split()).strip().lower()
    if not s:
        return True
    return any(marker in s for marker in _TECHNICAL_DESCRIPTION_MARKERS)


_NAV_NOISE_RE = re.compile(
    r"cookie|подписаться|subscribe now|sign in|log in|войти|регистрац|меню навигац|"
    r"copyright|все права защищены|advertisement|реклама\b|related articles|читайте также",
    re.IGNORECASE,
)


def _cyrillic_letter_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04ff")
    return cyr / len(letters)


def text_primarily_russian(text: str) -> bool:
    """True, если в тексте достаточно кириллицы, чтобы считать его русским."""
    return _cyrillic_letter_ratio(text) >= 0.35


# Английские служебные/смысловые слова в заголовке — сигнал, что перевод не доведён.
# Имена людей и бренды (Victoria, Uber, Pentagon) сюда не входят.
_TITLE_ENGLISH_CONTENT_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "for",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "with",
        "without",
        "from",
        "into",
        "over",
        "under",
        "about",
        "after",
        "before",
        "against",
        "between",
        "through",
        "during",
        "than",
        "then",
        "that",
        "this",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "might",
        "must",
        "its",
        "it's",
        "their",
        "his",
        "her",
        "our",
        "your",
        "who",
        "what",
        "when",
        "where",
        "why",
        "how",
        "not",
        "no",
        "yes",
        "just",
        "more",
        "most",
        "new",
        "says",
        "said",
        "say",
        "announces",
        "announce",
        "announced",
        "unveils",
        "unveil",
        "unveiled",
        "launches",
        "launch",
        "launched",
        "releases",
        "release",
        "released",
        "introduces",
        "introduce",
        "introduced",
        "reveals",
        "reveal",
        "revealed",
        "claims",
        "claim",
        "claimed",
        "plans",
        "plan",
        "planned",
        "wants",
        "want",
        "needs",
        "need",
        "gets",
        "get",
        "got",
        "gives",
        "give",
        "gave",
        "lets",
        "let",
        "makes",
        "make",
        "made",
        "takes",
        "take",
        "took",
        "uses",
        "use",
        "used",
        "using",
        "users",
        "user",
        "staff",
        "workers",
        "employees",
        "people",
        "man",
        "men",
        "woman",
        "women",
        "scientist",
        "scientists",
        "researcher",
        "researchers",
        "company",
        "companies",
        "major",
        "epic",
        "mass",
        "revenge",
        "hacking",
        "spree",
        "hires",
        "hire",
        "hired",
        "swarm",
        "agents",
        "agent",
        "against",
        "become",
        "became",
        "conscious",
        "toast",
        "challenging",
        "challenge",
        "understands",
        "understand",
        "industry",
        "automate",
        "automation",
        "logistics",
        "contract",
        "lands",
        "landed",
        "seeing",
        "see",
        "their",
        "passwords",
        "password",
        "sign",
        "login",
        "logins",
        "without",
        "developers",
        "developer",
        "worldwide",
        "better",
        "coding",
        "juniors",
        "junior",
        "future",
        "society",
        "artificial",
        "intelligence",
        "report",
        "reports",
        "according",
        "near",
        "almost",
        "rarely",
        "cheaply",
        "reach",
    }
)

_LATIN_TITLE_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.#'-]{0,}")
_UKRAINIAN_LETTERS_RE = re.compile(r"[іІїЇєЄґҐ]")

# Доля кириллицы, после которой заголовок считаем достаточно русским для шага 2.
_TITLE_RUSSIAN_CYR_RATIO = 0.55


def title_translation_acceptable(translated: str, *, source: str = "") -> bool:
    """Принимаем перевод, если это уже в основном русский заголовок."""
    t = str(translated or "").strip()
    if len(t) < 6:
        return False
    if _UKRAINIAN_LETTERS_RE.search(t):
        return False
    ratio = _cyrillic_letter_ratio(t)
    if ratio < _TITLE_RUSSIAN_CYR_RATIO:
        return False
    src = str(source or "").strip()
    if src and t.casefold() == src.casefold():
        return False
    # Не оставляем явные английские глаголы/служебные слова в «готовом» заголовке.
    for match in _LATIN_TITLE_WORD_RE.finditer(t):
        tok = match.group(0).lower().strip(".'\"")
        if tok.endswith("'s"):
            tok = tok[:-2]
        if tok in _TITLE_ENGLISH_CONTENT_WORDS:
            return False
    return True


def title_needs_russian_translation(title: str) -> bool:
    """True, если заголовок ещё не выглядит как русский для карточки шага 2.

    Важно: имена людей/бренды латиницей (Victoria, Uber, Pentagon) сами по себе
    не требуют перевода — иначе хороший русский перевод с именем отбрасывался,
    и в пуле оставался исходный английский заголовок.
    """
    t = str(title or "").strip()
    if not t:
        return False
    if _UKRAINIAN_LETTERS_RE.search(t):
        return True
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    if _cyrillic_letter_ratio(t) < _TITLE_RUSSIAN_CYR_RATIO:
        return True
    for match in _LATIN_TITLE_WORD_RE.finditer(t):
        tok = match.group(0).lower().strip(".'\"")
        if tok.endswith("'s"):
            tok = tok[:-2]
        if tok in _TITLE_ENGLISH_CONTENT_WORDS:
            return True
    return False


def brief_page_teaser(
    text: str,
    *,
    title: str = "",
    max_chars: int = 560,
    russian_only: bool = False,
) -> str:
    """Несколько предложений из уже скачанного текста страницы (без LLM)."""
    src = " ".join(str(text or "").split()).strip()
    if not src:
        return ""
    title_n = " ".join(str(title or "").split()).strip().lower()
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", src) if p.strip()]
    usable: list[str] = []
    for part in parts:
        if is_technical_candidate_description(part):
            continue
        if _NAV_NOISE_RE.search(part):
            continue
        low = part.lower()
        if title_n and (low == title_n or title_n.startswith(low[:40]) or low.startswith(title_n[:40])):
            continue
        if len(part) < 28:
            continue
        usable.append(part)
    if not usable:
        return ""
    russian = [p for p in usable if text_primarily_russian(p)]
    if russian_only:
        pool = russian
    else:
        pool = russian or usable
    if not pool:
        return ""
    picked: list[str] = []
    for part in pool:
        picked.append(part)
        if len(picked) >= 5 or len(" ".join(picked)) >= max_chars:
            break
    out = _trim_to_sentences(" ".join(picked), 5, max_chars=max_chars)
    if russian_only and out and not text_primarily_russian(out):
        return ""
    return out


def public_candidate_description(
    item: dict,
    *,
    max_chars: int = 560,
    require_russian: bool = True,
) -> str:
    """Читательское описание: своё поле, если оно не заглушка, иначе текст со страницы."""
    title = str(item.get("title") or "")
    desc = str(item.get("description") or "").strip()
    excerpt = str(item.get("article_excerpt") or item.get("reader_excerpt") or item.get("topic_corpus") or "")
    if (
        desc
        and not is_technical_candidate_description(desc)
        and text_primarily_russian(desc)
        and len(desc) >= 40
    ):
        return _trim_to_sentences(desc, 5, max_chars=max_chars)
    page = brief_page_teaser(
        excerpt,
        title=title,
        max_chars=max_chars,
        russian_only=require_russian,
    )
    if page and (not require_russian or text_primarily_russian(page)):
        return page
    if desc and not is_technical_candidate_description(desc):
        if not require_russian or text_primarily_russian(desc):
            return _trim_to_sentences(desc, 5, max_chars=max_chars)
    return ""


READER_TEXT_MAX_CHARS = 450
# Тема «Стиль»: один пост MAX на 5 новостей — можно длиннее, чем для ИИ.
READER_TEXT_MAX_CHARS_STYLE = 1000


def _trim_to_sentences(text: str, max_sentences: int, *, max_chars: int | None = None) -> str:
    s = " ".join(text.split())
    if not s:
        return ""
    parts = re.split(r"(?<=[.!?…])\s+", s)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return s
    result = " ".join(parts[:max_sentences])
    if max_chars is not None and len(result) > max_chars:
        parts_fit: list[str] = []
        cur = 0
        for part in parts:
            add = len(part) + (1 if parts_fit else 0)
            if cur + add > max_chars:
                break
            parts_fit.append(part)
            cur += add
        if parts_fit:
            result = " ".join(parts_fit)
        else:
            cut = result[:max_chars].rsplit(". ", 1)
            result = (cut[0] + ".") if len(cut) > 1 and cut[0] else result[:max_chars].rstrip(" ,;:-") + "."
    return result


def build_reader_text_fallback(
    essence: str, analysis: str, *, max_sentences: int = 4, max_chars: int = READER_TEXT_MAX_CHARS,
) -> str:
    """Эвристика, если LLM не вернул reader_text."""
    parts: list[str] = []
    essence_clean = sanitize_reader_description(essence)
    analysis_clean = sanitize_reader_description(analysis)
    if essence_clean:
        parts.append(_trim_to_sentences(essence_clean, 1))
    if analysis_clean:
        trimmed = _trim_to_sentences(analysis_clean, 2)
        if trimmed and trimmed not in " ".join(parts):
            parts.append(trimmed)
    combined = " ".join(parts).strip()
    if not combined and analysis_clean:
        combined = _trim_to_sentences(analysis_clean, max_sentences)
    return _trim_to_sentences(combined, max_sentences, max_chars=max_chars)


def build_platform_description(
    reader_text: str = "",
    *,
    essence: str = "",
    analysis: str = "",
    max_sentences: int = 4,
    max_chars: int = READER_TEXT_MAX_CHARS,
) -> str:
    """
    Текст под заголовком новости на площадках: 2–4 простых предложения, до 450 символов.
    Приоритет: reader_text из шага 4; иначе fallback из analysis + essence.
    """
    reader_text = sanitize_reader_description(reader_text)
    if reader_text:
        return _trim_to_sentences(reader_text, max_sentences, max_chars=max_chars)
    return build_reader_text_fallback(essence, analysis, max_sentences=max_sentences, max_chars=max_chars)
