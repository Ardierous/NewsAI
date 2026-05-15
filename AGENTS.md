# AGENTS.md — подсказки для агентов Cursor

## Нерабочие ссылки / серые карточки на шаге 1

**Обязательный документ:** [docs/STEP1_LINKS_RUNBOOK.md](docs/STEP1_LINKS_RUNBOOK.md)

Используйте его, если в запросе есть:

- «ссылки не работают», «битые URL», «не открывается»
- мало зелёных плашек «Ссылка рабочая» / «Можно в топ‑5»
- ошибка 502 на `POST /digests/{id}/step1/run`
- заголовки есть, верификация серая

**Не делайте без runbook:**

- не ослабляйте проверки, «чтобы прошло больше кандидатов», без понимания кода отказа;
- не возвращайте доверие к `url`/`title` от CrewAI без `_verify_llm_candidate_dict`;
- не удаляйте `_filter_score_url_mutations` и не принимайте URL из ScoringAgent.

**Быстрые команды после правок:**

```bash
cd backend
python -m pytest tests/test_step1_link_validation_smoke.py tests/test_step1_regression_pipeline.py -q
```

**Главный код:** `backend/app/services/digest_service.py` (`_fetch_article_page_bundle`, `_verify_llm_candidate_dict`, `_redirect_should_reject`, `_page_is_article_like`, `_filter_score_url_mutations`, `run_step_1`).

## Прочее

- Общий запуск и `.env`: [README.md](README.md)
- Контракт дайджеста: `backend/app/prompts/digest_contract.txt`
