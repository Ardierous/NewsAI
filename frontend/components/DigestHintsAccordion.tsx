"use client";

import Link from "next/link";
import { type CSSProperties, type ReactNode } from "react";

const c: CSSProperties = { color: "#e2e8f0" };
const li: CSSProperties = { lineHeight: 1.55, marginBottom: 8, fontSize: "0.92rem", color: "#cbd5e1" };
const dev: CSSProperties = {
  marginTop: 12,
  paddingTop: 12,
  borderTop: "1px solid #334155",
  fontSize: "0.86rem",
  color: "#94a3b8",
  lineHeight: 1.55,
};

function Acc({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="digest-step-details">
      <summary className="digest-step-summary">{title}</summary>
      <div className="digest-step-details-body">{children}</div>
    </details>
  );
}

export function DigestHintsAccordion() {
  return (
    <div id="digest-hints" className="card wizard-collapsible-card">
      <details className="digest-step-details">
        <summary className="digest-step-summary">
          Памятка: шаги и «под капотом» · статусы · шаги 0–4 · конфиг и код
        </summary>
        <div className="digest-step-details-body">
          <p style={{ fontSize: "0.88rem", color: "#94a3b8", lineHeight: 1.55, margin: "0 0 12px" }}>
            Свернута по умолчанию, как журнал проверки ссылок. Раскройте раздел целиком или отдельный шаг ниже.
          </p>
          <div className="digest-hints-stack">
            <Acc title="Введение: цепочка и статусы">
              <p style={{ ...li, marginTop: 0 }}>
                Конвейер: тип выпуска и окно дат → сбор кандидатов → выбор пяти → порядок → аналитика → финальные
                посты. Поле «Текущий статус» в шапке = статус в базе.
              </p>
              <ul style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
                <li style={li}>
                  <code style={c}>draft</code> — выпуск создан, тип не сохранён; шаг 1 заблокирован.
                </li>
                <li style={li}>
                  <code style={c}>step_0</code> — тип и окно сохранены; доступен шаг 1.
                </li>
                <li style={li}>
                  <code style={c}>step_1_candidates</code> — пул кандидатов в БД; повтор шага 1 пересоберёт список.
                </li>
                <li style={li}>
                  <code style={c}>selected</code> — пятёрка сохранена; доступны порядок и (после него) аналитика.
                </li>
                <li style={li}>
                  <code style={c}>analytics_ready</code> — аналитика готова; шаг 4.
                </li>
                <li style={{ ...li, marginBottom: 0 }}>
                  <code style={c}>final_ready</code> — тексты и файлы готовы.
                </li>
              </ul>
              <div style={dev}>
                <strong style={{ color: "#cbd5e1" }}>Панель:</strong> на главной показываются два выпуска (сегодня по МСК
                и предыдущий), остальные — в модалке «Все выпуски».{" "}
                <strong style={{ color: "#cbd5e1" }}>Код:</strong> <code style={c}>Digest</code> в{" "}
                <code style={c}>backend/app/models.py</code>; статусы — <code style={c}>digest_service.py</code> (
                <code style={c}>STATUS_*</code>); UI — <code style={c}>GET /digests/{"{id}"}</code>,{" "}
                <code style={c}>Dashboard.tsx</code>.
              </div>
            </Acc>

            <Acc title="Шаг 0 — тип дайджеста и окно новостей">
              <p style={{ ...li, marginTop: 0 }}>
                Задаётся тон (серьёзный / курьёзный / по календарю) и окно по дате публикации (календарные или рабочие
                дни). Без сохранения шага 0 сервер не переведёт выпуск в <code style={c}>step_0</code>, шаг 1 вернёт 400.
              </p>
              <ul style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
                <li style={li}>
                  Окно ограничивает шаг 1: материалы старше N дней от даты выпуска отсекаются (
                  <code style={c}>published_before_window</code>).
                </li>
                <li style={{ ...li, marginBottom: 0 }}>
                  Кнопка <strong>Настройки</strong> открывает модалку с параметрами приложения (комментарии «почему так» и
                  альтернативы) — не путать с «Настройки фильтра новостей» на шаге 1.
                </li>
              </ul>
              <div style={dev}>
                <strong style={{ color: "#cbd5e1" }}>API:</strong> <code style={c}>POST /digests/{"{id}"}/step0</code> →{" "}
                <code style={c}>run_step_0</code>. Дефолты шага 0 — <code style={c}>backend/app/digest_defaults.json</code>.
                Схемы — <code style={c}>backend/app/schemas.py</code>.
              </div>
            </Acc>

            <Acc title="Шаг 1 — сбор, tier-поиск, проверка ссылок">
              <p style={{ ...li, marginTop: 0 }}>
                Итеративный сбор URL батчами, верификация каждой страницы (GET, маркеры статьи, тема ИИ, окно дат,
                политика источников). Долго — нормально. Журнал проверки ссылок выше — свёрнутая сводка по последнему
                прогону.
              </p>
              <ul style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
                <li style={li}>
                  <strong>Tier-strict поиск</strong> (по умолчанию): домены tier-1…tier-4 из{" "}
                  <code style={c}>source_tiers.txt</code>, запросы с <code style={c}>site:</code>; вне политики —{" "}
                  <code style={c}>non_policy_source</code>.
                </li>
                <li style={li}>
                  <strong>Telegram-монитор</strong> подмешивает внешние ссылки из каналов (
                  <code style={c}>pipeline_settings.json</code>) — на карточке «Из Telegram», не «Ручная ссылка».
                </li>
                <li style={li}>
                  <strong>Ручное поле URL</strong> на шаге 1 — отдельный origin «Ручная ссылка (поле URL)»; Telegram-seed
                  не считается ручным вводом.
                </li>
                <li style={li}>
                  <strong>Фильтры и порог воронки</strong> — «Настройки фильтра новостей»: порядок, вкл/выкл,{" "}
                  <code style={c}>min_discovered_pages</code> (хранится в{" "}
                  <code style={c}>step1_filter_settings.json</code>).
                </li>
                <li style={{ ...li, marginBottom: 0 }}>
                  <strong>Автопоиск:</strong> в <code style={c}>.env</code> достаточно{" "}
                  <code style={c}>PROXYAPI_API_KEY</code>; включение fetch и лимиты — в{" "}
                  <code style={c}>pipeline_settings.json</code> (перекрываются через <code style={c}>.env</code> при
                  необходимости). Без поиска — ручные URL обязательны.
                </li>
              </ul>
              <div style={dev}>
                <strong style={{ color: "#cbd5e1" }}>API:</strong> <code style={c}>POST …/step1/run</code> →{" "}
                <code style={c}>run_step_1</code>. Ключевые функции: <code style={c}>_fetch_article_page_bundle</code>,{" "}
                <code style={c}>_verify_llm_candidate_dict</code>, <code style={c}>_build_manual_candidates</code>,{" "}
                <code style={c}>fetch_tier_prioritized_raw_urls</code> в <code style={c}>news_search.py</code>. Origin
                карточек: <code style={c}>candidate_origin.py</code>. Документы:{" "}
                <code style={c}>docs/STEP1_PIPELINE.md</code>, <code style={c}>docs/STEP1_LINKS_RUNBOOK.md</code>.
              </div>
            </Acc>

            <Acc title="Шаг 2 — выбор пяти и порядок">
              <p style={{ ...li, marginTop: 0 }}>
                Два подблока в мастере: <strong>выбор пятёрки</strong> (чекбоксы) и <strong>порядок</strong> (drag-and-drop).
                «Подтвердить 5» / «Оставь топ‑5» только сохраняют состав → <code style={c}>selected</code>. Аналитика
                запускается после «Применить порядок» или «Оптимально по мнению ИИ» (если включён автозапуск на сервере).
              </p>
              <ul style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
                <li style={li}>
                  Чекбокс активен только при «Читаемый заголовок», «Ссылка рабочая», «Можно в топ‑5». Пятёрку можно
                  перевыбрать на любом этапе — сбросятся аналитика и финал.
                </li>
                <li style={li}>
                  <strong>Порядок:</strong> перетаскивание, «Применить порядок», «Оптимально по мнению ИИ» (ProxyAPI). ИИ
                  даёт общую аргументацию порядка и пояснение к каждой позиции. После шага 3 — кнопка «Изменить порядок»
                  рядом с «Применить порядок».
                </li>
                <li style={li}>
                  <strong>Происхождение</strong> (не достоверность): Web-поиск / Из Telegram / Ручная ссылка / LLM-добор.
                </li>
                <li style={li}>
                  <strong>Tier и ✅/⚠️/❗</strong> — политика домена из <code style={c}>source_tiers.txt</code> (
                  <code style={c}>reliability_status</code>).
                </li>
                <li style={{ ...li, marginBottom: 0 }}>
                  Агрегаторы, дубликаты, «❗ без подтверждения» и нерабочие ссылки нельзя выбрать в топ‑5.
                </li>
              </ul>
              <div style={dev}>
                <strong style={{ color: "#cbd5e1" }}>API:</strong> <code style={c}>POST …/step2/select</code>,{" "}
                <code style={c}>POST …/step2/order</code>, <code style={c}>POST …/step2/order/ai-optimal</code>. Условие
                выбора на фронте: <code style={c}>candidateSelectableForStep2</code> в{" "}
                <code style={c}>DigestWizard.tsx</code>. Обоснование порядка — asset{" "}
                <code style={c}>step2_order_rationale</code>.
              </div>
            </Acc>

            <Acc title="Шаги 3–4 — аналитика и финал">
              <p style={{ ...li, marginTop: 0 }}>
                <strong>Шаг 3</strong> — редакторская аналитика по каждой из пяти новостей (суть, заметка, развёрнутый
                анализ) и хэштеги; материал для редактора, не для публикации читателям. <strong>Шаг 4</strong> — обложки
                (если включены), тексты площадок и QC. Под каждым заголовком в постах — простой текст для читателей: 2–4
                коротких предложения, до 450 символов без заголовка (<code style={c}>reader_copy.py</code>,{" "}
                <code style={c}>ReaderCopyAgent</code>).
              </p>
              <div style={dev}>
                <strong style={{ color: "#cbd5e1" }}>API:</strong> <code style={c}>POST …/step3/confirm-ready</code> →{" "}
                <code style={c}>run_step_3_analytics</code>; шаг 4: <code style={c}>generate-images</code>,{" "}
                <code style={c}>select-image</code>, <code style={c}>generate-texts</code>. Итог —{" "}
                <code style={c}>GET /digests/{"{id}"}</code>, экспорт <code style={c}>/docx</code> и изображения в{" "}
                <code style={c}>routes_digests.py</code>.
              </div>
            </Acc>

            <Acc title="Под капотом: конфиг, файлы, фронт">
              <ul style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
                <li style={li}>
                  <strong>Секреты:</strong> <code style={c}>backend/.env</code> — минимум{" "}
                  <code style={c}>PROXYAPI_API_KEY</code> (см. <code style={c}>.env.example</code>).
                </li>
                <li style={li}>
                  <strong>Пайплайн:</strong> <code style={c}>backend/app/pipeline_settings.json</code> →{" "}
                  <code style={c}>config.py</code> (batch, timebox, tier_strict, Telegram, web.fetch; .env поверх JSON).
                </li>
                <li style={li}>
                  <strong>Фильтры шага 1:</strong> <code style={c}>backend/app/step1_filter_settings.json</code> + UI
                  «Настройки фильтра новостей».
                </li>
                <li style={li}>
                  <strong>Политика источников:</strong> <code style={c}>backend/app/prompts/source_tiers.txt</code> +{" "}
                  <code style={c}>source_tiers_policy.py</code>.
                </li>
                <li style={li}>
                  <strong>Расходы LLM:</strong> <code style={c}>ProxyApiClient</code>,{" "}
                  <code style={c}>cost_tracker.py</code>, таблица <code style={c}>llm_cost_records</code>, лимиты{" "}
                  <code style={c}>step1_max_cost_rub</code> / <code style={c}>step2_max_cost_rub</code>.
                </li>
                <li style={li}>
                  <strong>UI мастера:</strong> <code style={c}>DigestWizard.tsx</code>,{" "}
                  <code style={c}>DigestHintsAccordion.tsx</code>, <code style={c}>globals.css</code>, запросы —{" "}
                  <code style={c}>frontend/lib/api.ts</code>.
                </li>
                <li style={{ ...li, marginBottom: 0 }}>
                  <strong>Маршруты:</strong> панель <code style={c}>Dashboard.tsx</code>, мастер{" "}
                  <code style={c}>app/digests/[id]/page.tsx</code>, общая шапка{" "}
                  <code style={c}>app/layout.tsx</code>.
                </li>
              </ul>
              <p style={{ ...li, marginTop: 12, marginBottom: 0 }}>
                Навигация: <Link href="/">панель выпусков</Link> — список дат; логотип на любой странице ведёт туда же.
                Подробнее по шагу 1 — в <code style={c}>README.md</code> и <code style={c}>docs/STEP1_PIPELINE.md</code>.
              </p>
            </Acc>
          </div>
        </div>
      </details>
    </div>
  );
}
