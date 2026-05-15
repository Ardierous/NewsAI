from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    model: str
    input_rub_per_1m: float
    output_rub_per_1m: float
    rationale: str


# Тарифы взяты из ProxyAPI pricing/list (OpenAI-модели, рубли за 1M токенов, НДС включен).
PRICING_RUB: dict[str, ModelPricing] = {
    "gpt-4.1": ModelPricing("gpt-4.1", 516.0, 2062.0, "Максимум качества, дороже."),
    "gpt-4.1-mini": ModelPricing("gpt-4.1-mini", 104.0, 413.0, "Оптимум качество/цена для редакторских задач."),
    "gpt-4.1-nano": ModelPricing("gpt-4.1-nano", 26.0, 104.0, "Самый дешёвый вариант для простых структурных задач."),
}


# Минимально достаточные рекомендации для текущего продукта (цель: полноценный дайджест с экономией).
# Прямой вызов ProxyAPI (без Crew) для оптимального порядка пятёрки на шаге 2.
STEP2_AI_ORDER_MODEL = "gpt-4.1-mini"

AGENT_MODEL_RECOMMENDATIONS: dict[str, str] = {
    "NewsResearchAgent": "gpt-4.1-mini",
    "SourceVerificationAgent": "gpt-4.1-mini",
    "ScoringAgent": "gpt-4.1-nano",
    "OrderingAgent": "gpt-4.1-nano",
    "AnalyticsAgent": "gpt-4.1-mini",
    "PlatformWriterAgent": "gpt-4.1-mini",
    "ImagePromptAgent": "gpt-4.1-nano",
    "QualityControlAgent": "gpt-4.1-nano",
}
