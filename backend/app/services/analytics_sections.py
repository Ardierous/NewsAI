"""Разбор аналитики шага 3 и сборка расширенных текстов для PDF."""

from __future__ import annotations

import re
from typing import Any

_ANALYSIS_BLOCK_RE = re.compile(
    r"(Аудитория|Почему важно|Польза|Вред|Возможные последствия)\s*:\s*",
    re.IGNORECASE,
)

_RUSSIA_HINT_RE = re.compile(
    r"росси|российск|russian|рф\b|москв|петербург|снг",
    re.IGNORECASE,
)


def split_sentences(text: str) -> list[str]:
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return []
    parts = re.split(r"(?<=[.!?…])\s+", s)
    return [p.strip() for p in parts if p.strip()]


def _dedupe_sentences(sentences: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sent in sentences:
        key = sent.lower()[:96]
        if key in seen:
            continue
        seen.add(key)
        out.append(sent)
    return out


def expand_to_sentence_budget(
    *sources: str,
    min_sentences: int,
    max_sentences: int,
) -> str:
    pool: list[str] = []
    for src in sources:
        pool.extend(split_sentences(src))
    pool = _dedupe_sentences(pool)
    if len(pool) < min_sentences:
        for src in sources:
            for sent in split_sentences(src):
                if sent not in pool:
                    pool.append(sent)
                if len(pool) >= min_sentences:
                    break
            if len(pool) >= min_sentences:
                break
    chosen = pool[:max_sentences]
    if not chosen:
        return ""
    text = " ".join(chosen)
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def parse_analysis_blocks(analysis: str) -> dict[str, str]:
    text = str(analysis or "").strip()
    if not text:
        return {
            "audience": "",
            "why": "",
            "benefit": "",
            "harm": "",
            "consequences": "",
        }
    matches = list(_ANALYSIS_BLOCK_RE.finditer(text))
    if not matches:
        return {
            "audience": "",
            "why": "",
            "benefit": text,
            "harm": "",
            "consequences": "",
        }
    key_map = {
        "аудитория": "audience",
        "почему важно": "why",
        "польза": "benefit",
        "вред": "harm",
        "возможные последствия": "consequences",
    }
    out = {k: "" for k in key_map.values()}
    for idx, match in enumerate(matches):
        label = match.group(1).strip().lower()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        slot = key_map.get(label)
        if slot:
            out[slot] = body
    return out


def _russia_relevance_paragraph(*sources: str) -> str:
    picked: list[str] = []
    for src in sources:
        for sent in split_sentences(src):
            if _RUSSIA_HINT_RE.search(sent):
                picked.append(sent)
    picked = _dedupe_sentences(picked)
    if len(picked) >= 3:
        return expand_to_sentence_budget(" ".join(picked), min_sentences=3, max_sentences=4)
    essence = " ".join(sources).strip()
    fallback = [
        "Для российских читателей важно видеть, как глобальные модные тренды доходят до локального рынка и повседневного гардероба.",
        "Такие материалы помогают заранее понять, какие силуэты, цвета и детали будут востребованы в ближайшие месяцы.",
    ]
    if essence:
        fallback.append(
            f"На фоне российского ритейла и медиа эта тема выглядит особенно заметной: {split_sentences(essence)[:1][0] if split_sentences(essence) else essence[:200]}."
        )
    else:
        fallback.append(
            "Даже без прямых упоминаний России тренд можно перенести на локальные бренды, маркетплейсы и стрит-стайл в крупных городах."
        )
    return expand_to_sentence_budget(" ".join(fallback), min_sentences=3, max_sentences=4)


def build_extended_news_sections(
    *,
    title: str,
    essence: str,
    comment: str,
    analysis: str,
    reader_text: str,
) -> list[tuple[str, str]]:
    """Семь блоков расширенного описания (ориентир 20–25 предложений на новость)."""
    blocks = parse_analysis_blocks(analysis)
    ctx_essence = essence.strip()
    ctx_reader = reader_text.strip()
    ctx_comment = comment.strip()
    all_analysis = " ".join(blocks.values())

    sections: list[tuple[str, str]] = [
        (
            "Краткий пересказ новости",
            expand_to_sentence_budget(
                ctx_reader,
                ctx_essence,
                blocks["why"],
                min_sentences=5,
                max_sentences=7,
            ),
        ),
        (
            "Для кого важно и почему",
            expand_to_sentence_budget(
                blocks["audience"],
                blocks["why"],
                ctx_essence,
                min_sentences=3,
                max_sentences=4,
            ),
        ),
        (
            "Польза для читателей",
            expand_to_sentence_budget(
                blocks["benefit"],
                ctx_reader,
                ctx_essence,
                min_sentences=3,
                max_sentences=4,
            ),
        ),
        (
            "Актуальность для российских читателей",
            _russia_relevance_paragraph(ctx_reader, ctx_essence, all_analysis, ctx_comment),
        ),
        (
            "Польза",
            expand_to_sentence_budget(blocks["benefit"], ctx_essence, min_sentences=2, max_sentences=3),
        ),
        (
            "Вред",
            expand_to_sentence_budget(blocks["harm"], ctx_comment, min_sentences=2, max_sentences=3),
        ),
        (
            "Последствия",
            expand_to_sentence_budget(
                blocks["consequences"],
                blocks["why"],
                ctx_comment,
                min_sentences=2,
                max_sentences=3,
            ),
        ),
    ]
    return [(heading, body) for heading, body in sections if body.strip()]


def extended_sections_sentence_count(sections: list[tuple[str, str]]) -> int:
    return sum(len(split_sentences(body)) for _, body in sections)
