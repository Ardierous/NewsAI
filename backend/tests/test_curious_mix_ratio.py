"""Ориентир доли курьёзных в пуле по ползунку шага 0 (N → ~N×10%)."""

from app.services import digest_service as ds
from app.services.digest_type_policy import curious_mix_target_count, resolve_curious_mix_ratio
from app.services.step1_candidate_policy import (
    CURIOUS_MIX_SEED_MARKER,
    classify_editorial_angle,
    reconcile_pool_editorial_angles,
)


def test_resolve_curious_mix_ratio_maps_slider_to_percent() -> None:
    assert resolve_curious_mix_ratio(1) == 0.1
    assert resolve_curious_mix_ratio(5) == 0.5
    assert resolve_curious_mix_ratio(7) == 0.7
    assert resolve_curious_mix_ratio(10) == 0.9
    assert curious_mix_target_count(20, 7) == 14
    assert curious_mix_target_count(20, 5) == 10


def _item(url: str, *, angle: str, score: int = 6) -> dict:
    host = url.split("/")[2]
    return {
        "url": url,
        "title": f"{'Смешной' if angle == 'curious' else 'Деловой'} ИИ-материал {host}",
        "source": host,
        "tier": "Tier-2",
        "total_score": score,
        "headline_editorial_ok": True,
        "link_status": True,
        "is_aggregator": False,
        "category": "technology",
        "description": "нейросеть чат-бот" + (" фейл вирусный мем" if angle == "curious" else " регулирование внедрение"),
        "editorial_angle": angle,
        "verification_comment": f"EDITORIAL_ANGLE:{angle}",
        "article_excerpt": "текст статьи про ИИ",
    }


def test_rebalance_prefers_about_70_percent_curious_when_slider_7() -> None:
    pool = [
        _item(f"https://curious{i}.example/a", angle="curious", score=5)
        for i in range(16)
    ] + [
        _item(f"https://serious{i}.example/a", angle="serious", score=9)
        for i in range(16)
    ]
    out = ds._rebalance_verified_pool(
        pool,
        target=20,
        digest_type="serious",
        per_host_cap=3,
        curious_mix_ratio=0.7,
    )
    curious_n = sum(1 for x in out if x.get("editorial_angle") == "curious")
    # Мягкий ориентир: около 70% (±2 карточки).
    assert 12 <= curious_n <= 16
    assert len(out) == 20


def test_rebalance_prefers_about_50_percent_curious_when_slider_5() -> None:
    pool = [
        _item(f"https://curious{i}.example/b", angle="curious", score=5)
        for i in range(12)
    ] + [
        _item(f"https://serious{i}.example/b", angle="serious", score=9)
        for i in range(12)
    ]
    out = ds._rebalance_verified_pool(
        pool,
        target=20,
        digest_type="serious",
        per_host_cap=3,
        curious_mix_ratio=0.5,
    )
    curious_n = sum(1 for x in out if x.get("editorial_angle") == "curious")
    assert 8 <= curious_n <= 12
    assert len(out) == 20


def test_classify_editorial_angle_marks_curious_mix_seed_at_high_slider() -> None:
    item = {
        "url": "https://www.vedomosti.ru/technology/articles/2026/01/01/test",
        "title": "Нейросеть перепутала заказ и удалила код",
        "description": "пользователи пожаловались на странный фейл",
        "tier": "Tier-1",
        "seed_marker": CURIOUS_MIX_SEED_MARKER,
        "curious_tone_score": 3,
    }
    assert classify_editorial_angle(item, curious_mix_ratio=0.9) == "curious"


def test_reconcile_pool_labels_about_90_percent_curious_when_slider_9() -> None:
    pool = [
        {
            "url": f"https://serious{i}.example/a",
            "title": f"Компания представила новую модель ИИ {i}",
            "description": "регулирование внедрение партнёрство",
            "tier": "Tier-2",
            "verification_comment": "EDITORIAL_ANGLE:serious",
            "editorial_angle": "serious",
            "article_excerpt": "официальный анонс",
        }
        for i in range(2)
    ] + [
        {
            "url": f"https://curious{i}.example/b",
            "title": f"Смешной фейл нейросети {i}",
            "description": "пользователи возмутились вирусный мем",
            "tier": "Curious-T1",
            "seed_marker": CURIOUS_MIX_SEED_MARKER,
            "curious_tone_score": 5,
            "verification_comment": "EDITORIAL_ANGLE:serious",
            "editorial_angle": "serious",
            "article_excerpt": "абсурд и кринж",
        }
        for i in range(18)
    ]
    reconcile_pool_editorial_angles(pool, 10, digest_type="serious")
    curious_n = sum(1 for x in pool if x.get("editorial_angle") == "curious")
    assert 16 <= curious_n <= 18
