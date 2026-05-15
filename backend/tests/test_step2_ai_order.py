from unittest.mock import MagicMock

from app.proxyapi_client import ProxyApiClient, _fallback_news_order, _parse_ordering_response


def test_parse_ordering_response_json_array():
    raw = '[{"candidate_id": 3, "output_position": 1, "ordering_reason": "Сильный заход"}]'
    parsed = _parse_ordering_response(raw)
    assert isinstance(parsed, list)
    assert parsed[0]["candidate_id"] == 3


def test_fallback_news_order_by_score():
    items = [
        {"candidate_id": 1, "total_score": 5},
        {"candidate_id": 2, "total_score": 12},
    ]
    out = _fallback_news_order(items)
    assert [x["candidate_id"] for x in out] == [2, 1]
    assert out[0]["output_position"] == 1


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
    assert len(out) == 5
    assert out[0]["candidate_id"] == 5
    assert out[0]["output_position"] == 1
