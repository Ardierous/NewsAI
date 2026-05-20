from app.services.step1_candidate_policy import (
    has_substantive_news_event_signal,
    is_product_tool_landing_url,
    is_substantive_press_for_pool,
    looks_like_product_tool_promo,
)


def test_product_tool_url_detected():
    assert is_product_tool_landing_url("https://example.com/products/ai-assistant")
    assert is_product_tool_landing_url("https://vendor.io/pricing/enterprise")
    assert not is_product_tool_landing_url("https://rbc.ru/technology/ai/123")


def test_tool_promo_headline_rejected_without_news_signal():
    item = {
        "url": "https://vendor.io/blog/new-feature",
        "title": "Компания launches new AI assistant tool for marketers",
        "description": "Try our free trial and sign up today.",
    }
    assert looks_like_product_tool_promo(item)
    assert not has_substantive_news_event_signal(item)
    assert not is_substantive_press_for_pool(item)


def test_corporate_press_with_deployment_is_substantive():
    item = {
        "url": "https://businesswire.com/news/home/123",
        "source": "businesswire.com",
        "title": "Acme announces national AI deployment partnership with government",
        "description": "Press release: $50 million investment plan for federal AI program.",
    }
    assert is_substantive_press_for_pool(item)
    assert not looks_like_product_tool_promo(item)
