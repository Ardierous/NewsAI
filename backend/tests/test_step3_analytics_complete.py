from app.crew.workflow import (
    _ANALYTICS_EDITORIAL,
    _READER_COPY_EDITORIAL,
    complete_analytics_result,
    complete_reader_descriptions_result,
)


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
                "comment": "Заметка 1",
                "analysis": "Анализ 1",
            },
            {
                "candidate_id": 3,
                "essence": "Суть 3",
                "comment": "Заметка 3",
                "analysis": "Анализ 3",
            },
            {
                "candidate_id": 4,
                "essence": "Суть 4",
                "comment": "Заметка 4",
                "analysis": "Анализ 4",
            },
            {
                "candidate_id": 5,
                "essence": "Суть 5",
                "comment": "Заметка 5",
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
                "comment": "Ключевая суть: пометка редактора.",
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


def test_analytics_prompt_is_editor_facing():
    prompt = _ANALYTICS_EDITORIAL.lower()
    assert "для редактора" in prompt
    assert "article_excerpt" in prompt
    assert "analysis_covers_six_angles" in prompt
    assert "editor_not_reader_copy" in prompt
    assert "готовое описание новости для публикации" not in prompt


def test_analytics_prompt_requires_overall_analysis_on_step3():
    prompt = _ANALYTICS_EDITORIAL.lower()
    assert "overall_analysis сформируй здесь же, на шаге аналитики" in prompt
    assert "всех 5 выбранных новостей" in prompt
    assert "общую линию выпуска" in prompt
    assert "overall_analysis_from_selected_five" in prompt


def test_reader_copy_prompt_requires_three_to_four_sentences():
    prompt = _READER_COPY_EDITORIAL.lower()
    assert "3–4 предложения" in prompt
    assert "article_excerpt" in prompt
    assert "reader_text_3_4_sentences" in prompt


def test_complete_reader_descriptions_fills_missing():
    items = [
        {
            "candidate_id": 1,
            "title": "A",
            "essence": "Суть A.",
            "analysis": "Анализ A. Второе предложение.",
            "article_excerpt": "Текст статьи про ИИ.",
        },
        {
            "candidate_id": 2,
            "title": "B",
            "essence": "Суть B.",
            "analysis": "Анализ B.",
            "article_excerpt": "Другой текст.",
        },
    ]
    partial = {
        "items": [
            {"candidate_id": 1, "reader_text": "Первое. Второе. Третье."},
        ],
        "self_check": [],
    }
    out = complete_reader_descriptions_result(partial, items)
    assert len(out["items"]) == 2
    assert out["items"][0]["reader_text"].count(".") >= 2
    assert out["items"][1]["reader_text"]
