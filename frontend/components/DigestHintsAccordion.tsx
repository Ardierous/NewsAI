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
    <div
      id="digest-hints"
      className="card digest-hints-panel"
      style={{ borderColor: "#334e68", background: "rgba(30, 41, 59, 0.65)" }}
    >
      <details className="digest-hints-root">
        <summary className="digest-hints-root-summary">Памятка: шаги и «под капотом»</summary>
        <div className="digest-hints-root-body">
          <p style={{ fontSize: "0.88rem", color: "#64748b", lineHeight: 1.55, margin: "0 0 12px" }}>
            Раскройте при необходимости. Внутри — разделы по шагам (каждый открывается отдельно): сначала смысл для редактора,
            затем куда смотреть в коде.
          </p>
          <div className="digest-hints-stack">
        <Acc title="Введение: зачем цепочка и статусы">
          <p style={{ ...li, marginTop: 0 }}>
            Конвейер фиксирует тип выпуска, собирает кандидатов, даёт выбрать пять новостей, при необходимости упорядочить
            их, затем строит аналитику и финальные посты. Поле «Текущий статус» в шапке мастера = тот же статус в базе.
          </p>
          <ul style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
            <li style={li}>
              <code style={c}>draft</code> — выпуск создан, тип не сохранён; шаг 1 заблокирован.
            </li>
            <li style={li}>
              <code style={c}>step_0</code> — тип сохранён; можно шаг 1.
            </li>
            <li style={li}>
              <code style={c}>step_1_candidates</code> — кандидаты в БД; повтор шага 1 пересоберёт список.
            </li>
            <li style={li}>
              <code style={c}>selected</code> — пять новостей сохранены; блок порядка и шаг 3 доступны.
            </li>
            <li style={li}>
              <code style={c}>analytics_ready</code> — аналитика готова; шаг 4.
            </li>
            <li style={{ ...li, marginBottom: 0 }}>
              <code style={c}>final_ready</code> — тексты и файлы готовы.
            </li>
          </ul>
          <div style={dev}>
            <strong style={{ color: "#cbd5e1" }}>Код:</strong> модель <code style={c}>Digest</code> в{" "}
            <code style={c}>backend/app/models.py</code>; смена статусов в <code style={c}>backend/app/services/digest_service.py</code>{" "}
            (константы <code style={c}>STATUS_*</code>). Сводка выпуска для UI — <code style={c}>GET /digests/{"{id}"}</code> в{" "}
            <code style={c}>backend/app/api/routes_digests.py</code> (сборка ответа из сервиса).
          </div>
        </Acc>

        <Acc title="Шаг 0 — тип дайджеста">
          <p style={{ ...li, marginTop: 0 }}>
            Задаётся тон (серьёзный / курьёзный / по календарю). Без этого сервер не переведёт выпуск в{" "}
            <code style={c}>step_0</code>, и шаг 1 вернёт 400.
          </p>
          <div style={dev}>
            <strong style={{ color: "#cbd5e1" }}>API:</strong> <code style={c}>POST /digests/{"{id}"}/step0</code> →{" "}
            <code style={c}>DigestService.run_step_0</code> (<code style={c}>digest_service.py</code>). Тело:{" "}
            <code style={c}>Step0Request</code> в <code style={c}>backend/app/schemas.py</code>. Тип и флаг «по умолчанию»
            пишутся в строку <code style={c}>digests</code>.
          </div>
        </Acc>

        <Acc title="Шаг 1 — кандидаты, поиск, проверка ссылок">
          <p style={{ ...li, marginTop: 0 }}>
            Сбор URL (ProxyAPI web_search, опционально SerpAPI/Tavily или ручные ссылки), затем Crew при нехватке, для каждого URL: GET
            страницы, маркеры статьи, согласование заголовка, тема ИИ, «читаемый» заголовок, агрегаторы. Долго — нормально.
          </p>
          <ul style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
            <li style={li}>
              <strong>С автопоиском:</strong> <code style={c}>ENABLE_WEB_FETCH=true</code>, <code style={c}>PROXYAPI_API_KEY</code> (веб-поиск через ProxyAPI), опционально{" "}
              <code style={c}>SERPAPI_API_KEY</code> / <code style={c}>TAVILY_API_KEY</code>.
            </li>
            <li style={{ ...li, marginBottom: 0 }}>
              <strong>Без поиска:</strong> <code style={c}>ENABLE_WEB_FETCH=false</code> — обязательны ручные URL в теле запроса шага 1.
            </li>
          </ul>
          <div style={dev}>
            <strong style={{ color: "#cbd5e1" }}>API:</strong> <code style={c}>POST /digests/{"{id}"}/step1/run</code> →{" "}
            <code style={c}>DigestService.run_step_1</code>. Ключевые функции в <code style={c}>digest_service.py</code>:{" "}
            <code style={c}>_fetch_article_page_bundle</code>, <code style={c}>_verify_llm_candidate_dict</code>,{" "}
            <code style={c}>_ai_digest_topic_matches</code>, <code style={c}>_build_manual_candidates</code>, константы отбраковки{" "}
            <code style={c}>REJECT_REASON_*</code>. Поиск без LLM: <code style={c}>backend/app/services/news_search.py</code>. Промпты
            агентов: <code style={c}>backend/app/crew/workflow.py</code> + <code style={c}>backend/app/prompts/digest_contract.txt</code>.
          </div>
        </Acc>

        <Acc title="Шаг 2 — выбор пяти и порядок">
          <p style={{ ...li, marginTop: 0 }}>
            Чекбоксы только у строк, прошедших серверные проверки (метки «Читаемый заголовок», «Ссылка рабочая», «Можно в
            топ‑5»). «Подтвердить 5» или «Оставь топ‑5» переводит в <code style={c}>selected</code>. Перетаскивание + «Применить
            порядок» вызывает отдельного агента упорядочивания.
          </p>
          <div style={dev}>
            <strong style={{ color: "#cbd5e1" }}>API:</strong> <code style={c}>POST …/step2/select</code> →{" "}
            <code style={c}>select_news</code>; <code style={c}>POST …/step2/order</code> → <code style={c}>run_step_2_order</code>. Логика
            выбора и лимиты — в <code style={c}>digest_service.py</code>. Условие «можно в топ‑5» на фронте:{" "}
            <code style={c}>candidateSelectableForStep2</code> в <code style={c}>frontend/components/DigestWizard.tsx</code>.
          </div>
        </Acc>

        <Acc title="Шаг 3 — аналитика и шаг 4 — финал">
          <p style={{ ...li, marginTop: 0 }}>
            Шаг 3: развёрнутая аналитика по каждой из пяти новостей + хэштеги (кнопка без ввода «готово» — пустая команда на
            сервере допустима). Шаг 4: финальные тексты платформ, картинка, QC; в поле — подтверждение <strong>Ок</strong> по
            контракту (<code style={c}>digest_contract.txt</code>).
          </p>
          <div style={dev}>
            <strong style={{ color: "#cbd5e1" }}>API:</strong> <code style={c}>POST …/step3/confirm-ready</code> →{" "}
            <code style={c}>run_step_3_analytics</code>; <code style={c}>POST …/step4/confirm-final</code> → <code style={c}>run_step_4_final</code>. Тела:{" "}
            <code style={c}>CommandRequest</code> в <code style={c}>schemas.py</code>. Итоговые блоки и ассеты пишутся в БД и отдаются
            тем же <code style={c}>GET /digests/{"{id}"}</code> и ссылками на <code style={c}>/docx</code> / изображение в{" "}
            <code style={c}>routes_digests.py</code>.
          </div>
        </Acc>

        <Acc title="Под капотом: конфиг и фронт">
          <ul style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
            <li style={li}>
              <strong>Бюджеты шагов:</strong> переменные <code style={c}>STEP1_MAX_COST_RUB</code>, <code style={c}>STEP2_MAX_COST_RUB</code> в{" "}
              <code style={c}>backend/.env</code> → читаются в <code style={c}>backend/app/config.py</code>, используются в{" "}
              <code style={c}>digest_service.py</code> (сообщения в <code style={c}>budget_notices</code> на UI).
            </li>
            <li style={li}>
              <strong>Очередь запросов к LLM и цены:</strong> <code style={c}>ProxyApiClient</code>,{" "}
              <code style={c}>app/services/cost_tracker.py</code>, таблица <code style={c}>llm_cost_records</code>.
            </li>
            <li style={li}>
              <strong>Этот мастер (кнопки, аккордеоны):</strong> <code style={c}>frontend/components/DigestWizard.tsx</code>,{" "}
              <code style={c}>DigestHintsAccordion.tsx</code>, стили <code style={c}>frontend/app/globals.css</code>. Запросы:{" "}
              <code style={c}>frontend/lib/api.ts</code>.
            </li>
            <li style={{ ...li, marginBottom: 0 }}>
              <strong>Панель списка выпусков:</strong> <code style={c}>frontend/components/Dashboard.tsx</code>. Общая шапка сайта:{" "}
              <code style={c}>frontend/app/layout.tsx</code>. Маршрут мастера: <code style={c}>frontend/app/digests/[id]/page.tsx</code>.
            </li>
          </ul>
          <p style={{ ...li, marginTop: 12, marginBottom: 0 }}>
            Навигация: <Link href="/">панель</Link> — список дат; логотип на любой странице ведёт туда же.
          </p>
        </Acc>
          </div>
        </div>
      </details>
    </div>
  );
}
