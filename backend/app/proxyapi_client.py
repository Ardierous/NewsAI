import base64
import logging
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.config import get_settings

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
                size="1200x630",
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
