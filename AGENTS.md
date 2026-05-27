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

**Быстрые команды после правок шага 1:**

```bash
cd backend
python -m pytest tests/test_step1_link_validation_smoke.py tests/test_step1_regression_pipeline.py -q
```

**Главный код:** `backend/app/services/digest_service.py` (`_fetch_article_page_bundle`, `_verify_llm_candidate_dict`, `_redirect_should_reject`, `_page_is_article_like`, `_filter_score_url_mutations`, `run_step_1`).

## Инспекция / регрессия

Полное описание — [README.md](README.md) § «Тестирование».

```bash
# Backend: все тесты
cd backend && python -m pytest tests -q

# Сквозной офлайн 0→4 (моки, без live ProxyAPI)
cd backend && python -m pytest tests/test_digest_pipeline_chain.py -q

# Frontend
cd frontend && npm run build

# Smoke backend
curl http://127.0.0.1:8000/health
```

**Browser E2E:** поднять `python main.py`, пройти мастер 0→4 на `/digests/{id}` (шаг 1 — долгий запрос без таймаута в браузере). Ориентиры UI: тексты кнопок на русском, заголовки «Шаг N — …», классы `news-candidate-*`, `btn-rebuild`. Шаг 2: сначала «Подтвердить 5» / «Оставь топ‑5», затем порядок («Применить порядок» / «Оптимально по мнению ИИ»); аналитика — после порядка.

## Модели Cursor (IDE)

Какую модель выбрать в Agent/Chat под задачу — **[docs/CURSOR_MODELS.md](docs/CURSOR_MODELS.md)** и правила `.cursor/rules/cursor-models*.mdc` (кратко: Codex — backend/код, fast Composer — UI, medium — вопросы без правок).

## Прочее

- Общий запуск и `.env`: [README.md](README.md)
- Параметры пайплайна (таймауты, батчи, telegram): `backend/app/pipeline_settings.json`
- Дорожка шага 1 (поиск, воронка, порог): [docs/STEP1_PIPELINE.md](docs/STEP1_PIPELINE.md)
- Контракт дайджеста: `backend/app/prompts/digest_contract.txt`
