from app.services import digest_service as ds


def _item(url: str, *, press: bool = False, score: int = 5) -> dict:
    host = url.split("/")[2] if "/" in url else "x.com"
    return {
        "url": url,
        "title": f"Title {host}",
        "source": host,
        "tier": "Tier-2",
        "total_score": score,
        "headline_editorial_ok": True,
        "link_status": True,
        "is_aggregator": False,
        "category": "press_release" if press else "technology",
        "description": "press" if press else "news",
    }


def test_rebalance_curious_skips_press_quota() -> None:
    pool = [
        _item("https://ria.ru/a1", press=False, score=9),
        _item("https://ria.ru/a2", press=False, score=8),
        _item("https://openai.com/news/pr1", press=True, score=9),
        _item("https://openai.com/news/pr2", press=True, score=8),
        _item("https://vc.ru/ai/funny", press=False, score=7),
    ]
    out = ds._rebalance_verified_pool(pool, target=4, digest_type="curious")
    press_in = sum(1 for x in out if ds._is_substantive_press_for_pool(x))
    assert press_in == 0
    assert len(out) == 4


def test_titles_near_duplicates_true_for_same_story() -> None:
    a = "OpenAI представила GPT-5.5 для корпоративных клиентов"
    b = "OpenAI представила GPT-5.5: релиз для корпоративных клиентов"
    assert ds._titles_are_near_duplicates(a, b) is True


def test_titles_near_duplicates_true_for_vedomosti_pair() -> None:
    a = "«Элемент» предложит нормы внедрения ИИ для микроэлектронных предприятий"
    b = "«Элемент» предложил создать ИИ-платформу для управления микроэлектроникой"
    assert ds._titles_are_near_duplicates(a, b) is True


def test_titles_near_duplicates_false_for_different_story() -> None:
    a = "Google выпустила новую модель Gemini для Workspace"
    b = "Meta открыла Llama Guard 4 для модерации контента"
    assert ds._titles_are_near_duplicates(a, b) is False


def test_reader_fallback_allowed_for_regular_external_host() -> None:
    assert ds._reader_fallback_allowed("https://www.vedomosti.ru/technology/news/2026/05/25/1199847-element-predlozhil") is True
    assert ds._reader_fallback_allowed("http://localhost:8000/health") is False
