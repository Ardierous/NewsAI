from types import SimpleNamespace

from app.services.digest_service import DigestService, _title_primarily_russian
from app.services.reader_copy import title_needs_russian_translation, title_translation_acceptable


def test_english_headline_is_not_primarily_russian():
    assert not _title_primarily_russian("OpenAI unveils GPT-5 for developers worldwide")
    assert _title_primarily_russian("OpenAI представила GPT-5 для разработчиков")


def test_mixed_and_english_headlines_need_translation():
    assert title_needs_russian_translation("Meta открыла Llama 4")
    assert title_needs_russian_translation("OpenAI unveils GPT-5 for developers worldwide")
    assert title_needs_russian_translation(
        "Man Hires Swarm of AI Agents to Go on Epic Hacking Spree Against Major Companies"
    )
    assert title_needs_russian_translation(
        "Victoria McClune is challenging how the AI industry understands its users"
    )
    assert not title_needs_russian_translation("OpenAI представила GPT-5 для разработчиков")
    assert not title_needs_russian_translation("Сбер запустил нового помощника")


def test_russian_with_latin_proper_names_is_acceptable():
    ru = "Victoria McClune бросает вызов тому, как индустрия ИИ понимает пользователей"
    assert title_translation_acceptable(ru, source="Victoria McClune is challenging how the AI industry understands its users")
    assert not title_needs_russian_translation(ru)

    ru2 = "Uber дала более 30 000 сотрудникам агента ChatGPT Work от OpenAI"
    # если в переводе осталось английское work как служебное — не принимаем
    assert title_needs_russian_translation("Uber gives more than 30,000 staff OpenAI's ChatGPT Work agent")


def test_ensure_russian_title_keeps_cyrillic():
    svc = DigestService.__new__(DigestService)
    svc.proxy = SimpleNamespace(chat=lambda **_kwargs: "should not be called")
    out = DigestService._ensure_russian_candidate_title(
        svc, 1, "https://example.com/a", "Сбер запустил нового помощника"
    )
    assert out == "Сбер запустил нового помощника"


def test_ensure_russian_title_accepts_translation_with_person_name():
    src = "Victoria McClune is challenging how the AI industry understands its users"
    ru = "Victoria McClune бросает вызов тому, как индустрия ИИ понимает пользователей"
    svc = DigestService.__new__(DigestService)
    svc.proxy = SimpleNamespace(chat=lambda **_kwargs: ru)
    out = DigestService._ensure_russian_candidate_title(
        svc, 1, "https://thenextweb.com/news/victoria-mcclune-ai-industry-users-silent-downgrade", src
    )
    assert out == ru
    assert title_translation_acceptable(out, source=src)


def test_ensure_russian_title_translates_english():
    svc = DigestService.__new__(DigestService)
    svc.proxy = SimpleNamespace(chat=lambda **_kwargs: "OpenAI представила GPT-5 для разработчиков")
    out = DigestService._ensure_russian_candidate_title(
        svc, 1, "https://example.com/a", "OpenAI unveils GPT-5 for developers worldwide"
    )
    assert "представила" in out
    assert _title_primarily_russian(out)
    assert not title_needs_russian_translation(out)


def test_ensure_russian_title_rejects_english_model_output():
    svc = DigestService.__new__(DigestService)
    svc.proxy = SimpleNamespace(chat=lambda **_kwargs: "OpenAI unveils GPT-5 for developers")
    src = "OpenAI unveils GPT-5 for developers worldwide"
    out = DigestService._ensure_russian_candidate_title(svc, 1, "https://example.com/a", src)
    assert out == src
