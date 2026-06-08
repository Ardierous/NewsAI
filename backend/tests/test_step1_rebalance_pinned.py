"""Rebalance не должен выкидывать отмеченные при частичной пересборке."""

from app.services import digest_service as ds


def test_rebalance_keeps_pinned_despite_ru_quota():
  ria = {
      "url": "https://ria.ru/20260603/ii-1.html",
      "title": "РИА новость",
      "tier": "Tier-1",
      "total_score": 3,
      "link_status": True,
      "headline_editorial_ok": True,
  }
  vedomosti = {
      "url": "https://www.vedomosti.ru/society/news/2026/06/04/1",
      "title": "Ведомости новость",
      "tier": "Tier-1",
      "total_score": 8,
      "link_status": True,
      "headline_editorial_ok": True,
  }
  openai = {
      "url": "https://openai.com/index/election-safeguards-2026/",
      "title": "OpenAI safeguards",
      "tier": "Tier-4",
      "total_score": 9,
      "link_status": True,
      "headline_editorial_ok": True,
  }
  pool = [openai, vedomosti, ria]
  pinned = {ds._url_fingerprint(ria["url"]), ds._url_fingerprint(vedomosti["url"])}
  out = ds._rebalance_verified_pool(pool, target=2, pinned_fps=pinned, digest_type="serious")
  fps = {ds._url_fingerprint(str(x.get("url") or "")) for x in out}
  assert pinned.issubset(fps)
