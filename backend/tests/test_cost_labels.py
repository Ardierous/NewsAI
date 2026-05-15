from app.services.cost_labels import agent_title_ru, enrich_llm_cost_row, operation_title_ru


def test_agent_title_ru():
    assert "кандидат" in agent_title_ru("NewsResearchAgent").lower()


def test_operation_title_ru():
    assert operation_title_ru("step_1_collect_pool").startswith("Сбор")


def test_enrich_llm_cost_row():
    row = enrich_llm_cost_row(
        {
            "step": "step_1",
            "agent_name": "NewsResearchAgent",
            "request_label": "step_1_collect_pool",
            "model": "gpt-4.1-mini",
            "cost_rub": 1.5,
        }
    )
    assert row["agent_title_ru"]
    assert row["operation_title_ru"]
    assert row["step_title_ru"] == "Шаг 1"
