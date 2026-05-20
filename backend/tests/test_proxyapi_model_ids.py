from app.crew.model_policy import proxyapi_chat_model, proxyapi_litellm_model


def test_litellm_model_adds_openai_prefix():
    assert proxyapi_litellm_model("gpt-4.1-mini") == "openai/gpt-4.1-mini"
    assert proxyapi_litellm_model("openai/gpt-4.1") == "openai/gpt-4.1"


def test_chat_model_same_as_litellm():
    assert proxyapi_chat_model("gpt-4.1-nano") == "openai/gpt-4.1-nano"
