from app.services.step1_candidate_policy import (
    apply_material_form_to_candidate,
    classify_material_form,
    decorate_title_with_material_form,
    has_substantive_news_event_signal,
    is_product_tool_landing_url,
    is_research_science_candidate,
    is_substantive_press_for_pool,
    is_training_education_candidate,
    is_training_pool_item,
    looks_like_product_tool_promo,
    MATERIAL_FORM_ARTICLE,
    MATERIAL_FORM_BREAKTHROUGH,
    MATERIAL_FORM_FINANCE,
    MATERIAL_FORM_LEGISLATION,
    MATERIAL_FORM_MILITARY,
    MATERIAL_FORM_PRESS,
    MATERIAL_FORM_PRESS,
    MATERIAL_FORM_RESEARCH,
    MATERIAL_FORM_SERVICE,
    MATERIAL_FORM_TRAINING,
    is_participation_invite_candidate,
    is_training_saas_landing_url,
    is_finance_candidate,
    should_reject_commercial_non_article,
    SERIOUS_POOL_THEME_QUOTAS,
)


def test_product_tool_url_detected():
    assert is_product_tool_landing_url("https://example.com/products/ai-assistant")
    assert is_product_tool_landing_url("https://vendor.io/pricing/enterprise")
    assert not is_product_tool_landing_url("https://rbc.ru/technology/ai/123")


def test_tool_promo_headline_rejected_without_news_signal():
    item = {
        "url": "https://vendor.io/blog/new-feature",
        "title": "Компания launches new AI assistant tool for marketers",
        "description": "Try our free trial and sign up today.",
    }
    assert looks_like_product_tool_promo(item)
    assert not has_substantive_news_event_signal(item)
    assert not is_substantive_press_for_pool(item)


def test_training_course_url_classified_and_title_tagged():
    item = {
        "url": "https://netology.ru/programs/machine-learning",
        "title": "Программа Machine Learning",
        "description": "Онлайн-школа: курс по нейросетям",
    }
    assert is_training_education_candidate(item)
    assert classify_material_form(item) == MATERIAL_FORM_TRAINING
    apply_material_form_to_candidate(item)
    assert item["title"].endswith("(обучение)")
    assert "NOT_AD:" in item["verification_comment"]
    assert is_training_pool_item(item)


def test_research_program_path_is_not_training():
    item = {
        "url": "https://www.mit.edu/research/projects/ai-breakthrough-2026",
        "title": "MIT scientists publish new benchmark for large models",
        "description": "Researchers announced breakthrough results in peer-reviewed study",
    }
    assert not is_training_education_candidate(item)
    assert is_research_science_candidate(item)
    assert classify_material_form(item) == MATERIAL_FORM_RESEARCH
    apply_material_form_to_candidate(item)
    assert item["title"].endswith("(исследование)")


def test_nplus1_science_article_classified_as_research():
    item = {
        "url": "https://nplus1.ru/material/2026/06/01/ai-lab-results",
        "title": "Лаборатория опубликовала новые данные по нейросети",
        "description": "Учёные описали эксперимент",
    }
    assert classify_material_form(item) == MATERIAL_FORM_RESEARCH


def test_ml_topic_in_news_title_is_not_training():
    item = {
        "url": "https://ria.ru/20260601/ml-breakthrough",
        "title": "Прорыв в машинном обучении: новая модель",
        "description": "Исследователи опубликовали результаты",
    }
    assert not is_training_education_candidate(item)
    assert classify_material_form(item) == MATERIAL_FORM_BREAKTHROUGH
    assert decorate_title_with_material_form(item["title"], MATERIAL_FORM_BREAKTHROUGH).endswith("(прорыв ИИ)")


def test_openai_blog_deployment_is_press_not_research():
    item = {
        "url": "https://openai.com/blog/deploying-gpt-enterprise-partnership",
        "title": "OpenAI announces enterprise deployment partnership",
        "description": "Official rollout and deployment plan with major partners",
    }
    assert is_substantive_press_for_pool(item)
    assert not is_research_science_candidate(item)
    assert classify_material_form(item) == MATERIAL_FORM_PRESS
    apply_material_form_to_candidate(item)
    assert item["title"].endswith("(пресс-релиз)")
    assert not looks_like_product_tool_promo(item)


def test_nvidia_blog_launch_not_product_promo():
    item = {
        "url": "https://blogs.nvidia.com/blog/2026/06/01/ai-factory-deployment/",
        "title": "NVIDIA launches AI factory deployment program",
        "description": "Company announces national deployment partnership",
    }
    assert is_substantive_press_for_pool(item)
    assert classify_material_form(item) == MATERIAL_FORM_PRESS


def test_yandex_education_university_phd_program_is_training_not_article():
    item = {
        "url": "https://education.yandex.ru/university/hse-ai-science",
        "title": "ИИ и машинное обучение — программа аспирантуры от НИУ ВШЭ",
        "description": (
            "Аспирантура 3 года. Исследователь. Аспиранты займутся фундаментальными "
            "исследованиями в области ИИ. Фундаментальная наука."
        ),
    }
    assert is_training_education_candidate(item)
    assert not is_research_science_candidate(item)
    assert classify_material_form(item) == MATERIAL_FORM_TRAINING
    apply_material_form_to_candidate(item)
    assert item["title"].endswith("(обучение)")


def test_yandex_education_learn_catalog_is_training_not_research():
    item = {
        "url": "https://education.yandex.ru/portalnew/learn",
        "title": "Изучайте | Яндекс Образование",
        "description": (
            "Каталог программ. Искусственный интеллект. Машинное обучение. "
            "Data Science. Исследования."
        ),
    }
    assert is_training_education_candidate(item)
    assert not is_research_science_candidate(item)
    assert classify_material_form(item) == MATERIAL_FORM_TRAINING
    apply_material_form_to_candidate(item)
    assert item["title"].endswith("(обучение)")


def test_sber_career_team_page_is_service_not_research():
    item = {
        "url": "https://developers.sber.ru/kak-v-sbere/teams/lab_ai",
        "title": "Присоединяйся к работе в команде Центр практического искусственного интеллекта",
        "description": (
            "Стратегическое подразделение Сбера в области ИИ. "
            "Фундаментальные исследования. Все вакансии. Хочу в команду."
        ),
    }
    assert is_participation_invite_candidate(item)
    assert not is_research_science_candidate(item)
    assert classify_material_form(item) == MATERIAL_FORM_SERVICE
    apply_material_form_to_candidate(item)
    assert item["title"].endswith("(услуга/реклама)")


def test_sber_vacancy_url_detected_as_non_article():
    url = "https://developers.sber.ru/kak-v-sbere/vacancies/data-scientist-lab-ai"
    assert is_product_tool_landing_url(url)
    item = {
        "url": url,
        "title": "Data Scientist CV+NLP в Лаборатория по искусственному интеллекту",
        "description": "Вакансия в команде Сбера",
    }
    assert is_participation_invite_candidate(item)
    form = classify_material_form(item)
    assert form == MATERIAL_FORM_SERVICE
    assert should_reject_commercial_non_article(item, form)


def test_neurolegal_about_is_service_not_article():
    url = "https://neurolegal.ya.ru/c/about"
    assert is_product_tool_landing_url(url)
    item = {
        "url": url,
        "title": "ИИ-помощник для юристов",
        "description": "Сервис Яндекса. Оставить заявку. Подписка и тарифы.",
    }
    assert classify_material_form(item) == MATERIAL_FORM_SERVICE
    apply_material_form_to_candidate(item)
    assert item["title"].endswith("(услуга/реклама)")
    assert should_reject_commercial_non_article(item, MATERIAL_FORM_SERVICE)


def test_yandex_cybersecurity_report_is_article_not_finance():
    item = {
        "url": "https://yandex.ru/company/news/11-06-2026-01",
        "title": "Яндекс выпустил отчёт, посвящённый ИИ-технологиям в кибербезопасности",
        "description": (
            "Защита инфраструктуры и пользователей. Антиробот отразил 866 атак. "
            "Заблокировано 4,9 млрд вредоносных писем и 11 млн аккаунтов."
        ),
    }
    form = classify_material_form(item)
    assert form == MATERIAL_FORM_ARTICLE
    assert not is_finance_candidate(item)


def test_ai_trainers_landing_rejected():
    url = "https://ai-trainers.ya.ru/ai/trener_jurispr"
    assert is_training_saas_landing_url(url)
    item = {"url": url, "title": "ИИ-тренажёр для юристов", "description": "Попробовать бесплатно"}
    form = classify_material_form(item)
    assert should_reject_commercial_non_article(item, form)


def test_manual_url_commercial_reject_for_vacancy_and_neurolegal():
    from app.services.step1_candidate_policy import manual_url_commercial_reject_reason

    assert manual_url_commercial_reject_reason(
        "https://developers.sber.ru/kak-v-sbere/vacancies/data-scientist-lab-ai",
        title="Data Scientist",
    )
    assert manual_url_commercial_reject_reason(
        "https://neurolegal.ya.ru/c/about",
        title="ИИ-помощник для юристов",
        topic_corpus="Оставить заявку. Подписка.",
    )
    assert manual_url_commercial_reject_reason(
        "https://ai-trainers.ya.ru/ai/trener_jurispr",
        title="ИИ-тренажёр",
    ) is not None
    assert (
        manual_url_commercial_reject_reason(
            "https://yandex.ru/company/news/11-06-2026-01",
            title="Яндекс выпустил отчёт о кибербезопасности",
            topic_corpus="отчёт об ИИ в безопасности",
        )
        is None
    )


def test_theme_classifiers_and_quotas():
    assert classify_material_form(
        {
            "url": "https://ria.ru/20260601/ai-law",
            "title": "Госдума приняла законопроект о регулировании ИИ",
            "description": "Новое законодательство и AI Act",
        }
    ) == MATERIAL_FORM_LEGISLATION
    assert classify_material_form(
        {
            "url": "https://rbc.ru/tech/ai-funding",
            "title": "Стартап привлёк инвестиции $200 million на развитие LLM",
            "description": "Раунд финансирования series B",
        }
    ) == MATERIAL_FORM_FINANCE
    assert classify_material_form(
        {
            "url": "https://lenta.ru/2026/06/01/military-ai",
            "title": "Минобороны внедряет ИИ в систему БПЛА",
            "description": "Военные дроны с нейросетью",
        }
    ) == MATERIAL_FORM_MILITARY
    assert classify_material_form(
        {
            "url": "https://ria.ru/20260601/ai-model-record",
            "title": "Прорыв в генеративном ИИ: новая модель превзошла рекорд",
            "description": "Компания представила milestone",
        }
    ) == MATERIAL_FORM_BREAKTHROUGH
    assert SERIOUS_POOL_THEME_QUOTAS == {
        "research": 2,
        "finance": 2,
        "training": 1,
        "military": 2,
        "breakthrough": 2,
        "legislation": 1,
    }


def test_corporate_press_with_deployment_is_substantive():
    item = {
        "url": "https://businesswire.com/news/home/123",
        "source": "businesswire.com",
        "title": "Acme announces national AI deployment partnership with government",
        "description": "Press release: $50 million investment plan for federal AI program.",
    }
    assert is_substantive_press_for_pool(item)
    assert not looks_like_product_tool_promo(item)
