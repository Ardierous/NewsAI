from unittest.mock import MagicMock

from app.proxyapi_client import ProxyApiClient, _fallback_news_order, _parse_ordering_payload, _parse_ordering_response


def test_parse_ordering_response_json_array():
    raw = '[{"candidate_id": 3, "output_position": 1, "ordering_reason": "Сильный заход"}]'
    parsed = _parse_ordering_response(raw)
    assert isinstance(parsed, list)
    assert parsed[0]["candidate_id"] == 3


def test_fallback_news_order_opens_with_accessible_story():
    items = [
        {
            "candidate_id": 1,
            "total_score": 20,
            "title": "ЕС ужесточил регулирование ИИ и инвестиции в инфраструктуру дата-центров",
        },
        {
            "candidate_id": 2,
            "total_score": 11,
            "title": "Как обычный пользователь может проверить ответ чат-бота в работе",
        },
        {"candidate_id": 3, "total_score": 9, "title": "Исследовательская статья про препринт arxiv"},
        {"candidate_id": 4, "total_score": 8, "title": "Компания объявила IPO и рост выручки"},
        {"candidate_id": 5, "total_score": 7, "title": "Забавный фейл голосового помощника в быту"},
    ]
    out = _fallback_news_order(items)
    assert out[0]["candidate_id"] == 2
    assert out[-1]["candidate_id"] == 5


def test_step2_order_prompt_puts_ordinary_reader_first():
    from app.services.digest_type_policy import step2_order_system_prompt

    prompt = step2_order_system_prompt("serious")
    low = prompt.lower()
    assert "обычный человек" in low or "рядового" in low
    assert "не ставь первой" in low
    assert "регулирование" in low


def test_fallback_news_order_by_score():
    items = [
        {"candidate_id": 1, "total_score": 5},
        {"candidate_id": 2, "total_score": 12},
    ]
    out = _fallback_news_order(items)
    assert [x["candidate_id"] for x in out] == [2, 1]
    assert out[0]["output_position"] == 1


def test_parse_ordering_response_json_object():
    raw = (
        '{"overall_rationale": "Сильный заход и ритм.", '
        '"items": [{"candidate_id": 3, "output_position": 1, "ordering_reason": "Сильный заход"}]}'
    )
    items, rationale = _parse_ordering_payload(raw)
    assert items[0]["candidate_id"] == 3
    assert "заход" in rationale.lower()


def test_suggest_news_order_validates_ids():
    client = ProxyApiClient.__new__(ProxyApiClient)
    client.chat = MagicMock(
        return_value=(
            "["
            '{"candidate_id": 5, "output_position": 1, "ordering_reason": "Заход"},'
            '{"candidate_id": 3, "output_position": 2, "ordering_reason": "Ритм"},'
            '{"candidate_id": 1, "output_position": 3, "ordering_reason": "Середина"},'
            '{"candidate_id": 4, "output_position": 4, "ordering_reason": "Нарастание"},'
            '{"candidate_id": 2, "output_position": 5, "ordering_reason": "Финал"}'
            "]"
        )
    )
    items = [{"candidate_id": i, "title": f"N{i}", "total_score": i} for i in range(1, 6)]
    out = client.suggest_news_order(items, digest_type="serious", model="gpt-4.1-mini")
    assert len(out["items"]) == 5
    assert out["items"][0]["candidate_id"] == 5
    assert out["items"][0]["output_position"] == 1
