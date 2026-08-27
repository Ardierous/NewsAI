import json
from collections import Counter

from app.database import SessionLocal
from app.models import Digest
from app.services.digest_service import DigestService
from app.services.step1_statistics import build_step1_statistics


def print_stats(db, digest_id: int, label: str) -> None:
    stats = build_step1_statistics(db, digest_id)
    summary = stats.summary
    meta = stats.step1_collection_meta or {}
    reject_summary = stats.rejected_reasons_summary or {}
    top_rejects = sorted(reject_summary.items(), key=lambda kv: kv[1], reverse=True)[:6]
    print(f"\n=== {label} ===")
    print(
        json.dumps(
            {
                "digest_id": digest_id,
                "in_pool": summary.in_pool,
                "verified_passed": summary.verified_passed,
                "total_links": summary.total_links,
                "rejected": summary.rejected,
                "stop_reason": meta.get("stop_reason"),
                "iterations": meta.get("iterations"),
                "step1_cost_rub": float(stats.pool_collection_stats.step1_total_rub or 0.0),
                "proxyapi_search_cost_est_rub": meta.get("proxyapi_web_search_cost_est_rub"),
                "dominant_rejects": top_rejects,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    db = SessionLocal()
    try:
        digest = db.query(Digest).order_by(Digest.id.desc()).first()
        if not digest:
            print("No digests found")
            return
        digest_id = int(digest.id)
        print(f"Target digest_id={digest_id}, status={digest.status}, step={digest.current_step}")
        print_stats(db, digest_id, "before")

        service = DigestService(db)
        service.run_step_1(digest_id, [], rebuild=True)

        print_stats(db, digest_id, "after")
    finally:
        db.close()


if __name__ == "__main__":
    main()
