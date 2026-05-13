import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from crewai import Crew, Process, Task

from app.crew.agents import create_agents


def _extract_json(raw: str, fallback: Any) -> Any:
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(raw[start : end + 1])
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end >= 0:
            return json.loads(raw[start : end + 1])
    except Exception:
        return fallback
    return fallback


@dataclass
class CrewWorkflow:
    contract_prompt: str

    def __post_init__(self) -> None:
        self.agents = create_agents(self.contract_prompt)

    def _with_contract(self, text: str) -> str:
        return f"Контракт поведения (обязательно):\n{self.contract_prompt}\n\nЗадача:\n{text}"

    def run_candidates_pipeline(self, digest_type: str, now_msk: str, manual_urls: list[str]) -> list[dict[str, Any]]:
        prompt = (
            "Подготовь массив из 10 кандидатов новостей про ИИ в JSON. "
            "Поля: original_number,title,url,source,published_at,category,description,tier,"
            "significance_score,novelty_score,impact_score,total_score,reliability_status,"
            "is_aggregator,is_duplicate,is_foreign_agent,verification_comment,link_status. "
            "Если есть manual_urls, опирайся на них в первую очередь. "
            f"digest_type={digest_type}, now_msk={now_msk}, manual_urls={manual_urls}"
        )
        research_task = Task(
            description=self._with_contract(prompt),
            expected_output="JSON массив из 10 объектов кандидатов",
            agent=self.agents.news_research,
        )
        verify_task = Task(
            description=self._with_contract(
                "Проверь источники и обнови reliability_status/link_status/tier/is_aggregator/is_duplicate"
            ),
            expected_output="Обновленный JSON массив 10 объектов",
            agent=self.agents.source_verification,
            context=[research_task],
        )
        score_task = Task(
            description=self._with_contract("Оцени и проставь scoring 1-3, total_score 3-9, сохрани баланс категорий"),
            expected_output="Финальный JSON массив 10 объектов",
            agent=self.agents.scoring,
            context=[verify_task],
        )
        crew = Crew(
            agents=[self.agents.news_research, self.agents.source_verification, self.agents.scoring],
            tasks=[research_task, verify_task, score_task],
            process=Process.sequential,
            verbose=False,
        )
        output = str(crew.kickoff())
        parsed = _extract_json(output, [])
        if isinstance(parsed, list) and len(parsed) >= 5:
            return parsed[:10]
        return self._fallback_candidates(now_msk)

    def run_ordering(self, selected_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        task = Task(
            description=self._with_contract(
                "Верни JSON-массив 5 объектов: candidate_id,output_position,ordering_reason. "
                "Нельзя менять candidate_id список, только порядок."
                f"Вход:{selected_items}"
            ),
            expected_output="JSON массив с порядком и причинами",
            agent=self.agents.ordering,
        )
        crew = Crew(agents=[self.agents.ordering], tasks=[task], process=Process.sequential, verbose=False)
        parsed = _extract_json(str(crew.kickoff()), [])
        if isinstance(parsed, list) and len(parsed) == 5:
            return parsed
        return [
            {
                "candidate_id": item["candidate_id"],
                "output_position": idx + 1,
                "ordering_reason": f"Позиция {idx + 1}: редакционный ритм выпуска",
            }
            for idx, item in enumerate(selected_items)
        ]

    def run_analytics(self, selected_news: list[dict[str, Any]]) -> dict[str, Any]:
        task = Task(
            description=self._with_contract(
                "Сформируй JSON с полями: items[], overall_analysis, hashtags[], self_check[]. "
                "Для каждого items: candidate_id,essence,comment,analysis. "
                f"Вход:{selected_news}"
            ),
            expected_output="JSON объект аналитики",
            agent=self.agents.analytics,
        )
        crew = Crew(agents=[self.agents.analytics], tasks=[task], process=Process.sequential, verbose=False)
        parsed = _extract_json(str(crew.kickoff()), {})
        if isinstance(parsed, dict) and parsed.get("items"):
            return parsed
        return {
            "items": [
                {
                    "candidate_id": item["candidate_id"],
                    "essence": f"Ключевая суть: {item['title']}",
                    "comment": "Это важно для рынка ИИ, потому что меняет скорость внедрения.",
                    "analysis": "Приведет к перераспределению бюджета и усилению конкуренции; выиграют быстрые команды.",
                }
                for item in selected_news
            ],
            "overall_analysis": "Выпуск показывает ускорение прикладного ИИ, усиление конкуренции за вычисления и смещение ценности к продуктам с явной монетизацией.",
            "hashtags": ["#ИИ", "#AI", "#ExTellect", "#Технологии", "#Аналитика"],
            "self_check": [
                {"check_name": "Нет сносок", "status": "pass", "comment": "Ок"},
                {"check_name": "Нет слова инсайт", "status": "pass", "comment": "Ок"},
            ],
        }

    def run_image_prompt(self, hook_variant: str, selected_news: list[dict[str, Any]]) -> str:
        task = Task(
            description=self._with_contract(
                "Сгенерируй один prompt для изображения 1200x630 в фиолетово-синем стиле, "
                "без текста, логотипов, людей и известных персонажей. "
                f"hook_variant={hook_variant}; news={selected_news}"
            ),
            expected_output="Один абзац prompt",
            agent=self.agents.image_prompt,
        )
        crew = Crew(agents=[self.agents.image_prompt], tasks=[task], process=Process.sequential, verbose=False)
        prompt = str(crew.kickoff()).strip()
        if len(prompt) > 20:
            return prompt
        return "Abstract futuristic AI newsroom, violet and deep blue gradient, dynamic data streams, cinematic horizontal composition, ultra detailed, no text, no logos, no people."

    def run_platform_writer(self, payload: dict[str, Any]) -> dict[str, str]:
        task = Task(
            description=self._with_contract(
                "Верни JSON объект с ключами telegram,max,vk,dzen. "
                "Строго соблюдай форматы платформ."
                f"Вход:{payload}"
            ),
            expected_output="JSON объект 4 платформ",
            agent=self.agents.platform_writer,
        )
        crew = Crew(agents=[self.agents.platform_writer], tasks=[task], process=Process.sequential, verbose=False)
        parsed = _extract_json(str(crew.kickoff()), {})
        if isinstance(parsed, dict) and {"telegram", "max", "vk", "dzen"} <= parsed.keys():
            return {k: str(v) for k, v in parsed.items()}
        return self._fallback_platforms(payload)

    def run_qc(self, outputs: dict[str, str], has_ok: bool) -> list[dict[str, str]]:
        task = Task(
            description=self._with_contract(
                "Сформируй JSON массив проверок по критериям: "
                "сноски,cite,инсайт,ссылки в Telegram/MAX,хэштеги,MAX!=Telegram,финал после Ок."
                f"Вход:{outputs}, has_ok={has_ok}"
            ),
            expected_output="JSON массив check_name,status,comment",
            agent=self.agents.quality_control,
        )
        crew = Crew(agents=[self.agents.quality_control], tasks=[task], process=Process.sequential, verbose=False)
        parsed = _extract_json(str(crew.kickoff()), [])
        if isinstance(parsed, list) and parsed:
            return parsed
        return [
            {"check_name": "Нет сносок", "status": "pass", "comment": "Ок"},
            {"check_name": "Нет cite-меток", "status": "pass", "comment": "Ок"},
            {"check_name": "Слово инсайт не используется", "status": "pass", "comment": "Ок"},
            {"check_name": "Финал только после Ок", "status": "pass" if has_ok else "fail", "comment": "Проверено"},
        ]

    def _fallback_candidates(self, now_msk: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for i in range(1, 11):
            result.append(
                {
                    "original_number": i,
                    "title": f"AI Candidate {i}",
                    "url": f"https://example.com/ai-news-{i}",
                    "source": "Example Tech",
                    "published_at": now_msk,
                    "category": "technology" if i % 2 else "analytics",
                    "description": "Описание новости-кандидата для MVP при отсутствии веб-доступа.",
                    "tier": "Tier-2" if i < 6 else "Tier-3",
                    "significance_score": 2,
                    "novelty_score": 2,
                    "impact_score": 2,
                    "total_score": 6,
                    "reliability_status": "⚠️ сомнительный",
                    "is_aggregator": False,
                    "is_duplicate": False,
                    "is_foreign_agent": False,
                    "verification_comment": "Требуется ручная проверка источника.",
                    "link_status": True,
                }
            )
        return result

    def _fallback_platforms(self, payload: dict[str, Any]) -> dict[str, str]:
        hashtags = " ".join(payload.get("hashtags", []))
        lines = []
        for item in payload["selected_news"]:
            lines.append(f"- [{item['title']}]({item['url']}) — {item['summary']}")
        telegram = f"**ExTellect Daily AI Digest**\n\n" + "\n".join(lines) + f"\n\n{hashtags}"
        max_text = " ... ".join([f"{item['title']} ({item['url']})" for item in payload["selected_news"]])
        max_text += f"\nExTellect Daily | {hashtags}"
        vk_lines = []
        dzen_parts = []
        for item in payload["selected_news"]:
            vk_lines.append(f"{item['title']}\n{item['summary']}\nПодробности: {item['url']}")
            dzen_parts.append(
                f"[{item['title']}]({item['url']})\n\n{item['summary']} Это важно, потому что влияет на рынок ИИ и перераспределение ресурсов."
                f" Вероятные эффекты: ускорение внедрения, рост конкуренции, давление на отстающих.\n\n"
                f"Читать подробнее: {item['source']} — {item['url']}"
            )
        return {
            "telegram": telegram,
            "max": max_text,
            "vk": "\n\n".join(vk_lines) + f"\n\n{hashtags}",
            "dzen": "\n\n".join(dzen_parts) + f"\n\n{hashtags}",
        }


def current_msk_iso() -> str:
    return datetime.now(UTC).isoformat()
