"""Фильтры шага 1: единый «Дайджест ИИ» использует профиль serious."""

import json

import pytest

from app.services import step1_filter_settings as settings_mod
from app.services.digest_type_policy import normalize_digest_type
from app.services.step1_filter_settings import (
    load_step1_filter_settings,
    save_step1_filter_settings,
)
from app.services.step1_filters import filter_def_applies_to_digest_type, step1_filter_catalog_payload


@pytest.fixture
def step1_settings_file(tmp_path, monkeypatch):
    path = tmp_path / "step1_filter_settings.json"
    monkeypatch.setattr(settings_mod, "_STEP1_FILTER_SETTINGS_PATH", path)
    return path


def test_unified_ai_uses_serious_profile_not_curious_only_filter(step1_settings_file):
    boot = settings_mod._bootstrap_filter_config()
    path = step1_settings_file
    path.write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")

    for dtype in ("serious", "curious", None):
        profile_key = settings_mod.step1_filter_profile_key(dtype)
        assert profile_key == "serious"
        settings = load_step1_filter_settings(dtype)
        ids = {f["id"] for f in settings["filters"]}
        assert "off_topic_not_curious" not in ids
        assert filter_def_applies_to_digest_type("off_topic_not_curious", dtype) is False


def test_legacy_curious_section_still_in_raw_file(step1_settings_file):
    boot = settings_mod._bootstrap_filter_config()
    step1_settings_file.write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
    raw = json.loads(step1_settings_file.read_text(encoding="utf-8"))
    curious_ids = {f["id"] for f in raw["curious"]["filters"]}
    assert "off_topic_not_curious" in curious_ids


def test_saving_serious_does_not_change_curious_section_in_file(step1_settings_file):
    boot = settings_mod._bootstrap_filter_config()
    path = step1_settings_file
    path.write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")

    save_step1_filter_settings(
        {
            "version": 2,
            "min_discovered_pages": 42,
            "min_collection_iterations": 3,
            "filters": [{"id": "invalid_url", "enabled": True, "order": 1}],
        },
        digest_type="serious",
    )

    assert load_step1_filter_settings("serious")["min_discovered_pages"] == 42
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["curious"]["min_discovered_pages"] == 10


def test_published_date_undefined_in_unified_serious_profile(step1_settings_file):
    boot = settings_mod._bootstrap_filter_config()
    step1_settings_file.write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
    serious_ids = {f["id"] for f in load_step1_filter_settings("serious")["filters"]}
    assert "published_date_undefined" in serious_ids


def test_catalog_payload_unified_uses_serious_scope():
    for dtype in ("serious", "curious"):
        catalog = step1_filter_catalog_payload(dtype)
        ids = {x["id"] for x in catalog}
        assert "off_topic_not_curious" not in ids


def test_normalize_digest_type_always_serious():
    assert normalize_digest_type("curious") == "serious"
    assert normalize_digest_type("serious") == "serious"
    assert normalize_digest_type(None) == "serious"


def test_v1_file_migrates_to_separate_profiles(step1_settings_file):
    step1_settings_file.write_text(
        json.dumps(
            {
                "version": 1,
                "min_discovered_pages": 28,
                "min_collection_iterations": 6,
                "filters": [
                    {"id": "invalid_url", "enabled": True, "order": 1},
                    {"id": "off_topic_not_curious", "enabled": True, "order": 2},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    serious = load_step1_filter_settings("serious")
    from app.services.step1_filter_settings import _load_raw_settings_file

    raw = _load_raw_settings_file()
    curious = raw["curious"]

    assert serious["min_discovered_pages"] == 28
    assert curious["min_discovered_pages"] == 28
    assert not any(f["id"] == "off_topic_not_curious" for f in serious["filters"])
    assert any(f["id"] == "off_topic_not_curious" for f in curious["filters"])
