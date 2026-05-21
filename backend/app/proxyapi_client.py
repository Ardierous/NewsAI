import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.config import get_settings
from app.crew.model_policy import STEP2_AI_ORDER_MODEL, proxyapi_chat_model
from app.services.news_search import extract_http_urls_from_text, extract_urls_from_responses_payload

logger = logging.getLogger("app.proxyapi")


def _is_proxyapi_budget_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "budget exceeded" in text or "402" in text


class ProxyApiClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self._last_api_response: Any = None
        self.last_error_kind: str | None = None
        self.client = OpenAI(
            api_key=settings.proxyapi_api_key,
            base_url=settings.proxyapi_base_url,
            default_headers={"Authorization": f"Bearer {settings.proxyapi_api_key}"},
        )

    def chat(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
        try:
            response = self.client.chat.completions.create(
                model=proxyapi_chat_model(model or self.settings.proxyapi_model),
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            self._last_api_response = response
            return response.choices[0].message.content or ""
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            logger.exception("Ошибка chat.completions | model=%s", model or self.settings.proxyapi_model)
            raise RuntimeError("ProxyAPI chat request failed") from exc

    def generate_image(self, prompt: str, output_file: Path, model: str | None = None) -> Path:
        try:
            image = self.client.images.generate(
                model=model or self.settings.proxyapi_image_model,
                prompt=prompt,
                # Для gpt-image-1 ProxyAPI принимает только фиксированный набор размеров.
                size="1536x1024",
            )
            self._last_api_response = image
            b64_json = image.data[0].b64_json
            output_file.write_bytes(base64.b64decode(b64_json))
            logger.info(
                "Изображение сохранено | path=%s model=%s",
                output_file.name,
                model or self.settings.proxyapi_image_model,
            )
            return output_file
        except Exception as exc:
            logger.exception("Ошибка images.generate | model=%s", model or self.settings.proxyapi_image_model)
            raise RuntimeError("ProxyAPI image generation failed") from exc

    def search_news_article_urls(
        self,
        query: str,
        limit: int = 15,
        *,
        search_context_size: str | None = None,
        allowed_hosts: list[str] | None = None,
    ) -> list[str]:
        """
        Реальный веб-поиск через ProxyAPI (OpenAI Responses API + tool web_search).
        Документация: https://proxyapi.ru/docs/openai-web-search
        """
        if not self.settings.proxyapi_web_search_enabled:
            return []
        ctx_size = (search_context_size or self.settings.proxyapi_web_search_context_size).strip().lower()
        if ctx_size not in ("low", "medium", "high"):
            ctx_size = "medium"
        user_prompt = (
            f"Найди до {limit} свежих новостей (часовой пояс Europe/Moscow) по теме: {query}. "
            "Строго соблюдай ограничения дат из запроса (after:/окно публикации). "
            "Только материалы про искусственный интеллект, нейросети, машинное обучение или крупные модели (GPT, Gemini, Claude и т.п.). "
        )
        if allowed_hosts:
            hosts_csv = ", ".join(str(h).strip() for h in allowed_hosts if str(h).strip())
            user_prompt += (
                f"Искать ТОЛЬКО на доменах из политики источников: {hosts_csv}. "
                "Не возвращай URL с других сайтов. "
            )
        else:
            user_prompt += (
                "Можно использовать агрегаторы и дайджесты для поиска сюжета, но итоговые ссылки должны вести на первоисточник. "
            )
        user_prompt += (
            "Нужны прямые URL отдельных HTML-статей (не рубрики, не ленты, не разделы вроде /neiroseti или /articles/artificial_intelligence/, "
            "не агрегаторы, не Google News, не Reddit, не главные страницы, не поиск). "
            f"Ответ: строго JSON-массив из не более {limit} строк — каждая строка один полный URL, без markdown и без пояснений."
        )
        location = {
            "type": "approximate",
            "country": "RU",
            "city": "Moscow",
            "region": "Moscow",
        }
        tools = [
            {
                "type": "web_search",
                "search_context_size": ctx_size,
                "user_location": location,
            }
        ]
        model = self.settings.proxyapi_web_search_model
        self.last_error_kind = None
        try:
            response = self.client.responses.create(
                model=model,
                tools=tools,
                input=[{"role": "user", "content": user_prompt}],
            )
            self._last_api_response = response
            urls = extract_urls_from_responses_payload(response, limit=limit)
            if urls:
                logger.info(
                    "ProxyAPI web_search (responses) | model=%s count=%s context=%s",
                    model,
                    len(urls),
                    ctx_size,
                )
                return urls
            logger.warning(
                "ProxyAPI web_search (responses): пустой список URL, без повторного search-preview | model=%s",
                model,
            )
            return []
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            logger.warning("ProxyAPI responses web_search failed, fallback to search-preview", exc_info=True)
        return self._search_news_urls_chat_preview(user_prompt, limit, search_context_size=ctx_size)

    def _search_news_urls_chat_preview(
        self,
        user_prompt: str,
        limit: int,
        *,
        search_context_size: str = "medium",
    ) -> list[str]:
        preview_model = self.settings.proxyapi_web_search_preview_model
        ctx_size = search_context_size if search_context_size in ("low", "medium", "high") else "medium"
        try:
            response = self.client.chat.completions.create(
                model=preview_model,
                messages=[{"role": "user", "content": user_prompt}],
                web_search_options={
                    "search_context_size": ctx_size,
                    "user_location": {
                        "type": "approximate",
                        "approximate": {
                            "country": "RU",
                            "city": "Moscow",
                            "region": "Moscow",
                        },
                    },
                },
            )
            self._last_api_response = response
            text = response.choices[0].message.content or ""
            urls = extract_http_urls_from_text(text, limit=limit)
            if urls:
                logger.info(
                    "ProxyAPI web_search (chat preview) | model=%s count=%s context=%s",
                    preview_model,
                    len(urls),
                    ctx_size,
                )
            return urls
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            logger.exception("ProxyAPI chat search-preview failed | model=%s", preview_model)
            return []

    def suggest_news_order(
        self, items: list[dict[str, Any]], digest_type: str = "serious", model: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Оптимальный порядок пятёрки для удержания читателя (без CrewAI).
        Возвращает список {candidate_id, output_position, ordering_reason}.
        """
        use_model = model or STEP2_AI_ORDER_MODEL
        system_prompt = (
            "Ты выпускающий редактор дайджеста ExTellect про искусственный интеллект. "
            "Твоя задача — расставить ровно 5 уже отобранных новостей в порядке output_position от 1 до 5 "
            "так, чтобы максимизировать интерес читателя к выпуску: сильный заход в позиции 1, "
            "логичный ритм в середине, запоминающийся финал в позиции 5. "
            "Учитывай заголовки, описания, баллы total_score и tier источника. "
            "Нельзя добавлять или удалять candidate_id — только переставить. "
            "Ответ — только JSON-массив из 5 объектов без markdown."
        )
        user_prompt = (
            f"digest_type={digest_type}\n"
            f"Новости для упорядочивания:\n{json.dumps(items, ensure_ascii=False)}\n"
            "Поля ответа каждого объекта: candidate_id (int), output_position (1..5, все уникальны), "
            "ordering_reason (1–2 предложения на русском: почему эта позиция удерживает читателя)."
        )
        raw = self.chat(system_prompt, user_prompt, model=use_model)
        parsed = _parse_ordering_response(raw)
        allowed_ids = {int(x["candidate_id"]) for x in items if x.get("candidate_id") is not None}
        if not isinstance(parsed, list) or len(parsed) != 5:
            return _fallback_news_order(items)
        out: list[dict[str, Any]] = []
        seen_pos: set[int] = set()
        seen_ids: set[int] = set()
        for row in parsed:
            if not isinstance(row, dict):
                return _fallback_news_order(items)
            try:
                cid = int(row.get("candidate_id"))
                pos = int(row.get("output_position"))
            except (TypeError, ValueError):
                return _fallback_news_order(items)
            if cid not in allowed_ids or cid in seen_ids or pos < 1 or pos > 5 or pos in seen_pos:
                return _fallback_news_order(items)
            seen_ids.add(cid)
            seen_pos.add(pos)
            reason = str(row.get("ordering_reason") or "").strip() or f"Позиция {pos}: редакционный ритм выпуска."
            out.append({"candidate_id": cid, "output_position": pos, "ordering_reason": reason[:500]})
        if seen_ids != allowed_ids or seen_pos != {1, 2, 3, 4, 5}:
            return _fallback_news_order(items)
        out.sort(key=lambda x: int(x["output_position"]))
        return out

    def response_json(self, system_prompt: str, user_prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.responses.create(
                model=self.settings.proxyapi_model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={"format": {"type": "json_schema", "name": "payload", "schema": response_schema}},
            )
            text = response.output_text
            import json

            return json.loads(text)
        except Exception as exc:
            logger.exception("Ошибка responses.create | model=%s", self.settings.proxyapi_model)
            raise RuntimeError("ProxyAPI responses request failed") from exc


def _parse_ordering_response(raw: str) -> Any:
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()
    try:
        if text.startswith("["):
            return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return []


def _fallback_news_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(items, key=lambda x: (-int(x.get("total_score") or 0), int(x.get("candidate_id") or 0)))
    return [
        {
            "candidate_id": int(item["candidate_id"]),
            "output_position": idx,
            "ordering_reason": f"Позиция {idx}: резервный порядок по суммарному баллу ({item.get('total_score', 0)}).",
        }
        for idx, item in enumerate(ranked, start=1)
    ]
