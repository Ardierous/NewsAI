"""Фильтры шага 1 не смешиваются между serious и curious."""

import json

import pytest

from app.services import step1_filter_settings as settings_mod
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


def test_curious_only_filter_not_in_serious_profile(step1_settings_file):
    boot = settings_mod._bootstrap_filter_config()
    path = step1_settings_file
    path.write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")

    serious = load_step1_filter_settings("serious")
    curious = load_step1_filter_settings("curious")
    serious_ids = {f["id"] for f in serious["filters"]}
    curious_ids = {f["id"] for f in curious["filters"]}

    assert "off_topic_not_curious" not in serious_ids
    assert "off_topic_not_curious" in curious_ids
    assert filter_def_applies_to_digest_type("off_topic_not_curious", "serious") is False
    assert filter_def_applies_to_digest_type("off_topic_not_curious", "curious") is True


def test_saving_serious_does_not_change_curious_min_pages(step1_settings_file):
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
    assert load_step1_filter_settings("curious")["min_discovered_pages"] == 15


def test_published_date_undefined_only_for_serious(step1_settings_file):
    boot = settings_mod._bootstrap_filter_config()
    step1_settings_file.write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
    curious_ids = {f["id"] for f in load_step1_filter_settings("curious")["filters"]}
    serious_ids = {f["id"] for f in load_step1_filter_settings("serious")["filters"]}
    assert "published_date_undefined" not in curious_ids
    assert "published_date_undefined" in serious_ids


def test_catalog_payload_scoped_by_digest_type():
    serious_catalog = step1_filter_catalog_payload("serious")
    curious_catalog = step1_filter_catalog_payload("curious")
    serious_ids = {x["id"] for x in serious_catalog}
    curious_ids = {x["id"] for x in curious_catalog}

    assert "off_topic_not_curious" not in serious_ids
    assert "off_topic_not_curious" in curious_ids
    assert len(curious_ids) >= len(serious_ids)


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
    curious = load_step1_filter_settings("curious")

    assert serious["min_discovered_pages"] == 28
    assert curious["min_discovered_pages"] == 15
    assert not any(f["id"] == "off_topic_not_curious" for f in serious["filters"])
    assert any(f["id"] == "off_topic_not_curious" for f in curious["filters"])
