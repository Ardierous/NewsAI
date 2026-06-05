# Шаг 1 — блок-схема поиска и фильтрации ссылок

Понятное описание того, что делает backend при `POST /digests/{id}/step1/run`: от запуска до сохранения пула кандидатов. **Пояснения встроены в узлы блок-схем** (не только в текст ниже).

Связанные документы: [STEP1_PIPELINE.md](STEP1_PIPELINE.md), [STEP1_LINKS_RUNBOOK.md](STEP1_LINKS_RUNBOOK.md).

---

## Где тратятся время и токены ProxyAPI

| Этап | Долго? | ProxyAPI / другое | Метод / API |
|------|--------|-------------------|-------------|
| Проверка баланса ключа | быстро | ProxyAPI REST | `GET …/proxyapi/balance` (`ProxyApiCostTracker.get_balance_snapshot`) |
| Tier-поиск сырых URL | **да, 1–3 мин на проход** | **ProxyAPI** web_search (`low`), лимит **6** батчей/collect; SerpAPI/Tavily опциональны | см. [STEP1_COST_OPTIMIZATION_STEPS.md](STEP1_COST_OPTIMIZATION_STEPS.md) |
| Prefilter (без HTTP) | быстро | локально | без LLM |
| HTTP-верификация страниц | **да, 1–2 мин на 30–40 URL** | **не ProxyAPI** | обычный `requests.get` к сайтам |
| Crew fallback (если &lt;10) | очень долго | ProxyAPI через Crew/LiteLLM | `chat.completions` агентов |
| Перевод заголовка | иногда | ProxyAPI | `ProxyApiClient.chat` |

**Главный тормоз в вашем логе:** не «один медленный raw-запрос», а цепочка **много tier-батчей → HTTP-проверка → мало прошло → повторный добор** (15+ минут на одну итерацию при конверсии 2 из 34).

---

## Общая блок-схема шага 1

```mermaid
flowchart TD
    START(["Пользователь нажимает «Запустить сбор»<br/>POST /digests/id/step1/run<br/>тело: manual_urls, news_window_days"]) --> INIT

    INIT["Подготовка run_step_1<br/>─── Окно дат (шаг 0) ───<br/>update_news_window: days + kind раб/календ<br/>→ earliest … anchor (напр. 28.05–02.06)<br/>используется в after: и published_before_window<br/>─── Цели сбора (план, не результат) ───<br/>STEP1_MIN_VERIFIED = 10 проверенных статей<br/>target_pool ≈ 15 для UI · cap collect ≤ 30<br/>это НЕ «10–30 raw URL»<br/>─── Настройки ───<br/>step1_filter_settings.json · soft/hard timeout<br/>serious+ tier_strict или curious · журнал отбраковки<br/>сброс шагов 2–4 при полной пересборке"]
    INIT --> BAL{"ProxyAPI: баланс и бюджет<br/>GET /proxyapi/balance<br/>+ лимит STEP1_MAX_COST_RUB на выпуск"}

    BAL -->|нулевой баланс / бюджет| STOP402(["Стоп 402<br/>PROXYAPI_ZERO_BALANCE /<br/>бюджет ключа исчерпан<br/>алерт в UI · сбор не стартует"])
    BAL -->|OK| TG

    TG["Telegram-монитор (если включён)<br/>GET t.me/s/канал · timeout ~4 с<br/>внешние URL в seed · без ProxyAPI<br/>фильтр по earliest дате окна"]
    TG --> MANUAL

    MANUAL["Ручные URL (поле шага 1)<br/>HTTP GET каждой страницы<br/>без ProxyAPI web_search<br/>невалидные → журнал, не блокируют tier-поиск"]
    MANUAL --> LOOP

    subgraph ITER["Итеративный цикл (_step1_collect_iterative_batches)"]
        LOOP{"Стоп цикла?<br/>• POST step1/cancel · user_cancelled<br/>• ProxyAPI без средств · 402<br/>• hard_time_limit_sec (240 с)<br/>• verified ≥ 10 и ≥ 2 итерации → target_min_met<br/>• verified ≥ collection_target (≈15 UI)<br/>• soft timeout 150 с + уже ≥ 10 verified<br/>• post_collect пропускается, если pre ≥ 10"}
        LOOP -->|нет| RAW
        RAW["① Сбор СЫРЫХ URL (raw_unique)<br/>fetch_tier_prioritized_raw_urls<br/>tier-1→4 · батчи по 3 домена · ProxyAPI<br/>42 raw ≠ 10 verified · см. § raw"]
        RAW --> PRE["② Prefilter (без HTTP, без LLM)<br/>duplicate · tier · агрегатор · дата в path<br/>отсечка до скачивания страниц"]
        PRE --> HTTP["③ HTTP-верификация<br/>до ~34–58 URL · ThreadPool 6 workers<br/>requests.get к сайтам · не ProxyAPI<br/>_verify_llm_candidate_dict"]
        HTTP --> ADD["④ В verified_pool только если<br/>headline_editorial_ok + link_status<br/>иначе register_reject (серая карточка)"]
        ADD --> SUP["⑤ Supplement / top-up<br/>если verified &lt; need · повтор tier-батчей<br/>тот же алгоритм raw, меньший fetch_limit"]
        SUP --> LOOP
        LOOP -->|да| POSTLOOP
    end

    POSTLOOP["После цикла: rebalance<br/>≤ 2 новости с одного домена<br/>seed listing fallback если мало verified<br/>HTTP-разбор лент source_tiers"]
    POSTLOOP --> MIN{"verified_pool ≥ 10?<br/>STEP1_MIN_VERIFIED<br/>иначе 502 после всех доборов"}
    MIN -->|нет, редко| CREW["Crew fallback (legacy)<br/>ProxyAPI chat.completions<br/>NewsResearch → Verify → Score<br/>очень долго · обходится tier-поиском"]
    CREW --> MIN2{"≥ 10 после Crew?"}
    MIN2 -->|нет| FAIL502(["502 Bad Request<br/>breakdown: published_before_window,<br/>http_unreachable, …<br/>частичный пул может сохраниться"])
    MIN -->|да| SAVE
    MIN2 -->|да| SAVE
    SAVE["Сохранение NewsCandidate в БД<br/>step1_collection_meta (elapsed, funnel)<br/>status = step_1 · UI → шаг 2"]
    SAVE --> END(["Ответ API: список кандидатов<br/>на шаге 2 выбираете топ-5"])
```

---

## Детально: сбор сырых URL

**Важно:** «сырые URL» (`raw_unique`) — это ещё **не** проверенные новости. Это ссылки из поиска **до** prefilter и HTTP. Цель verified (минимум **10** статей в пуле) — отдельный этап ниже по схеме.

Код: `_collect_search_verified_candidates` → `fetch_tier_prioritized_raw_urls` → `fetch_article_urls_raw_merged` → `ProxyApiClient.search_news_article_urls`.

### Уровень 1 — обход tier-1…4 (`fetch_tier_prioritized_raw_urls`)

```mermaid
flowchart TD
    IN(["Вход из итерации сбора<br/>_collect_search_verified_candidates<br/>need_verified = сколько ещё не хватает"]) --> POL

    POL["Загрузка политики source_tiers.txt<br/>tier-1 (RIA, Vedomosti…) → tier-4 (OpenAI, Sber…)<br/>+ search_seed_urls — подсказки разделов<br/>URL вне tier-1…4 не попадут в raw"]
    POL --> TLOOP

    subgraph TIERLOOP["Цикл по tier (Tier-1 → Tier-4)"]
        TLOOP["Следующий tier-N<br/>список доменов из политики<br/>остановка если _enough_coverage()"]
        TLOOP --> BLOOP
        subgraph BATCHLOOP["Цикл батчей (до 3 домена за запрос)"]
            BLOOP["Батч доменов<br/>напр. ria.ru + interfax.ru + vedomosti.ru<br/>лог: Tier-поиск: Tier-1 | hosts=…"]
            BLOOP --> QBUILD
            QBUILD["Текст запроса к ProxyAPI<br/>window_prefix: after:YYYY-MM-DD<br/>topic_terms: тема ИИ + исключения промо<br/>seed_hint: URL разделов из policy<br/>site:h1 OR site:h2 OR site:h3<br/>product_excludes: landing/инструменты"]
            QBUILD --> FM["fetch_article_urls_raw_merged<br/>limit per_batch ≈ 6–12 URL<br/>см. схему провайдеров ниже"]
            FM --> ACC["_accept(urls) накопление merged[]<br/>• только http(s) + is_policy_tier_source<br/>• dedup по URL (seen set)<br/>• max 2–4 URL с одного hostname<br/>• лишнее отбрасывается до prefilter"]
            ACC --> COV{"_enough_coverage()?<br/>len(merged) ≥ raw_target (~30)<br/>И unique_hosts ≥ 6–14<br/>иначе следующий батч/tier"}
            COV -->|нет, есть ещё батчи| BLOOP
        end
        COV -->|нет, tier закончился| TLOOP
        COV -->|да| CAP
    end

    CAP["merged[:fetch_limit]<br/>fetch_limit до ~78 (estimated_raw_for_10)<br/>лог: Tier-поиск: итого count=… unique_hosts=…"]
    CAP --> RESCUE{"Stale rescue?<br/>≥25% raw с датой в path<br/>раньше окна digest"}
    RESCUE -->|да| R1["Один доп. запрос tier-1<br/>fetch_article_urls_raw_merged<br/>_step1_fresh_tier1_query"]
    RESCUE -->|нет| OUT
    R1 --> OUT(["raw_unique[] в _collect_search<br/>глобальный dedup seen_raw<br/>→ search_url_prefilter<br/>⚠ ещё НЕ verified"])
```

| Параметр | Типичное значение | Смысл |
|----------|-------------------|--------|
| `fetch_limit` | до 78 (из `estimated_raw_for_10`) | Верхняя граница raw за один проход |
| `raw_target` | 18–30 | Когда остановить обход tier |
| `min_unique_hosts_target` | 6–14 | Минимум разных доменов в raw |
| `max_urls_per_host` | 2–4 | Не класть все ссылки с одного сайта |
| `per_batch_limit` | 6–12 | Лимит URL с одного ProxyAPI-батча |

**digest 20 (факт):** один полный проход tier → **42 raw**, **14 хостов**, **~99 с**; supplement/top-up повторяют тот же алгоритм с меньшим `fetch_limit`.

### Уровень 2 — один батч: провайдеры (`fetch_article_urls_raw_merged`)

На **каждый** tier-батч вызывается один merge — не «кто первый ответил», а **объединение всех провайдеров**.

```mermaid
flowchart TD
    Q["Query одного tier-батча<br/>after: + тема ИИ + site: + excludes<br/>allowed_hosts = домены батча"]
    Q --> P1

    subgraph PROXY["ProxyAPI (PROXYAPI_WEB_SEARCH_ENABLED)"]
        P1["① responses.create + tool web_search<br/>model: gpt-4o-mini · context low/med/high<br/>prompt: JSON-массив URL · только после after:<br/>только статьи · только домены батча<br/>~5–10 с на батч · основные токены"]
        P1 -->|URL есть| UNIQ
        P1 -->|0 URL| EMPTY["Пустая выдача<br/>search-preview НЕ вызывается<br/>экономия токенов · лог: пустой список"]
        EMPTY --> UNIQ
        P1 -->|ошибка API 429/5xx| P2
        P2["② Fallback chat.completions<br/>gpt-4o-mini-search-preview<br/>только если ① упал с exception<br/>не при «0 URL»"]
        P2 --> UNIQ
    end

    Q --> S3["③ SerpAPI google_news<br/>если SERPAPI_API_KEY в .env<br/>параллельно ProxyAPI · не заменяет"]
    S3 --> UNIQ
    Q --> T4["④ Tavily search<br/>если TAVILY_API_KEY<br/>include_domains = домены батча"]
    T4 --> UNIQ

    UNIQ["_uniq_urls(merged провайдеров)<br/>дедуп внутри одного батча<br/>лог: Веб-поиск count=… cap=…"]
    UNIQ --> ACC2["→ _accept в tier-цикле<br/>tier policy · dedup глобальный<br/>лимит 2–4 URL/хост в merged[]"]
```

### Уровень 3 — когда tier-поиск повторяется

| Ситуация | Что происходит |
|----------|----------------|
| Первая итерация collect | Полный `fetch_tier_prioritized_raw_urls` (`fetch_limit` ≈ 78) |
| Supplement / top-up в цикле | Тот же tier-обход, меньший cap (напр. 24 URL) |
| Много `published_before_window` | Опциональный rescue-батч только по tier-1 |
| Seed listing fallback | **Не** ProxyAPI: HTTP-разбор лент из `search_seed_urls` |

### Пояснения простым языком

1. **Сырые URL ≠ verified.** ProxyAPI может вернуть 42 ссылки; после prefilter+HTTP в пул попадут единицы.

2. **Tier-поиск** — десятки маленьких запросов по `source_tiers.txt`, не один «найди новости про ИИ». Каждый батч ≈ 5–10 с ProxyAPI.

3. **ProxyAPI Responses + web_search** — основной метод; модель возвращает JSON-массив URL.

4. **Chat search-preview** — только при **ошибке** API; при пустой выдаче второй раз не платим.

5. **SerpAPI / Tavily** — подмешиваются в каждый батch, если ключи в `.env`.

6. После `raw_unique` идёт **prefilter** (без HTTP), затем **HTTP-верификация** — отдельный этап, не ProxyAPI.

---

## Детально: prefilter → HTTP → фильтры на странице

```mermaid
flowchart TD
    RAWIN(["raw_unique после tier-поиска<br/>ссылки из ProxyAPI · ещё не проверены"]) --> PF

    subgraph PFILTER["Prefilter search_url_prefilter_reason<br/>без HTTP · без LLM · по порядку фильтров"]
        PF["Обход каждого URL"]
        PF --> PF1["invalid_url · duplicate_url_skip<br/>recent_top5_repeat (зафиксированные топ-5)"]
        PF1 --> PF2["non_policy_source — вне tier-1…4<br/>aggregator_source · forbidden_media"]
        PF2 --> PF3["news_listing_page — часть лент<br/>не отсекаем: пойдут на разворот"]
        PF3 --> PF4["llm_hallucinated_url · product_tool_page<br/>дата в path → published_before_window"]
    end

    PFILTER --> PRIOR["Приоритизация очереди HTTP<br/>tier-1 и свежие URL первыми<br/>лимит urls_sent_to_http ~34–58"]
    PRIOR --> INGEST

    subgraph INGEST["_ingest_step1_urls_with_listing_expansion"]
        INGEST{"listing / topic URL?<br/>vc.ru/ai · habr.com/hubs/…"}
        INGEST -->|да| EXP["HTTP GET ленты<br/>извлечь до 4 дочерних статей<br/>_expand_listing_url_candidates"]
        INGEST -->|нет| POOL
        EXP --> POOL["pending queue<br/>max ~34–58 URL за проход<br/>max 4 URL с одного host"]
        POOL --> WORK["ThreadPoolExecutor 6 workers<br/>requests.get HTML каждого URL<br/>таймаут ~4–8 с · не ProxyAPI"]
        WORK --> VER["_verify_llm_candidate_dict<br/>редирект OK? · заголовок editorial<br/>тема ИИ · дата в окне · не промо/лента"]
    end

    VER --> OK{"page_verified?<br/>headline OK ∧ link OK"}
    OK -->|да| POOLADD["verified_pool[]<br/>считается для STEP1_MIN_VERIFIED=10"]
    OK -->|нет| REJ["register_reject + код<br/>серая карточка в UI<br/>напр. published_before_window"]
```

### Типичные причины «мало в пуле» после долгого прогона

| Симптом в логе | Что значит |
|----------------|------------|
| `Tier-поиск: итого … count=42 unique_hosts=14` | Сырых ссылок достаточно, проблема не в поиске |
| `prefilter=8 http=34 need_verified=10` | 8 отсеяли до HTTP, 34 пошли на проверку |
| `verified=2 elapsed_sec=57` | **Узкое место:** HTTP + фильтры на странице |
| `published_before_window` в статистике | Выдача ProxyAPI отдаёт **старые** URL |
| `ProxyAPI web_search: пустой список` | Батч tier не дал ссылок — токены частично потрачены, URL нет |
| Повтор tier-поиска через 3–5 мин | **top-up / supplement** — добор до минимума 10 |

---

## Остановка и ошибки

```mermaid
flowchart LR
    A["Кнопка «Остановить»<br/>POST step1/cancel"] --> B["Флаг отмены в цикле"]
    B --> C["Сохранить частичный пул<br/>или 499 если пусто"]

    D["402 ProxyAPI<br/>нулевой баланс / бюджет ключа"] --> E["Стоп сбора + алерт в UI"]

    F["502 после всех проходов"] --> G["&lt;10 проверенных<br/>+ breakdown причин"]
```

---

## Ссылки на код

| Узел схемы | Файл / функция |
|------------|----------------|
| Итеративный цикл | `digest_service.py` → `_step1_collect_iterative_batches` |
| Tier-обход raw | `news_search.py` → `fetch_tier_prioritized_raw_urls` |
| Merge провайдеров батча | `news_search.py` → `fetch_article_urls_raw_merged` |
| Stale rescue tier-1 | `digest_service.py` → `_collect_search_verified_candidates` |
| ProxyAPI web_search | `proxyapi_client.py` → `search_news_article_urls` |
| Prefilter | `news_search.py` → `search_url_prefilter_reason` |
| HTTP + verify | `digest_service.py` → `_ingest_step1_urls_with_listing_expansion`, `_verify_llm_candidate_dict` |
| Разворот лент | `digest_service.py` → `_expand_listing_url_candidates` |
| Отмена | `step1_cancellation.py`, `POST …/step1/cancel` |

Документация ProxyAPI web_search: [proxyapi.ru/docs/openai-web-search](https://proxyapi.ru/docs/openai-web-search).
