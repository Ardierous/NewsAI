from app.crew.workflow import complete_analytics_result


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
