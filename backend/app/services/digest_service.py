import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crew.workflow import CrewWorkflow, current_msk_iso
from app.models import Analytics, Asset, Digest, FinalOutput, NewsCandidate, QualityCheck, SelectedNews
from app.proxyapi_client import ProxyApiClient
from app.services.export_service import build_docx

logger = logging.getLogger("app.digest")

STATUS_DRAFT = "draft"
STATUS_STEP0 = "step_0"
STATUS_STEP1 = "step_1_candidates"
STATUS_SELECTED = "selected"
STATUS_ANALYTICS = "analytics_ready"
STATUS_FINAL = "final_ready"


class DigestService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.proxy = ProxyApiClient()
        contract = self.settings.prompts_path.read_text(encoding="utf-8")
        self.workflow = CrewWorkflow(contract_prompt=contract)

    def create_digest_for_today(self) -> Digest:
        today = date.today()
        digest = self.db.query(Digest).filter(Digest.date == today).first()
        if digest:
            logger.info("Выпуск на сегодня уже существует | digest_id=%s date=%s", digest.id, today)
            return digest
        digest = Digest(date=today, status=STATUS_DRAFT, current_step=STATUS_DRAFT)
        self.db.add(digest)
        self.db.commit()
        self.db.refresh(digest)
        logger.info("Создан новый выпуск на сегодня | digest_id=%s date=%s", digest.id, today)
        return digest

    def list_digests(self) -> list[Digest]:
        return self.db.query(Digest).order_by(Digest.date.desc()).all()

    def get_digest(self, digest_id: int) -> Digest:
        digest = self.db.query(Digest).filter(Digest.id == digest_id).first()
        if not digest:
            raise HTTPException(status_code=404, detail="Digest not found")
        return digest

    def run_step_0(self, digest_id: int, digest_type: str | None) -> Digest:
        digest = self.get_digest(digest_id)
        if digest_type is None:
            weekday = datetime.now().weekday()
            digest_type = "serious" if weekday < 5 else "curious"
        if digest_type not in {"serious", "curious"}:
            raise HTTPException(status_code=400, detail="digest_type must be serious or curious")
        digest.digest_type = digest_type
        digest.status = STATUS_STEP0
        digest.current_step = STATUS_STEP0
        self.db.commit()
        self.db.refresh(digest)
        logger.info("Шаг 0: тип дайджеста | digest_id=%s type=%s", digest.id, digest_type)
        return digest

    def run_step_1(self, digest_id: int, manual_urls: list[str]) -> list[NewsCandidate]:
        digest = self.get_digest(digest_id)
        if digest.status not in {STATUS_STEP0, STATUS_STEP1}:
            raise HTTPException(status_code=400, detail="Step 1 requires step_0")

        if not self.settings.enable_web_fetch and not manual_urls:
            raise HTTPException(
                status_code=400,
                detail="Нет веб-доступа. Вставьте вручную 5-10 ссылок в поле manual_urls.",
            )

        self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).delete()
        self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).delete()
        self.db.query(Analytics).filter(Analytics.digest_id == digest.id).delete()
        self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).delete()
        self.db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).delete()
        self.db.query(Asset).filter(Asset.digest_id == digest.id).delete()

        logger.info(
            "Шаг 1: запуск сбора кандидатов | digest_id=%s manual_urls=%s",
            digest.id,
            len(manual_urls),
        )
        candidates = self.workflow.run_candidates_pipeline(
            digest_type=digest.digest_type or "serious",
            now_msk=current_msk_iso(),
            manual_urls=manual_urls,
        )
        entities: list[NewsCandidate] = []
        seen = set()
        for item in candidates[:10]:
            link_ok = self._check_url(item.get("url", ""))
            dup_key = item.get("url", "").strip().lower()
            is_duplicate = dup_key in seen or bool(item.get("is_duplicate", False))
            seen.add(dup_key)
            entity = NewsCandidate(
                digest_id=digest.id,
                original_number=int(item.get("original_number", len(entities) + 1)),
                title=str(item.get("title", ""))[:500],
                url=str(item.get("url", ""))[:1000],
                source=str(item.get("source", ""))[:255],
                tier=str(item.get("tier", "Tier-3"))[:32],
                published_at=str(item.get("published_at", "")),
                category=str(item.get("category", "technology"))[:120],
                description=str(item.get("description", "")),
                significance_score=int(item.get("significance_score", 1)),
                novelty_score=int(item.get("novelty_score", 1)),
                impact_score=int(item.get("impact_score", 1)),
                total_score=int(item.get("total_score", 3)),
                reliability_status=str(item.get("reliability_status", "⚠️ сомнительный")),
                link_status=link_ok and bool(item.get("link_status", True)),
                is_foreign_agent=bool(item.get("is_foreign_agent", False)),
                is_aggregator=bool(item.get("is_aggregator", False)),
                is_duplicate=is_duplicate,
                verification_comment=str(item.get("verification_comment", "")),
            )
            entities.append(entity)
            self.db.add(entity)

        digest.status = STATUS_STEP1
        digest.current_step = STATUS_STEP1
        self.db.commit()
        logger.info("Шаг 1: сохранено кандидатов | digest_id=%s count=%s", digest.id, len(entities))
        return entities

    def select_news(self, digest_id: int, selected_ids: list[int], top5: bool) -> list[SelectedNews]:
        digest = self.get_digest(digest_id)
        if digest.status != STATUS_STEP1:
            raise HTTPException(status_code=400, detail="Selection requires step_1_candidates")

        candidates = self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).all()
        allowed = [
            c
            for c in candidates
            if c.link_status and c.reliability_status != "❗ без подтверждения" and not c.is_aggregator and not c.is_duplicate
        ]
        if top5:
            chosen = sorted(allowed, key=lambda x: x.total_score, reverse=True)[:5]
        else:
            if len(selected_ids) != 5:
                raise HTTPException(status_code=400, detail="Нужно выбрать ровно 5 новостей")
            id_set = set(selected_ids)
            chosen = [c for c in allowed if c.id in id_set]
            if len(chosen) != 5:
                raise HTTPException(status_code=400, detail="Выбраны недопустимые новости")

        self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).delete()
        created: list[SelectedNews] = []
        for idx, c in enumerate(chosen, start=1):
            item = SelectedNews(
                digest_id=digest.id,
                candidate_id=c.id,
                original_number=c.original_number,
                output_position=idx,
                ordering_reason="Выбрано пользователем",
            )
            self.db.add(item)
            created.append(item)
        digest.status = STATUS_SELECTED
        digest.current_step = "step_1_5"
        self.db.commit()
        logger.info(
            "Выбор новостей | digest_id=%s top5=%s candidate_ids=%s",
            digest.id,
            top5,
            [c.id for c in chosen],
        )
        return created

    def run_step_1_5_order(self, digest_id: int, ordered_candidate_ids: list[int]) -> list[SelectedNews]:
        digest = self.get_digest(digest_id)
        if digest.status != STATUS_SELECTED:
            raise HTTPException(status_code=400, detail="Ordering requires selected status")
        selected = self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.id).all()
        if len(selected) != 5:
            raise HTTPException(status_code=400, detail="Need exactly 5 selected news")

        selected_ids = [s.candidate_id for s in selected]
        if ordered_candidate_ids:
            if set(ordered_candidate_ids) != set(selected_ids):
                raise HTTPException(status_code=400, detail="Можно менять только порядок выбранных новостей")
            order_payload = [{"candidate_id": cid} for cid in ordered_candidate_ids]
        else:
            order_payload = [{"candidate_id": cid} for cid in selected_ids]

        agent_order = self.workflow.run_ordering(order_payload)
        for row in selected:
            for ord_item in agent_order:
                if ord_item["candidate_id"] == row.candidate_id:
                    row.output_position = int(ord_item["output_position"])
                    row.ordering_reason = str(ord_item["ordering_reason"])
        self.db.commit()
        ordered = self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.output_position).all()
        logger.info(
            "Шаг 1.5: порядок | digest_id=%s positions=%s",
            digest.id,
            [(r.candidate_id, r.output_position) for r in ordered],
        )
        return ordered

    def run_step_2_analytics(self, digest_id: int, command: str) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        if command.strip().lower() != "готово":
            raise HTTPException(status_code=400, detail='Для шага 2 нужно ввести команду "готово"')
        if digest.status != STATUS_SELECTED:
            raise HTTPException(status_code=400, detail="Step 2 requires selected status")

        self.db.query(Analytics).filter(Analytics.digest_id == digest.id).delete()
        self.db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).delete()

        logger.info("Шаг 2: аналитика | digest_id=%s", digest.id)
        selected = (
            self.db.query(SelectedNews)
            .filter(SelectedNews.digest_id == digest.id)
            .order_by(SelectedNews.output_position.asc())
            .all()
        )
        payload = []
        for item in selected:
            candidate = self.db.query(NewsCandidate).filter(NewsCandidate.id == item.candidate_id).first()
            if candidate:
                payload.append(
                    {
                        "candidate_id": candidate.id,
                        "title": candidate.title,
                        "source": candidate.source,
                        "url": candidate.url,
                        "published_at": candidate.published_at,
                    }
                )
        result = self.workflow.run_analytics(payload)
        for item in result.get("items", []):
            candidate = self.db.query(NewsCandidate).filter(NewsCandidate.id == item["candidate_id"]).first()
            if not candidate:
                continue
            self.db.add(
                Analytics(
                    digest_id=digest.id,
                    candidate_id=candidate.id,
                    essence=str(item.get("essence", "")),
                    comment=str(item.get("comment", "")),
                    analysis=str(item.get("analysis", "")),
                    source_url=candidate.url,
                    source_name=candidate.source,
                    published_at=candidate.published_at,
                )
            )
        checks = result.get("self_check", [])
        for c in checks:
            self.db.add(
                QualityCheck(
                    digest_id=digest.id,
                    check_name=str(c.get("check_name", "Self check")),
                    status=str(c.get("status", "pass")),
                    comment=str(c.get("comment", "")),
                )
            )

        hashtag_asset = Asset(
            digest_id=digest.id,
            type="hashtags",
            path="",
            prompt=" ".join(result.get("hashtags", [])),
        )
        self.db.add(hashtag_asset)

        digest.status = STATUS_ANALYTICS
        digest.current_step = STATUS_ANALYTICS
        self.db.commit()
        logger.info("Шаг 2: готово | digest_id=%s analytics_rows=%s", digest.id, len(result.get("items", [])))
        return result

    def run_step_3_final(self, digest_id: int, command: str, hook_variant: str | None) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        if command.strip().lower() not in {"ок", "ok"}:
            raise HTTPException(status_code=400, detail='Для шага 3 нужно ввести команду "Ок"')
        if digest.status != STATUS_ANALYTICS:
            raise HTTPException(status_code=400, detail="Step 3 requires analytics_ready")

        rotation = ["A", "B", "V"]
        hook = hook_variant if hook_variant in rotation else rotation[digest.id % 3]
        logger.info("Шаг 3: финальная сборка | digest_id=%s hook=%s", digest.id, hook)
        analytics_rows = self.db.query(Analytics).filter(Analytics.digest_id == digest.id).all()
        selected_rows = (
            self.db.query(SelectedNews)
            .filter(SelectedNews.digest_id == digest.id)
            .order_by(SelectedNews.output_position.asc())
            .all()
        )
        selected_payload = []
        for row in selected_rows:
            candidate = self.db.query(NewsCandidate).filter(NewsCandidate.id == row.candidate_id).first()
            analytics = next((a for a in analytics_rows if a.candidate_id == row.candidate_id), None)
            if not candidate or not analytics:
                continue
            selected_payload.append(
                {
                    "title": candidate.title,
                    "url": candidate.url,
                    "source": candidate.source,
                    "summary": f"{analytics.essence} {analytics.analysis}",
                }
            )

        hashtags_asset = (
            self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "hashtags").order_by(Asset.id.desc()).first()
        )
        hashtags = hashtags_asset.prompt.split() if hashtags_asset and hashtags_asset.prompt else ["#ИИ", "#AI"]
        image_prompt = self.workflow.run_image_prompt(hook, selected_payload)
        image_path = self.settings.image_dir / f"digest_{digest.id}.png"
        self.proxy.generate_image(image_prompt, image_path)
        self.db.add(Asset(digest_id=digest.id, type="image", path=str(image_path), prompt=image_prompt))

        outputs = self.workflow.run_platform_writer(
            {
                "hook_variant": hook,
                "selected_news": selected_payload,
                "hashtags": hashtags,
                "date": digest.date.isoformat(),
            }
        )

        self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).delete()
        for platform, content in outputs.items():
            self.db.add(
                FinalOutput(
                    digest_id=digest.id,
                    platform=platform,
                    content=content,
                    character_count=len(content),
                    qc_status="pending",
                )
            )
        self.db.commit()

        checks = self.workflow.run_qc(outputs, has_ok=True)
        self.db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).delete()
        failed = False
        for c in checks:
            status = str(c.get("status", "pass")).lower()
            self.db.add(
                QualityCheck(
                    digest_id=digest.id,
                    check_name=str(c.get("check_name", "QC")),
                    status=status,
                    comment=str(c.get("comment", "")),
                )
            )
            if status not in {"pass", "ok", "success"}:
                failed = True
        self.db.commit()

        if failed:
            regenerated = self.workflow.run_platform_writer(
                {
                    "hook_variant": hook,
                    "selected_news": selected_payload,
                    "hashtags": hashtags,
                    "fix_mode": True,
                }
            )
            for row in self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).all():
                row.content = regenerated.get(row.platform, row.content)
                row.character_count = len(row.content)
                row.qc_status = "repaired"
            self.db.commit()

        digest.status = STATUS_FINAL
        digest.current_step = STATUS_FINAL
        self.db.commit()

        docx_path = self.settings.docx_dir / f"digest_{digest.id}.docx"
        build_docx(self.db, digest, docx_path)
        self.db.add(Asset(digest_id=digest.id, type="docx", path=str(docx_path), prompt="final export"))
        self.db.commit()
        logger.info(
            "Шаг 3: завершено | digest_id=%s image=%s docx=%s",
            digest.id,
            image_path.name,
            docx_path.name,
        )
        return {"hook_variant": hook, "image_path": str(image_path), "docx_path": str(docx_path)}

    def _check_url(self, url: str) -> bool:
        if not url.startswith("http"):
            return False
        try:
            response = requests.head(url, timeout=5, allow_redirects=True)
            if response.status_code < 400:
                return True
            response = requests.get(url, timeout=5)
            return response.status_code < 400
        except Exception:
            return False
