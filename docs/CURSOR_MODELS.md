# Модели Cursor в проекте News

Краткий гайд по выбору модели в IDE (не путать с ProxyAPI `gpt-4.1-*` в `backend/app/crew/model_policy.py`).

Правила для агента: `.cursor/rules/cursor-models*.mdc`.

## Agent / Composer

| Ситуация | Модель |
|----------|--------|
| `digest_service.py`, шаг 1, pytest, Crew | **gpt-5.3-codex** |
| Фича на несколько файлов backend+frontend | **composer-2.5** или Codex |
| Плашки, свёртки, `DigestWizard`, CSS | **composer-2.5-fast** |
| Не знаете, с чего начать | **Auto** (дороже по токенам) |

## Chat (вопрос без Agent)

| Ситуация | Модель |
|----------|--------|
| «Как устроено», план, документация | **gpt-5.5-medium** |
| Патч, рефакторинг, тест | **gpt-5.3-codex** |
| Сложный баг, два провала подряд | **claude-4.6-sonnet-medium-thinking** |

## Экономия токенов

1. В Agent — `@` только нужные файлы.
2. Не держать **Codex** для «объясни tier-strict» — это **medium**.
3. Повторяющиеся мелочи в UI — **composer-2.5-fast**, не Auto.
4. После правок шага 1 — см. [AGENTS.md](../AGENTS.md) (pytest smoke + regression).

## Документация проекта

| Задача | Документ |
|--------|----------|
| Обзор MVP | [README.md](../README.md) |
| Шаг 1 | [STEP1_PIPELINE.md](STEP1_PIPELINE.md) |
| Битые ссылки | [STEP1_LINKS_RUNBOOK.md](STEP1_LINKS_RUNBOOK.md) |
| Агентам Cursor | [AGENTS.md](../AGENTS.md) |

## Где задать модель в Cursor

- **Composer / Agent**: выпадающий список модели в панели чата.
- **Chat**: выбор модели в обычном чате.
- Проектные подсказки подхватываются из `.cursor/rules/` автоматически.
