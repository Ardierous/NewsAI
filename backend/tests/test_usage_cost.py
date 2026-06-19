from types import SimpleNamespace

from app.services.usage_cost import (
    estimate_cost_rub_from_crew_usage,
    estimate_cost_rub_from_image_response,
    estimate_cost_rub_from_response,
    estimate_cost_rub_from_usage,
    estimate_proxyapi_request_fee_rub,
)


def test_estimate_cost_gpt4o_mini():
    cost = estimate_cost_rub_from_usage("gpt-4o-mini", 10_000, 2_000)
    assert cost is not None
    assert 0.5 < cost < 2.0


def test_web_search_request_fee_responses():
    cost = estimate_cost_rub_from_usage("gpt-4o-mini", 0, 0, kind="responses.web_search")
    assert cost == 1.0


def test_web_search_preview_request_fee():
    assert estimate_proxyapi_request_fee_rub("chat.web_search_preview") == 2.69


def test_estimate_cost_from_openai_response():
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500))
    cost = estimate_cost_rub_from_response("gpt-4.1-mini", response)
    assert cost is not None
    assert cost > 0


def test_estimate_cost_from_crew_usage_dict():
    usage = {"prompt_tokens": 50_000, "completion_tokens": 8_000}
    cost = estimate_cost_rub_from_crew_usage("gpt-4.1-mini", usage)
    assert cost is not None
    assert cost > 0


def test_estimate_cost_from_image_response():
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=12_000, output_tokens=3_000))
    cost = estimate_cost_rub_from_image_response("gpt-image-1", response)
    assert cost is not None
    assert cost > 10
