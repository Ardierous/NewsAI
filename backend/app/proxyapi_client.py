import base64
import logging
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.config import get_settings
from app.services.news_search import extract_http_urls_from_text, extract_urls_from_responses_payload

logger = logging.getLogger("app.proxyapi")


class ProxyApiClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.proxyapi_api_key,
            base_url=settings.proxyapi_base_url,
            default_headers={"Authorization": f"Bearer {settings.proxyapi_api_key}"},
        )

    def chat(self, system_prompt: str, user_prompt: str, model: str | None = None) -> str:
        try:
            response = self.client.chat.completions.create(
                model=model or self.settings.proxyapi_model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
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

    def search_news_article_urls(self, query: str, limit: int = 15) -> list[str]:
        """
        Реальный веб-поиск через ProxyAPI (OpenAI Responses API + tool web_search).
        Документация: https://proxyapi.ru/docs/openai-web-search
        """
        if not self.settings.proxyapi_web_search_enabled:
            return []
        user_prompt = (
            f"Найди до {limit} свежих новостей за последние 96 часов (часовой пояс Europe/Moscow) по теме: {query}. "
            "Только материалы про искусственный интеллект, нейросети, машинное обучение или крупные модели (GPT, Gemini, Claude и т.п.). "
            "Нужны прямые URL HTML-статей с сайтов изданий (не агрегаторы, не Google News, не Reddit, не главные страницы, не поиск). "
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
                "search_context_size": self.settings.proxyapi_web_search_context_size,
                "user_location": location,
            }
        ]
        model = self.settings.proxyapi_web_search_model
        try:
            response = self.client.responses.create(
                model=model,
                tools=tools,
                input=[{"role": "user", "content": user_prompt}],
            )
            urls = extract_urls_from_responses_payload(response, limit=limit)
            if urls:
                logger.info("ProxyAPI web_search (responses) | model=%s count=%s", model, len(urls))
                return urls
        except Exception:
            logger.warning("ProxyAPI responses web_search failed, fallback to search-preview", exc_info=True)
        return self._search_news_urls_chat_preview(user_prompt, limit)

    def _search_news_urls_chat_preview(self, user_prompt: str, limit: int) -> list[str]:
        preview_model = self.settings.proxyapi_web_search_preview_model
        try:
            response = self.client.chat.completions.create(
                model=preview_model,
                messages=[{"role": "user", "content": user_prompt}],
                web_search_options={
                    "search_context_size": self.settings.proxyapi_web_search_context_size,
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
            text = response.choices[0].message.content or ""
            urls = extract_http_urls_from_text(text, limit=limit)
            if urls:
                logger.info("ProxyAPI web_search (chat preview) | model=%s count=%s", preview_model, len(urls))
            return urls
        except Exception:
            logger.exception("ProxyAPI chat search-preview failed | model=%s", preview_model)
            raise RuntimeError("ProxyAPI web search request failed") from None

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
