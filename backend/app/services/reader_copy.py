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
