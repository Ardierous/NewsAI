"""Авторазбор доминирующих причин отбраковки и рекомендации «больше пула за меньше ₽»."""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.services.step1_statistics import REJECT_REASON_LABELS_RU, reject_label_ru
from app.services.step1_usage_breakdown import estimate_proxyapi_cost_from_meta

logger = logging.getLogger(__name__)

Priority = Literal["high", "medium", "low"]

# Порог доли от всех отбраковок, чтобы считать причину «доминирующей»
DOMINANCE_SHARE_PCT = 15.0


def _int_val(obj: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(obj.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _float_val(obj: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(obj.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _sorted_reject_counts(rejected_summary: dict[str, int]) -> list[tuple[str, int]]:
    items = [(str(k), int(v)) for k, v in rejected_summary.items() if int(v or 0) > 0]
    items.sort(key=lambda x: (-x[1], x[0]))
    return items


def build_dominant_rejects(
    rejected_summary: dict[str, int],
    *,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    items = _sorted_reject_counts(rejected_summary)
    total = sum(c for _, c in items) or 1
    out: list[dict[str, Any]] = []
    for code, count in items[:top_n]:
        share = round(100.0 * count / total, 1)
        out.append(
            {
                "code": code,
                "label": reject_label_ru(code),
                "count": count,
                "share_pct": share,
                "is_dominant": share >= DOMINANCE_SHARE_PCT or (out == [] and count > 0),
            }
        )
    return out


def build_funnel_bottlenecks(meta: dict[str, Any], *, verified_in_pool: int) -> list[dict[str, Any]]:
    if not meta:
        return []
    raw = _int_val(meta, "urls_raw_merged") or _int_val(meta, "urls_raw_unique")
    prefilter = _int_val(meta, "urls_prefilter_rejected")
    sent_http = _int_val(meta, "urls_sent_to_http")
    verified_meta = _int_val(meta, "verified_total", verified_in_pool)
    bottlenecks: list[dict[str, Any]] = []

    if raw > 0 and prefilter >= max(3, raw // 3):
        bottlenecks.append(
            {
                "stage": "prefilter",
                "label": "До HTTP (prefilter)",
                "lost": prefilter,
                "detail": f"Из {raw} сырых URL {prefilter} отсеяны до загрузки страницы (домен, дата в URL, дубликат).",
            }
        )
    if sent_http > 0 and verified_meta < max(3, sent_http // 5):
        lost_http = max(0, sent_http - verified_meta)
        if lost_http >= 3:
            bottlenecks.append(
                {
                    "stage": "http_verify",
                    "label": "HTTP-проверка",
                    "lost": lost_http,
                    "detail": f"На HTTP ушло {sent_http}, в пул попало {verified_meta} — основной отсев на verify.",
                }
            )
    registry_raw = _int_val(meta, "urls_raw_from_registry")
    web_skipped = _int_val(meta, "web_search_skipped")
    if registry_raw > 0:
        bottlenecks.append(
            {
                "stage": "registry_reuse",
                "label": "Реестр (без web_search)",
                "lost": 0,
                "detail": f"Из реестра подставлено {registry_raw} сырых URL"
                + (" — веб-поиск пропущен." if web_skipped else "."),
            }
        )
    cap_hit = bool(meta.get("web_search_api_cap_hit"))
    if cap_hit or str(meta.get("stop_reason") or "") == "web_search_api_cap":
        bottlenecks.append(
            {
                "stage": "web_search_cap",
                "label": "Лимит web_search",
                "lost": 0,
                "detail": "Сработал cap вызовов ProxyAPI web_search — дальше поиск не шёл, опирайтесь на реестр и кэш.",
            }
        )
    return bottlenecks


def build_efficiency_notes(
    meta: dict[str, Any],
    *,
    total_links: int,
    in_pool: int,
    step1_cost_rub: float,
) -> list[str]:
    notes: list[str] = []
    cost = step1_cost_rub if step1_cost_rub > 0 else estimate_proxyapi_cost_from_meta(meta)
    if cost > 0 and in_pool > 0:
        notes.append(f"≈{cost / in_pool:.2f} ₽ на одну ссылку в пуле ({in_pool} шт.).")
    elif cost > 5 and in_pool == 0:
        notes.append(f"За шаг 1 ≈{cost:.2f} ₽, в пул 0 — траты в основном на web_search без результата.")
    if total_links > 0 and cost > 0:
        notes.append(f"≈{cost / total_links:.2f} ₽ на одну проверенную ссылку в журнале ({total_links} шт.).")

    cache_hits = _int_val(meta, "web_search_cache_hits")
    api_calls = _int_val(meta, "web_search_api_calls")
    if cache_hits > 0:
        notes.append(f"Попаданий в кэш web_search: {cache_hits} (экономия повторных вызовов API).")
    elif api_calls >= 5:
        notes.append(f"Вызовов web_search API: {api_calls}, кэш не использовался — со временем повторные прогоны дешевле.")

    raw_registry = _int_val(meta, "urls_raw_registry_added") or _int_val(meta, "urls_raw_from_registry")
    if raw_registry > 0:
        notes.append(f"В реестр добавлено/переиспользовано сырых URL: {raw_registry}.")
    return notes


def build_recommendations(
    *,
    digest_type: str,
    rejected_summary: dict[str, int],
    dominant_rejects: list[dict[str, Any]],
    meta: dict[str, Any],
    summary: dict[str, Any],
    registry_buckets: dict[str, int],
) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    def add(priority: Priority, title: str, detail: str) -> None:
        if title in seen_titles:
            return
        seen_titles.add(title)
        recs.append({"priority": priority, "title": title, "detail": detail})

    counts = dict(rejected_summary)
    total_rej = sum(counts.values()) or 1
    in_pool = _int_val(summary, "in_pool")
    stop = str(meta.get("stop_reason") or "")

    dominant_codes = {d["code"] for d in dominant_rejects if d.get("is_dominant")}

    if stop == "web_search_api_cap" or meta.get("web_search_api_cap_hit"):
        add(
            "high",
            "Сначала реестр и кэш, не новый поиск",
            "Лимит web_search исчерпан. Следующий прогон: убедитесь, что step1_url_registry_reuse_enabled=true; "
            "сырые URL из реестра (bucket raw) идут без API. Повторный прогон с тем же окном дат дешевле за счёт кэша web_search.",
        )

    if counts.get("http_unreachable", 0) >= 3 or "http_unreachable" in dominant_codes:
        share = 100 * counts.get("http_unreachable", 0) / total_rej
        add(
            "high",
            "Снизить долю http_unreachable",
            f"~{share:.0f}% отбраковок — страница не открылась. Дешевле: reuse проверенных URL из реестра; "
            "не гонять Crew на сомнительные домены; при 20+ отказах домен уходит в autoblock tiers. "
            "Добавьте 5–10 прямых рабочих URL вручную на шаге 1 — без web_search.",
        )

    halluc = counts.get("url_mutated_between_agents", 0) + counts.get("llm_hallucinated_url", 0)
    if halluc >= 2 or "url_mutated_between_agents" in dominant_codes or "llm_hallucinated_url" in dominant_codes:
        add(
            "high",
            "Меньше выдуманных URL от Crew",
            "Ссылки менялись между агентами или не открываются — типичный «мусор» от LLM, не экономия HTTP. "
            "Опирайтесь на URL из web_search (citations) и реестра; сократите добор Crew, если verified уже есть из поиска.",
        )

    if counts.get("published_before_window", 0) >= 3 or "published_before_window" in dominant_codes:
        add(
            "high",
            "Расширить окно дат на шаге 0",
            "Много материалов старше окна поиска — бесплатное увеличение пула: +2–3 календарных дня "
            "или «календарные» вместо «рабочих» дней.",
        )

    if digest_type == "curious" and (
        counts.get("off_topic_not_curious", 0) >= 3 or "off_topic_not_curious" in dominant_codes
    ):
        add(
            "medium",
            "Курьёз: сузить поиск, не ослаблять все фильтры",
            "Доминирует off_topic_not_curious (сухой официоз). Дешевле сменить угол запроса (entertainment anchor), "
            "чем отключать HTTP-фильтры. Проверьте curious_source_hosts.",
        )

    if digest_type == "serious" and (counts.get("off_topic_not_ai", 0) >= 3 or "off_topic_not_ai" in dominant_codes):
        add(
            "medium",
            "Серьёзный: тема не ИИ",
            "Много off_topic_not_ai — поиск тянет не ту тематику. Уточните seed/tier-хосты или ручные URL с Habr/VC/RBC tech.",
        )

    if counts.get("aggregator_source", 0) >= 2 or "aggregator_source" in dominant_codes:
        add(
            "medium",
            "Меньше агрегаторов в выдаче",
            "Google News / Reddit / ленты — мёртвый трафик на HTTP. Tier-strict и прямые site: в запросе экономят verify.",
        )

    raw_bucket = int(registry_buckets.get("raw", 0) or 0)
    verified_bucket = int(registry_buckets.get("verified", 0) or 0)
    if raw_bucket >= 10 and _int_val(meta, "urls_raw_from_registry", 0) < 3:
        add(
            "medium",
            "Использовать накопленный реестр",
            f"В реестре {raw_bucket} сырых URL (90 дн.) — при следующем прогоне они должны подставляться до web_search. "
            f"Уже verified: {verified_bucket}.",
        )

    if in_pool >= 5 and counts.get("excluded_from_final_pool", 0) >= 3:
        add(
            "low",
            "Пул есть, урезает rebalance",
            "Часть ссылок прошла verify, но excluded_from_final_pool — это лимиты финального списка, не поиск. "
            "Смотрите квоты rebalance, не тратьте ₽ на лишний web_search.",
        )

    if in_pool < 5 and stop in {"hard_timeout", "soft_timeout_after_collect", "soft_timeout_final_attempt"}:
        add(
            "medium",
            "Время вышло раньше пула",
            "Остановка по таймауту при малом пуле. Для дешёвого добора: сначала реестр + ручные URL; "
            "увеличьте окно дат вместо лишних итераций web_search.",
        )

    if not recs and in_pool >= 10:
        add(
            "low",
            "Пул в норме",
            "Доминирующих проблем не видно. Для экономии — не пересобирайте без нужды: кэш и реестр работают на повторных прогонах.",
        )
    elif not recs:
        add(
            "medium",
            "Мало данных для вывода",
            "Запустите шаг 1 ещё раз или нажмите «Обновить» в статистике после завершения прогона.",
        )

    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: order.get(str(r["priority"]), 9))
    return recs


def build_step1_insights(
    *,
    digest_type: str,
    rejected_summary: dict[str, int],
    step1_collection_meta: dict[str, Any],
    summary: dict[str, Any],
    registry_buckets: dict[str, int],
    step1_cost_rub: float = 0.0,
) -> dict[str, Any]:
    in_pool = _int_val(summary, "in_pool")
    total_links = _int_val(summary, "total_links")
    rejected = _int_val(summary, "rejected")

    dominant = build_dominant_rejects(rejected_summary)
    bottlenecks = build_funnel_bottlenecks(step1_collection_meta, verified_in_pool=in_pool)
    efficiency = build_efficiency_notes(
        step1_collection_meta,
        total_links=total_links,
        in_pool=in_pool,
        step1_cost_rub=step1_cost_rub,
    )
    recommendations = build_recommendations(
        digest_type=digest_type,
        rejected_summary=rejected_summary,
        dominant_rejects=dominant,
        meta=step1_collection_meta,
        summary=summary,
        registry_buckets=registry_buckets,
    )

    stop = str(step1_collection_meta.get("stop_reason") or "")
    top = dominant[0] if dominant else None
    if top and top.get("is_dominant"):
        headline = (
            f"Доминирует «{top['label']}» — {top['count']} ({top['share_pct']}% отбраковок). "
            f"В пуле {in_pool}, проверено {total_links}."
        )
    elif in_pool == 0 and rejected > 0:
        headline = f"В пул не попала ни одна ссылка из {total_links} проверенных ({rejected} отбраковано)."
    elif in_pool >= 10:
        headline = f"Пул {in_pool} ссылок — явных перекосов в отбраковке нет."
    else:
        headline = f"В пуле {in_pool} из {total_links} проверенных URL."

    if stop:
        headline += f" Стоп: {stop}."

    payload = {
        "headline": headline.strip(),
        "stop_reason": stop,
        "dominant_rejects": dominant,
        "funnel_bottlenecks": bottlenecks,
        "efficiency_notes": efficiency,
        "recommendations": recommendations,
    }
    if dominant:
        top_codes = ", ".join(f"{d['code']}={d['count']}" for d in dominant[:3] if d.get("is_dominant"))
        if top_codes:
            logger.info(
                "Шаг 1 insights | digest_type=%s in_pool=%s dominant=%s stop=%s",
                digest_type,
                in_pool,
                top_codes,
                stop or "-",
            )
    return payload
