import base64
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import re
from pathlib import Path
from typing import Any, Iterator
from datetime import date

from openai import OpenAI

from app.config import get_settings
from app.crew.model_policy import STEP2_AI_ORDER_MODEL, proxyapi_chat_model
from app.source_tiers_policy import get_source_tiers_policy
from app.services.news_search import (
    extract_http_urls_from_text,
    extract_urls_from_chat_web_search_response,
    extract_urls_from_responses_payload,
)
from app.services.step1_web_search_stats import (
    consume_web_search_api_call,
    mark_step1_web_search_api_cap_hit,
    record_empty_citation_web_search,
    refund_web_search_api_call,
    reset_empty_citation_streak,
)
from app.services.usage_cost import estimate_cost_rub_from_usage, estimate_proxyapi_request_fee_rub, extract_token_usage

logger = logging.getLogger("app.proxyapi")


def build_web_search_user_prompt(
    query: str,
    limit: int,
    *,
    curious_search: bool = False,
    allowed_hosts: list[str] | None = None,
    source_tiers_path: Any = None,
) -> str:
    """Текст user-сообщения, которое уходит в ProxyAPI responses.create(input=…)."""
    if curious_search:
        user_prompt = (
            f"Найди до {limit} СВЕЖИХ новостей (часовой пояс Europe/Moscow, строго после даты в after: из запроса). "
            "ОБЯЗАТЕЛЬНО: игнорируй все старые/архивные/evergreen статьи даже если они идеально релевантны по ключам — бери ТОЛЬКО опубликованные после указанной даты (after:). "
            "Если инструмент поиска возвращает старые URL — отбрасывай их и продолжай поиск свежих. "
            "Приоритет: публикации за последние 1-7 дней в пределах окна. "
            "Тема выпуска: свежие курьёзы, фейлы и забавные истории про ИИ, нейросети, LLM и чат-ботов — не сухие tech-новости. "
            "Режим курьёзного дайджеста: ищи ТОЛЬКО забавные, смешные, вирусные, абсурдные и неожиданные истории про ИИ "
            "(фейлы, глюки, жалобы пользователей, мемы, дипфейки, странные кейсы, кринж, пранки). "
            "НЕ возвращай обычные tech/product-новости, обзоры моделей, регуляторику, инвестиции, конференции и пресс-релизы. "
            "Если материал звучит как деловая или нейтральная AI-новость без юмора/фейла — пропускай. "
        )
    else:
        user_prompt = (
            f"Найди до {limit} СВЕЖИХ новостей про ИИ, нейросети, LLM (часовой пояс Europe/Moscow, строго после даты в after: из запроса). "
            "ОБЯЗАТЕЛЬНО: игнорируй все старые/архивные/evergreen статьи даже если они идеально релевантны по ключам — бери ТОЛЬКО опубликованные после указанной даты (after:). "
            "Если инструмент поиска возвращает старые URL — отбрасывай их и продолжай поиск свежих. "
            "Приоритет: публикации за последние 1-7 дней в пределах окна. "
            "Тема выпуска: новости про искусственный интеллект, нейросети, LLM и крупные языковые модели "
            "(GPT, Gemini, Claude, Яндекс, Гигачат, Qwen, DeepSeek и т.п.), машинное обучение. "
        )
    if allowed_hosts:
        clean_hosts = [str(h).strip() for h in allowed_hosts if str(h).strip()]
        hosts_csv = ", ".join(clean_hosts)
        if curious_search:
            user_prompt += (
                f"Искать ТОЛЬКО на доменах курьёзных источников: {hosts_csv}. "
                "Не возвращай URL с других сайтов. "
            )
        else:
            policy = get_source_tiers_policy(source_tiers_path)
            has_aggregator_scope = any(
                any(marker in host.lower() for marker in policy.aggregator_hosts) for host in clean_hosts
            )
            if has_aggregator_scope:
                user_prompt += (
                    f"Опорные домены для поиска сюжета: {hosts_csv}. "
                    "Можно использовать их как агрегаторы-навигацию, но в ответе верни прямые URL первоисточников "
                    "на сайтах из policy tier-1..tier-4. "
                )
            else:
                user_prompt += (
                    f"Искать ТОЛЬКО на доменах из политики источников: {hosts_csv}. "
                    "Не возвращай URL с других сайтов. "
                )
    else:
        user_prompt += (
            "Можно использовать агрегаторы и дайджесты для поиска сюжета, но итоговые ссылки должны вести на первоисточник. "
        )
    clean_query = (query or "").strip()
    if clean_query:
        user_prompt += (
            f"\n\nПоисковый запрос (используй его при вызове web_search, включая after:/before: и site:):\n"
            f"{clean_query}\n"
        )
    user_prompt += (
        "\nНужны прямые URL отдельных HTML-статей (не рубрики, не ленты, не разделы вроде /neiroseti или /articles/artificial_intelligence/, "
        "не агрегаторы, не Google News, не Reddit, не главные страницы, не поиск). "
        "КРИТИЧНО: не выдумывай и не конструируй URL по шаблону — копируй только точные ссылки из результатов поиска (snippets). "
        "Не подставляй даты или numeric id «на глаз» (типичные 404 на technologyreview.com, wired.com и т.п.). "
        f"Ответ: строго JSON-массив из не более {limit} строк — каждая строка один полный URL, без markdown и без пояснений."
    )
    return user_prompt
_EST_COST_TOTAL_RUB = 0.0
_LOG_CONTEXT: ContextVar[dict[str, str]] = ContextVar("proxyapi_log_context", default={})
_BILLABLE_WEB_SEARCH_KINDS = frozenset({"responses.web_search", "chat.web_search_preview"})


def _safe_header_value(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\r\n]+", " ", text)
    return text[:256]


def _log_headers(source: str) -> dict[str, str]:
    ctx = dict(_LOG_CONTEXT.get() or {})
    ctx["Source"] = source
    ctx.setdefault("App", "news-digest")
    headers: dict[str, str] = {}
    for key, value in ctx.items():
        safe_key = re.sub(r"[^A-Za-z0-9_-]+", "-", str(key).strip())[:64].strip("-")
        safe_value = _safe_header_value(value)
        if safe_key and safe_value:
            headers[f"X-Log-{safe_key}"] = safe_value
    return headers


@contextmanager
def proxyapi_log_context(**kwargs: Any) -> Iterator[None]:
    clean = {str(k): _safe_header_value(v) for k, v in kwargs.items() if v is not None}
    token = _LOG_CONTEXT.set({**(_LOG_CONTEXT.get() or {}), **clean})
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def set_proxyapi_log_context(**kwargs: Any) -> None:
    """Метаданные для ProxyAPI X-Log-* в текущем рабочем потоке."""
    clean = {str(k): _safe_header_value(v) for k, v in kwargs.items() if v is not None}
    _LOG_CONTEXT.set(clean)


def _extract_cached_tokens(usage: Any) -> int | None:
    if usage is None:
        return None
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if cached is not None:
            try:
                return int(cached)
            except (TypeError, ValueError):
                pass
    if isinstance(usage, dict):
        nested = usage.get("prompt_tokens_details") or {}
        if isinstance(nested, dict) and nested.get("cached_tokens") is not None:
            try:
                return int(nested["cached_tokens"])
            except (TypeError, ValueError):
                return None
    return None


def _log_proxyapi_usage(response: Any, *, kind: str, model: str) -> None:
    global _EST_COST_TOTAL_RUB
    request_id = _proxyapi_request_id(response)
    tokens = extract_token_usage(response)
    service_fee = estimate_proxyapi_request_fee_rub(kind) if kind in _BILLABLE_WEB_SEARCH_KINDS else 0.0
    prompt = completion = cached = None
    token_cost_rub = 0.0
    est_cost_rub: float | None = None

    if tokens is not None:
        prompt, completion = tokens
        est_cost_rub = estimate_cost_rub_from_usage(
            model,
            int(prompt or 0),
            int(completion or 0),
            kind=kind,
        )
        if est_cost_rub is not None and service_fee > 0:
            token_cost_rub = max(0.0, float(est_cost_rub) - service_fee)
    elif kind in _BILLABLE_WEB_SEARCH_KINDS and service_fee > 0:
        est_cost_rub = round(service_fee, 6)

    if kind in _BILLABLE_WEB_SEARCH_KINDS and est_cost_rub is not None:
        try:
            from app.services.step1_web_search_stats import record_web_search_est_cost

            record_web_search_est_cost(service_rub=service_fee, token_rub=token_cost_rub)
        except ImportError:
            pass

    usage = getattr(response, "usage", None)
    if usage is not None:
        cached = _extract_cached_tokens(usage)
        if prompt is None:
            prompt = getattr(usage, "prompt_tokens", None) or (
                usage.get("prompt_tokens") if isinstance(usage, dict) else None
            )
        if completion is None:
            completion = getattr(usage, "completion_tokens", None) or (
                usage.get("completion_tokens") if isinstance(usage, dict) else None
            )

    if est_cost_rub is not None:
        _EST_COST_TOTAL_RUB += est_cost_rub

    if kind in _BILLABLE_WEB_SEARCH_KINDS or tokens is not None or est_cost_rub is not None:
        logger.info(
            "ProxyAPI usage | kind=%s model=%s request_id=%s prompt_tokens=%s completion_tokens=%s "
            "cached_tokens=%s service_fee_rub=%s token_cost_rub=%s est_cost_rub=%s est_total_rub=%s",
            kind,
            model,
            request_id or "-",
            prompt if prompt is not None else "-",
            completion if completion is not None else "-",
            cached if cached is not None else "-",
            f"{service_fee:.6f}" if service_fee > 0 else "-",
            f"{token_cost_rub:.6f}" if token_cost_rub > 0 else "-",
            f"{est_cost_rub:.6f}" if est_cost_rub is not None else "-",
            f"{_EST_COST_TOTAL_RUB:.6f}",
        )
        return

    if request_id:
        logger.info("ProxyAPI request | kind=%s model=%s request_id=%s usage=-", kind, model, request_id)


def _proxyapi_request_id(response: Any) -> str | None:
    for attr in ("_request_id", "request_id", "id"):
        value = getattr(response, attr, None)
        if value:
            return str(value)
    return None


def _proxyapi_error_details(exc: BaseException) -> tuple[str | None, str | None, str | None, str | None]:
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    error_type = getattr(exc, "type", None) or getattr(exc, "code", None)
    error_message = getattr(exc, "message", None)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            error_type = error_type or err.get("type") or err.get("code")
            error_message = error_message or err.get("message")
        else:
            error_type = error_type or body.get("type") or body.get("code")
            error_message = error_message or body.get("message")
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if request_id is None and headers is not None:
        try:
            request_id = headers.get("X-Request-ID") or headers.get("x-request-id")
        except Exception:
            request_id = None
    return (
        str(status) if status is not None else None,
        str(request_id) if request_id else None,
        str(error_type) if error_type else None,
        str(error_message) if error_message else None,
    )


def _log_proxyapi_exception(exc: BaseException, *, kind: str, model: str) -> None:
    status, request_id, error_type, error_message = _proxyapi_error_details(exc)
    logger.warning(
        "ProxyAPI error | kind=%s model=%s status=%s request_id=%s error_type=%s error_message=%s error=%s",
        kind,
        model,
        status or "-",
        request_id or "-",
        error_type or "-",
        (error_message or "-")[:500],
        str(exc)[:500],
    )


def _is_proxyapi_budget_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    return (
        status == 402
        or "budget exceeded" in text
        or "402" in text
        or ("insufficient" in text and "balance" in text)
        or ("нулев" in text and "баланс" in text)
        or "zero balance" in text
        or ("balance" in text and "0.0" in text)
    )


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

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        *,
        max_completion_tokens: int = 800,
        log_source: str = "chat",
    ) -> str:
        use_model = proxyapi_chat_model(model or self.settings.proxyapi_model)
        try:
            response = self.client.chat.completions.create(
                model=use_model,
                temperature=0.2,
                max_completion_tokens=max(16, int(max_completion_tokens or 800)),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                extra_headers=_log_headers(log_source),
            )
            self._last_api_response = response
            _log_proxyapi_usage(response, kind="chat.completions", model=use_model)
            return response.choices[0].message.content or ""
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            _log_proxyapi_exception(exc, kind="chat.completions", model=use_model)
            logger.exception("Ошибка chat.completions | model=%s", use_model)
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
        fallback_on_empty: bool = False,
        curious_search: bool = False,
    ) -> list[str]:
        """
        Реальный веб-поиск через ProxyAPI (OpenAI Responses API + tool web_search).
        Документация: https://proxyapi.ru/docs/openai-web-search
        """
        if not self.settings.proxyapi_web_search_enabled:
            return []
        if not consume_web_search_api_call(kind="responses"):
            logger.warning("ProxyAPI web_search: лимит API-вызовов шага 1 исчерпан | query=%s", query[:120])
            self.last_error_kind = "api_cap"
            return []
        ctx_size = (search_context_size or self.settings.proxyapi_web_search_context_size).strip().lower()
        if ctx_size not in ("low", "medium", "high"):
            ctx_size = "medium"
        user_prompt = build_web_search_user_prompt(
            query,
            limit,
            curious_search=curious_search,
            allowed_hosts=allowed_hosts,
            source_tiers_path=self.settings.source_tiers_path,
        )
        logger.info(
            "ProxyAPI web_search prompt | context=%s curious=%s hosts=%s prompt_len=%s",
            ctx_size,
            curious_search,
            len(allowed_hosts or []),
            len(user_prompt),
        )
        logger.debug("ProxyAPI web_search user_prompt:\n%s", user_prompt)
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
                max_output_tokens=min(900, max(220, int(limit) * 28)),
                extra_headers=_log_headers("step1_web_search"),
            )
            self._last_api_response = response
            _log_proxyapi_usage(response, kind="responses.web_search", model=model)
            urls = extract_urls_from_responses_payload(response, limit=limit, citations_only=True)
            if urls:
                reset_empty_citation_streak()
                logger.info(
                    "ProxyAPI web_search (responses) | model=%s count=%s context=%s citations_only=true",
                    model,
                    len(urls),
                    ctx_size,
                )
                return urls
            refund_web_search_api_call(kind="responses")
            record_empty_citation_web_search()
            logger.warning(
                "ProxyAPI web_search (responses): нет citation URL | model=%s preview=%s",
                model,
                fallback_on_empty,
            )
            from app.services.step1_web_search_stats import (
                step1_strict_web_search_economy,
                step1_web_search_api_cap_reached,
            )

            allow_preview = fallback_on_empty and (
                not step1_strict_web_search_economy(self.settings)
                or not step1_web_search_api_cap_reached()
            )
            if allow_preview:
                preview_urls = self._search_news_urls_chat_preview(
                    user_prompt,
                    limit,
                    search_context_size=ctx_size,
                )
                if preview_urls:
                    logger.info(
                        "ProxyAPI web_search: fallback chat-preview (citations) | model=%s count=%s context=%s",
                        self.settings.proxyapi_web_search_preview_model,
                        len(preview_urls),
                        ctx_size,
                    )
                return preview_urls
            return []
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            _log_proxyapi_exception(exc, kind="responses.web_search", model=model)
            logger.warning("ProxyAPI responses web_search failed, fallback to search-preview", exc_info=True)
        from app.services.step1_web_search_stats import (
            step1_strict_web_search_economy,
            step1_web_search_api_cap_reached,
        )

        if step1_strict_web_search_economy(self.settings) and step1_web_search_api_cap_reached():
            return []
        if step1_strict_web_search_economy(self.settings) and not fallback_on_empty:
            return []
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
        if not consume_web_search_api_call(kind="preview"):
            mark_step1_web_search_api_cap_hit()
            return []
        try:
            response = self.client.chat.completions.create(
                model=preview_model,
                messages=[{"role": "user", "content": user_prompt}],
                max_completion_tokens=min(900, max(220, int(limit) * 28)),
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
                extra_headers=_log_headers("step1_web_search_preview"),
            )
            self._last_api_response = response
            _log_proxyapi_usage(response, kind="chat.web_search_preview", model=preview_model)
            urls = extract_urls_from_chat_web_search_response(response, limit=limit, citations_only=True)
            if urls:
                reset_empty_citation_streak()
                logger.info(
                    "ProxyAPI web_search (chat preview) | model=%s count=%s context=%s citations_only=true",
                    preview_model,
                    len(urls),
                    ctx_size,
                )
                return urls
            refund_web_search_api_call(kind="preview")
            record_empty_citation_web_search()
            return []
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            _log_proxyapi_exception(exc, kind="chat.web_search_preview", model=preview_model)
            logger.exception("ProxyAPI chat search-preview failed | model=%s", preview_model)
            return []

    def fetch_telegram_digest_seed_urls(
        self,
        channel: str,
        *,
        earliest_date: date | None = None,
        max_digest_posts: int = 3,
        post_text_filter: str = "Дайджест",
        max_links: int = 30,
        max_pages: int = 2,
        search_context_size: str | None = None,
    ) -> tuple[list[str], list[str]]:
        """
        Ссылки из публичной ленты t.me/s/ через ProxyAPI web_search (без прямого доступа backend к t.me).
        Возвращает (urls из ответа модели, фрагменты HTML с tgme_widget_message для локального парсера).
        """
        from app.services.telegram_channel_monitor import normalize_channel_username

        _ = max_pages  # зарезервировано для будущей пагинации через несколько web_search-запросов

        if not self.settings.proxyapi_web_search_enabled:
            return [], []

        ch = normalize_channel_username(channel) or (channel or "").strip().lower()
        if not ch:
            return [], []

        ctx_size = (search_context_size or "high").strip().lower()
        if ctx_size not in ("low", "medium", "high"):
            ctx_size = "high"

        channel_url = f"https://t.me/s/{ch}"
        date_clause = ""
        if isinstance(earliest_date, date):
            date_clause = (
                f"Учитывай только посты не старше {earliest_date.isoformat()} (Europe/Moscow). "
            )

        user_prompt = (
            f"Открой публичную веб-ленту Telegram {channel_url} (можно искать «site:t.me/s/{ch} Утро-Дайджест»). "
            f"Найди {max_digest_posts} последних постов, в заголовке или тексте которых есть «{post_text_filter}» "
            f"(формат «Утро-Дайджест ДД.ММ.ГГГГ»). Пропускай посты про вакансии и рекламу без «{post_text_filter}». "
            f"{date_clause}"
            "Из каждого такого поста извлеки все гиперссылки на внешние новостные статьи (http/https). "
            "Не включай: t.me, telegram.me, telegram.org, telesco.pe, technokratos.com и служебные ссылки канала. "
            f"Верни строго JSON-массив из не более {max_links} строк — каждая строка один полный URL статьи, "
            "без markdown и пояснений. Если видишь HTML ленты с классом tgme_widget_message — используй его для ссылок."
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
                max_output_tokens=min(1200, max(350, int(max_links) * 30)),
                extra_headers=_log_headers("step1_telegram_search"),
            )
            self._last_api_response = response
            _log_proxyapi_usage(response, kind="responses.telegram_search", model=model)
            urls = extract_urls_from_responses_payload(response, limit=max_links, citations_only=True)
            html_pages = self._extract_tme_html_snippets(response)
            if urls:
                logger.info(
                    "ProxyAPI telegram digest | channel=%s urls=%s context=%s",
                    ch,
                    len(urls),
                    ctx_size,
                )
            return urls, html_pages
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            _log_proxyapi_exception(exc, kind="responses.telegram_search", model=model)
            logger.warning(
                "ProxyAPI telegram digest failed, fallback to search-preview | channel=%s",
                ch,
                exc_info=True,
            )
        preview_urls = self._search_telegram_urls_chat_preview(
            user_prompt,
            max_links,
            search_context_size=ctx_size,
        )
        return preview_urls, []

    def _extract_tme_html_snippets(self, response: Any) -> list[str]:
        snippets: list[str] = []
        blobs: list[str] = []
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            blobs.append(output_text)
        for item in getattr(response, "output", None) or []:
            for block in getattr(item, "content", None) or []:
                if getattr(block, "type", None) == "output_text":
                    text = getattr(block, "text", "") or ""
                    if text.strip():
                        blobs.append(text)
        for text in blobs:
            if "tgme_widget_message" not in text:
                continue
            snippets.append(text)
        return snippets

    def _search_telegram_urls_chat_preview(
        self,
        user_prompt: str,
        limit: int,
        *,
        search_context_size: str = "high",
    ) -> list[str]:
        preview_model = self.settings.proxyapi_web_search_preview_model
        ctx_size = search_context_size if search_context_size in ("low", "medium", "high") else "high"
        try:
            response = self.client.chat.completions.create(
                model=preview_model,
                messages=[{"role": "user", "content": user_prompt}],
                max_completion_tokens=min(1200, max(350, int(limit) * 30)),
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
                extra_headers=_log_headers("step1_telegram_preview"),
            )
            self._last_api_response = response
            _log_proxyapi_usage(response, kind="chat.telegram_preview", model=preview_model)
            text = response.choices[0].message.content or ""
            urls = extract_http_urls_from_text(text, limit=limit)
            if urls:
                logger.info(
                    "ProxyAPI telegram digest (chat preview) | model=%s count=%s",
                    preview_model,
                    len(urls),
                )
            return urls
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            _log_proxyapi_exception(exc, kind="chat.telegram_preview", model=preview_model)
            logger.exception("ProxyAPI telegram chat preview failed | model=%s", preview_model)
            return []

    def suggest_news_order(
        self, items: list[dict[str, Any]], digest_type: str = "serious", model: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Оптимальный порядок пятёрки для удержания читателя (без CrewAI).
        Возвращает список {candidate_id, output_position, ordering_reason}.
        """
        from app.services.digest_type_policy import normalize_digest_type, step2_order_system_prompt

        use_model = model or STEP2_AI_ORDER_MODEL
        system_prompt = step2_order_system_prompt(normalize_digest_type(digest_type))
        user_prompt = (
            f"digest_type={digest_type}\n"
            f"Новости для упорядочивания:\n{json.dumps(items, ensure_ascii=False)}\n"
            "Ответ — JSON-объект: overall_rationale (3–5 предложений, почему этот порядок оптимален для читателя), "
            "items — массив из 5 объектов с полями candidate_id (int), output_position (1..5, все уникальны), "
            "ordering_reason (1–2 предложения: почему эта позиция удерживает читателя)."
        )
        raw = self.chat(
            system_prompt,
            user_prompt,
            model=use_model,
            max_completion_tokens=900,
            log_source="step2_order",
        )
        parsed_items, overall_rationale = _parse_ordering_payload(raw)
        allowed_ids = {int(x["candidate_id"]) for x in items if x.get("candidate_id") is not None}
        if not isinstance(parsed_items, list) or len(parsed_items) != 5:
            fallback_items = _fallback_news_order(items)
            return {
                "items": fallback_items,
                "overall_rationale": (
                    "Резервный порядок по суммарным баллам: от самой заметной новости к финальному аккорду."
                ),
            }
        out: list[dict[str, Any]] = []
        seen_pos: set[int] = set()
        seen_ids: set[int] = set()
        for row in parsed_items:
            if not isinstance(row, dict):
                fallback_items = _fallback_news_order(items)
                return {
                    "items": fallback_items,
                    "overall_rationale": (
                        "Резервный порядок по суммарным баллам: от самой заметной новости к финальному аккорду."
                    ),
                }
            try:
                cid = int(row.get("candidate_id"))
                pos = int(row.get("output_position"))
            except (TypeError, ValueError):
                fallback_items = _fallback_news_order(items)
                return {
                    "items": fallback_items,
                    "overall_rationale": (
                        "Резервный порядок по суммарным баллам: от самой заметной новости к финальному аккорду."
                    ),
                }
            if cid not in allowed_ids or cid in seen_ids or pos < 1 or pos > 5 or pos in seen_pos:
                fallback_items = _fallback_news_order(items)
                return {
                    "items": fallback_items,
                    "overall_rationale": (
                        "Резервный порядок по суммарным баллам: от самой заметной новости к финальному аккорду."
                    ),
                }
            seen_ids.add(cid)
            seen_pos.add(pos)
            reason = str(row.get("ordering_reason") or "").strip() or f"Позиция {pos}: редакционный ритм выпуска."
            out.append({"candidate_id": cid, "output_position": pos, "ordering_reason": reason[:500]})
        if seen_ids != allowed_ids or seen_pos != {1, 2, 3, 4, 5}:
            fallback_items = _fallback_news_order(items)
            return {
                "items": fallback_items,
                "overall_rationale": (
                    "Резервный порядок по суммарным баллам: от самой заметной новости к финальному аккорду."
                ),
            }
        out.sort(key=lambda x: int(x["output_position"]))
        return {"items": out, "overall_rationale": overall_rationale[:2000]}

    def response_json(self, system_prompt: str, user_prompt: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.responses.create(
                model=self.settings.proxyapi_model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={"format": {"type": "json_schema", "name": "payload", "schema": response_schema}},
                max_output_tokens=1200,
                extra_headers=_log_headers("responses_json"),
            )
            self._last_api_response = response
            _log_proxyapi_usage(response, kind="responses.json", model=self.settings.proxyapi_model)
            text = response.output_text
            import json

            return json.loads(text)
        except Exception as exc:
            if _is_proxyapi_budget_error(exc):
                self.last_error_kind = "budget_exceeded"
            _log_proxyapi_exception(exc, kind="responses.json", model=self.settings.proxyapi_model)
            logger.exception("Ошибка responses.create | model=%s", self.settings.proxyapi_model)
            raise RuntimeError("ProxyAPI responses request failed") from exc


def _parse_ordering_response(raw: str) -> Any:
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()
    try:
        if text.startswith("[") or text.startswith("{"):
            return json.loads(text)
    except json.JSONDecodeError:
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
    except json.JSONDecodeError:
        return []
    return []


def _parse_ordering_payload(raw: str) -> tuple[list[Any], str]:
    parsed = _parse_ordering_response(raw)
    if isinstance(parsed, dict):
        items = parsed.get("items")
        rationale = str(parsed.get("overall_rationale") or "").strip()
        if isinstance(items, list):
            return items, rationale
    if isinstance(parsed, list):
        return parsed, ""
    return [], ""


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
