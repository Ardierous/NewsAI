from dataclasses import dataclass

from crewai import Agent, LLM

from app.config import get_settings
from app.crew.model_policy import AGENT_MODEL_RECOMMENDATIONS, proxyapi_litellm_model


@dataclass
class CrewAgents:
    news_research: Agent
    source_verification: Agent
    scoring: Agent
    ordering: Agent
    analytics: Agent
    reader_copy: Agent
    platform_writer: Agent
    image_prompt: Agent
    quality_control: Agent


def build_llm(model_name: str | None = None) -> LLM:
    settings = get_settings()
    raw = model_name or settings.proxyapi_model
    return LLM(
        model=proxyapi_litellm_model(raw),
        base_url=settings.proxyapi_base_url,
        api_key=settings.proxyapi_api_key,
        temperature=0.2,
    )


def create_agents(system_contract: str) -> CrewAgents:
    return CrewAgents(
        news_research=Agent(
            role="NewsResearchAgent",
            goal="Найти 10 релевантных новостей ИИ за последние 2 рабочих дня/до 96 часов по МСК",
            backstory="Ты редактор-исследователь, работаешь строго по контракту и шкале Tier-1…Tier-5 из раздела о приоритетах источников.",
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["NewsResearchAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
        source_verification=Agent(
            role="SourceVerificationAgent",
            goal="Проверять достоверность, юридическую пригодность, дубли, агрегаторы и статусы источников",
            backstory="Ты фактчекер редакции ExTellect: Tier-1…Tier-4 по списку приоритетов, агрегаторы Tier-5 — отдельная проверка и флаги.",
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["SourceVerificationAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
        scoring=Agent(
            role="ScoringAgent",
            goal="Оценить новости по значимости, новизне, влиянию и сбалансировать подборку",
            backstory="Ты выпускающий редактор, оптимизируешь ценность выпуска.",
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["ScoringAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
        ordering=Agent(
            role="OrderingAgent",
            goal="Выстроить 5 выбранных новостей в сильный редакционный порядок",
            backstory="Ты драматург ленты, усиливаешь удержание читателя.",
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["OrderingAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
        analytics=Agent(
            role="AnalyticsAgent",
            goal="Подготовить редакторскую аналитику по новостям: суть, записка и пометки для выпускающего",
            backstory=(
                "Ты выпускающий редактор технологического медиа. Пишешь материал для коллеги-редактора: "
                "суть новости в одну строку и analysis в строгой структуре из блоков: аудитория, почему важно, "
                "польза, вред, возможные последствия. Без служебных меток и без готового текста для читателей."
            ),
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["AnalyticsAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
        reader_copy=Agent(
            role="ReaderCopyAgent",
            goal="Написать простое и понятное описание новости для читателей (до 450 символов)",
            backstory=(
                "Ты автор технологического медиа. Пишешь простым разговорным языком, как будто "
                "объясняешь другу. Синтезируешь аналитику и текст статьи в короткий понятный текст "
                "без канцелярита, штампов и официоза; лёгкая ирония допустима."
            ),
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["ReaderCopyAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
        platform_writer=Agent(
            role="PlatformWriterAgent",
            goal="Создать финальные тексты отдельно для Telegram, MAX, ВКонтакте и Дзен",
            backstory="Ты кросс-платформенный редактор, соблюдаешь формат каждой площадки.",
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["PlatformWriterAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
        image_prompt=Agent(
            role="ImagePromptAgent",
            goal="Подготовить промпт для единого обложечного изображения выпуска",
            backstory="Ты арт-директор визуального стиля ExTellect.",
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["ImagePromptAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
        quality_control=Agent(
            role="QualityControlAgent",
            goal="Провести финальную самопроверку всех блоков и выдать чек-лист качества",
            backstory="Ты QA-редактор, блокируешь выпуск при нарушении контракта.",
            llm=build_llm(AGENT_MODEL_RECOMMENDATIONS["QualityControlAgent"]),
            allow_delegation=False,
            verbose=False,
        ),
    )
