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


def sanitize_reader_description(text: str) -> str:
    """Убирает служебную информацию и штампы из текста для площадок."""
    s = str(text or "").strip()
    if not s:
        return ""
    s = _SERVICE_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    for pref in _BANNED_PREFIXES:
        if s.lower().startswith(pref):
            s = s[len(pref) :].strip()
    low = s.lower()
    for phrase in _BANNED_PHRASES:
        s = re.sub(re.escape(phrase), "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;.")
    return s


def build_platform_description(essence: str, comment: str, analysis: str = "") -> str:
    """
    Текст под заголовком новости на площадках: 2–3 предложения для читателя.
    Приоритет: поле comment уже должно быть готовым редакционным описанием.
    """
    comment = sanitize_reader_description(comment)
    essence = sanitize_reader_description(essence)
    if comment:
        return _trim_to_sentences(comment, 3)
    if essence:
        return _trim_to_sentences(essence, 3)
    return _trim_to_sentences(sanitize_reader_description(analysis), 3)


def _trim_to_sentences(text: str, max_sentences: int) -> str:
    s = " ".join(text.split())
    if not s:
        return ""
    parts = re.split(r"(?<=[.!?…])\s+", s)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return s
    return " ".join(parts[:max_sentences])
