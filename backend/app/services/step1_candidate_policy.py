"""Правила отбора кандидатов шага 1: новости vs промо инструментов/функционала."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

MaterialForm = Literal[
    "article",
    "training",
    "service",
    "press",
    "research",
    "finance",
    "military",
    "breakthrough",
    "legislation",
]

MATERIAL_FORM_ARTICLE: MaterialForm = "article"
MATERIAL_FORM_TRAINING: MaterialForm = "training"
MATERIAL_FORM_SERVICE: MaterialForm = "service"
MATERIAL_FORM_PRESS: MaterialForm = "press"
MATERIAL_FORM_RESEARCH: MaterialForm = "research"
MATERIAL_FORM_FINANCE: MaterialForm = "finance"
MATERIAL_FORM_MILITARY: MaterialForm = "military"
MATERIAL_FORM_BREAKTHROUGH: MaterialForm = "breakthrough"
MATERIAL_FORM_LEGISLATION: MaterialForm = "legislation"

# Квоты тем для пула кандидатов только серьёзного выпуска (digest_type=serious).
# Курьёзный выпуск (curious) эти квоты не использует.
SERIOUS_POOL_THEME_QUOTAS: dict[str, int] = {
    MATERIAL_FORM_RESEARCH: 2,
    MATERIAL_FORM_FINANCE: 2,
    MATERIAL_FORM_TRAINING: 1,
    MATERIAL_FORM_MILITARY: 2,
    MATERIAL_FORM_BREAKTHROUGH: 2,
    MATERIAL_FORM_LEGISLATION: 1,
}
THEMED_POOL_FORMS = frozenset(SERIOUS_POOL_THEME_QUOTAS.keys())

MATERIAL_FORM_MARKER_PREFIX = "MATERIAL_FORM:"
NOT_AD_MARKER_PREFIX = "NOT_AD:"
EDITORIAL_ANGLE_MARKER_PREFIX = "EDITORIAL_ANGLE:"
NOT_AD_DISCLOSURE_RU = "не реклама"

EditorialAngle = Literal["serious", "curious"]

EDITORIAL_ANGLE_LABELS: dict[str, str] = {
    "serious": "серьёз",
    "curious": "курьёз",
}

MATERIAL_FORM_TITLE_LABELS: dict[str, str] = {
    MATERIAL_FORM_ARTICLE: "статья",
    MATERIAL_FORM_TRAINING: "обучение",
    MATERIAL_FORM_SERVICE: "услуга/реклама",
    MATERIAL_FORM_PRESS: "пресс-релиз",
    MATERIAL_FORM_RESEARCH: "исследование",
    MATERIAL_FORM_FINANCE: "финансы",
    MATERIAL_FORM_MILITARY: "военная сфера",
    MATERIAL_FORM_BREAKTHROUGH: "прорыв ИИ",
    MATERIAL_FORM_LEGISLATION: "законодательство",
}

_MATERIAL_FORM_TITLE_SUFFIX_RE = re.compile(
    r"\s*\((?:статья|обучение|услуга(?:/реклама)?|пресс-релиз|исследование|финансы|"
    r"военная сфера|прорыв ИИ|законодательство)\)\s*$",
    re.IGNORECASE,
)

PRESS_RELEASE_HOST_MARKERS = (
    "businesswire.com",
    "prnewswire.com",
    "globenewswire.com",
)

PRESS_RELEASE_PATH_MARKERS = (
    "/press",
    "/press-release",
    "/pressrelease",
    "/newsroom",
    "/media-center",
    "/media/",
    "/investor/news",
)

CORPORATE_OFFICIAL_HOST_MARKERS = (
    "openai.com",
    "anthropic.com",
    "deepmind.google",
    "ai.googleblog.com",
    "blog.google",
    "blogs.nvidia.com",
    "nvidia.com",
    "microsoft.com",
    "ibm.com",
    "meta.com",
    "about.fb.com",
    "yandex.ru",
    "sber.ru",
    "sberbank.ru",
    "vk.com",
    "vk.company",
    "cognitive.ru",
    "abbyy.com",
)

_CORPORATE_PRESS_PATH_RE = re.compile(
    r"/(?:blog|news|press|newsroom|announcements?|"
    r"press[-_]?(?:center|releases?)|media[-_]?center|"
    r"company/press|about/news|discover/blog|"
    r"novosti|company/news)",
    re.IGNORECASE,
)

_CORPORATE_AI_EVENT_RES = re.compile(
    r"\b(?:"
    r"внедрени[ея]|развертыван|deployment|партнёрств|partnership|"
    r"инвестици|investment|соглашен|acquisition|слияни|merger|"
    r"представил[аи]?|объявил[аи]?|announces?|announced|official(?:ly)?|"
    r"запуск(?:ает|а)?|launch(?:es|ed)?|rolls? out|introduces?|unveils?|"
    r"план(?:ы)?|roadmap|strategy|программ(?:а|ы) (?:внедрения|развития)|"
    r"нов(?:ая|ый|ое) модел|new model|generative ai|генеративн|"
    r"LLM|GPT|Claude|Gemini|"
    r"федеральн|национальн|государственн|"
    r"пресс[- ]релиз|press release"
    r")\b",
    re.IGNORECASE,
)

_PRODUCT_TOOL_URL_PATH_RE = re.compile(
    r"(?:^|/)(?:product|products|tool|tools|feature|features|functionality|platform|"
    r"solutions?|pricing|price|demo|trial|signup|sign-up|register|download|"
    r"chatbot|assistant|copilot|widget|plugin|extension|integrations?|"
    r"use-cases?|usecases?|get-started|getstarted|try-now|freetrial)(?:/|$)",
    re.IGNORECASE,
)

_SAAS_PRODUCT_LANDING_PATH_RE = re.compile(
    r"(?:^|/)(?:c)/(about|pricing|features?|product|demo|trial|connect|landing)(?:/|$)",
    re.IGNORECASE,
)

_SAAS_PRODUCT_HOST_MARKERS = (
    "neurolegal.ya.ru",
)

_TRAINING_SAAS_HOST_MARKERS = (
    "ai-trainers.ya.ru",
)

_TRAINING_SAAS_PATH_RE = re.compile(
    r"/(?:ai/)?trener[\w_-]*",
    re.IGNORECASE,
)

_PRODUCT_TOOL_PROMO_RES = (
    re.compile(
        r"\b(?:попробуйте|попробовать|зарегистрируйтесь|скачайте|бесплатн(?:о|ый|ая)|"
        r"оставить заявку|оформить подписку|подключить(?:\s+от)?|"
        r"новый инструмент|новая функци[яю]|функционал|возможност(?:ь|и) (?:сервиса|платформы)|"
        r"наш(?:а|и)? (?:сервис|платформа|бот|ассистент|инструмент)|"
        r"ии[- ]помощник для|тариф(?:ы|а)?(?:\s+подписки)?|"
        r"как пользоваться|инструкция по использованию)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:try (?:our|the)|sign up|free trial|new feature|product update|"
        r"ai assistant|chatbot|copilot|tool(?:s)? for|how to use|user guide|"
        r"now available in|rolling out to users)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:launches?|introduces?|rolls? out|unveils?)\b.{0,80}\b(?:tool|feature|app|assistant|"
        r"copilot|platform|product|beta|plugin|widget)\b",
        re.IGNORECASE,
    ),
)

_PRACTICAL_REVIEW_SIGNAL_RES = (
    re.compile(
        r"\b(?:обзор|сравнени[ея]|подборк[аи]|топ[- ]?\d+|"
        r"для (?:работы|учёбы|учебы|офиса|бизнеса|дом[ау]|повседневных задач)|"
        r"как использовать|как применять|пошагов(?:о|ая)|"
        r"что выбрать|какой сервис выбрать|кейс использования|"
        r"эконом(?:ит|ия) времени|автоматизац(?:ия|ии) рутин(?:ы|ных задач))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:review|comparison|best ai tools|top ai tools|"
        r"for work|for students|for small business|use cases?|"
        r"hands-on|practical guide|workflow automation|time saving)\b",
        re.IGNORECASE,
    ),
)

_NEWS_EVENT_SIGNAL_RES = (
    re.compile(
        r"\b(?:прорыв|breakthrough|открыти[ея]|исследовани[ея]|study|published|paper|"
        r"регулирован|закон|постановлен|decree|министерств|правительств|"
        r"инвестици|финансирован|raised \$|series [a-d]|партнёрств|partnership|"
        r"соглашен|договор|acquisition|слияни|merger|"
        r"внедрени[ея]|развертыван|deployment|пилотн(?:ая|ый) программ|"
        r"план(?:ы)? (?:развития|внедрения|цифровизации)|strategy|roadmap|"
        r"отчёт|report|результат|достиг|benchmark|"
        r"пресс-релиз|press release|official(?:ly)? announced|"
        r"лаборатор|university|openai|deepmind|"
        r"\d+\s*(?:млн|млрд|million|billion)|%\s+(?:рост|снижен))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:запустил(?:и)? (?:национальн|федеральн|государственн)|"
        r"объявил(?:и)? (?:о|план|программ)|announces? (?:plan|program|partnership|investment))\b",
        re.IGNORECASE,
    ),
)


_SUPPORT_DOC_HOST_MARKERS = (
    "browser.yandex.ru",
)
_SUPPORT_DOC_PATH_RE = re.compile(
    r"(?:^|/)(?:support|help|docs|troubleshooting|faq)(?:/|$|\.html)",
    re.IGNORECASE,
)


def _text_blob(item: dict[str, Any], extra: str = "") -> str:
    parts = [
        str(item.get("title") or ""),
        str(item.get("description") or ""),
        str(extra or ""),
    ]
    return " ".join(parts).strip()


def is_support_documentation_url(url: str) -> bool:
    """Справка, help, FAQ, документация — не новостная статья."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    try:
        parsed = urlparse(u)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
    except Exception:
        return False
    if any(marker in host for marker in _SUPPORT_DOC_HOST_MARKERS):
        return True
    if host.endswith("yandex.ru") and ("/support/" in path or "/help/" in path):
        return True
    if "aistudio.yandex.ru" in host and "/docs" in path:
        return True
    if "developers.sber.ru" in host and "/help" in path:
        return True
    return bool(_SUPPORT_DOC_PATH_RE.search(path))


def is_training_saas_landing_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    if _host_marker_hit(u, _TRAINING_SAAS_HOST_MARKERS):
        return True
    try:
        path = (urlparse(u).path or "").lower()
    except Exception:
        return False
    return bool(_TRAINING_SAAS_PATH_RE.search(path))


def is_product_tool_landing_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    try:
        path = (urlparse(u).path or "").lower().rstrip("/") or "/"
    except Exception:
        return False
    if _PARTICIPATION_INVITE_URL_PATH_RE.search(path):
        return True
    if _SAAS_PRODUCT_LANDING_PATH_RE.search(path):
        return True
    if _host_marker_hit(u, _SAAS_PRODUCT_HOST_MARKERS):
        return True
    if is_training_saas_landing_url(u):
        return True
    if _PRODUCT_TOOL_URL_PATH_RE.search(path):
        return True
    if re.search(r"/(?:ai-)?tools?(?:/|$)", path, re.IGNORECASE):
        return True
    return False


def should_reject_commercial_non_article(
    item: dict[str, Any],
    material_form: MaterialForm,
    extra: str = "",
) -> bool:
    """Вакансии, карьера и рекламные лендинги — не новостная статья для пула."""
    url = str(item.get("url") or "")
    practical_review = has_practical_review_signal(item, extra)
    if is_participation_invite_candidate(item, extra):
        return True
    if material_form == MATERIAL_FORM_SERVICE:
        return True
    if material_form == MATERIAL_FORM_TRAINING and is_training_saas_landing_url(url):
        return True
    if is_product_tool_landing_url(url) and not has_substantive_news_event_signal(item, extra):
        return True
    if (
        looks_like_product_tool_promo(item, extra)
        and not has_substantive_news_event_signal(item, extra)
        and not practical_review
    ):
        return True
    return False


def commercial_non_article_reject_reason(
    item: dict[str, Any],
    material_form: MaterialForm,
    extra: str = "",
) -> str:
    """Код отбраковки для should_reject_commercial_non_article."""
    url = str(item.get("url") or "")
    if is_participation_invite_candidate(item, extra) or (
        is_product_tool_landing_url(url) and material_form != MATERIAL_FORM_SERVICE
    ):
        return "product_tool_page"
    return "product_tool_promo"


def has_substantive_news_event_signal(item: dict[str, Any], extra: str = "") -> bool:
    text = _text_blob(item, extra)
    if len(text) < 12:
        return False
    return any(rx.search(text) for rx in _NEWS_EVENT_SIGNAL_RES)


def has_practical_review_signal(item: dict[str, Any], extra: str = "") -> bool:
    """Прикладной обзор/сравнение для обычного пользователя (работа/быт), не лендинг."""
    text = _text_blob(item, extra)
    if len(text) < 12:
        return False
    return any(rx.search(text) for rx in _PRACTICAL_REVIEW_SIGNAL_RES)


def is_corporate_official_announcement_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    low = u.lower()
    if any(m in low for m in PRESS_RELEASE_HOST_MARKERS):
        return True
    if any(m in low for m in PRESS_RELEASE_PATH_MARKERS):
        return True
    if not _host_marker_hit(low, CORPORATE_OFFICIAL_HOST_MARKERS):
        return False
    try:
        path = (urlparse(u).path or "").lower()
    except Exception:
        return False
    if _CORPORATE_PRESS_PATH_RE.search(path):
        return True
    if re.search(r"/blog/\d{4,}", path):
        return True
    return False


def has_corporate_ai_announcement_signal(item: dict[str, Any], extra: str = "") -> bool:
    text = _text_blob(item, extra)
    if len(text) < 10:
        return False
    return bool(_CORPORATE_AI_EVENT_RES.search(text))


def looks_like_product_tool_promo(item: dict[str, Any], extra: str = "") -> bool:
    url = str(item.get("url") or "")
    text = _text_blob(item, extra)
    if is_corporate_official_announcement_url(url) and (
        has_corporate_ai_announcement_signal(item, extra)
        or has_substantive_news_event_signal(item, extra)
    ):
        return False
    if is_product_tool_landing_url(url):
        return True
    if not text:
        return False
    promo_hits = sum(1 for rx in _PRODUCT_TOOL_PROMO_RES if rx.search(text))
    if promo_hits >= 2:
        return True
    if promo_hits >= 1 and not has_substantive_news_event_signal(item, extra):
        return True
    return False


def is_press_release_candidate_dict(item: dict[str, Any]) -> bool:
    """Широкая метка «похоже на пресс/официальное» — для статистики и эвристик."""
    url = str(item.get("url") or "").lower()
    source = str(item.get("source") or "").lower()
    title = str(item.get("title") or "").lower()
    desc = str(item.get("description") or "").lower()
    if is_corporate_official_announcement_url(str(item.get("url") or "")):
        return True
    if any(m in source for m in PRESS_RELEASE_HOST_MARKERS):
        return True
    if any(m in url for m in PRESS_RELEASE_HOST_MARKERS):
        return True
    if any(m in url for m in PRESS_RELEASE_PATH_MARKERS):
        return True
    keywords = (
        "press release",
        "news release",
        "пресс-релиз",
        "официально объявил",
        "публично объявила",
        "официальное заявление",
        "официально представил",
        "официально объявила",
    )
    return any(k in title or k in desc for k in keywords)


def is_substantive_press_for_pool(item: dict[str, Any], extra: str = "") -> bool:
    """
    Пресс/официальное для квоты пула: факты, планы, внедрения, партнёрства крупных компаний —
    не страницы инструментов и не «как пользоваться продуктом».
    """
    if looks_like_product_tool_promo(item, extra):
        return False
    url = str(item.get("url") or "")
    if is_corporate_official_announcement_url(url):
        return has_corporate_ai_announcement_signal(item, extra) or has_substantive_news_event_signal(
            item, extra
        )
    if not is_press_release_candidate_dict(item):
        return False
    return has_substantive_news_event_signal(item, extra) or has_corporate_ai_announcement_signal(
        item, extra
    )


def is_editorial_news_candidate(item: dict[str, Any], extra: str = "") -> bool:
    """Обычная новостная статья СМИ (не промо продукта)."""
    return not looks_like_product_tool_promo(item, extra)


_EDUCATION_URL_PATH_RE = re.compile(
    r"(?:^|/)(?:"
    r"course|courses|"
    r"(?:^|/)learn(?:ing)?(?:/|$)|"
    r"(?:^|/)training(?:/|$)|"
    r"education(?:al)?(?:/|$)|"
    r"bootcamp|boot-camp|masterclass|master-class|"
    r"webinar|seminar|workshop|tutorials?|"
    r"курс(?:ы|а|ов)?|"
    r"(?:^|/)обучен(?:ие|ия)(?:/|$)|"
    r"программ(?:а|ы)[-_]обучен|"
    r"вебинар|семинар|мастер-класс|"
    r"(?:^|/)школ[аы](?:/|$)|учебн(?:ый|ая|ое)|интенсив"
    r")(?:/|$)",
    re.IGNORECASE,
)

_EDUCATION_HOST_MARKERS = (
    "coursera.org",
    "stepik.org",
    "netology.ru",
    "skillbox.ru",
    "gb.ru",
    "hexlet.io",
    "otus.ru",
    "skillfactory.ru",
    "practicum.yandex",
    "education.yandex.ru",
    "openedu.ru",
    "lektorium.tv",
    "edu.ru",
)

_EDUCATION_NEWS_PATH_RE = re.compile(
    r"/(?:news|article|press|novosti|journal|zhurnal|magazine|events?)(?:/|$)",
    re.IGNORECASE,
)

_EDUCATION_PROGRAM_PATH_RE = re.compile(
    r"/(?:learn(?:ing)?|portalnew|university|programs?|programmy|"
    r"aspirantur|magistratur|бакалавриат|catalog|katalog)(?:/|$)",
    re.IGNORECASE,
)

_RESEARCH_SCIENCE_HOST_MARKERS = (
    "nplus1.ru",
    "indicator.ru",
    "nauka.tass.ru",
    "scientificrussia.ru",
    "sciencedaily.com",
    "techxplore.com",
    "spectrum.ieee.org",
    "technologyreview.com",
    "nature.com",
    "science.org",
    "pnas.org",
)

_RESEARCH_SCIENCE_PATH_RE = re.compile(
    r"/(?:research|science|papers?|studies|publications?|preprint|"
    r"benchmark|findings|discovery|"
    r"issledovan|nauchn|открыти)",
    re.IGNORECASE,
)

_RESEARCH_FOCUS_TEXT_RES = (
    re.compile(
        r"\b(?:открыти[ея]|исследовани[ея]|научн\w*|"
        r"учёны\w*|scientists?|researchers?|peer[- ]reviewed|preprint|"
        r"benchmark|лаборатор\w*|"
        r"опубликован\w*\s+в|journal|paper|эксперимент\w*|"
        r"новые данные|научные данные|гипотез)\b",
        re.IGNORECASE,
    ),
)

_EDUCATION_TEXT_RES = (
    re.compile(
        r"\b(?:курс(?:ы|а|ов)?|программ(?:а|ы)(?:\s+обучения|\s+аспирантуры|"
        r"\s+магистратуры|\s+бакалавриата)?|онлайн[- ]школ[аы]|"
        r"аспирантур[аы]?|магистратур[аы]?|бакалавриат|"
        r"вебинар|мастер[- ]класс|интенсив|дистанционн(?:ое|ый) обучение|"
        r"учебн(?:ый|ая) программ|bootcamp|masterclass|online course)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:online course|training program|certification program|"
        r"learn (?:ai|machine learning)|educational program)\b",
        re.IGNORECASE,
    ),
)

_ML_TOPIC_FALSE_POSITIVE_RE = re.compile(r"машинн\w*\s+обучен", re.IGNORECASE)

_SERVICE_URL_PATH_RE = re.compile(
    r"(?:^|/)(?:services?|услуг[аи]|consulting|консалтинг|subscription|подписк[аи])(?:/|$)",
    re.IGNORECASE,
)

_PARTICIPATION_INVITE_URL_PATH_RE = re.compile(
    r"/(?:teams?|vacancies|vakansii|careers?|jobs?|rabota|"
    r"kak-v-sbere|join(?:-us)?|recruitment|hiring|"
    r"work-with-us|работа-в)(?:/|$)",
    re.IGNORECASE,
)

_PARTICIPATION_INVITE_TEXT_RES = (
    re.compile(
        r"\b(?:присоединяйся|присоединиться к (?:работе|команде)|"
        r"хочу в команду|работай с нами|все вакансии|"
        r"(?:мы |команда )?ищ(?:ет|ем)|открытые вакансии|"
        r"карьер(?:а|у) в|join (?:our|the) team|we(?:'re| are) hiring|"
        r"view (?:all )?vacancies|apply now)\b",
        re.IGNORECASE,
    ),
)

_SERVICE_TEXT_RES = (
    re.compile(
        r"\b(?:услуг[аи]|консалтинг|под ключ|заказать (?:разработку|внедрение)|"
        r"наши услуги|service package|оставить заявку|оформить подписку|"
        r"подключить(?:\s+от)?|попробовать(?:\s+бесплатно)?|тариф(?:ы|а)?)\b",
        re.IGNORECASE,
    ),
)


def _host_marker_hit(url: str, markers: tuple[str, ...]) -> bool:
    low = (url or "").lower()
    return any(m in low for m in markers)


def _is_research_science_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    low = u.lower()
    try:
        path = (urlparse(u).path or "").lower()
    except Exception:
        path = ""
    if _RESEARCH_SCIENCE_PATH_RE.search(path):
        return True
    if is_corporate_official_announcement_url(u):
        return False
    if _host_marker_hit(low, _RESEARCH_SCIENCE_HOST_MARKERS):
        if re.search(r"/(?:topic|tag|category|search|hub)(?:/|$)", path):
            return False
        return True
    if ("openai.com" in low or "anthropic.com" in low or "deepmind.google" in low) and re.search(
        r"/research/", path, re.IGNORECASE
    ):
        return True
    if "habr.com" in low and re.search(r"/(?:post|articles)/\d+", path):
        text_hint = path
        if re.search(r"(?:research|science|ml|ai|нейросет|исследован)", text_hint, re.IGNORECASE):
            return True
    return False


def _education_url_signal(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    try:
        path = (urlparse(u).path or "").lower()
    except Exception:
        path = ""
    on_education_host = _host_marker_hit(u, _EDUCATION_HOST_MARKERS)
    if on_education_host and _EDUCATION_NEWS_PATH_RE.search(path):
        return False
    if on_education_host and _EDUCATION_PROGRAM_PATH_RE.search(path):
        return True
    if on_education_host:
        return True
    if _EDUCATION_PROGRAM_PATH_RE.search(path) and re.search(
        r"/university/", path, re.IGNORECASE
    ):
        return True
    if _is_research_science_url(u):
        return False
    return bool(_EDUCATION_URL_PATH_RE.search(path))


def is_participation_invite_candidate(item: dict[str, Any], extra: str = "") -> bool:
    """Призыв вступить в команду, вакансии, карьерные лендинги — не новость и не исследование."""
    url = str(item.get("url") or "")
    text = _text_blob(item, extra)
    try:
        path = (urlparse(url).path or "").lower()
    except Exception:
        path = ""
    if _PARTICIPATION_INVITE_URL_PATH_RE.search(path):
        return True
    if any(rx.search(text) for rx in _PARTICIPATION_INVITE_TEXT_RES):
        if has_substantive_news_event_signal(item, extra) and not re.search(
            r"\b(?:присоединяйся|хочу в команду|вакансии|we(?:'re| are) hiring)\b",
            text,
            re.IGNORECASE,
        ):
            return False
        return True
    return False


def is_training_education_candidate(item: dict[str, Any], extra: str = "") -> bool:
    """Страница курса, программы обучения, вебинара — не новостная статья."""
    url = str(item.get("url") or "")
    text = _text_blob(item, extra)
    if _education_url_signal(url):
        return True
    if any(rx.search(text) for rx in _EDUCATION_TEXT_RES):
        if _ML_TOPIC_FALSE_POSITIVE_RE.search(text) and not _education_url_signal(url):
            return False
        if re.search(r"/(?:news|article|press|novosti|media)/", url, re.IGNORECASE):
            return False
        return True
    return False


def is_service_offer_candidate(item: dict[str, Any], extra: str = "") -> bool:
    """Коммерческая услуга (не новость и не курс): консалтинг, подписка, карьерные лендинги."""
    if is_training_education_candidate(item, extra):
        return False
    if is_participation_invite_candidate(item, extra):
        return True
    url = str(item.get("url") or "")
    text = _text_blob(item, extra)
    if is_product_tool_landing_url(url) and not has_substantive_news_event_signal(item, extra):
        return True
    try:
        path = (urlparse(url).path or "").lower()
    except Exception:
        path = ""
    if _SERVICE_URL_PATH_RE.search(path) and not re.search(
        r"/(?:news|article|press|blog|novosti)/", path, re.IGNORECASE
    ):
        return True
    if any(rx.search(text) for rx in _SERVICE_TEXT_RES):
        if has_substantive_news_event_signal(item, extra):
            return False
        return True
    return False


def is_research_science_candidate(item: dict[str, Any], extra: str = "") -> bool:
    """Прорывы, публикации, научные данные и исследования про ИИ — не курсы и не промо."""
    if is_training_education_candidate(item, extra):
        return False
    if is_participation_invite_candidate(item, extra):
        return False
    if is_service_offer_candidate(item, extra):
        return False
    url = str(item.get("url") or "")
    if _education_url_signal(url):
        return False
    if is_corporate_official_announcement_url(url) and (
        has_corporate_ai_announcement_signal(item, extra)
        or has_substantive_news_event_signal(item, extra)
    ):
        return False
    text = _text_blob(item, extra)
    if _is_research_science_url(url):
        return True
    if not any(rx.search(text) for rx in _RESEARCH_FOCUS_TEXT_RES):
        return False
    if looks_like_product_tool_promo(item, extra) and not has_substantive_news_event_signal(item, extra):
        return False
    return True


_LEGISLATION_RES = re.compile(
    r"\b(?:закон(?:опроект)?|законодательств|регулирован|регулятор|"
    r"AI Act|EU Act|постановлени[ея]|указ президент|госдум|совфед|"
    r"минцифр|роскомнадзор|федеральн\w+ закон|compliance|"
    r"запрет(?:ить)?|лицензирован|sanctions|decree)\b",
    re.IGNORECASE,
)

_FINANCE_RES = re.compile(
    r"\b(?:инвестици|привлёк|привлек|финансирован|financing|funding|raised \$|"
    r"series [a-d]|IPO|valuation|раунд [a-d]|"
    r"выручк|earnings|revenue|капитализац|"
    r"акци[ия]\w*(?:\s+(?:на бирже|выросл|упал)))\b",
    re.IGNORECASE,
)

_MILITARY_RES = re.compile(
    r"\b(?:военн|military|defense|defence|оборон|pentagon|нато|nato|"
    r"минобороны|drones?|БПЛА|UAV|armed forces|"
    r"артиллери|missile|ракет|боев(?:ой|ые))\b",
    re.IGNORECASE,
)

_AI_TOPIC_RES = re.compile(
    r"\b(?:искусственн\w+ интеллект|нейросет|нейро-сет|\bИИ\b|\bAI\b|LLM|GPT|"
    r"generative|автономн\w+)\b",
    re.IGNORECASE,
)

_BREAKTHROUGH_RES = re.compile(
    r"\b(?:прорыв|breakthrough|революци|рекордн|state-of-the-art|"
    r"превзошл|surpass|новая модель|new model|SOTA|"
    r"впервые удалось|first time|milestones?)\b",
    re.IGNORECASE,
)


def is_legislation_candidate(item: dict[str, Any], extra: str = "") -> bool:
    return bool(_LEGISLATION_RES.search(_text_blob(item, extra)))


def is_finance_candidate(item: dict[str, Any], extra: str = "") -> bool:
    return bool(_FINANCE_RES.search(_text_blob(item, extra)))


def manual_url_commercial_reject_reason(
    stored: str,
    *,
    title: str = "",
    topic_corpus: str = "",
) -> str | None:
    """Отбраковка ручной ссылки: вакансия, лендинг услуги, промо-тренажёр."""
    item = {"url": stored, "title": title, "description": ""}
    extra = f"{title} {topic_corpus}".strip()
    form = classify_material_form(item, extra=extra)
    if should_reject_commercial_non_article(item, form, extra):
        return commercial_non_article_reject_reason(item, form, extra)
    return None


def is_military_ai_candidate(item: dict[str, Any], extra: str = "") -> bool:
    text = _text_blob(item, extra)
    return bool(_MILITARY_RES.search(text) and _AI_TOPIC_RES.search(text))


def is_ai_breakthrough_candidate(item: dict[str, Any], extra: str = "") -> bool:
    if is_research_science_candidate(item, extra):
        return False
    if is_legislation_candidate(item, extra) or is_military_ai_candidate(item, extra):
        return False
    return bool(_BREAKTHROUGH_RES.search(_text_blob(item, extra)))


def classify_material_form(item: dict[str, Any], extra: str = "") -> MaterialForm:
    if is_training_education_candidate(item, extra):
        return MATERIAL_FORM_TRAINING
    if is_participation_invite_candidate(item, extra):
        return MATERIAL_FORM_SERVICE
    if is_legislation_candidate(item, extra):
        return MATERIAL_FORM_LEGISLATION
    if is_military_ai_candidate(item, extra):
        return MATERIAL_FORM_MILITARY
    if is_finance_candidate(item, extra):
        return MATERIAL_FORM_FINANCE
    if is_research_science_candidate(item, extra):
        return MATERIAL_FORM_RESEARCH
    if is_ai_breakthrough_candidate(item, extra):
        return MATERIAL_FORM_BREAKTHROUGH
    if is_service_offer_candidate(item, extra):
        return MATERIAL_FORM_SERVICE
    if is_substantive_press_for_pool(item, extra):
        url_low = str(item.get("url") or "").lower()
        if "/company/news/" in url_low or re.search(r"/news/\d{4}-\d{2}-\d{2}", url_low):
            return MATERIAL_FORM_ARTICLE
        return MATERIAL_FORM_PRESS
    return MATERIAL_FORM_ARTICLE


def strip_material_form_title_suffix(title: str) -> str:
    return _MATERIAL_FORM_TITLE_SUFFIX_RE.sub("", (title or "").strip()).strip()


def decorate_title_with_material_form(title: str, form: MaterialForm) -> str:
    base = strip_material_form_title_suffix(title)
    label = MATERIAL_FORM_TITLE_LABELS.get(form, MATERIAL_FORM_TITLE_LABELS[MATERIAL_FORM_ARTICLE])
    if not base:
        return f"({label})"
    return f"{base} ({label})"


def parse_editorial_angle_from_comment(comment: str | None) -> EditorialAngle:
    text = str(comment or "")
    if f"{EDITORIAL_ANGLE_MARKER_PREFIX}curious" in text:
        return "curious"
    if f"{EDITORIAL_ANGLE_MARKER_PREFIX}serious" in text:
        return "serious"
    return "serious"


def classify_editorial_angle(
    item: dict[str, Any],
    *,
    digest_type: str | None = None,
    extra: str = "",
) -> EditorialAngle:
    """Угол материала для плашки «курьёз / серьёз» в пуле кандидатов."""
    from app.curious_source_policy import is_curious_policy_source
    from app.services.digest_type_policy import is_curious_digest
    from app.source_tiers_policy import is_policy_tier_source

    if is_curious_digest(digest_type):
        return "curious"

    url = str(item.get("url") or "")
    title = str(item.get("title") or "")
    corpus = _text_blob(item, extra)
    curious_src = is_curious_policy_source(url)
    tier_src = is_policy_tier_source(url)

    if curious_src and not tier_src:
        return "curious"
    if tier_src and not curious_src:
        return "serious"

    from app.services.curious_tone import (
        curious_tone_score,
        has_curious_positive_signal,
        has_curious_serious_blockers,
        passes_curious_tone_gate,
    )

    tone = int(item.get("curious_tone_score", 0) or 0) or curious_tone_score(title, corpus)
    if passes_curious_tone_gate(title, corpus, url=url) or tone >= 4:
        return "curious"
    if has_curious_positive_signal(title, corpus) and not has_curious_serious_blockers(title, corpus):
        return "curious"
    return "serious"


def parse_material_form_from_comment(comment: str | None) -> MaterialForm:
    text = str(comment or "")
    for form in (
        MATERIAL_FORM_PRESS,
        MATERIAL_FORM_TRAINING,
        MATERIAL_FORM_SERVICE,
        MATERIAL_FORM_RESEARCH,
        MATERIAL_FORM_FINANCE,
        MATERIAL_FORM_MILITARY,
        MATERIAL_FORM_BREAKTHROUGH,
        MATERIAL_FORM_LEGISLATION,
        MATERIAL_FORM_ARTICLE,
    ):
        if f"{MATERIAL_FORM_MARKER_PREFIX}{form}" in text:
            return form
    return MATERIAL_FORM_ARTICLE


def has_not_ad_disclosure(comment: str | None) -> bool:
    return NOT_AD_MARKER_PREFIX in str(comment or "")


def _strip_policy_markers(comment: str) -> str:
    tokens = []
    for token in str(comment or "").split():
        if token.startswith(MATERIAL_FORM_MARKER_PREFIX):
            continue
        if token.startswith(NOT_AD_MARKER_PREFIX):
            continue
        if token.startswith(EDITORIAL_ANGLE_MARKER_PREFIX):
            continue
        tokens.append(token)
    return " ".join(tokens).strip()


def apply_material_form_to_candidate(
    item: dict[str, Any],
    *,
    extra: str = "",
    digest_type: str | None = None,
) -> MaterialForm:
    """Классифицирует форму материала и пишет её в material_form / verification_comment (без метки в заголовке)."""
    form = classify_material_form(item, extra=extra)
    angle = classify_editorial_angle(item, digest_type=digest_type, extra=extra)
    item["material_form"] = form
    item["editorial_angle"] = angle
    title = str(item.get("title") or "").strip()
    if title:
        item["title"] = strip_material_form_title_suffix(title)[:500]
    comment = _strip_policy_markers(str(item.get("verification_comment") or ""))
    comment = f"{comment} {MATERIAL_FORM_MARKER_PREFIX}{form} {EDITORIAL_ANGLE_MARKER_PREFIX}{angle}".strip()
    if form in (MATERIAL_FORM_TRAINING, MATERIAL_FORM_SERVICE):
        comment = f"{comment} {NOT_AD_MARKER_PREFIX}{NOT_AD_DISCLOSURE_RU}".strip()
    item["verification_comment"] = comment
    return form


def pool_item_theme(item: dict[str, Any]) -> str:
    form = str(item.get("material_form") or "") or parse_material_form_from_comment(
        str(item.get("verification_comment") or "")
    )
    if form in THEMED_POOL_FORMS:
        return form
    return ""


def theme_pool_quota(theme: str) -> int:
    return int(SERIOUS_POOL_THEME_QUOTAS.get(theme, 0) or 0)


def is_theme_pool_item(item: dict[str, Any], theme: str) -> bool:
    return pool_item_theme(item) == theme


def is_training_pool_item(item: dict[str, Any]) -> bool:
    return is_theme_pool_item(item, MATERIAL_FORM_TRAINING)


def is_research_pool_item(item: dict[str, Any]) -> bool:
    return is_theme_pool_item(item, MATERIAL_FORM_RESEARCH)


def is_press_pool_item(item: dict[str, Any]) -> bool:
    form = str(item.get("material_form") or "") or parse_material_form_from_comment(
        str(item.get("verification_comment") or "")
    )
    if form == MATERIAL_FORM_PRESS:
        return True
    return is_substantive_press_for_pool(item)
