"""GET /config — сводка настроек для UI."""

from fastapi.testclient import TestClient

from app.main import app


def test_get_app_config_returns_sections_without_secrets():
    client = TestClient(app)
    res = client.get("/config")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data.get("sections"), list) and len(data["sections"]) >= 5
    blob = res.text.lower()
    assert "sk-" not in blob
    proxy = next(s for s in data["sections"] if s["id"] == "proxyapi")
    key_row = next(i for i in proxy["items"] if i["label"] == "API key")
    assert key_row["value"] in {"задан", "не задан"}
    assert key_row.get("why_chosen")
    assert key_row.get("alternatives")
    step1 = next(s for s in data["sections"] if s["id"] == "step1_pipeline")
    tier_row = next(i for i in step1["items"] if "Tier-строгий" in i["label"])
    assert "tier" in tier_row["why_chosen"].lower() or "Tier" in tier_row["why_chosen"]
    assert tier_row["alternatives"]
    web = next(s for s in data["sections"] if s["id"] == "web")
    assert any(i["label"].startswith("Автосбор") for i in web["items"])
