"""Настройки пайплайна из backend/app/pipeline_settings.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_settings import SettingsConfigDict

from app import config as config_mod
from app.pipeline_settings import (
    normalize_pipeline_config,
    pipeline_settings_flat,
    read_pipeline_config,
)


def _settings_without_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXYAPI_API_KEY", "test-key")
    monkeypatch.setattr(
        config_mod.Settings,
        "model_config",
        SettingsConfigDict(env_file_encoding="utf-8", extra="ignore"),
    )
    config_mod.clear_settings_cache()


def test_normalize_pipeline_config_clamps_hard_above_soft(tmp_path: Path) -> None:
    raw = {
        "version": 1,
        "step1": {"soft_time_limit_sec": 600, "hard_time_limit_sec": 120},
    }
    cfg = normalize_pipeline_config(raw)
    assert cfg["step1"]["soft_time_limit_sec"] == 600
    assert cfg["step1"]["hard_time_limit_sec"] == 600


def test_read_pipeline_config_from_custom_file(tmp_path: Path) -> None:
    path = tmp_path / "pipeline_settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "step1": {"batch_size": 7, "hard_time_limit_sec": 900},
                "logging": {"level": "DEBUG"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    flat = pipeline_settings_flat(path)
    assert flat["step1_batch_size"] == 7
    assert flat["step1_hard_time_limit_sec"] == 900
    assert flat["log_level"] == "DEBUG"


def test_settings_use_json_when_env_not_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "pipeline_settings.json"
    path.write_text(
        json.dumps({"version": 1, "step1": {"batch_size": 11, "verify_workers": 9}}, ensure_ascii=False),
        encoding="utf-8",
    )
    _settings_without_dotenv(monkeypatch)
    monkeypatch.setattr(config_mod, "pipeline_settings_flat", lambda: pipeline_settings_flat(path))
    settings = config_mod.get_settings()
    assert settings.step1_batch_size == 11
    assert settings.step1_verify_workers == 9
    config_mod.clear_settings_cache()


def test_settings_enable_web_fetch_from_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "pipeline_settings.json"
    path.write_text(json.dumps({"version": 1, "web": {"enable_fetch": True}}, ensure_ascii=False), encoding="utf-8")
    _settings_without_dotenv(monkeypatch)
    monkeypatch.setattr(config_mod, "pipeline_settings_flat", lambda: pipeline_settings_flat(path))
    settings = config_mod.get_settings()
    assert settings.enable_web_fetch is True
    config_mod.clear_settings_cache()


def test_env_overrides_enable_web_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "pipeline_settings.json"
    path.write_text(json.dumps({"version": 1, "web": {"enable_fetch": True}}, ensure_ascii=False), encoding="utf-8")
    _settings_without_dotenv(monkeypatch)
    monkeypatch.setenv("ENABLE_WEB_FETCH", "false")
    monkeypatch.setattr(config_mod, "pipeline_settings_flat", lambda: pipeline_settings_flat(path))
    settings = config_mod.get_settings()
    assert settings.enable_web_fetch is False
    config_mod.clear_settings_cache()


def test_env_overrides_pipeline_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "pipeline_settings.json"
    path.write_text(
        json.dumps({"version": 1, "step1": {"batch_size": 11}}, ensure_ascii=False),
        encoding="utf-8",
    )
    _settings_without_dotenv(monkeypatch)
    monkeypatch.setenv("STEP1_BATCH_SIZE", "42")
    monkeypatch.setattr(config_mod, "pipeline_settings_flat", lambda: pipeline_settings_flat(path))
    settings = config_mod.get_settings()
    assert settings.step1_batch_size == 42
    config_mod.clear_settings_cache()
