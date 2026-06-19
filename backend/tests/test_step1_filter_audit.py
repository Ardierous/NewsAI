from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.pipeline_settings import normalize_pipeline_config, pipeline_settings_flat
from app.services.curious_tone import explain_curious_gates
from app.services.step1_filter_audit import Step1CuriousToneAudit, configure_step1_filter_audit


def test_explain_curious_gates_product_launch_sidebar() -> None:
    title = "Xiaomi представила ИИ для программистов"
    corpus = "курьёз смешной фейл " * 30
    expl = explain_curious_gates(title, corpus)
    assert expl["pool_pass"] is False
    assert expl["pool_reason"] in {"sidebar_false_positive", "serious_title_no_positive", "dry_serious"}


def test_explain_curious_gates_funny_lead() -> None:
    title = "ИИ-радиостанция начала вещать бред ночью"
    corpus = "Слушатели смеются над абсурдными ответами нейросети — вирусный кринж."
    expl = explain_curious_gates(title, corpus)
    assert expl["pool_pass"] is True
    assert expl["tone_score"] >= 1


def test_curious_tone_audit_respects_max_events(caplog: pytest.LogCaptureFixture) -> None:
    settings = SimpleNamespace(
        step1_curious_tone_log_enabled=True,
        step1_curious_tone_log_accept=True,
        step1_curious_tone_log_reject=True,
        step1_curious_tone_log_low_tone=True,
        step1_curious_tone_log_max_events=2,
        step1_curious_tone_title_preview_chars=80,
        step1_curious_tone_corpus_preview_chars=40,
        step1_curious_tone_include_signals=True,
    )
    audit = Step1CuriousToneAudit(settings)
    audit.begin_run(99)
    for i in range(5):
        audit.record(
            url=f"https://vc.ru/ai/{i}",
            title=f"Смешной фейл {i}",
            corpus="абсурд",
            stage="verify",
            outcome="accept",
        )
    summary = audit.flush_summary()
    assert summary["events_logged"] == 2
    assert summary["digest_id"] == 99


def test_pipeline_settings_expose_step1_logging(tmp_path) -> None:
    import json
    from pathlib import Path

    path = Path(tmp_path) / "pipeline_settings.json"
    path.write_text(
        json.dumps(
            {
                "logging": {
                    "step1": {
                        "filter_stats_every_n": 10,
                        "curious_tone": {"max_events_per_run": 77, "enabled": False},
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = normalize_pipeline_config(json.loads(path.read_text(encoding="utf-8")))
    flat = pipeline_settings_flat(path)
    assert cfg["logging"]["step1"]["filter_stats_every_n"] == 10
    assert cfg["logging"]["step1"]["curious_tone"]["max_events_per_run"] == 77
    assert flat["step1_curious_tone_log_enabled"] is False


def test_configure_step1_filter_audit_interval() -> None:
    configure_step1_filter_audit(SimpleNamespace(step1_log_filter_stats_every_n=3))
    from app.services.step1_filter_audit import filter_stats_log_interval

    assert filter_stats_log_interval() == 3
