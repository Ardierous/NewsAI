"""Каталог фильтров шага 1 (метаданные для UI; значения — в step1_filter_settings.json по типу выпуска)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.digest_type_policy import is_curious_digest, normalize_digest_type

DigestFilterScope = Literal["shared", "serious", "curious"]


@dataclass(frozen=True)
class Step1FilterDef:
    id: str
    label_ru: str
    description_ru: str
    stage: str
    default_enabled: bool = True
    locked: bool = False
    digest_scope: DigestFilterScope = "shared"


STEP1_FILTER_CATALOG: tuple[Step1FilterDef, ...] = (
    Step1FilterDef("invalid_url", "Невалидный URL", "Отсекает строки, которые не являются корректными http/https ссылками.", "pre_http"),
    Step1FilterDef("duplicate_url_skip", "Дубликат URL", "Исключает URL, который уже проверялся в этом запуске.", "pre_http"),
    Step1FilterDef(
        "recent_top5_repeat",
        "Повтор из топ-5",
        "Исключает ту же страницу статьи (URL), что уже была в топ-5 одного из 7 предыдущих выпусков. "
        "Другой URL — другая публикация, даже если сюжет похож.",
        "pre_http",
    ),
    Step1FilterDef("aggregator_source", "Агрегатор", "Отсекает агрегаторы и сборщики новостей вместо первоисточников.", "pre_http"),
    Step1FilterDef(
        "forbidden_media_source",
        "Запрещённый источник",
        "Отсекает источники из blocked/tier5 по правилам проекта.",
        "pre_http",
    ),
    Step1FilterDef(
        "non_policy_source",
        "Вне политики источников",
        "Серьёзный выпуск: домены вне tier-1…tier-4 (source_tiers.txt). "
        "Курьёзный: домены вне curious_source_hosts.txt.",
        "pre_http",
    ),
    Step1FilterDef("news_listing_page", "Лента/рубрика", "Отсекает страницы-списки, разделы, теги и поисковые выдачи.", "pre_http"),
    Step1FilterDef(
        "llm_hallucinated_url",
        "Подозрительный URL",
        "Отсекает URL с признаками галлюцинации или некорректной структуры.",
        "pre_http",
    ),
    Step1FilterDef("product_tool_page", "Страница продукта", "Отсекает лендинги инструментов вместо новостных публикаций.", "pre_http"),
    Step1FilterDef(
        "published_before_window",
        "Дата вне окна",
        "Отсекает материалы с известной датой публикации раньше окна шага 0 (дата в URL или на странице).",
        "pre_http",
    ),
    Step1FilterDef(
        "published_date_undefined",
        "Дата не определена",
        "Отсекает материалы, у которых не удалось извлечь дату публикации со страницы (и нет даты в URL). "
        "Для курьёзного выпуска не применяется — у vc.ru и др. дата часто только в тексте.",
        "verify",
        default_enabled=False,
        digest_scope="serious",
    ),
    Step1FilterDef(
        "http_unreachable",
        "Недоступная страница",
        "Отсекает URL, которые не открываются по HTTP после попыток и fallback.",
        "verify",
    ),
    Step1FilterDef("url_redirect_mismatch", "Нерелевантный редирект", "Отсекает URL, ведущие редиректом на другую нерелевантную страницу.", "verify"),
    Step1FilterDef("no_article_markers", "Нет признаков статьи", "Отсекает страницы без маркеров полноценной статьи.", "verify"),
    Step1FilterDef("non_article_page", "Не статья", "Отсекает страницы без корректного заголовка материала.", "verify"),
    Step1FilterDef("off_topic_not_ai", "Не по теме ИИ", "Отсекает публикации вне тематики ИИ/нейросетей.", "verify"),
    Step1FilterDef(
        "off_topic_not_curious",
        "Критерий курьёзности",
        "Фильтр забавного тона: отсекает сухие деловые новости без неожиданного угла (только курьёзный выпуск).",
        "verify",
        default_enabled=True,
        digest_scope="curious",
    ),
    Step1FilterDef("headline_low_quality", "Низкое качество заголовка", "Отсекает технические/служебные заголовки.", "verify"),
    Step1FilterDef("product_tool_promo", "Промо инструмента", "Отсекает маркетинговые тексты о продукте вместо новости.", "verify"),
    Step1FilterDef("placeholder_candidate", "Заглушка", "Отсекает технические placeholder-кандидаты.", "verify"),
    Step1FilterDef(
        "url_mutated_between_agents",
        "Подмена URL между этапами",
        "Блокирует кандидаты, где URL изменился между verify и score.",
        "verify",
    ),
)


STEP1_FILTER_DEF_BY_ID: dict[str, Step1FilterDef] = {x.id: x for x in STEP1_FILTER_CATALOG}


def filter_def_applies_to_digest_type(filter_id: str, digest_type: str | None) -> bool:
    fdef = STEP1_FILTER_DEF_BY_ID.get(filter_id)
    if not fdef:
        return False
    scope = fdef.digest_scope
    curious = is_curious_digest(digest_type)
    if scope == "shared":
        return True
    if scope == "curious":
        return curious
    if scope == "serious":
        return not curious
    return True


def step1_filter_catalog_payload(digest_type: str | None = None) -> list[dict[str, Any]]:
    dtype = normalize_digest_type(digest_type)
    return [
        {
            "id": f.id,
            "label_ru": f.label_ru,
            "description_ru": f.description_ru,
            "stage": f.stage,
            "default_enabled": f.default_enabled,
            "locked": f.locked,
            "digest_scope": f.digest_scope,
        }
        for f in STEP1_FILTER_CATALOG
        if filter_def_applies_to_digest_type(f.id, dtype)
    ]


def step1_enabled_map(states: list[dict[str, Any]] | None) -> dict[str, bool]:
    return {str(x["id"]): bool(x["enabled"]) for x in (states or [])}
