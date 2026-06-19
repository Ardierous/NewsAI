"""Сводка времени и денег шага 1 по инструментам для UI."""

from __future__ import annotations

from app.services.usage_cost import estimate_proxyapi_request_fee_rub


def _format_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m} мин {s} с"
    return f"{s} с"

TOOL_ORDER = (
    "web_search",
    "alt_search",
    "http_verify",
    "telegram",
    "crew",
    "seed_fallback",
    "other",
)

TOOL_LABELS: dict[str, str] = {
    "web_search": "Веб-поиск ProxyAPI",
    "alt_search": "SerpAPI / Tavily",
    "http_verify": "HTTP-проверка ссылок",
    "telegram": "Монитор Telegram",
    "crew": "CrewAI добор",
    "seed_fallback": "Seed-листинги",
    "other": "Прочее",
}

TOOL_COLORS: dict[str, str] = {
    "web_search": "#38bdf8",
    "alt_search": "#a78bfa",
    "http_verify": "#4ade80",
    "telegram": "#f472b6",
    "crew": "#fb923c",
    "seed_fallback": "#fbbf24",
    "other": "#94a3b8",
}

_COST_LABEL_TO_TOOL: dict[str, str] = {
    "proxyapi_web_search_urls": "web_search",
    "proxyapi_web_search_supplement": "web_search",
    "proxyapi_web_search": "web_search",
    "run_candidates_research": "crew",
    "run_candidates_verify": "crew",
    "run_candidates_score": "crew",
    "run_candidates_refill": "crew",
    "step_1_collect_pool": "_lump",
}


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _int_meta(meta: dict, key: str) -> int:
    return max(0, int(meta.get(key, 0) or 0))


def estimate_proxyapi_cost_from_meta(meta: dict | None) -> float:
    """Оценка ₽ за web_search: service + токены (как в CSV ProxyAPI), иначе по счётчикам вызовов."""
    if not meta:
        return 0.0
    for key in ("web_search_cost_est_rub", "proxyapi_web_search_cost_est_rub"):
        stored = meta.get(key)
        if stored is not None:
            try:
                value = round(max(0.0, float(stored)), 4)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    service = _num(meta.get("web_search_service_cost_est_rub"))
    tokens = _num(meta.get("web_search_token_cost_est_rub"))
    if service + tokens > 0:
        return round(service + tokens, 4)
    preview = _int_meta(meta, "web_search_preview_calls")
    total_calls = _int_meta(meta, "web_search_api_calls")
    responses = _int_meta(meta, "web_search_response_calls")
    if responses <= 0 and total_calls > 0:
        responses = max(0, total_calls - preview)
    cost = responses * estimate_proxyapi_request_fee_rub("responses.web_search")
    cost += preview * estimate_proxyapi_request_fee_rub("chat.web_search_preview")
    return round(max(0.0, cost), 4)


def resolve_step1_proxyapi_cost_rub(
    meta: dict | None,
    *,
    step1_total_rub: float = 0.0,
    last_run_cost_rub: float | None = None,
    step1_costs: list[dict] | None = None,
) -> tuple[float, str]:
    """Итог ₽ шага 1 и источник цифры для UI."""
    llm_sum = sum(_num(r.get("cost_rub")) for r in (step1_costs or []))
    balance_delta = _num(meta.get("proxyapi_balance_delta_rub") if meta else 0)
    est_ws = estimate_proxyapi_cost_from_meta(meta)
    total = round(
        max(
            _num(step1_total_rub),
            _num(last_run_cost_rub),
            llm_sum,
            est_ws,
            balance_delta,
        ),
        4,
    )
    if balance_delta > 0 and balance_delta >= total - 0.01:
        return total, "balance"
    if llm_sum > 0 and llm_sum >= total - 0.01:
        return total, "records"
    if est_ws > 0:
        return total, "estimate"
    return total, "none"


def _cost_by_tool_from_records(step1_costs: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {k: 0.0 for k in TOOL_ORDER}
    lump = 0.0
    for row in step1_costs:
        label = str(row.get("request_label") or "").strip()
        cost = _num(row.get("cost_rub"))
        if cost <= 0:
            continue
        bucket = _COST_LABEL_TO_TOOL.get(label)
        if bucket == "_lump":
            lump += cost
        elif bucket:
            out[bucket] += cost
        else:
            out["other"] += cost
    if lump > 0:
        assigned = sum(out.values())
        remaining = max(0.0, lump)
        if assigned <= 0.0001:
            out["other"] += remaining
        else:
            for key in TOOL_ORDER:
                if key == "other":
                    continue
                share = out[key] / assigned
                part = round(remaining * share, 6)
                out[key] += part
                remaining -= part
            out["other"] += max(0.0, remaining)
    return out


def _estimate_cost_by_tool(meta: dict, total_cost: float, step1_costs: list[dict]) -> dict[str, float]:
    explicit = _cost_by_tool_from_records(step1_costs)
    explicit_total = sum(explicit.values())
    if explicit_total > 0:
        if total_cost > explicit_total + 0.0001:
            explicit["other"] += round(total_cost - explicit_total, 6)
        return explicit

    est_ws = estimate_proxyapi_cost_from_meta(meta)
    total = max(float(total_cost), est_ws)
    if total <= 0:
        return {k: 0.0 for k in TOOL_ORDER}

    if est_ws >= total - 0.0001:
        out = {k: 0.0 for k in TOOL_ORDER}
        out["web_search"] = round(total, 4)
        return out

    out = {k: 0.0 for k in TOOL_ORDER}
    out["web_search"] = round(est_ws, 4)
    out["other"] = round(total - est_ws, 6)
    return out


def _phase_sec_from_meta(meta: dict) -> dict[str, int]:
    raw = meta.get("phase_sec")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key in TOOL_ORDER:
        if key in raw:
            out[key] = max(0, int(raw.get(key, 0) or 0))
    return out


def _estimate_time_by_tool(meta: dict, total_elapsed_sec: int) -> dict[str, int]:
    total = max(0, int(total_elapsed_sec))
    if total <= 0:
        return {k: 0 for k in TOOL_ORDER}

    measured = _phase_sec_from_meta(meta)
    measured_sum = sum(measured.values())
    if measured_sum > 0:
        if measured_sum < total:
            measured["other"] = measured.get("other", 0) + (total - measured_sum)
        return measured

    api_calls = _int_meta(meta, "web_search_api_calls")
    sent_http = _int_meta(meta, "urls_sent_to_http")
    web_sec = min(total, max(api_calls * 12, 8 if api_calls else 0))
    http_ratio = min(0.92, sent_http / max(1, sent_http + 6))
    http_sec = min(total - web_sec, int(total * http_ratio))
    if http_sec + web_sec > total:
        http_sec = max(0, total - web_sec)
    other_sec = max(0, total - web_sec - http_sec)
    return {
        "web_search": web_sec,
        "alt_search": 0,
        "http_verify": http_sec,
        "telegram": 0,
        "crew": 0,
        "seed_fallback": 0,
        "other": other_sec,
    }


def _tool_metrics(meta: dict, tool_id: str) -> dict[str, int | str | None]:
    if tool_id == "web_search":
        calls = _int_meta(meta, "web_search_api_calls")
        preview = _int_meta(meta, "web_search_preview_calls")
        cache_hits = _int_meta(meta, "web_search_cache_hits")
        citations = _int_meta(meta, "web_search_citation_urls")
        dropped = _int_meta(meta, "web_search_model_urls_dropped")
        parts: list[str] = []
        if calls:
            parts.append(f"{calls} API-выз.")
            if preview:
                parts.append(f"preview {preview}")
        if cache_hits:
            parts.append(f"кэш {cache_hits}")
        if citations:
            parts.append(f"citations {citations}")
        if dropped:
            parts.append(f"отброшено {dropped}")
        est = estimate_proxyapi_cost_from_meta(meta)
        service = _num(meta.get("web_search_service_cost_est_rub"))
        tokens = _num(meta.get("web_search_token_cost_est_rub"))
        if est > 0:
            if service > 0 and tokens > 0:
                parts.append(f"≈{est:.0f} ₽ (service {service:.0f} + tokens {tokens:.0f})")
            else:
                parts.append(f"≈{est:.0f} ₽")
        return {"calls": calls, "detail": " · ".join(parts) if parts else None}
    if tool_id == "http_verify":
        sent = _int_meta(meta, "urls_sent_to_http")
        return {"urls": sent, "detail": f"{sent} URL на проверку" if sent else None}
    if tool_id == "alt_search":
        raw = _int_meta(meta, "urls_raw_unique") or _int_meta(meta, "urls_raw_merged")
        return {"urls": raw, "detail": None}
    if tool_id == "seed_fallback":
        scanned = _int_meta(meta, "seed_listing_fallback_scanned")
        return {"calls": scanned, "detail": f"сканировано {scanned} seed" if scanned else None}
    return {"detail": None}


def build_step1_usage_breakdown(
    meta: dict | None,
    *,
    step1_costs: list[dict] | None = None,
    step1_total_rub: float = 0.0,
    last_run_cost_rub: float | None = None,
) -> dict | None:
    if not meta:
        return None

    total_elapsed = max(0, _int_meta(meta, "elapsed_sec"))
    total_cost, cost_source = resolve_step1_proxyapi_cost_rub(
        meta,
        step1_total_rub=step1_total_rub,
        last_run_cost_rub=last_run_cost_rub,
        step1_costs=step1_costs,
    )
    time_by_tool = _estimate_time_by_tool(meta, total_elapsed)
    cost_by_tool = _estimate_cost_by_tool(meta, total_cost, step1_costs or [])

    tools: list[dict] = []
    for tool_id in TOOL_ORDER:
        time_sec = int(time_by_tool.get(tool_id, 0) or 0)
        cost_rub = round(float(cost_by_tool.get(tool_id, 0.0) or 0.0), 4)
        if time_sec <= 0 and cost_rub <= 0.0001:
            continue
        metrics = _tool_metrics(meta, tool_id)
        tools.append(
            {
                "id": tool_id,
                "label": TOOL_LABELS[tool_id],
                "color": TOOL_COLORS[tool_id],
                "time_sec": time_sec,
                "time_human": _format_duration(time_sec),
                "cost_rub": cost_rub,
                "time_share": round(time_sec / total_elapsed, 4) if total_elapsed > 0 else 0.0,
                "cost_share": round(cost_rub / total_cost, 4) if total_cost > 0 else 0.0,
                **{k: v for k, v in metrics.items() if v is not None},
            }
        )

    if not tools and total_elapsed <= 0 and total_cost <= 0:
        return None

    cost_source_note = {
        "balance": "по разнице баланса ProxyAPI до/после шага 1",
        "records": "по записям llm_cost_records",
        "estimate": "оценка: ~1 ₽ за responses web_search, ~2,69 ₽ за chat preview",
        "none": "",
    }.get(cost_source, "")

    raw_unique = _int_meta(meta, "urls_raw_unique") or _int_meta(meta, "urls_raw_merged")
    funnel = {
        "raw_urls": raw_unique,
        "prefilter_rejected": _int_meta(meta, "urls_prefilter_rejected"),
        "sent_to_http": _int_meta(meta, "urls_sent_to_http"),
        "verified_total": _int_meta(meta, "verified_total"),
        "conversion_e2e_pct": meta.get("conversion_e2e_pct"),
        "conversion_http_pct": meta.get("conversion_http_pct"),
    }

    return {
        "total_time_sec": total_elapsed,
        "total_time_human": _format_duration(total_elapsed),
        "total_cost_rub": total_cost,
        "cost_source": cost_source,
        "cost_source_note": cost_source_note,
        "tools": tools,
        "funnel": funnel,
        "summary": {
            "iterations": _int_meta(meta, "iterations"),
            "stop_reason": str(meta.get("stop_reason") or "").strip() or None,
            "verified_total": _int_meta(meta, "verified_total"),
            "batch_size": max(1, _int_meta(meta, "batch_size") or 20),
            "collection_target": max(
                10,
                _int_meta(meta, "collection_target_pages") or _int_meta(meta, "target_max_candidates") or 15,
            ),
        },
    }
