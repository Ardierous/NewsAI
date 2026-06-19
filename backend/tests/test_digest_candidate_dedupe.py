from app.services.digest_service import DigestService


def test_dedupe_verified_pool_dicts_keeps_first_url():
    pool = [
        {"url": "https://a.test/one", "title": "First"},
        {"url": "https://a.test/one", "title": "Second"},
        {"url": "https://b.test/two", "title": "Third"},
    ]
    out = DigestService._dedupe_verified_pool_dicts(pool)
    assert len(out) == 2
    assert out[0]["title"] == "First"
    assert out[1]["title"] == "Third"


def test_dedupe_verified_pool_dicts_normalizes_same_page():
    pool = [
        {"url": "https://www.example.com/news/1/", "title": "A"},
        {"url": "https://example.com/news/1", "title": "B"},
    ]
    out = DigestService._dedupe_verified_pool_dicts(pool)
    assert len(out) == 1
    assert out[0]["title"] == "A"
