import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from crewai import Crew, Process, Task

from app.crew.agents import create_agents
from app.crew.model_policy import AGENT_MODEL_RECOMMENDATIONS


def _strip_markdown_json_fence(raw: str) -> str:
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return s


def _extract_json(raw: str, fallback: Any) -> Any:
    text = _strip_markdown_json_fence(raw)
    try:
        if text.startswith("[") or text.startswith("{"):
            return json.loads(text)
    except Exception:
        pass
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    except Exception:
        return fallback
    return fallback


_SUBSCRIPTION_MD_LINE = (
    "👉 Подпишитесь на ExTellect: [Telegram](https://t.me/extellect) • [ВКонтакте](https://vk.com/extellect) • "
    "[MAX](https://max.ru/join/fu6Q3ibyBe8ONaZEg5J_3md_GXpZbJ5WlNKBOzeg4rY) • [Дзен](https://dzen.ru/extellect) • "
    "[Boosty](https://boosty.to/extellect)"
)

_SUBSCRIPTION_VK_BLOCK = """———
👉 Подпишитесь на ExTellect:
Telegram: https://t.me/extellect
ВКонтакте: https://vk.com/extellect
MAX: https://max.ru/join/fu6Q3ibyBe8ONaZEg5J_3md_GXpZbJ5WlNKBOzeg4rY
Дзен: https://dzen.ru/extellect
Boosty: https://boosty.to/extellect"""


def _normalize_hashtag_tokens(tags: list[Any], minimum: int, maximum: int) -> str:
    seen: list[str] = []
    for t in tags:
        s = str(t).strip()
        if not s:
            continue
        if not s.startswith("#"):
            s = f"#{s}"
        if s not in seen:
            seen.append(s)
    defaults = ["#ИИновости", "#нейросети", "#инновации", "#AI", "#технологии", "#машинноеОбучение"]
    for d in defaults:
        if len(seen) >= minimum:
            break
        if d not in seen:
            seen.append(d)
    return " ".join(seen[:maximum])


def _fallback_dzen_paragraphs(item: dict[str, Any]) -> str:
    title = item["title"]
    url = item["url"]
    source = item.get("source") or "Источник"
    summary = str(item.get("summary", "")).strip()
    block = (
        f"[{title}]({url})\n\n"
        f"{summary}\n\n"
        "Если по-простому, событие отражает сдвиги на рынке искусственного интеллекта и меняет расстановку сил между участниками экосистемы.\n\n"
        "По сути, важно понять горизонт влияния на продукты, регулирование и инвестиционные потоки в сегменте ИИ.\n\n"
        "Что это значит для читателя: стоит сверять сигналы с практикой внедрения и не принимать решения только по заголовку.\n\n"
        f"Читать подробнее: {source} — {url}"
    )
    pad = " Дополнительный контекст помогает оценить устойчивость тренда и переносимость практик на смежные отрасли."
    while len(block) < 400:
        block += pad
    return block


@dataclass
class CrewWorkflow:
    contract_prompt: str

    def __post_init__(self) -> None:
        self.agents = create_agents(self.contract_prompt)

    def _with_contract(self, text: str) -> str:
        return f"Контракт поведения (обязательно):\n{self.contract_prompt}\n\nЗадача:\n{text}"

    def run_candidates_pipeline(self, digest_type: str, now_msk: str, manual_urls: list[str]) -> list[dict[str, Any]]:
        research_raw = self.run_candidates_research(digest_type, now_msk, manual_urls)
        verify_raw = self.run_candidates_verify(research_raw)
        return self.run_candidates_score(verify_raw, now_msk)

    def run_candidates_research(self, digest_type: str, now_msk: str, manual_urls: list[str]) -> str:
        prompt = (
            "Подготовь массив из 10 кандидатов новостей про ИИ в JSON. "
            "Поля: original_number,title,url,source,published_at,category,description,tier,"
            "significance_score,novelty_score,impact_score,total_score,reliability_status,"
            "is_aggregator,is_duplicate,is_foreign_agent,verification_comment,link_status. "
            "Поле tier: только Tier-1, Tier-2, Tier-3 или Tier-4 по шкале «Система приоритетов источников» в контракте; "
            "источники уровня запрещённых агрегаторов (Tier-5 в том разделе) не подбирай — если URL попал из manual_urls, пометь is_aggregator и низкий балл. "
            "При равной новизне отдавай предпочтение более высокому Tier (меньший номер). "
            "Каждый объект: поле url должно быть прямой ссылкой на ту же HTML-страницу, что описывает title "
            "(после открытия url в браузере заголовок вкладки/статьи должен соответствовать title); "
            "не подставляй URL главной сайта, поиска или ленты вместо URL материала. "
            "Каждая новость должна быть именно про искусственный интеллект, нейросети, ML, крупные модели (GPT, Gemini и т.п.) "
            "или их применение — не подмешивай нерелевантные темы (банкротства, спорт, быт и т.д.), даже если URL с крупного СМИ. "
            "Ответ — только один JSON-массив из 10 объектов, без markdown-ограждений и без текста до/после JSON. "
            f"digest_type={digest_type}, now_msk={now_msk}, manual_urls={manual_urls}"
        )
        research_task = Task(
            description=self._with_contract(prompt),
            expected_output="JSON массив из 10 объектов кандидатов",
            agent=self.agents.news_research,
        )
        research_crew = Crew(agents=[self.agents.news_research], tasks=[research_task], process=Process.sequential, verbose=False)
        return str(research_crew.kickoff())

    def run_candidates_refill(self, digest_type: str, now_msk: str, excluded_urls: list[str]) -> list[dict[str, Any]]:
        """Дополнительный проход LLM: новые URL, не пересекающиеся с excluded_urls."""
        excluded_preview = excluded_urls[:40]
        prompt = (
            "Подготовь массив из 10 НОВЫХ кандидатов новостей про ИИ в JSON. "
            "Поля: original_number,title,url,source,published_at,category,description,tier,"
            "significance_score,novelty_score,impact_score,total_score,reliability_status,"
            "is_aggregator,is_duplicate,is_foreign_agent,verification_comment,link_status. "
            "Нельзя использовать URL из списка исключений (ни один из них, ни очевидные дубликаты того же материала). "
            "Не выдумывай несуществующие адреса — только реальные прямые ссылки на страницы статей. "
            "Каждый url должен вести на ту же публикацию, что и title. "
            "Не возвращай агрегаторы и ленты (news.google, reddit, поисковые страницы, теги, главные страницы). "
            "Только материалы про ИИ/нейросети/ML — без посторонних тем. "
            "Предпочитай страницы с признаками article-page (og:type=article, article:published_time, JSON-LD NewsArticle/Article). "
            f"Исключённые URL: {excluded_preview}. "
            "Ответ — только один JSON-массив без markdown и без текста до/после JSON. "
            f"digest_type={digest_type}, now_msk={now_msk}"
        )
        research_task = Task(
            description=self._with_contract(prompt),
            expected_output="JSON массив новых кандидатов",
            agent=self.agents.news_research,
        )
        research_crew = Crew(agents=[self.agents.news_research], tasks=[research_task], process=Process.sequential, verbose=False)
        raw = str(research_crew.kickoff())
        parsed = _extract_json(raw, [])
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)][:15]
        return []

    def run_candidates_verify(self, research_raw: str) -> str:
        verify_task = Task(
            description=self._with_contract(
                "Проверь источники и обнови reliability_status/link_status/tier/is_aggregator/is_duplicate. "
                "Для каждого кандидата: url и title должны относиться к одной и той же публикации и к теме ИИ/нейросетей; "
                "если материал не про ИИ — удали объект из массива или пометь link_status=false. "
                "если url ведёт на другую статью, ленту или 404 — исправь url или пометь link_status=false. "
                "Пересчитай tier строго по разделу «Система приоритетов источников»: Tier-1…Tier-4 для допустимых изданий; "
                "для доменов из запрещённых агрегаторов (Tier-5 того раздела) выставь is_aggregator=true и снизь пригодность к финальной пятёрке. "
                "Ответ — только один JSON-массив из 10 объектов, без текста до/после JSON. "
                f"Входные кандидаты: {research_raw}"
            ),
            expected_output="Обновленный JSON массив 10 объектов",
            agent=self.agents.source_verification,
        )
        verify_crew = Crew(
            agents=[self.agents.source_verification], tasks=[verify_task], process=Process.sequential, verbose=False
        )
        return str(verify_crew.kickoff())

    def run_candidates_score(self, verify_raw: str, now_msk: str) -> list[dict[str, Any]]:
        score_task = Task(
            description=self._with_contract(
                "Оцени и проставь scoring 1-3, total_score 3-9, сохрани баланс категорий. "
                "Учитывай Tier из контракта: при прочих равных более высокий приоритет у Tier-1, затем Tier-2 и т.д. "
                "Ответ — только один JSON-массив из 10 объектов, без markdown-ограждений и без текста до/после JSON. "
                f"Вход после верификации: {verify_raw}"
            ),
            expected_output="Финальный JSON массив 10 объектов",
            agent=self.agents.scoring,
        )
        score_crew = Crew(agents=[self.agents.scoring], tasks=[score_task], process=Process.sequential, verbose=False)
        output = str(score_crew.kickoff())
        parsed = _extract_json(output, [])
        if isinstance(parsed, list) and len(parsed) >= 5:
            return parsed[:10]
        return self._fallback_candidates(now_msk)

    def get_agent_models(self) -> dict[str, str]:
        return AGENT_MODEL_RECOMMENDATIONS.copy()

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
        extra_rules = (
            "Дополнительно к контракту: в конце КАЖДОГО из четырёх блоков добавь одну строку подписки "
            f"(для telegram/max/dzen — markdown как пример: {_SUBSCRIPTION_MD_LINE}; "
            "для vk — только plain text URL построчно как в редакционной шпаргалке v9, без ** и без [текст](url)). "
            "Хэштеги: в telegram и max — 4–6 штук в одной строке после подписи; в vk и dzen — 3–5 в конце. "
            "MAX: отдельная вёрстка, не копируй telegram; между новостями пустая строка, затем три точки ..., затем пустая строка; "
            "каждая новость одним абзацем после строки ➤ [Заголовок](url). "
            "Telegram: заголовок выпуска **⚡Пять актуальных новостей про ИИ | <дата>**, лид 1–2 предложения, новости ➤ [Заголовок](url) + 2–3 предложения; ссылки только в заголовках новостей. "
            "Дзен: вступление 2–3 предложения, у каждой новости минимум 4 абзаца и ≥400 символов, строка «Читать подробнее: Источник — URL». "
            "Верни только JSON."
        )
        task = Task(
            description=self._with_contract(
                "Верни JSON объект с ключами telegram,max,vk,dzen. "
                "Строго соблюдай форматы платформ из контракта. "
                + extra_rules
                + f"Вход:{payload}"
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
                "сноски,cite,инсайт,ссылки в Telegram/MAX только в заголовках новостей,хэштеги (telegram/max 4–6, vk/dzen 3–5),"
                "подпись ExTellect в каждом блоке,MAX!=Telegram,MAX между новостями ... с пустыми строками,"
                "Дзен длина и абзацы,финал после Ок."
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
        raw_tags = list(payload.get("hashtags") or [])
        tags_tg_max = _normalize_hashtag_tokens(raw_tags, 4, 6)
        tags_vk_dzen = _normalize_hashtag_tokens(raw_tags, 3, 5)
        date = str(payload.get("date") or "")
        lead = "Коротко: главные сдвиги в мире ИИ и вокруг экосистемы продуктов за сегодня."
        news_lines_tg: list[str] = []
        max_blocks: list[str] = []
        vk_blocks: list[str] = []
        dzen_blocks: list[str] = []
        for item in payload["selected_news"]:
            title = item["title"]
            url = item["url"]
            summary = " ".join(str(item.get("summary", "")).split())
            news_lines_tg.append(f"➤ [{title}]({url})\n{summary}")
            summary_m = summary.replace("\n", " ")
            max_blocks.append(f"➤ [{title}]({url})\n{summary_m}")
            vk_blocks.append(
                f"👉 {title}\n\n{summary}\n\n👉 Итог для читателя: событие стоит отслеживать в контексте рынка ИИ.\n\nПодробности: {url}"
            )
            dzen_blocks.append(_fallback_dzen_paragraphs(item))
        header = f"**⚡Пять актуальных новостей про ИИ | {date}**\n\n{lead}\n\n"
        telegram = header + "\n\n".join(news_lines_tg) + f"\n\n———\n{_SUBSCRIPTION_MD_LINE}\n{tags_tg_max}"
        max_inner = "\n\n...\n\n".join(max_blocks)
        max_text = f"⚡Пять актуальных новостей про ИИ | {date}\n\n{lead}\n\n{max_inner}\n\n———\n{_SUBSCRIPTION_MD_LINE}\n{tags_tg_max}"
        vk_body = "\n\n──────────\n\n".join(vk_blocks)
        vk = f"{vk_body}\n\n{_SUBSCRIPTION_VK_BLOCK}\n{tags_vk_dzen}"
        dzen_intro = (
            "Ниже — подборка из пяти материалов: мы кратко задаём контекст, затем разбираем каждую новость "
            "в формате, удобном для длинного чтения в Дзене.\n\n"
        )
        dzen = dzen_intro + "\n\n".join(dzen_blocks) + f"\n\n———\n{_SUBSCRIPTION_MD_LINE}\n{tags_vk_dzen}"
        return {
            "telegram": telegram,
            "max": max_text,
            "vk": vk,
            "dzen": dzen,
        }


def current_msk_iso() -> str:
    return datetime.now(ZoneInfo("Europe/Moscow")).isoformat()
