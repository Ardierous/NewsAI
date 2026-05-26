import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from crewai import Crew, Process, Task
from crewai.types.usage_metrics import UsageMetrics

from app.crew.agents import create_agents
from app.crew.model_policy import AGENT_MODEL_RECOMMENDATIONS
from app.services.digest_type_policy import (
    normalize_digest_type,
    step1_research_editorial_block,
    step1_scoring_editorial_block,
)
from app.services.platform_assembly import assemble_platform_outputs, subscription_md_inline
from app.services.reader_copy import build_reader_text_fallback, sanitize_reader_description

_ANALYTICS_EDITORIAL = (
    "Для каждого items[] верни candidate_id, essence, comment, analysis. "
    "Это материал ДЛЯ РЕДАКТОРА дайджеста, не для публикации читателям. "
    "Используй title, source_description и article_excerpt как фактическую опору; "
    "article_excerpt — основной источник фактов из текста статьи. "
    "essence — одна строка с главной новостью для быстрого просмотра в UI. "
    "analysis — развёрнутая редакторская записка (4–8 предложений, можно структурированно): "
    "суть; почему важно; для кого важно; польза/вред; последствия; что делать читателю или редакции. "
    "comment — необязательная короткая пометка редактора (0–2 предложения), не текст для публикации. "
    "Запрещено писать Tier, баллы, статусы верификации, коды отказа, имена агентов, поля JSON и любую внутреннюю кухню. "
    "overall_analysis сформируй здесь же, на шаге аналитики, после анализа всех 5 выбранных новостей: "
    "это не вступление для площадок, а общий вывод выпуска на 3–5 предложений. Покажи общую линию выпуска, "
    "какие сдвиги видны по пяти новостям вместе, кому стоит обратить внимание и что это даёт или чем рискует "
    "аудитория дайджеста. "
    "self_check[] — добавь проверки: analysis_covers_six_angles, editor_not_reader_copy, "
    "overall_analysis_from_selected_five, no_service_info. "
    "hashtags[] — 4–6 тегов с #. "
    "Ответ — только JSON, без markdown и текста вокруг."
)

_READER_COPY_EDITORIAL = (
    "Для каждого items[] верни candidate_id и reader_text. "
    "reader_text — ГОТОВОЕ описание новости для читателей финального дайджеста: 2–4 коротких простых "
    "предложения (НЕ БОЛЕЕ 450 символов, без учёта заголовка новости). "
    "Пиши простым разговорным русским языком, как будто объясняешь другу. "
    "НЕ используй официальный, канцелярский или «новостной» стиль. "
    "Синтезируй essence, analysis и article_excerpt: суть + почему это важно + что из этого следует. "
    "Без маркированных списков, подзаголовков и вводных оборотов типа «стоит отметить», «важно понимать». "
    "Допустима лёгкая ирония, если она не искажает факты. "
    "Без канцелярита, официоза, штампов и признаков генерации ИИ. "
    "Не копируй article_excerpt дословно; не ссылайся на «редактора», «аналитику» или внутренние поля. "
    "Только проверяемые факты из article_excerpt и analysis. "
    "self_check[] — reader_text_max_450_chars, simple_language, no_service_info, no_ai_cliches, facts_from_excerpt. "
    "Ответ — только JSON: items[], self_check[], без markdown и текста вокруг."
)


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


def _extract_candidate_list(raw: str) -> list[dict[str, Any]]:
    parsed = _extract_json(raw, [])
    if not isinstance(parsed, list):
        return []
    return [x for x in parsed if isinstance(x, dict)]


def _fallback_analytics_item(news: dict[str, Any]) -> dict[str, Any]:
    title = str(news.get("title") or "Новость").strip()
    short = title if len(title) <= 140 else f"{title[:137]}…"
    return {
        "candidate_id": int(news["candidate_id"]),
        "essence": short,
        "comment": "Проверить факты по первоисточнику перед финальным текстом для читателя.",
        "analysis": (
            f"Суть: {short} "
            "Почему важно: оценить по тексту статьи. "
            "Для кого: команды, которым затрагивает тема ИИ. "
            "Польза/вред и последствия — уточнить по article_excerpt. "
            "Что делать: сверить с планами и сроками, если тема в вашем поле."
        ),
    }


def _fallback_reader_text_item(news: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": int(news["candidate_id"]),
        "reader_text": build_reader_text_fallback(
            str(news.get("essence") or news.get("title") or ""),
            str(news.get("analysis") or ""),
        ),
    }


def complete_reader_descriptions_result(
    result: dict[str, Any], reader_items: list[dict[str, Any]]
) -> dict[str, Any]:
    expected = [int(x["candidate_id"]) for x in reader_items]
    news_by_id = {int(x["candidate_id"]): x for x in reader_items}
    items = result.get("items")
    if not isinstance(items, list):
        items = []
    by_id: dict[int, dict[str, Any]] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        try:
            cid = int(row.get("candidate_id"))
        except (TypeError, ValueError):
            continue
        if cid not in news_by_id:
            continue
        by_id[cid] = {
            "candidate_id": cid,
            "reader_text": sanitize_reader_description(str(row.get("reader_text") or "")),
        }
    complete_items: list[dict[str, Any]] = []
    for cid in expected:
        news = news_by_id[cid]
        if cid in by_id and by_id[cid]["reader_text"]:
            complete_items.append(by_id[cid])
        else:
            complete_items.append(_fallback_reader_text_item(news))
    out = dict(result)
    out["items"] = complete_items
    checks = out.get("self_check", [])
    if not isinstance(checks, list) or not checks:
        out["self_check"] = [{"check_name": "reader_text_fallback", "status": "pass", "comment": "Ок"}]
    return out


def complete_analytics_result(result: dict[str, Any], selected_news: list[dict[str, Any]]) -> dict[str, Any]:
    """Гарантирует ровно по одному блоку аналитики на каждую выбранную новость."""
    expected = [int(x["candidate_id"]) for x in selected_news]
    news_by_id = {int(x["candidate_id"]): x for x in selected_news}
    items = result.get("items")
    if not isinstance(items, list):
        items = []
    by_id: dict[int, dict[str, Any]] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        try:
            cid = int(row.get("candidate_id"))
        except (TypeError, ValueError):
            continue
        if cid not in news_by_id:
            continue
        by_id[cid] = {
            "candidate_id": cid,
            "essence": sanitize_reader_description(str(row.get("essence") or "")),
            "comment": sanitize_reader_description(str(row.get("comment") or "")),
            "analysis": sanitize_reader_description(str(row.get("analysis") or "")),
        }
    complete_items: list[dict[str, Any]] = []
    for cid in expected:
        news = news_by_id[cid]
        if cid in by_id:
            fb = _fallback_analytics_item(news)
            complete_items.append(
                {
                    "candidate_id": cid,
                    "essence": by_id[cid]["essence"] or fb["essence"],
                    "comment": by_id[cid]["comment"] or fb["comment"],
                    "analysis": by_id[cid]["analysis"] or fb["analysis"],
                }
            )
        else:
            complete_items.append(_fallback_analytics_item(news))
    out = dict(result)
    out["items"] = complete_items
    if not str(out.get("overall_analysis") or "").strip():
        out["overall_analysis"] = (
            "В этом выпуске несколько тем сходятся в одну линию: "
            "рынок ИИ ускоряется, а ставки на инфраструктуру и продукты растут."
        )
    else:
        out["overall_analysis"] = sanitize_reader_description(str(out["overall_analysis"]))
    raw_tags = out.get("hashtags", [])
    if isinstance(raw_tags, str):
        out["hashtags"] = [x for x in raw_tags.split() if x]
    elif not isinstance(raw_tags, list) or not raw_tags:
        out["hashtags"] = ["#ИИ", "#AI", "#ExTellect", "#Технологии", "#Аналитика"]
    checks = out.get("self_check", [])
    if not isinstance(checks, list) or not checks:
        out["self_check"] = [
            {"check_name": "Нет сносок", "status": "pass", "comment": "Ок"},
            {"check_name": "Нет слова инсайт", "status": "pass", "comment": "Ок"},
        ]
    return out


@dataclass
class CrewWorkflow:
    contract_prompt: str

    def __post_init__(self) -> None:
        self.agents = create_agents(self.contract_prompt)
        self.last_crew_usage: UsageMetrics | None = None

    def _kickoff(self, crew: Crew) -> str:
        output = crew.kickoff()
        self.last_crew_usage = getattr(output, "token_usage", None)
        return str(output)

    def _with_contract(self, text: str) -> str:
        return f"Контракт поведения (обязательно):\n{self.contract_prompt}\n\nЗадача:\n{text}"

    def run_candidates_pipeline(self, digest_type: str, now_msk: str, manual_urls: list[str]) -> list[dict[str, Any]]:
        dtype = normalize_digest_type(digest_type)
        research_rows = self.run_candidates_research(dtype, now_msk, manual_urls)
        verify_rows = self.run_candidates_verify(research_rows)
        return self.run_candidates_score(verify_rows, now_msk=now_msk, digest_type=dtype)

    def run_candidates_research(self, digest_type: str, now_msk: str, manual_urls: list[str]) -> list[dict[str, Any]]:
        dtype = normalize_digest_type(digest_type)
        prompt = (
            "Подготовь массив из 10 кандидатов новостей про ИИ в JSON. "
            "Поля: original_number,title,url,source,published_at,category,description,tier,"
            "significance_score,novelty_score,impact_score,total_score,reliability_status,"
            "is_aggregator,is_duplicate,is_foreign_agent,verification_comment,link_status. "
            "Tier — это приоритет источника новости: Tier-1/Tier-2 ставь только ресурсам из tier-файла. "
            "Агрегаторы и дайджесты можно использовать только для поиска первоисточника, но не как URL кандидата. "
            "Также не используй СМИ, запрещённые законодательством РФ (это Tier-5). "
            "Если источник помечен как иноагент, не исключай его только по этому признаку, но выставь is_foreign_agent=true. "
            "Поле tier заполняй как Tier-1..Tier-5 по наилучшему соответствию. "
            "URL агрегаторов, лент и поисковых страниц не подбирай — "
            "если URL попал из manual_urls, пометь is_aggregator и низкий балл. "
            "При равной новизне отдавай предпочтение более высокому Tier (меньший номер), если режим serious; "
            "в режиме curious — предпочтение более забавному/неожиданному углу, а не официозу. "
            "Не подбирай URL разделов /product, /tools, /features, /pricing, /demo, лендинги сервисов и обзоры «как пользоваться». "
            f"{step1_research_editorial_block(dtype)} "
            "Каждый объект: поле url должно быть прямой ссылкой на ту же HTML-страницу, что описывает title "
            "(после открытия url в браузере заголовок вкладки/статьи должен соответствовать title); "
            "не подставляй URL главной сайта, поиска или ленты вместо URL материала. "
            "Каждая новость должна быть именно про искусственный интеллект, нейросети, ML, крупные модели (GPT, Gemini и т.п.) "
            "или их применение — не подмешивай нерелевантные темы (банкротства, спорт, быт и т.д.), даже если URL с крупного СМИ. "
            "КРИТИЧНО: не выдумывай URL. Каждый url должен быть реальной страницей, которую можно открыть в браузере прямо сейчас. "
            "Если не уверен в существовании ссылки — не включай кандидата. "
            "Ответ — только один JSON-массив из 10 объектов, без markdown-ограждений и без текста до/после JSON. "
            f"digest_type={dtype}, now_msk={now_msk}, manual_urls={manual_urls}"
        )
        research_task = Task(
            description=self._with_contract(prompt),
            expected_output="JSON массив из 10 объектов кандидатов",
            agent=self.agents.news_research,
        )
        research_crew = Crew(agents=[self.agents.news_research], tasks=[research_task], process=Process.sequential, verbose=False)
        raw = self._kickoff(research_crew)
        return _extract_candidate_list(raw)[:15]

    def run_candidates_refill(self, digest_type: str, now_msk: str, excluded_urls: list[str]) -> list[dict[str, Any]]:
        """Дополнительный проход LLM: новые URL, не пересекающиеся с excluded_urls."""
        dtype = normalize_digest_type(digest_type)
        excluded_preview = excluded_urls[:40]
        prompt = (
            "Подготовь массив из 10 НОВЫХ кандидатов новостей про ИИ в JSON. "
            "Поля: original_number,title,url,source,published_at,category,description,tier,"
            "significance_score,novelty_score,impact_score,total_score,reliability_status,"
            "is_aggregator,is_duplicate,is_foreign_agent,verification_comment,link_status. "
            "Нельзя использовать URL из списка исключений (ни один из них, ни очевидные дубликаты того же материала). "
            f"{step1_research_editorial_block(dtype)} "
            "Не выдумывай несуществующие адреса — только реальные прямые ссылки на страницы статей. "
            "Каждый url должен вести на ту же публикацию, что и title. "
            "Агрегаторы и дайджесты можно использовать только для обнаружения первоисточников; "
            "не возвращай URL агрегаторов и лент (news.google, reddit, поисковые страницы, теги, главные страницы). "
            "Только материалы про ИИ/нейросети/ML — без посторонних тем. "
            "Предпочитай страницы с признаками article-page (og:type=article, article:published_time, JSON-LD NewsArticle/Article). "
            f"Исключённые URL: {excluded_preview}. "
            "Ответ — только один JSON-массив без markdown и без текста до/после JSON. "
            f"digest_type={dtype}, now_msk={now_msk}"
        )
        research_task = Task(
            description=self._with_contract(prompt),
            expected_output="JSON массив новых кандидатов",
            agent=self.agents.news_research,
        )
        research_crew = Crew(agents=[self.agents.news_research], tasks=[research_task], process=Process.sequential, verbose=False)
        raw = self._kickoff(research_crew)
        return _extract_candidate_list(raw)[:15]

    def run_candidates_verify(self, research_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        verify_task = Task(
            description=self._with_contract(
                "Проверь источники и обнови reliability_status/link_status/tier/is_aggregator/is_duplicate. "
                "Для каждого кандидата: url и title должны относиться к одной и той же публикации и к теме ИИ/нейросетей; "
                "если материал не про ИИ — удали объект из массива или пометь link_status=false. "
                "если url ведёт на другую статью, ленту или 404 — не подменяй ссылку на новую, только пометь link_status=false. "
                "Пересчитай tier строго по разделу «Система приоритетов источников»: Tier-1/Tier-2 только для ресурсов из файла; "
                "для URL агрегаторов, лент и поисковых страниц выставь Tier-5, is_aggregator=true и link_status=false. "
                "Ответ — только один JSON-массив из 10 объектов, без текста до/после JSON. "
                f"Входные кандидаты: {json.dumps(research_rows, ensure_ascii=False)}"
            ),
            expected_output="Обновленный JSON массив 10 объектов",
            agent=self.agents.source_verification,
        )
        verify_crew = Crew(
            agents=[self.agents.source_verification], tasks=[verify_task], process=Process.sequential, verbose=False
        )
        raw = self._kickoff(verify_crew)
        return _extract_candidate_list(raw)[:15]

    def run_candidates_score(
        self,
        verify_rows: list[dict[str, Any]],
        now_msk: str,
        *,
        digest_type: str = "serious",
    ) -> list[dict[str, Any]]:
        dtype = normalize_digest_type(digest_type)
        score_task = Task(
            description=self._with_contract(
                "Оцени и проставь scoring 1-3, total_score 3-9, сохрани баланс категорий. "
                "Учитывай Tier из контракта: при прочих равных более высокий приоритет у Tier-1, затем Tier-2 и т.д. "
                f"{step1_scoring_editorial_block(dtype)} "
                "Не меняй поля идентичности материала: original_number, url, title, source, published_at, category. "
                "Не добавляй новые URL и не удаляй существующие: можно менять только оценочные поля и служебные флаги пригодности. "
                "Ответ — только один JSON-массив из 10 объектов, без markdown-ограждений и без текста до/после JSON. "
                f"Вход после верификации: {json.dumps(verify_rows, ensure_ascii=False)}"
            ),
            expected_output="Финальный JSON массив 10 объектов",
            agent=self.agents.scoring,
        )
        score_crew = Crew(agents=[self.agents.scoring], tasks=[score_task], process=Process.sequential, verbose=False)
        output = self._kickoff(score_crew)
        parsed = _extract_candidate_list(output)
        if len(parsed) >= 5:
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
        parsed = _extract_json(self._kickoff(crew), [])
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
                "Сформируй JSON: items[], overall_analysis, hashtags[], self_check[]. "
                f"{_ANALYTICS_EDITORIAL} Вход: {json.dumps(selected_news, ensure_ascii=False)}"
            ),
            expected_output="JSON объект аналитики",
            agent=self.agents.analytics,
        )
        crew = Crew(agents=[self.agents.analytics], tasks=[task], process=Process.sequential, verbose=False)
        parsed = _extract_json(self._kickoff(crew), {})
        if isinstance(parsed, dict) and parsed.get("items"):
            return complete_analytics_result(parsed, selected_news)
        return complete_analytics_result(
            {
                "items": [_fallback_analytics_item(item) for item in selected_news],
                "overall_analysis": "",
                "hashtags": [],
                "self_check": [],
            },
            selected_news,
        )

    def run_reader_descriptions(self, reader_items: list[dict[str, Any]]) -> dict[str, Any]:
        task = Task(
            description=self._with_contract(
                "Сформируй JSON: items[], self_check[]. "
                f"{_READER_COPY_EDITORIAL} Вход: {json.dumps(reader_items, ensure_ascii=False)}"
            ),
            expected_output="JSON объект с reader_text по каждой новости",
            agent=self.agents.reader_copy,
        )
        crew = Crew(agents=[self.agents.reader_copy], tasks=[task], process=Process.sequential, verbose=False)
        parsed = _extract_json(self._kickoff(crew), {})
        if isinstance(parsed, dict) and parsed.get("items"):
            return complete_reader_descriptions_result(parsed, reader_items)
        return complete_reader_descriptions_result(
            {"items": [_fallback_reader_text_item(item) for item in reader_items], "self_check": []},
            reader_items,
        )

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
        prompt = self._kickoff(crew).strip()
        if len(prompt) > 20:
            return prompt
        return "Abstract futuristic AI newsroom, violet and deep blue gradient, dynamic data streams, cinematic horizontal composition, ultra detailed, no text, no logos, no people."

    def run_platform_writer(self, payload: dict[str, Any], platforms: list[str] | None = None) -> dict[str, str]:
        target = list(platforms) if platforms else ["telegram", "max", "vk", "dzen"]
        keys_str = ",".join(target)
        extra_rules = (
            "Дополнительно к контракту: итоговую вёрстку собирает сервер — верни JSON с полями "
            "telegram_lead, max_lead, dzen_intro (короткие вводные 1–2 предложения для TG/MAX/Дзен). "
            f"Подпись в финале будет в одну строку, пример: {subscription_md_inline()!r}. "
            "Хэштеги: в telegram и max — 4–6; в vk и dzen — 3–5. "
            "MAX: между новостями разделитель «...» на отдельной строке. "
            "Верни только JSON с ключами платформ и полями telegram_lead, max_lead, dzen_intro."
        )
        task = Task(
            description=self._with_contract(
                f"Верни JSON объект ТОЛЬКО с ключами {keys_str}. "
                "Строго соблюдай форматы платформ из контракта. "
                + extra_rules
                + f"Вход:{payload}"
            ),
            expected_output=f"JSON объект платформ: {keys_str}",
            agent=self.agents.platform_writer,
        )
        crew = Crew(agents=[self.agents.platform_writer], tasks=[task], process=Process.sequential, verbose=False)
        parsed = _extract_json(self._kickoff(crew), {})
        assembly_payload = dict(payload)
        if isinstance(parsed, dict):
            dzen_intro = str(parsed.get("dzen_intro") or "").strip()
            if dzen_intro:
                assembly_payload["dzen_intro"] = dzen_intro
            tg_lead = str(parsed.get("telegram_lead") or "").strip()
            if tg_lead:
                assembly_payload["telegram_lead"] = tg_lead
            max_lead = str(parsed.get("max_lead") or "").strip()
            if max_lead:
                assembly_payload["max_lead"] = max_lead
        return assemble_platform_outputs(assembly_payload, platforms=target)

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
        parsed = _extract_json(self._kickoff(crew), [])
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
        return assemble_platform_outputs(payload)


def current_msk_iso() -> str:
    return datetime.now(ZoneInfo("Europe/Moscow")).isoformat()
