import json
from pathlib import Path

from app.services import step1_conversion_stats as cs


def test_compute_funnel_conversions():
    out = cs.compute_funnel_conversions(
        urls_raw_merged=120,
        urls_raw_unique=54,
        urls_prefilter_rejected=20,
        urls_sent_to_http=30,
        verified_total=10,
    )
    assert out["urls_raw_unique"] == 54
    assert out["conversion_e2e_pct"] == round(10 / 54 * 100, 1)
    assert out["conversion_http_pct"] == round(10 / 30 * 100, 1)
    assert out["conversion_prefilter_pct"] == round(30 / 54 * 100, 1)


def test_estimate_raw_urls_for_target():
    assert cs.estimate_raw_urls_for_target(0.2, target=10) == 70
    assert cs.estimate_raw_urls_for_target(None) == cs.estimate_raw_urls_for_target(cs.DEFAULT_E2E_CONVERSION)
    assert cs.estimate_raw_urls_for_target(0.05) == cs.MAX_RAW_FETCH


def test_history_median_and_record(tmp_path: Path):
    path = tmp_path / "hist.json"
    assert cs.median_e2e_for_digest_type("serious", path=path) is None
    cs.record_e2e_sample("serious", 0.12, path=path)
    cs.record_e2e_sample("serious", 0.18, path=path)
    cs.record_e2e_sample("serious", 0.24, path=path)
    assert cs.median_e2e_for_digest_type("serious", path=path) == 0.18
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["serious"] == [0.12, 0.18, 0.24]


def test_apply_funnel_conversions_to_meta(tmp_path, monkeypatch):
    path = tmp_path / "hist.json"
    cs.record_e2e_sample("curious", 0.15, path=path)
    monkeypatch.setattr(cs, "_HISTORY_PATH", path)
    meta = {
        "urls_raw_merged": 80,
        "urls_raw_unique": 40,
        "urls_prefilter_rejected": 10,
        "urls_sent_to_http": 25,
        "verified_total": 8,
    }
    out = cs.apply_funnel_conversions_to_meta(meta, digest_type="curious")
    assert out["conversion_e2e_pct"] == 20.0
    assert out["conversion_e2e_baseline"] == 0.15
    assert out["estimated_raw_for_10"] >= cs.MIN_RAW_FETCH
