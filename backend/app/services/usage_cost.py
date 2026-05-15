"""Оценка стоимости запроса по usage из ответа OpenAI/ProxyAPI."""
from __future__ import annotations

from typing import Any

from app.crew.model_policy import PRICING_RUB, ModelPricing

# Модели вне PRICING_RUB (типичные тарифы ProxyAPI, ₽ за 1M токенов, НДС).
_EXTRA_PRICING: dict[str, ModelPricing] = {
    "gpt-4o-mini": ModelPricing("gpt-4o-mini", 36.0, 144.0, "Веб-поиск и лёгкие задачи."),
    "gpt-4o": ModelPricing("gpt-4o", 360.0, 1440.0, "Тяжёлые задачи."),
    # Тарифы ProxyAPI для GPT Image (₽ / 1M токенов, ориентир на gpt-image-2).
    "gpt-image-1": ModelPricing("gpt-image-1", 1520.0, 9100.0, "Генерация обложек по токенам."),
    "gpt-image-2": ModelPricing("gpt-image-2", 1520.0, 9100.0, "Генерация обложек по токенам."),
}


def _pricing_for_model(model: str | None) -> ModelPricing | None:
    if not model:
        return None
    key = model.split("/")[-1].strip().lower()
    return PRICING_RUB.get(key) or _EXTRA_PRICING.get(key)


def estimate_cost_rub_from_usage(model: str | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    pricing = _pricing_for_model(model)
    if pricing is None:
        return None
    if pricing.input_rub_per_1m <= 0 and pricing.output_rub_per_1m <= 0:
        return None
    cost = (max(0, prompt_tokens) / 1_000_000.0) * pricing.input_rub_per_1m
    cost += (max(0, completion_tokens) / 1_000_000.0) * pricing.output_rub_per_1m
    return round(cost, 6) if cost > 0 else None


def extract_token_usage(obj: Any) -> tuple[int, int] | None:
    """prompt_tokens, completion_tokens из ответа OpenAI SDK или dict."""
    if obj is None:
        return None
    usage = obj
    if not isinstance(usage, dict):
        usage = getattr(obj, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        pt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        ct = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    else:
        pt = int(getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None) or 0)
        ct = int(getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None) or 0)
    if pt <= 0 and ct <= 0:
        return None
    return pt, ct


def estimate_cost_rub_from_response(model: str | None, response: Any) -> float | None:
    tokens = extract_token_usage(response)
    if tokens is None:
        return None
    return estimate_cost_rub_from_usage(model, tokens[0], tokens[1])


def estimate_cost_rub_from_crew_usage(model: str | None, usage: Any) -> float | None:
    """Оценка по token_usage из CrewAI после kickoff."""
    if usage is None:
        return None
    if isinstance(usage, dict):
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
    else:
        pt = int(getattr(usage, "prompt_tokens", None) or 0)
        ct = int(getattr(usage, "completion_tokens", None) or 0)
    if pt <= 0 and ct <= 0:
        return None
    return estimate_cost_rub_from_usage(model, pt, ct)


def estimate_cost_rub_from_image_response(model: str | None, response: Any) -> float | None:
    usage = getattr(response, "usage", None) if response is not None else None
    if usage is None:
        return None
    pt = int(getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None) or 0)
    ct = int(getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None) or 0)
    if pt <= 0 and ct <= 0:
        return None
    return estimate_cost_rub_from_usage(model, pt, ct)
