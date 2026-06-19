"""Счётчики ProxyAPI web_search за один прогон шага 1 (контекст запроса)."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class Step1WebSearchStats:
    api_calls: int = 0
    response_calls: int = 0
    preview_calls: int = 0
    cache_hits: int = 0
    citation_urls: int = 0
    model_urls_dropped: int = 0
    service_cost_est_rub: float = 0.0
    token_cost_est_rub: float = 0.0
    api_cap: int | None = None
    cap_hit: bool = False
    empty_citation_streak: int = 0

    def to_meta(self) -> dict[str, int | bool | float | None]:
        total_cost = round(float(self.service_cost_est_rub) + float(self.token_cost_est_rub), 4)
        return {
            "web_search_api_calls": int(self.api_calls),
            "web_search_response_calls": int(self.response_calls),
            "web_search_preview_calls": int(self.preview_calls),
            "web_search_cache_hits": int(self.cache_hits),
            "web_search_citation_urls": int(self.citation_urls),
            "web_search_model_urls_dropped": int(self.model_urls_dropped),
            "web_search_service_cost_est_rub": round(float(self.service_cost_est_rub), 4),
            "web_search_token_cost_est_rub": round(float(self.token_cost_est_rub), 4),
            "web_search_cost_est_rub": total_cost,
            "web_search_api_cap": int(self.api_cap) if self.api_cap is not None else None,
            "web_search_api_cap_hit": bool(self.cap_hit),
        }

    def merge_into(self, meta: dict) -> None:
        _FLOAT_KEYS = frozenset(
            {
                "web_search_service_cost_est_rub",
                "web_search_token_cost_est_rub",
                "web_search_cost_est_rub",
            }
        )
        for key, value in self.to_meta().items():
            if value is None:
                continue
            if key == "web_search_api_cap_hit":
                meta[key] = bool(meta.get(key)) or bool(value)
                continue
            if key == "web_search_api_cap":
                prev = meta.get(key)
                if prev is None:
                    meta[key] = int(value)
                else:
                    meta[key] = max(int(prev), int(value))
                continue
            if key in _FLOAT_KEYS:
                meta[key] = round(float(meta.get(key, 0) or 0) + float(value), 4)
                continue
            meta[key] = int(meta.get(key, 0) or 0) + int(value)

    def apply_to_meta(self, meta: dict) -> None:
        """Подставляет счётчики текущего прогона (без суммирования при повторном persist)."""
        for key, value in self.to_meta().items():
            if value is None:
                continue
            meta[key] = value


_STATS: ContextVar[Step1WebSearchStats | None] = ContextVar("step1_web_search_stats", default=None)


def reset_step1_web_search_stats() -> Step1WebSearchStats:
    stats = Step1WebSearchStats()
    _STATS.set(stats)
    return stats


def current_step1_web_search_stats() -> Step1WebSearchStats | None:
    return _STATS.get()


def set_step1_web_search_api_cap(limit: int | None) -> None:
    stats = _STATS.get()
    if stats is None:
        return
    cap = max(0, int(limit)) if limit is not None else None
    stats.api_cap = cap if cap and cap > 0 else None


def step1_web_search_api_cap() -> int | None:
    stats = _STATS.get()
    if stats is None or stats.api_cap is None:
        return None
    return int(stats.api_cap)


def step1_web_search_api_cap_reached() -> bool:
    stats = _STATS.get()
    if stats is None:
        return False
    if stats.cap_hit:
        return True
    cap = stats.api_cap
    return cap is not None and stats.api_calls >= cap


def mark_step1_web_search_api_cap_hit() -> None:
    stats = _STATS.get()
    if stats is not None:
        stats.cap_hit = True


def record_web_search_api_call(*, kind: str = "responses") -> None:
    stats = _STATS.get()
    if stats is not None:
        stats.api_calls += 1
        if kind == "preview":
            stats.preview_calls += 1
        else:
            stats.response_calls += 1
    try:
        from app.services.step1_live_progress import sync_live_from_web_search_stats

        sync_live_from_web_search_stats()
    except Exception:
        pass


def consume_web_search_api_call(*, kind: str = "responses") -> bool:
    """True — вызов разрешён и учтён; False — достигнут потолок шага 1."""
    stats = _STATS.get()
    if stats is None:
        return True
    cap = stats.api_cap
    if cap is not None and stats.api_calls >= cap:
        stats.cap_hit = True
        return False
    record_web_search_api_call(kind=kind)
    if cap is not None and stats.api_calls >= cap:
        stats.cap_hit = True
    return True


def record_web_search_cache_hit() -> None:
    stats = _STATS.get()
    if stats is not None:
        stats.cache_hits += 1


def record_web_search_citation_urls(count: int, *, model_urls_dropped: int = 0) -> None:
    stats = _STATS.get()
    if stats is None:
        return
    stats.citation_urls += max(0, int(count))
    stats.model_urls_dropped += max(0, int(model_urls_dropped))
    try:
        from app.services.step1_live_progress import sync_live_from_web_search_stats

        sync_live_from_web_search_stats()
    except Exception:
        pass


def record_web_search_est_cost(*, service_rub: float, token_rub: float = 0.0) -> None:
    """Накопительная оценка ₽ за web_search: service (1 ₽/вызов) + токены gpt-4o-mini."""
    stats = _STATS.get()
    if stats is None:
        return
    stats.service_cost_est_rub += max(0.0, float(service_rub))
    stats.token_cost_est_rub += max(0.0, float(token_rub))


def refund_web_search_api_call(*, kind: str = "responses") -> None:
    """Вернуть слот cap, если вызов не дал citation URL (деньги ProxyAPI всё равно списаны)."""
    stats = _STATS.get()
    if stats is None or stats.api_calls <= 0:
        return
    stats.api_calls -= 1
    if kind == "preview" and stats.preview_calls > 0:
        stats.preview_calls -= 1
    elif stats.response_calls > 0:
        stats.response_calls -= 1
    cap = stats.api_cap
    if cap is not None and stats.api_calls < cap:
        stats.cap_hit = False
    try:
        from app.services.step1_live_progress import sync_live_from_web_search_stats

        sync_live_from_web_search_stats()
    except Exception:
        pass


def record_empty_citation_web_search(*, threshold: int = 4) -> bool:
    """
    Учёт подряд идущих пустых citation-ответов.
    Возвращает True, если пора временно не вызывать ProxyAPI web_search в этом прогоне.
    """
    stats = _STATS.get()
    if stats is None:
        return False
    streak = int(getattr(stats, "empty_citation_streak", 0) or 0) + 1
    stats.empty_citation_streak = streak
    return streak >= max(3, int(threshold))


def reset_empty_citation_streak() -> None:
    stats = _STATS.get()
    if stats is not None:
        stats.empty_citation_streak = 0
