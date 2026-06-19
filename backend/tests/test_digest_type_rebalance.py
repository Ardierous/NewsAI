from app.services import digest_service as ds


def _item(url: str, *, press: bool = False, score: int = 5, title: str | None = None, curious_low: bool = False) -> dict:
    host = url.split("/")[2] if "/" in url else "x.com"
    return {
        "url": url,
        "title": title or f"Title {host}",
        "source": host,
        "tier": "Tier-2",
        "total_score": score,
        "curious_tone_low": curious_low,
        "headline_editorial_ok": True,
        "link_status": True,
        "is_aggregator": False,
        "category": "press_release" if press else "technology",
        "description": "press" if press else "news",
    }


def test_rebalance_curious_skips_serious_theme_quotas() -> None:
    pool = []
    for i in range(5):
        pool.append(
            {
                **_item(
                    f"https://publisher{i}.example.com/research/{i}",
                    score=10 - i,
                    title=f"Смешной фейл нейросети в эксперименте {i}",
                ),
                "material_form": "research",
                "verification_comment": "MATERIAL_FORM:research",
            }
        )
    serious_out = ds._rebalance_verified_pool(pool, target=5, digest_type="serious")
    curious_out = ds._rebalance_verified_pool(pool, target=5, digest_type="curious")
    assert sum(1 for x in serious_out if x.get("material_form") == "research") <= 2
    assert sum(1 for x in curious_out if x.get("material_form") == "research") == 5


def test_rebalance_curious_skips_press_quota() -> None:
    pool = [
        _item("https://ria.ru/a1", press=False, score=9, title="Смешной фейл нейросети в метро"),
        _item("https://ria.ru/a2", press=False, score=8, title="Абсурдный глюк чат-бота"),
        _item(
            "https://openai.com/news/pr1",
            press=True,
            score=9,
            title="OpenAI представила новую модель GPT",
        ),
        _item(
            "https://openai.com/news/pr2",
            press=True,
            score=8,
            title="Google анонсировала релиз Gemini",
        ),
        _item("https://vc.ru/ai/funny", press=False, score=7, title="Gemini сломала сайт разработчика"),
    ]
    out = ds._rebalance_verified_pool(pool, target=4, digest_type="curious")
    press_in = sum(1 for x in out if ds._is_substantive_press_for_pool(x))
    assert press_in == 0
    assert len(out) == 3


def test_rebalance_curious_prefers_entertaining_story_over_dry_ai_news() -> None:
    dry = _item(
        "https://habr.com/ru/news/dry",
        score=9,
        title="Google представила новую версию Gemini для разработчиков",
        curious_low=True,
    )
    dry["tier"] = "Curious-T2"
    funny = _item(
        "https://vc.ru/ai/funny",
        score=5,
        title="Gemini сломала сайт, удалив 30 тысяч строк кода",
    )
    funny["tier"] = "Curious-T1"
    out = ds._rebalance_verified_pool([dry, funny], target=1, digest_type="curious")
    assert out[0]["url"] == funny["url"]


def test_rebalance_curious_prefers_tier1_when_tone_equal() -> None:
    t1 = _item("https://www.popmech.ru/a", score=6, title="Смешной фейл нейросети")
    t1["tier"] = "Curious-T1"
    t2 = _item("https://habr.com/ru/news/b", score=6, title="Тоже смешной фейл нейросети")
    t2["tier"] = "Curious-T2"
    out = ds._rebalance_verified_pool([t2, t1], target=1, digest_type="curious")
    assert out[0]["url"] == t1["url"]


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
