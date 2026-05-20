from app.crew.workflow import _ANALYTICS_EDITORIAL, complete_analytics_result


def test_complete_analytics_result_fills_missing_item():
    selected = [
        {"candidate_id": 1, "title": "Новость A", "source": "S", "url": "https://a.example", "published_at": ""},
        {"candidate_id": 2, "title": "Новость B", "source": "S", "url": "https://b.example", "published_at": ""},
        {"candidate_id": 3, "title": "Новость C", "source": "S", "url": "https://c.example", "published_at": ""},
        {"candidate_id": 4, "title": "Новость D", "source": "S", "url": "https://d.example", "published_at": ""},
        {"candidate_id": 5, "title": "Новость E", "source": "S", "url": "https://e.example", "published_at": ""},
    ]
    partial = {
        "items": [
            {
                "candidate_id": 1,
                "essence": "Суть 1",
                "comment": "Коммент 1",
                "analysis": "Анализ 1",
            },
            {
                "candidate_id": 3,
                "essence": "Суть 3",
                "comment": "Коммент 3",
                "analysis": "Анализ 3",
            },
            {
                "candidate_id": 4,
                "essence": "Суть 4",
                "comment": "Коммент 4",
                "analysis": "Анализ 4",
            },
            {
                "candidate_id": 5,
                "essence": "Суть 5",
                "comment": "Коммент 5",
                "analysis": "Анализ 5",
            },
        ],
        "overall_analysis": "Общий контекст",
        "hashtags": ["#ИИ"],
        "self_check": [],
    }
    out = complete_analytics_result(partial, selected)
    assert len(out["items"]) == 5
    ids = [int(x["candidate_id"]) for x in out["items"]]
    assert ids == [1, 2, 3, 4, 5]
    missing = next(x for x in out["items"] if x["candidate_id"] == 2)
    assert "Новость B" in missing["essence"]
    assert missing["comment"]
    assert missing["analysis"]


def test_complete_analytics_strips_service_markers():
    selected = [
        {"candidate_id": 1, "title": "Тест", "source": "S", "url": "https://a.example", "published_at": ""},
    ]
    partial = {
        "items": [
            {
                "candidate_id": 1,
                "essence": "Tier-1: суть",
                "comment": "Ключевая суть: для читателя два предложения. Второе предложение.",
                "analysis": "Анализ",
            },
        ],
        "overall_analysis": "Tier-2 общий вывод",
        "hashtags": ["#ИИ"],
        "self_check": [],
    }
    out = complete_analytics_result(partial, selected)
    item = out["items"][0]
    assert "Tier" not in item["essence"]
    assert "ключевая суть" not in item["comment"].lower()
    assert "Tier" not in out["overall_analysis"]


def test_analytics_prompt_requires_publication_ready_comment():
    prompt = _ANALYTICS_EDITORIAL.lower()
    assert "готовое описание новости для публикации" in prompt
    assert "сервер не должен чинить стиль" in prompt
    assert "source_description" in prompt
    assert "comment_2_3_sentences" in prompt
    assert "краткая суть новости" in prompt
    assert "почему это важно" in prompt
    assert "для кого это важно" in prompt
    assert "читателей дайджеста" in prompt
    assert "какую пользу или вред" in prompt


def test_analytics_prompt_requires_overall_analysis_on_step3():
    prompt = _ANALYTICS_EDITORIAL.lower()
    assert "overall_analysis сформируй здесь же, на шаге аналитики" in prompt
    assert "всех 5 выбранных новостей" in prompt
    assert "общую линию выпуска" in prompt
    assert "overall_analysis_from_selected_five" in prompt
