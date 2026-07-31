"use client";

import type { CSSProperties, ReactNode } from "react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AsyncProgress, StepProgressBar } from "./AsyncProgress";
import {
  WizardStepStatus,
  getStep1PhaseText,
  getStep3PhaseText,
  getStep4ImagesPhaseText,
  getStep4PhaseText,
  getStep4TextsPhaseText,
  type Step3ProgressMode,
} from "./WizardStepStatus";
import { DigestHintsAccordion } from "./DigestHintsAccordion";
import { isProxyapiBudgetError, ProxyapiBudgetAlert } from "./ProxyapiBudgetAlert";
import { Step1LivePanel, type Step1LiveProgress } from "./Step1LivePanel";
import { SourceTiersModal } from "./SourceTiersModal";
import { api, assetUrl } from "../lib/api";

const PLATFORM_ORDER = ["telegram", "max", "vk", "dzen"] as const;

const PLATFORM_LABELS: Record<string, string> = {
  telegram: "Телеграм",
  max: "MAX",
  vk: "ВКонтакте",
  dzen: "Дзен",
};

function hostFromUrl(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function isRussianHost(host: string): boolean {
  const h = String(host || "").toLowerCase();
  if (!h) return false;
  if (h.endsWith(".ru") || h.endsWith(".su")) return true;
  const markers = ["tass.ru", "interfax.ru", "vedomosti.ru", "kommersant.ru", "rbc.ru", "cnews.ru", "vc.ru", "ria.ru"];
  return markers.some((m) => h.includes(m));
}

function isPressLikeCandidate(c: { url?: string; source?: string; title?: string; description?: string }): boolean {
  const url = String(c.url || "").toLowerCase();
  const title = String(c.title || "").toLowerCase();
  const desc = String(c.description || "").toLowerCase();
  const blob = `${title} ${desc}`;
  if (["/product", "/tools", "/features", "/pricing", "/demo", "/trial", "/chatbot", "/assistant"].some((m) => url.includes(m)))
    return false;
  if (/\b(попробуйте|новая функци|функционал|free trial|new feature|ai assistant|chatbot)\b/i.test(blob)) return false;
  if (["businesswire.com", "prnewswire.com", "globenewswire.com"].some((m) => url.includes(m))) return true;
  if (["/press", "/press-release", "/newsroom", "/media-center"].some((m) => url.includes(m))) return true;
  if (!["press release", "пресс-релиз", "официально объявил", "партнёрств", "инвестици", "регулирован", "прорыв", "внедрени"].some(
    (k) => blob.includes(k),
  ))
    return false;
  return true;
}

function truncateText(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
}

/** Дата публикации новости для карточек (шаг 1 / 3). Всегда возвращает строку для отображения. */
function formatNewsPublishedAt(iso: string | undefined): string {
  const s = String(iso || "").trim();
  if (!s || s === "UNDEFINED" || s.startsWith("1970-")) return "Дата не определена";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return "Дата не определена";
  return d.toLocaleString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Дата выпуска для заголовка (без «#id»). */
function formatDigestDateLabel(iso: string | undefined): string {
  if (!iso) return "…";
  const d = String(iso).split("T")[0];
  const parts = d.split("-").map((x) => parseInt(x, 10));
  if (parts.length !== 3 || parts.some((n) => Number.isNaN(n))) return iso;
  const [y, m, day] = parts;
  const dt = new Date(y, m - 1, day);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

/** Заглушки пайплайна без реального веб-поиска (fallback в backend). */
type PoolCollectionStatsPayload = {
  pool?: {
    total: number;
    press_count: number;
    press_share: number;
    ru_count: number;
    ru_share: number;
    max_per_source: number;
    foreign_agent_count: number;
    forbidden_count: number;
  };
  last_run?: {
    run_number: number;
    duration_human?: string;
    duration_sec?: number | null;
    cost_rub?: number;
    news_count?: number;
    started_at?: string | null;
    completed_at?: string | null;
  } | null;
  step1_total_rub?: number;
  history?: Array<{
    run_number: number;
    duration_human?: string;
    cost_rub?: number;
    news_count?: number;
    started_at?: string | null;
    completed_at?: string | null;
  }>;
  step1_usage?: Step1UsageBreakdown | null;
};

type Step1UsageTool = {
  id: string;
  label: string;
  color: string;
  time_sec: number;
  time_human: string;
  cost_rub: number;
  time_share: number;
  cost_share: number;
  calls?: number;
  urls?: number;
  detail?: string | null;
};

type Step1UsageBreakdown = {
  total_time_sec: number;
  total_time_human: string;
  total_cost_rub: number;
  cost_source?: string;
  cost_source_note?: string;
  tools: Step1UsageTool[];
  funnel: {
    raw_urls?: number;
    prefilter_rejected?: number;
    sent_to_http?: number;
    verified_total?: number;
    conversion_e2e_pct?: number | null;
    conversion_http_pct?: number | null;
  };
  summary: {
    iterations?: number;
    stop_reason?: string | null;
    verified_total?: number;
    batch_size?: number;
    collection_target?: number;
  };
};

function formatStep1StopReason(reason: string | undefined): string {
  const r = String(reason || "").trim();
  if (!r) return "—";
  const map: Record<string, string> = {
    target_reached: "цель набрана",
    target_min_met: "минимум 20 набран, ранний стоп",
    soft_timeout_target_met: "soft-лимит, минимум набран",
    soft_timeout_after_collect: "soft-лимит после батча",
    hard_timeout_after_collect: "hard-лимит после батча",
    soft_timeout_final_attempt: "soft-лимит, финальная попытка",
    hard_timeout: "hard-лимит времени",
    budget_limit: "лимит бюджета",
    no_progress_target_met: "минимум набран, прогресс остановился",
    no_progress: "нет нового прогресса",
    user_cancelled: "остановлено пользователем",
    proxyapi_budget_exceeded: "исчерпан баланс ProxyAPI",
    web_search_api_cap: "лимит вызовов web_search (~бюджет шага 1)",
  };
  return map[r] || r;
}

function formatRunWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StackedShareBar({
  segments,
  ariaLabel,
}: {
  segments: Array<{ key: string; share: number; color: string; label: string }>;
  ariaLabel: string;
}) {
  const visible = segments.filter((s) => s.share > 0.001);
  if (!visible.length) return null;
  return (
    <div className="step1-usage-bar" role="img" aria-label={ariaLabel}>
      {visible.map((s) => (
        <div
          key={s.key}
          className="step1-usage-bar-seg"
          style={{ width: `${Math.max(2, s.share * 100)}%`, background: s.color }}
          title={`${s.label}: ${(s.share * 100).toFixed(0)}%`}
        />
      ))}
    </div>
  );
}

function Step1UsageStatsPanel({
  usage,
  stopReasonLabel,
  releaseCostRub,
  releaseCostFinalized,
  topRejectReasons,
  rejectSamples,
}: {
  usage: Step1UsageBreakdown | null | undefined;
  stopReasonLabel: string;
  releaseCostRub?: number | null;
  releaseCostFinalized?: boolean;
  topRejectReasons: Array<[string, number]>;
  rejectSamples: Array<{ code: string; sample: Record<string, unknown> }>;
}) {
  if (!usage) return null;
  const { funnel, summary, tools } = usage;
  const timeSegments = tools.map((t) => ({
    key: t.id,
    share: t.time_share,
    color: t.color,
    label: t.label,
  }));
  const costSegments = tools.map((t) => ({
    key: t.id,
    share: t.cost_share,
    color: t.color,
    label: t.label,
  }));

  return (
    <div className="step1-usage-panel">
      <div className="step1-usage-header">
        <strong>Ресурсы шага 1</strong>
        <span className="step1-usage-header-meta">
          итераций {summary.iterations ?? 0} · {stopReasonLabel} · в пуле {summary.verified_total ?? 0}
        </span>
      </div>

      <div className="step1-usage-totals">
        <div className="step1-usage-total-card">
          <span className="step1-usage-total-label">Время</span>
          <span className="step1-usage-total-value">{usage.total_time_human || "—"}</span>
        </div>
        <div className="step1-usage-total-card">
          <span className="step1-usage-total-label">ProxyAPI (шаг 1)</span>
          <span className="step1-usage-total-value">
            {Number(usage.total_cost_rub ?? 0).toFixed(2)} ₽
          </span>
          {usage.cost_source_note ? (
            <span className="step1-usage-total-sub">{usage.cost_source_note}</span>
          ) : null}
          {releaseCostRub != null && Number(releaseCostRub) > Number(usage.total_cost_rub ?? 0) + 0.01 ? (
            <span className="step1-usage-total-sub">
              по выпуску (все шаги): {Number(releaseCostRub).toFixed(2)} ₽
              {releaseCostFinalized ? " · зафикс." : ""}
            </span>
          ) : null}
        </div>
        <div className="step1-usage-total-card">
          <span className="step1-usage-total-label">Цель воронки</span>
          <span className="step1-usage-total-value">{summary.collection_target ?? 15}</span>
          <span className="step1-usage-total-sub">батч {summary.batch_size ?? 20}</span>
        </div>
      </div>

      {Number(usage.total_cost_rub ?? 0) > 10 && (summary.verified_total ?? 0) < 1 ? (
        <p className="wizard-hint-warn" style={{ marginTop: 0, marginBottom: 12, fontSize: "0.88rem" }}>
          За шаг 1 списано ≈{Number(usage.total_cost_rub).toFixed(0)} ₽, но в пул не попала ни одна ссылка — в основном
          это вызовы веб-поиска ProxyAPI (~1 ₽ за responses, ~2,69 ₽ за preview), пока воронка не находит подходящие
          статьи.
        </p>
      ) : null}

      <div className="step1-usage-bars">
        <div>
          <div className="step1-usage-bar-title">Время по инструментам</div>
          <StackedShareBar segments={timeSegments} ariaLabel="Доля времени по инструментам" />
        </div>
        {Number(usage.total_cost_rub ?? 0) > 0 ? (
          <div>
            <div className="step1-usage-bar-title">Стоимость по инструментам</div>
            <StackedShareBar segments={costSegments} ariaLabel="Доля стоимости по инструментам" />
          </div>
        ) : null}
      </div>

      <div className="step1-usage-tools">
        {tools.map((tool) => (
          <div key={tool.id} className="step1-usage-tool-card">
            <div className="step1-usage-tool-head">
              <span className="step1-usage-tool-dot" style={{ background: tool.color }} />
              <span className="step1-usage-tool-label">{tool.label}</span>
            </div>
            <div className="step1-usage-tool-metrics">
              <span>{tool.time_human || `${tool.time_sec} с`}</span>
              <span>{Number(tool.cost_rub ?? 0) > 0 ? `${Number(tool.cost_rub).toFixed(2)} ₽` : "0 ₽"}</span>
            </div>
            <div className="step1-usage-tool-bar-track">
              <div
                className="step1-usage-tool-bar-fill"
                style={{ width: `${Math.max(4, tool.time_share * 100)}%`, background: tool.color }}
              />
            </div>
            {tool.detail ? <div className="step1-usage-tool-detail">{tool.detail}</div> : null}
          </div>
        ))}
      </div>

      {(funnel.raw_urls ?? 0) > 0 ? (
        <div className="step1-usage-funnel">
          <div className="step1-usage-funnel-title">Воронка URL</div>
          <div className="step1-usage-funnel-flow">
            <div className="step1-usage-funnel-step">
              <span className="step1-usage-funnel-num">{funnel.raw_urls}</span>
              <span className="step1-usage-funnel-cap">сырые</span>
            </div>
            <span className="step1-usage-funnel-arrow">→</span>
            <div className="step1-usage-funnel-step muted">
              <span className="step1-usage-funnel-num">−{funnel.prefilter_rejected ?? 0}</span>
              <span className="step1-usage-funnel-cap">до HTTP</span>
            </div>
            <span className="step1-usage-funnel-arrow">→</span>
            <div className="step1-usage-funnel-step">
              <span className="step1-usage-funnel-num">{funnel.sent_to_http ?? 0}</span>
              <span className="step1-usage-funnel-cap">на HTTP</span>
            </div>
            <span className="step1-usage-funnel-arrow">→</span>
            <div className="step1-usage-funnel-step accent">
              <span className="step1-usage-funnel-num">{funnel.verified_total ?? 0}</span>
              <span className="step1-usage-funnel-cap">в пуле</span>
            </div>
          </div>
          {funnel.conversion_e2e_pct != null ? (
            <div className="step1-usage-funnel-conv">
              конверсия в пул <strong>{Number(funnel.conversion_e2e_pct).toFixed(1)}%</strong>
              {funnel.conversion_http_pct != null ? (
                <> · HTTP→пул {Number(funnel.conversion_http_pct).toFixed(1)}%</>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {topRejectReasons.length > 0 ? (
        <div className="step1-usage-rejects">
          <span className="step1-usage-rejects-label">Топ отбраковки:</span>
          {topRejectReasons.map(([code, count]) => (
            <span key={code} className="news-chip warn">
              {REJECT_REASON_LABELS[code] ?? code} · {count}
            </span>
          ))}
        </div>
      ) : null}

      {rejectSamples.length > 0 ? (
        <details className="step1-usage-samples">
          <summary>Примеры отсева ({rejectSamples.length})</summary>
          <ul>
            {rejectSamples.map(({ code, sample }, idx) => (
              <li key={`${code}-${idx}`}>
                <strong>{REJECT_REASON_LABELS[code] ?? code}</strong>
                {": "}
                {String(sample.host || "").trim() || hostFromUrl(String(sample.url || "")) || "без домена"}
                {sample.published_at ? ` · ${String(sample.published_at).slice(0, 10)}` : ""}
                {sample.title ? ` · ${truncateText(String(sample.title), 90)}` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function PoolCollectionStatsPanel({
  stats,
  showHistory = false,
}: {
  stats: PoolCollectionStatsPayload | null | undefined;
  showHistory?: boolean;
}) {
  const pool = stats?.pool;
  const last = stats?.last_run;
  if (!pool?.total && !last) return null;
  const history = (stats?.history || []).filter((h) => h.run_number !== last?.run_number);
  return (
    <div
      className="wizard-hint-why-body"
      style={{
        marginTop: 12,
        marginBottom: 12,
        padding: "12px 14px",
        borderRadius: 8,
        border: "1px solid #334155",
        background: "rgba(30, 41, 59, 0.55)",
        lineHeight: 1.5,
      }}
    >
      <div style={{ marginBottom: 8 }}>
        <strong>Итоги сбора пула</strong>
        {last ? (
          <span style={{ color: "#94a3b8", fontWeight: 400 }}>
            {" "}
            · запуск №{last.run_number}
            {last.completed_at ? ` · ${formatRunWhen(last.completed_at)}` : ""}
          </span>
        ) : null}
      </div>
      {last ? (
        <p style={{ margin: "0 0 10px", fontSize: "0.95rem" }}>
          <strong>Время подготовки:</strong> {last.duration_human || "—"}
          {" · "}
          <strong>Стоимость запуска:</strong> {Number(last.cost_rub ?? 0).toFixed(2)} ₽
          {typeof last.news_count === "number" ? (
            <>
              {" · "}
              <strong>Найдено в логе шага 1:</strong> {last.news_count}
            </>
          ) : null}
          {stats?.step1_total_rub != null && stats.step1_total_rub > 0 ? (
            <>
              {" · "}
              <span style={{ color: "#94a3b8" }}>
                всего по шагу 1 (все запуски): {Number(stats.step1_total_rub).toFixed(2)} ₽
              </span>
            </>
          ) : null}
        </p>
      ) : null}
      {pool && pool.total > 0 ? (
        <p style={{ margin: 0, fontSize: "0.95rem" }}>
          <strong>Состав пула:</strong> {pool.total} новостей, пресс-релизы/официальные — {pool.press_count} (
          {(pool.press_share * 100).toFixed(0)}%), российские источники — {pool.ru_count} (
          {(pool.ru_share * 100).toFixed(0)}%), макс. на источник — {pool.max_per_source}, иноагенты —{" "}
          {pool.foreign_agent_count}, запрещённые — {pool.forbidden_count}.
        </p>
      ) : null}
      {showHistory && history.length > 0 ? (
        <details style={{ marginTop: 10, fontSize: "0.9rem" }}>
          <summary style={{ cursor: "pointer", color: "#7dd3fc" }}>Предыдущие запуски сбора ({history.length})</summary>
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            {history.map((h) => (
              <li key={h.run_number} style={{ marginBottom: 4 }}>
                №{h.run_number}: {h.duration_human || "—"}, {Number(h.cost_rub ?? 0).toFixed(2)} ₽
                {h.news_count != null ? `, в логе ${h.news_count}` : ""}
                {h.completed_at ? ` · ${formatRunWhen(h.completed_at)}` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function looksLikeDemoCandidate(c: { title?: string; source?: string; url?: string }): boolean {
  const t = String(c.title || "");
  if (t.startsWith("AI Candidate")) return true;
  if (String(c.source || "") === "Example Tech") return true;
  if (String(c.url || "").includes("example.com/ai-news")) return true;
  return false;
}

function headlineEditorialOk(c: { headline_editorial_ok?: boolean; page_verified?: boolean; link_status?: boolean }): boolean {
  if (typeof c.headline_editorial_ok === "boolean") return c.headline_editorial_ok;
  return Boolean(c.page_verified && c.link_status);
}

function linkOkForStep1(c: { link_status?: boolean }): boolean {
  return Boolean(c.link_status);
}

function candidateSelectableForStep2(c: {
  headline_editorial_ok?: boolean;
  page_verified?: boolean;
  link_status?: boolean;
  reliability_status?: string;
  is_aggregator?: boolean;
  is_duplicate?: boolean;
}): boolean {
  if (!headlineEditorialOk(c) || !linkOkForStep1(c)) return false;
  if (c.is_aggregator) return false;
  if (c.is_duplicate) return false;
  if (String(c.reliability_status || "") === "❗ без подтверждения") return false;
  return true;
}

/** Те же правила, что `DigestService._is_manual_required_candidate` (обязательные URL с шага 1). */
function isManualRequiredCandidate(c: { verification_comment?: string; description?: string }): boolean {
  const comment = String(c.verification_comment || "");
  const desc = String(c.description || "");
  if (comment.includes("TELEGRAM_SEED:") || desc.includes("Telegram-монитор")) return false;
  return (
    comment.includes("MANUAL_REQUIRED:") ||
    desc.includes("поле URL на шаге 1") ||
    desc.includes("поле URL на шаге 2")
  );
}

type Step2PickCandidate = {
  id: number;
  url?: string;
  source?: string;
  total_score?: number;
  verification_comment?: string;
  description?: string;
  headline_editorial_ok?: boolean;
  page_verified?: boolean;
  link_status?: boolean;
  reliability_status?: string;
  is_aggregator?: boolean;
  is_duplicate?: boolean;
};

const STEP2_MAX_PER_DOMAIN = 2;
const STEP1_POOL_MAX_PER_DOMAIN = 3;

function step1RebuildProgressLabel(selectedCount: number): string {
  if (selectedCount > 0) {
    return `Шаг 1: пересборка пула (сохранить ${selectedCount}, добрать остальные; подождите)…`;
  }
  return "Шаг 1: дополнение пула (сохранить текущие, искать новые; подождите)…";
}

function step1RebuildConfirmMessage(selectedCount: number, pastStep2: boolean): string {
  if (selectedCount > 0) {
    const base =
      `Пересобрать пул, оставив ${selectedCount} отмеченных новостей?\n\n` +
      "Остальные места в списке кандидатов будут заполнены заново (те же seed-ленты, web_search, проверка).\n" +
      "Снятые галочки и их URL в новый пул не вернутся.\n";
    if (pastStep2) {
      return (
        base +
        "Сотрутся подтверждённая пятёрка, порядок, аналитика и финал — их нужно пройти заново.\n\n" +
        "Тип дайджеста (шаг 0) сохранится. Продолжить?"
      );
    }
    return base + "Отмеченные галочки сохранятся. Тип дайджеста (шаг 0) сохранится. Продолжить?";
  }
  const base =
    "Дополнить пул новыми кандидатами?\n\n" +
    "Все текущие карточки в пуле сохранятся; система запустит тот же поиск, что при первом сборе, и добавит новые ссылки.\n" +
    "Чтобы заменить пул, оставив только часть карточек — отметьте их галочками и нажмите «Пересобрать пул (оставить N)».\n";
  if (pastStep2) {
    return (
      base +
      "Подтверждённая пятёрка, порядок, аналитика и финал сбросятся — их нужно пройти заново.\n\n" +
      "Тип дайджеста (шаг 0) сохранится. Продолжить?"
    );
  }
  return base + "Отмеченные галочки сохранятся. Тип дайджеста (шаг 0) сохранится. Продолжить?";
}

function step1RebuildButtonLabel(selectedCount: number): string {
  if (selectedCount > 0) {
    return `Пересобрать пул (оставить ${selectedCount})`;
  }
  return "Дополнить пул кандидатов";
}

function step1RebuildButtonTitle(selectedCount: number, pastStep2: boolean): string {
  if (selectedCount > 0) {
    return `Закрепить ${selectedCount} отмеченных; остальные слоты пула (до 15) — новый поиск и проверка`;
  }
  if (pastStep2) {
    return "Сохранить весь текущий пул и добавить новые (тот же поиск); сброс шагов 2–4";
  }
  return "Сохранить весь текущий пул и добавить новые (seed-ленты, web_search, проверка); галочки сохранятся";
}

function publisherHostForCandidate(c: { url?: string; source?: string }): string {
  const fromUrl = c.url ? hostFromUrl(String(c.url)) : "";
  return (fromUrl || String(c.source || "")).toLowerCase().replace(/^www\./, "");
}

function countSelectedOnHost(
  host: string,
  selected: number[],
  candidates: { id: number; url?: string; source?: string }[],
): number {
  if (!host) return 0;
  return selected.filter((id) => {
    const row = candidates.find((c) => c.id === id);
    return row ? publisherHostForCandidate(row) === host : false;
  }).length;
}

function canAddCandidateToSelection(
  candidate: Step2PickCandidate,
  selected: number[],
  candidates: Step2PickCandidate[],
): boolean {
  if (!candidateSelectableForStep2(candidate)) return false;
  if (selected.includes(candidate.id)) return true;
  if (selected.length >= 5) return false;
  const host = publisherHostForCandidate(candidate);
  return countSelectedOnHost(host, selected, candidates) < STEP2_MAX_PER_DOMAIN;
}

/** Зеркало `select_news(..., top5=True)` на бэкенде — для мгновенной отметки чекбоксов. */
function pickTop5ByRating(candidates: Step2PickCandidate[]): number[] {
  const strictAllowed = candidates.filter((c) => candidateSelectableForStep2(c));
  const mandatory = strictAllowed.filter((c) => isManualRequiredCandidate(c));
  const chosen: Step2PickCandidate[] = [];
  const hostCounts = new Map<string, number>();

  const tryAdd = (c: Step2PickCandidate) => {
    if (chosen.length >= 5) return;
    if (chosen.some((x) => x.id === c.id)) return;
    const host = publisherHostForCandidate(c);
    const onHost = hostCounts.get(host) || 0;
    if (onHost >= STEP2_MAX_PER_DOMAIN) return;
    chosen.push(c);
    hostCounts.set(host, onHost + 1);
  };

  for (const c of mandatory) tryAdd(c);
  for (const c of [...strictAllowed].sort((a, b) => (b.total_score ?? 0) - (a.total_score ?? 0))) {
    tryAdd(c);
  }
  return chosen.map((c) => c.id);
}

function pickManualRequiredSelectableIds(candidates: Step2PickCandidate[]): number[] {
  return candidates
    .filter((c) => candidateSelectableForStep2(c) && isManualRequiredCandidate(c))
    .map((c) => c.id);
}

/** Тот же отпечаток, что `article_page_fingerprint` в backend/step1_recent_top5.py */
function articlePageFingerprint(url: string): string {
  try {
    const p = new URL((url || "").trim());
    const host = (p.hostname || "").toLowerCase().replace(/^www\./, "");
    const path = (p.pathname || "").replace(/\/$/, "") || "/";
    return `${host}${path.toLowerCase()}`;
  } catch {
    return "";
  }
}

function resolveKeptCandidateIds(
  candidates: { id: number; url?: string; headline_editorial_ok?: boolean; page_verified?: boolean; link_status?: boolean; reliability_status?: string; is_aggregator?: boolean; is_duplicate?: boolean }[],
  keptUrls: string[],
): number[] {
  const fps = keptUrls.map(articlePageFingerprint).filter(Boolean);
  if (!fps.length) return [];
  const byFp = new Map<string, number>();
  for (const c of candidates) {
    const fp = articlePageFingerprint(String(c.url || ""));
    if (fp && !byFp.has(fp)) byFp.set(fp, c.id);
  }
  const out: number[] = [];
  for (const fp of fps) {
    const id = byFp.get(fp);
    if (id == null) continue;
    const row = candidates.find((c) => c.id === id);
    if (row && candidateSelectableForStep2(row)) out.push(id);
  }
  return out;
}

const CATEGORY_LABELS: Record<string, string> = {
  manual: "Ручная ссылка (поле URL)",
  telegram_seed: "Из Telegram",
  search: "Web-поиск",
  llm_crew: "LLM-добор",
  technology: "LLM-добор",
  analytics: "LLM-добор",
};

const EDITORIAL_ANGLE_CHIP_LABELS: Record<string, string> = {
  serious: "Серьёз",
  curious: "Курьёз",
};

function editorialAngleChipClass(angle: string | undefined | null): string {
  const key = String(angle || "serious").toLowerCase();
  return key === "curious" ? "news-chip angle-curious" : "news-chip angle-serious";
}

function editorialAngleLabel(angle: string | undefined | null): string {
  const key = String(angle || "serious").toLowerCase();
  return EDITORIAL_ANGLE_CHIP_LABELS[key] || EDITORIAL_ANGLE_CHIP_LABELS.serious;
}

const MATERIAL_FORM_CHIP_LABELS: Record<string, string> = {
  article: "Форма: статья",
  training: "Форма: обучение",
  service: "Форма: услуга/реклама",
  press: "Форма: пресс-релиз",
  research: "Форма: исследование",
  finance: "Форма: финансы",
  military: "Форма: военная сфера",
  breakthrough: "Форма: прорыв ИИ",
  legislation: "Форма: законодательство",
};

const MATERIAL_FORM_TITLE_SUFFIX_RE =
  /\s*\((?:статья|обучение|услуга(?:\/реклама)?|пресс-релиз|исследование|финансы|военная сфера|прорыв ИИ|законодательство)\)\s*$/i;

function displayCandidateTitle(title: string | undefined | null): string {
  return String(title || "")
    .replace(MATERIAL_FORM_TITLE_SUFFIX_RE, "")
    .trim();
}

function resolveOriginCategory(c: {
  category?: string;
  verification_comment?: string;
  description?: string;
}): string {
  const comment = String(c.verification_comment || "");
  const desc = String(c.description || "");
  const cat = String(c.category || "").trim().toLowerCase();
  if (comment.includes("TELEGRAM_SEED:") || desc.includes("Telegram-монитор") || cat === "telegram_seed") {
    return "telegram_seed";
  }
  if (comment.includes("Источник из веб-поиска") || cat === "search") {
    return "search";
  }
  if (cat === "llm_crew" || cat === "technology" || cat === "analytics") {
    return "llm_crew";
  }
  if (desc.includes("поле URL на шаге 1")) {
    return "manual";
  }
  if (comment.includes("MANUAL_REQUIRED:")) {
    return "telegram_seed";
  }
  if (cat === "manual") {
    return "telegram_seed";
  }
  return cat || "search";
}

function categoryLabel(c: {
  category?: string;
  verification_comment?: string;
  description?: string;
}): string {
  const key = resolveOriginCategory(c);
  if (!key) return "";
  return CATEGORY_LABELS[key] || key;
}

function isTier5ForbiddenMedia(c: { tier?: string; is_aggregator?: boolean }): boolean {
  const tier = String(c.tier || "");
  if (tier === "Curious-T5") return true;
  return tier === "Tier-5" && !Boolean(c.is_aggregator);
}

function rejectReasonCodes(text: string): string[] {
  return String(text || "")
    .split(/\s+/)
    .filter((t) => t.startsWith("REJECT_REASON:"))
    .map((t) => t.replace("REJECT_REASON:", "").trim())
    .filter(Boolean);
}

const REJECT_REASON_LABELS: Record<string, string> = {
  aggregator_source:
    "ссылка ведёт на ленту или «сборщик» новостей (Google News, Reddit и т.п.), а не на саму статью — нужна прямая ссылка на текст материала",
  http_unreachable:
    "страница не открылась: сеть, блокировка, ошибка сайта, страница не найдена или доступ закрыт, долгое ожидание или битый адрес",
  no_article_markers:
    "похоже не отдельная статья, а раздел сайта, главная или сервисная страница без признаков материала «как в газете»",
  news_listing_page:
    "это лента или рубрика со списком новостей, а не отдельная статья — в дайджест попадут ссылки на материалы из неё",
  non_article_page: "на странице нет нормального заголовка материала (как у обычной статьи)",
  off_topic_not_ai: "по тексту страницы тема не про искусственный интеллект и нейросети — это другая тематика",
  off_topic_not_curious:
    "только курьёзный выпуск: сухой официоз (релиз, регуляторика, инвестиции) отклонён; нейтральные AI-материалы остаются с пониженным приоритетом",
  excluded_from_final_pool:
    "страница прошла проверку, но не вошла в финальный список кандидатов (лимит rebalance, квоты источников)",
  headline_low_quality:
    "заголовок выглядит как служебный номер или код (например, номер дела), а не как название новости",
  invalid_url: "адрес ссылки указан неверно или не начинается с http/https",
  placeholder_candidate: "это учебная заглушка в данных, а не реальная новость из интернета",
  manual_unverified: "ручную ссылку не удалось подтвердить по содержимому страницы",
  url_mutated_between_agents:
    "ссылка изменилась в процессе обработки и больше не совпадает с исходной проверенной страницей",
  llm_hallucinated_url:
    "ссылка не открывается — похоже, адрес придуман моделью, а не взят из реальной публикации",
  published_before_window:
    "известная дата публикации раньше окна шага 0 (по дате в URL или на странице)",
  published_date_undefined:
    "не удалось определить дату публикации (ни в URL, ни в разметке страницы); фильтр по умолчанию выключен",
  url_redirect_mismatch:
    "ссылка ведёт на другую страницу (редирект на главную или другой материал), не на заявленную новость",
  forbidden_media_source:
    "источник входит в Tier-5 (запрещённые законом РФ СМИ) и исключён из кандидатного пула",
  non_policy_source:
    "домен не входит в tier-1…tier-4 из политики источников — при строгом поиске такие URL не собираются",
  unknown_reject: "точная причина в данных не указана",
  duplicate_url_skip: "ссылка уже проверялась в этом запуске или исключена как дубликат",
  recent_top5_repeat:
    "та же страница статьи уже была в топ-5 одного из 7 предыдущих зафиксированных выпусков (после «Зафиксировать»); при включённом фильтре не попадает в пул шага 1",
  product_tool_page: "страница продукта/инструмента, а не новостная публикация",
  product_tool_promo: "промо инструмента или функции, а не новостное событие",
};

const MANUAL_SCORE_REASON_OPTIONS: { value: string; label: string }[] = [
  { value: "published_out_of_range", label: "дата не в диапазоне" },
  { value: "http_unreachable", label: "ссылка не открылась" },
  { value: "url_redirect_mismatch", label: "ссылка открылась на другую страницу" },
  { value: "off_topic_not_ai", label: "не про ИИ" },
  { value: "other", label: "другое" },
];

/** Свёрнутый блок «зачем / как устроено» — мелкий summary и тело. */
function WizardWhy({ summary = "Подробнее: зачем так и как устроено", children }: { summary?: string; children: ReactNode }) {
  return (
    <details className="wizard-hint-why">
      <summary>{summary}</summary>
      <div className="wizard-hint-why-body">{children}</div>
    </details>
  );
}

async function copyPlainTextToClipboard(text: string): Promise<void> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    /* Clipboard может быть недоступен — fallback ниже */
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "true");
  ta.style.position = "fixed";
  ta.style.left = "0";
  ta.style.top = "0";
  ta.style.width = "2px";
  ta.style.height = "2px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  if (!ok) {
    throw new Error("copy failed");
  }
}

function htmlToPlainText(html: string): string {
  if (typeof document === "undefined") return html;
  const doc = new DOMParser().parseFromString(html, "text/html");
  return (doc.body.textContent ?? html).replace(/\u00a0/g, " ").trim();
}

async function copyHtmlToClipboard(html: string, plainFallback: string): Promise<void> {
  const wrapped = `<!DOCTYPE html><html><body><!--StartFragment-->${html}<!--EndFragment--></body></html>`;
  try {
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard?.write &&
      typeof ClipboardItem !== "undefined"
    ) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([wrapped], { type: "text/html" }),
          "text/plain": new Blob([plainFallback], { type: "text/plain" }),
        }),
      ]);
      return;
    }
  } catch {
    /* fallback на plain text */
  }
  await copyPlainTextToClipboard(plainFallback);
}

type Props = { digestId: number };

type RunningStepKey = "init" | "0" | "1" | "2pick" | "2order" | "3" | "4" | "4img" | "4txt";
type ManualScoreReason = "published_out_of_range" | "http_unreachable" | "url_redirect_mismatch" | "off_topic_not_ai" | "other";
type DiscoveredDraft = { score: "" | "1" | "2" | "3"; reason: "" | ManualScoreReason; reasonOther: string };
type Step1FilterCatalogItem = {
  id: string;
  label_ru: string;
  description_ru: string;
  stage: "pre_http" | "verify" | "pool";
  default_enabled: boolean;
  locked: boolean;
};
type Step1FilterState = { id: string; enabled: boolean; order: number };

/** Соответствие текста прогресса карточке шага (для полосы у шага). */
function parseRunningStepFromLabel(label: string): RunningStepKey | null {
  const t = label.trim();
  if (!t) return null;
  if (t.includes("Загрузка выпуска")) return "init";
  if (t.includes("Сохранение типа") || t.includes("типа дайджеста")) return "0";
  if (t.includes("Шаг 1:")) return "1";
  if (t.includes("Шаг 2–3") || t.includes("Шаг 2-3")) return "3";
  if (t.includes("Шаг 2: применение порядка") || t.includes("Шаг 2: оптимальный порядок")) return "2order";
  if (t.includes("Шаг 2:")) return "2pick";
  if (t.includes("Шаг 3:")) return "3";
  if (t.includes("Шаг 4: обложки")) return "4img";
  if (t.includes("Шаг 4: тексты")) return "4txt";
  if (t.includes("Шаг 4:")) return "4";
  return null;
}

export function DigestWizard({ digestId }: Props) {
  const [digest, setDigest] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [runningStepKey, setRunningStepKey] = useState<RunningStepKey | null>(null);
  const [progressLabel, setProgressLabel] = useState("");
  const [error, setError] = useState("");
  const [manualUrls, setManualUrls] = useState("");
  const [step2ManualUrls, setStep2ManualUrls] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [orderEditMode, setOrderEditMode] = useState(false);
  const [hookVariant, setHookVariant] = useState<"A" | "B" | "V" | "">("");
  const [step4Platforms, setStep4Platforms] = useState<Record<string, boolean>>({
    telegram: true,
    max: true,
    vk: true,
    dzen: true,
  });
  const [selectedImageVariant, setSelectedImageVariant] = useState<number | null>(null);
  const [draggedId, setDraggedId] = useState<number | null>(null);
  const [copyStatus, setCopyStatus] = useState<Record<string, "idle" | "ok" | "err">>({});
  const copyTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const step1CardRef = useRef<HTMLDivElement | null>(null);
  const step2CardRef = useRef<HTMLDivElement | null>(null);
  const step2OrderCardRef = useRef<HTMLDivElement | null>(null);
  const step3CardRef = useRef<HTMLDivElement | null>(null);
  const step4CardRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollToStep2Ref = useRef(false);
  const pendingPreselectKeptUrlsRef = useRef<string[]>([]);
  const pendingManualUrlsRef = useRef<string[]>([]);
  const lastSelectedUrlsRef = useRef<string[]>([]);
  const newsWindowHydratedForRef = useRef<number | null>(null);
  const step3ModeRef = useRef<Step3ProgressMode>("analytics");
  const step1AbortRef = useRef<AbortController | null>(null);
  const [step1Stopping, setStep1Stopping] = useState(false);
  const [step1Elapsed, setStep1Elapsed] = useState(0);
  const [step1Live, setStep1Live] = useState<Step1LiveProgress | null>(null);
  const [step3Elapsed, setStep3Elapsed] = useState(0);
  const [step4Elapsed, setStep4Elapsed] = useState(0);
  const [newsWindowDays, setNewsWindowDays] = useState(3);
  const [newsWindowDayKind, setNewsWindowDayKind] = useState<"calendar" | "working">("working");
  const [digestTopic, setDigestTopic] = useState<"ai" | "style">("ai");
  const [showAllFoundNews, setShowAllFoundNews] = useState(false);
  const [showStep1Statistics, setShowStep1Statistics] = useState(false);
  const [step1StatsLoading, setStep1StatsLoading] = useState(false);
  const [step1StatsError, setStep1StatsError] = useState("");
  const [step1StatsData, setStep1StatsData] = useState<any | null>(null);
  const [step1StatsReasonFilter, setStep1StatsReasonFilter] = useState<string>("");
  const [step1AuditFilter, setStep1AuditFilter] = useState<"all" | "in_pool" | "rejected">("all");
  const [discoveredDrafts, setDiscoveredDrafts] = useState<Record<number, DiscoveredDraft>>({});
  const [discoveredSaveState, setDiscoveredSaveState] = useState<
    Record<number, { saving?: boolean; ok?: boolean; error?: string; exportPath?: string }>
  >({});
  const [ratingsExportPath, setRatingsExportPath] = useState("");
  const [ratingsDownloadBusy, setRatingsDownloadBusy] = useState(false);
  const [ratingsDownloadError, setRatingsDownloadError] = useState("");
  const [showStep1FilterSettings, setShowStep1FilterSettings] = useState(false);
  const [step1FilterCatalog, setStep1FilterCatalog] = useState<Step1FilterCatalogItem[]>([]);
  const [step1FilterStates, setStep1FilterStates] = useState<Step1FilterState[]>([]);
  const [step1FilterCounters, setStep1FilterCounters] = useState<Record<string, number>>({});
  const [step1JournalTotalsApi, setStep1JournalTotalsApi] = useState<{ total: number; in_pool: number; rejected: number } | null>(
    null,
  );
  const [step1FiltersAppliedLastRun, setStep1FiltersAppliedLastRun] = useState<
    Array<{ id: string; enabled: boolean; order: number }>
  >([]);
  const [step1MinDiscoveredPages, setStep1MinDiscoveredPages] = useState(20);
  const [step1MinCollectionIterations, setStep1MinCollectionIterations] = useState(5);
  const [step1FilterLoading, setStep1FilterLoading] = useState(false);
  const [step1FilterSaving, setStep1FilterSaving] = useState(false);
  const [step1FilterError, setStep1FilterError] = useState("");
  const [draggedFilterId, setDraggedFilterId] = useState<string | null>(null);
  const [showAppConfigModal, setShowAppConfigModal] = useState(false);
  const [showSourceTiersModal, setShowSourceTiersModal] = useState(false);
  const [appConfigLoading, setAppConfigLoading] = useState(false);
  const [appConfigError, setAppConfigError] = useState("");
  const [appConfig, setAppConfig] = useState<{
    sections: Array<{
      id: string;
      title: string;
      file: string;
      items: Array<{
        label: string;
        value: string;
        source: string;
        hint?: string | null;
        why_chosen?: string;
        alternatives?: string;
      }>;
    }>;
    env_overrides: string[];
    note: string;
  } | null>(null);

  const sortedOutputs = useMemo(() => {
    const list = [...(digest?.outputs || [])];
    const orderIndex = (p: string) => {
      const i = (PLATFORM_ORDER as readonly string[]).indexOf(p);
      return i === -1 ? 99 : i;
    };
    return list.sort((a, b) => orderIndex(a.platform) - orderIndex(b.platform));
  }, [digest?.outputs]);

  const flashCopyFeedback = useCallback((platform: string, ok: boolean) => {
    const prev = copyTimersRef.current[platform];
    if (prev) clearTimeout(prev);
    setCopyStatus((s) => ({ ...s, [platform]: ok ? "ok" : "err" }));
    copyTimersRef.current[platform] = setTimeout(() => {
      setCopyStatus((s) => ({ ...s, [platform]: "idle" }));
      delete copyTimersRef.current[platform];
    }, 2800);
  }, []);

  const handleCopyPlatform = useCallback(
    async (platform: string, text: string) => {
      try {
        if ((platform === "max" || platform === "dzen") && /<a\s+href=/i.test(text)) {
          await copyHtmlToClipboard(text, htmlToPlainText(text));
        } else {
          await copyPlainTextToClipboard(text);
        }
        flashCopyFeedback(platform, true);
      } catch {
        flashCopyFeedback(platform, false);
      }
    },
    [flashCopyFeedback],
  );

  useEffect(() => {
    return () => {
      Object.values(copyTimersRef.current).forEach((t) => clearTimeout(t));
    };
  }, []);

  const loadDigest = useCallback(
    async (opts?: { skipProgress?: boolean; label?: string; preserveError?: boolean; preserveSelection?: boolean }) => {
      if (opts?.label) {
        setProgressLabel(opts.label);
        const rk = parseRunningStepFromLabel(opts.label);
        if (rk !== null) setRunningStepKey(rk);
      }
      if (!opts?.skipProgress) setLoading(true);
      try {
        if (!opts?.preserveError) setError("");
        const data = await api.getDigest(digestId);
        setDigest(data);
        if (data.selected?.length) {
          const sorted = [...data.selected].sort(
            (a: { output_position?: number }, b: { output_position?: number }) =>
              (a.output_position ?? 0) - (b.output_position ?? 0),
          );
          setSelected(sorted.map((s: { candidate_id: number }) => s.candidate_id));
          pendingPreselectKeptUrlsRef.current = [];
          pendingManualUrlsRef.current = [];
        } else if (!opts?.preserveSelection) {
          setSelected([]);
        }
        // Иначе выбор восстановит useEffect: kept/manual URL или ручные карточки пула.
      } catch (e) {
        setError((e as Error).message);
        throw e;
      } finally {
        if (!opts?.skipProgress) {
          setLoading(false);
          setProgressLabel("");
          setRunningStepKey(null);
        }
      }
    },
    [digestId],
  );

  useEffect(() => {
    void loadDigest({ label: "Загрузка выпуска…" }).catch(() => undefined);
  }, [digestId, loadDigest]);

  useEffect(() => {
    if (manualUrls.trim()) return;
    const list = digest?.candidates as
      | { url?: string; verification_comment?: string; description?: string }[]
      | undefined;
    if (!list?.length) return;
    const urls = [
      ...new Set(
        list
          .filter((c) => isManualRequiredCandidate(c))
          .map((c) => String(c.url || "").trim())
          .filter(Boolean),
      ),
    ];
    if (urls.length) setManualUrls(urls.join("\n"));
  }, [digest?.candidates, manualUrls]);

  useEffect(() => {
    newsWindowHydratedForRef.current = null;
  }, [digestId]);

  useEffect(() => {
    const d = digest?.digest;
    if (!d || newsWindowHydratedForRef.current === digestId) return;
    newsWindowHydratedForRef.current = digestId;
    if (d.news_window_days != null) setNewsWindowDays(Number(d.news_window_days) || 3);
    const kind = d.news_window_day_kind;
    if (kind === "working" || kind === "calendar") {
      setNewsWindowDayKind(kind);
    }
    const topic = d.digest_topic;
    if (topic === "ai" || topic === "style") {
      setDigestTopic(topic);
    }
  }, [digestId, digest?.digest]);

  useEffect(() => {
    const list = digest?.candidates as
      | {
          id: number;
          url?: string;
          page_verified?: boolean;
          link_status?: boolean;
          headline_editorial_ok?: boolean;
          reliability_status?: string;
          is_aggregator?: boolean;
          is_duplicate?: boolean;
        }[]
      | undefined;
    if (!list?.length) return;
    const validIds = new Set(list.map((c) => c.id));
    const st = digest?.digest?.status as string | undefined;
    const pastStep2 = st === "selected" || st === "analytics_ready" || st === "final_ready";

    const pendingUrls = pendingPreselectKeptUrlsRef.current;
    if (pendingUrls.length > 0) {
      pendingPreselectKeptUrlsRef.current = [];
      const remapped = resolveKeptCandidateIds(list, pendingUrls);
      if (remapped.length) {
        setSelected(remapped);
        return;
      }
    }

    if (pendingManualUrlsRef.current.length > 0) {
      const urls = pendingManualUrlsRef.current;
      pendingManualUrlsRef.current = [];
      const remapped = resolveKeptCandidateIds(list, urls);
      if (remapped.length) {
        setSelected(remapped);
        return;
      }
    }

    setSelected((prev) => {
      const stillValid = prev.filter((id) => validIds.has(id));
      if (stillValid.length !== prev.length && lastSelectedUrlsRef.current.length) {
        const remapped = resolveKeptCandidateIds(list, lastSelectedUrlsRef.current);
        if (remapped.length) return remapped;
      }
      if (stillValid.length > 0) {
        return stillValid.filter((id) => {
          if (pastStep2) return true;
          const row = list.find((x) => x.id === id);
          return row ? candidateSelectableForStep2(row) : false;
        });
      }
      if (!pastStep2) {
        const manualIds = pickManualRequiredSelectableIds(list as Step2PickCandidate[]);
        if (manualIds.length) return manualIds;
      }
      return [];
    });
  }, [digest?.candidates, digest?.digest?.status]);

  const candidatesSorted = useMemo(
    () => [...(digest?.candidates || [])].sort((a, b) => a.original_number - b.original_number),
    [digest],
  );

  const candidatesGroupedByDomain = useMemo(() => {
    const groups = new Map<string, typeof candidatesSorted>();
    for (const c of candidatesSorted) {
      const host = publisherHostForCandidate(c) || "—";
      const list = groups.get(host) ?? [];
      list.push(c);
      groups.set(host, list);
    }
    return [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b, "ru"))
      .map(([host, items]) => ({
        host,
        items: [...items].sort((a, b) => a.original_number - b.original_number),
      }));
  }, [candidatesSorted]);

  useEffect(() => {
    lastSelectedUrlsRef.current = selected
      .map((id) => candidatesSorted.find((c) => c.id === id)?.url)
      .filter((url): url is string => Boolean(url));
  }, [selected, candidatesSorted]);

  const hasCandidatePool = candidatesSorted.length > 0;
  const hasSelectableInPool = useMemo(
    () => candidatesSorted.some((c) => candidateSelectableForStep2(c)),
    [candidatesSorted],
  );

  const digestStatus = digest?.digest?.status as string | undefined;
  const isDraft = digestStatus === "draft";
  const canRunStep1 = digestStatus === "step_0" || digestStatus === "step_1_candidates";
  const pastStep2ForRebuild =
    digestStatus === "selected" || digestStatus === "analytics_ready" || digestStatus === "final_ready";
  const hasConfirmedSelection = (digest?.selected?.length ?? 0) > 0;
  const hasAnalyticsBlocks = (digest?.analytics?.length ?? 0) > 0;
  const canSelect =
    hasCandidatePool &&
    hasSelectableInPool &&
    digestStatus !== "draft" &&
    digestStatus !== "step_0";
  const canAddStep2ManualUrl =
    hasCandidatePool && digestStatus !== "draft" && digestStatus !== "step_0";
  const selectionWasSaved =
    hasConfirmedSelection ||
    hasAnalyticsBlocks ||
    digestStatus === "selected" ||
    digestStatus === "analytics_ready" ||
    digestStatus === "final_ready";
  const canOrder =
    (digest?.selected?.length ?? 0) === 5 &&
    (digestStatus === "selected" || orderEditMode);
  const analyticsDone = digestStatus === "analytics_ready" || digestStatus === "final_ready";
  const canChangeOrderFromLaterSteps =
    (digest?.selected?.length ?? 0) === 5 &&
    (digestStatus === "analytics_ready" || digestStatus === "final_ready" || analyticsDone);
  const canAnalytics = digestStatus === "selected" || digestStatus === "analytics_ready";
  const canStep4 = analyticsDone;
  const isFinal = digestStatus === "final_ready";
  const releaseCostFinalized = Boolean(digest?.release_cost_finalized);
  const hasStep4Images = (digest?.image_variants?.length ?? 0) > 0;
  const hasStep4Outputs = (digest?.outputs?.length ?? 0) > 0;
  const showStep4Results = isFinal || hasStep4Images || hasStep4Outputs;

  const handleFinalizeRelease = useCallback(async () => {
    if (!isFinal || releaseCostFinalized) return;
    const ok = window.confirm(
      "Зафиксировать выпуск?\n\n" +
        "Будет записана накопительная сумма ProxyAPI с начала работы над этим выпуском (шаг 0). " +
        "После фиксации сумма «По выпуску» не изменится при дальнейших запросах к API.\n\n" +
        "Топ-5 этого выпуска попадёт в список исключений для будущих сборов пула (фильтр «повтор из прошлых топ-5»).",
    );
    if (!ok) return;
    setError("");
    try {
      setLoading(true);
      setProgressLabel("Фиксация стоимости выпуска…");
      const res = await api.finalizeRelease(digestId);
      await loadDigest();
      if (!res.already_finalized) {
        setProgressLabel(`Зафиксировано: ${Number(res.release_cost_rub).toFixed(2)} ₽`);
      }
    } catch (e) {
      setError((e as Error).message || "Не удалось зафиксировать выпуск.");
    } finally {
      setLoading(false);
      setProgressLabel("");
    }
  }, [digestId, isFinal, loadDigest, releaseCostFinalized]);

  const selectedPlatformsList = useMemo(
    () => PLATFORM_ORDER.filter((p) => step4Platforms[p]),
    [step4Platforms],
  );

  useEffect(() => {
    if (digest?.step4_selected_image_variant != null) {
      setSelectedImageVariant(Number(digest.step4_selected_image_variant));
    }
  }, [digest?.step4_selected_image_variant]);

  const discoveredNewsSorted = useMemo(
    () =>
      [...(digest?.discovered_news || [])].sort((a: any, b: any) =>
        String(a.published_at || "").localeCompare(String(b.published_at || "")),
      ),
    [digest?.discovered_news],
  );
  const step1AuditCounts = useMemo(() => {
    const rows = digest?.discovered_news || [];
    const inPool = rows.filter((r: { in_candidate_pool?: boolean }) => r.in_candidate_pool).length;
    return { total: rows.length, inPool, rejected: rows.length - inPool };
  }, [digest?.discovered_news]);
  const step1JournalSummaryLine = useMemo(
    () =>
      `Журнал проверки ссылок (шаг 1) · проверено ${step1AuditCounts.total} · в списке кандидатов (шаг 2) ${step1AuditCounts.inPool} · отбраковано ${step1AuditCounts.rejected}`,
    [step1AuditCounts],
  );
  const [step1SummaryCopied, setStep1SummaryCopied] = useState(false);
  const copyStep1JournalSummary = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(step1JournalSummaryLine);
      setStep1SummaryCopied(true);
      window.setTimeout(() => setStep1SummaryCopied(false), 2000);
    } catch {
      setStep1SummaryCopied(false);
    }
  }, [step1JournalSummaryLine]);
  const rejectReasonSummary = useMemo(() => {
    const raw = digest?.rejected_reasons_summary;
    if (!raw || typeof raw !== "object") return { entries: [] as [string, number][], total: 0 };
    const entries = Object.entries(raw).filter(([, count]) => Number(count) > 0) as [string, number][];
    const total = entries.reduce((sum, [, count]) => sum + Number(count), 0);
    return { entries, total };
  }, [digest?.rejected_reasons_summary]);
  const rejectAuditSamples = useMemo(() => {
    const raw = digest?.step1_reject_audit;
    const samplesByReason = raw && typeof raw === "object" ? (raw as any).samples_by_reason : null;
    if (!samplesByReason || typeof samplesByReason !== "object") return [] as Array<{ code: string; sample: any }>;
    return rejectReasonSummary.entries
      .flatMap(([code]) => {
        const samples = Array.isArray(samplesByReason[code]) ? samplesByReason[code] : [];
        return samples.slice(0, 2).map((sample: any) => ({ code, sample }));
      })
      .slice(0, 4);
  }, [digest?.step1_reject_audit, rejectReasonSummary.entries]);
  const step1RejectBreakdown = useMemo(() => {
    const byId = new Map(step1FilterCatalog.map((x) => [x.id, x]));
    return Object.entries(step1FilterCounters)
      .filter(([, n]) => Number(n) > 0)
      .map(([id, count]) => ({
        id,
        count: Number(count),
        label: byId.get(id)?.label_ru ?? REJECT_REASON_LABELS[id] ?? id,
      }))
      .sort((a, b) => b.count - a.count);
  }, [step1FilterCatalog, step1FilterCounters]);

  const step1CountersSum = useMemo(
    () => step1RejectBreakdown.reduce((acc, x) => acc + x.count, 0),
    [step1RejectBreakdown],
  );

  const step1ModalJournal = useMemo(() => {
    const src = step1JournalTotalsApi ?? step1AuditCounts;
    return {
      total: src.total,
      inPool: "in_pool" in src ? src.in_pool : src.inPool,
      rejected: src.rejected,
    };
  }, [step1JournalTotalsApi, step1AuditCounts]);

  const hasStep1StatisticsData = useMemo(() => {
    if (step1AuditCounts.total > 0) return true;
    const pcs = digest?.pool_collection_stats as PoolCollectionStatsPayload | undefined;
    if (pcs?.last_run || pcs?.pool?.total) return true;
    const meta = digest?.step1_collection_meta;
    return Boolean(meta && typeof meta === "object" && Object.keys(meta).length > 0);
  }, [step1AuditCounts, digest?.pool_collection_stats, digest?.step1_collection_meta]);

  const openStep1Statistics = useCallback(async () => {
    setShowStep1Statistics(true);
    setStep1StatsLoading(true);
    setStep1StatsError("");
    try {
      const data = await api.getStep1Statistics(digestId);
      setStep1StatsData(data);
    } catch (e) {
      setStep1StatsData(null);
      setStep1StatsError(e instanceof Error ? e.message : "Не удалось загрузить статистику");
    } finally {
      setStep1StatsLoading(false);
    }
  }, [digestId]);

  const step1StatsLinkRows = useMemo(() => {
    const apiLinks = Array.isArray(step1StatsData?.links) ? step1StatsData.links : null;
    const rows =
      apiLinks ??
      [...(digest?.discovered_news || [])].sort((a: any, b: any) => {
        const ap = a.in_candidate_pool ? 0 : 1;
        const bp = b.in_candidate_pool ? 0 : 1;
        if (ap !== bp) return ap - bp;
        return String(b.published_at || "").localeCompare(String(a.published_at || ""));
      });
    let filtered = rows;
    if (step1AuditFilter === "in_pool") {
      filtered = filtered.filter((r: any) => r.in_candidate_pool || r.outcome === "in_pool");
    } else if (step1AuditFilter === "rejected") {
      filtered = filtered.filter(
        (r: any) => !(r.in_candidate_pool || r.outcome === "in_pool") && r.outcome !== "verified_only",
      );
    }
    if (step1StatsReasonFilter) {
      filtered = filtered.filter((r: any) => {
        const codes = Array.isArray(r.reject_codes) ? r.reject_codes : rejectReasonCodes(String(r.verification_comment || ""));
        return codes.includes(step1StatsReasonFilter);
      });
    }
    return filtered;
  }, [step1StatsData, digest?.discovered_news, step1AuditFilter, step1StatsReasonFilter]);

  const step1FiltersOrdered = useMemo(() => {
    const byId = new Map(step1FilterCatalog.map((x) => [x.id, x]));
    const merged = step1FilterStates
      .map((s) => {
        const cat = byId.get(s.id);
        if (!cat) return null;
        return {
          ...cat,
          enabled: cat.locked ? true : Boolean(s.enabled),
          order: Number(s.order) || 0,
          count: Number(step1FilterCounters[s.id] || 0),
        };
      })
      .filter(Boolean) as Array<Step1FilterCatalogItem & { enabled: boolean; order: number; count: number }>;
    merged.sort((a, b) => a.order - b.order || a.label_ru.localeCompare(b.label_ru, "ru"));
    return merged;
  }, [step1FilterCatalog, step1FilterStates, step1FilterCounters]);

  const reorderStep1Filters = useCallback((fromId: string, toId: string) => {
    if (!fromId || !toId || fromId === toId) return;
    setStep1FilterStates((prev) => {
      const list = [...prev].sort((a, b) => a.order - b.order);
      const from = list.findIndex((x) => x.id === fromId);
      const to = list.findIndex((x) => x.id === toId);
      if (from < 0 || to < 0) return prev;
      const [moved] = list.splice(from, 1);
      list.splice(to, 0, moved);
      return list.map((x, idx) => ({ ...x, order: idx + 1 }));
    });
  }, []);

  const toggleStep1Filter = useCallback(
    (filterId: string) => {
      const cat = step1FilterCatalog.find((x) => x.id === filterId);
      if (!cat) return;
      setStep1FilterStates((prev) =>
        prev.map((x) => (x.id === filterId ? { ...x, enabled: !x.enabled } : x)),
      );
    },
    [step1FilterCatalog],
  );
  /** Пересборка: когда пул уже есть, выпуск на шаге 1+ или прошёл выбор (повтор после 502 / обновление ленты). */
  const showRebuildPoolButton =
    !isDraft &&
    (hasCandidatePool ||
      digestStatus === "step_1_candidates" ||
      pastStep2ForRebuild);

  useEffect(() => {
    const rows = digest?.discovered_news as any[] | undefined;
    if (!rows) return;
    const next: Record<number, DiscoveredDraft> = {};
    for (const row of rows) {
      const scoreRaw = row?.manual_score;
      const score = scoreRaw === 1 || scoreRaw === 2 || scoreRaw === 3 ? String(scoreRaw) as "1" | "2" | "3" : "";
      const reasonRaw = String(row?.manual_reason || "") as ManualScoreReason | "";
      const reason = MANUAL_SCORE_REASON_OPTIONS.some((x) => x.value === reasonRaw) ? reasonRaw : "";
      next[Number(row.id)] = {
        score,
        reason,
        reasonOther: String(row?.manual_reason_other || ""),
      };
    }
    setDiscoveredDrafts(next);
    setDiscoveredSaveState({});
  }, [digest?.discovered_news]);

  const toggleSelected = (id: number) => {
    const row = candidatesSorted.find((x: any) => x.id === id);
    if (!row || !candidateSelectableForStep2(row)) return;
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (!canAddCandidateToSelection(row, prev, candidatesSorted)) return prev;
      return [...prev, id];
    });
  };

  const applyTop5AndSelect = async () => {
    const ids = pickTop5ByRating(candidatesSorted);
    if (ids.length < 5) {
      setError(
        "Недостаточно кандидатов с меткой «Можно в топ‑5» для автоматического выбора. Отметьте пятёрку вручную или пересоберите пул.",
      );
      return;
    }
    setSelected(ids);
    requestAnimationFrame(() => {
      const first = document.getElementById(`news-candidate-${ids[0]}`);
      first?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    await run("Шаг 2: топ-5 по рейтингу…", async () => {
      const res = await api.selectNews(digestId, [], true);
      if (Array.isArray(res?.selected_ids) && res.selected_ids.length === 5) {
        setSelected(res.selected_ids);
      }
    });
  };

  const addStep2ManualUrls = async () => {
    const urls = step2ManualUrls
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!urls.length) {
      setError("Вставьте хотя бы одну ссылку на статью.");
      return;
    }
    await run("Шаг 2: проверка и добавление ссылки в пул…", async () => {
      const res = await api.step2AddManualUrls(digestId, urls);
      setStep2ManualUrls("");
      await loadDigest({ skipProgress: true, preserveSelection: true });
      const addedIds = (res?.added || [])
        .filter((row) => row.id && candidateSelectableForStep2(row))
        .map((row) => row.id);
      if (addedIds.length) {
        setSelected((prev) => {
          const next = [...prev];
          for (const id of addedIds) {
            if (next.length >= 5) break;
            if (!next.includes(id)) next.push(id);
          }
          return next;
        });
      }
      if (res?.skipped_duplicates?.length) {
        setError(`Уже в пуле: ${res.skipped_duplicates.join(", ")}`);
      } else if (res?.detail && !(res?.added || []).length) {
        setError(res.detail);
      } else if ((res?.added || []).length && !addedIds.length) {
        setError(
          "Ссылка добавлена, но не прошла проверку «Можно в топ‑5». Откройте карточку — причина в описании; укажите прямую ссылку на статью про ИИ.",
        );
      }
    });
  };

  const confirmSelectedFive = async () => {
    const validSelected = selected.filter((id) => {
      const row = candidatesSorted.find((c: any) => c.id === id);
      return Boolean(row) && candidateSelectableForStep2(row);
    });
    if (validSelected.length !== 5) {
      setSelected(validSelected.slice(0, 5));
      setError(
        "Состав кандидатов обновился, и часть отмеченных новостей стала недоступна. Проверьте список и подтвердите 5 новостей снова.",
      );
      return;
    }
    await run("Шаг 2: сохранение выбранных пяти новостей…", () =>
      api.selectNews(digestId, validSelected, false),
    );
  };

  const updateDiscoveredDraft = useCallback((newsId: number, patch: Partial<DiscoveredDraft>) => {
    setDiscoveredDrafts((prev) => {
      const base: DiscoveredDraft = prev[newsId] ?? { score: "", reason: "", reasonOther: "" };
      const next = { ...base, ...patch };
      if (next.score === "3") {
        next.reason = "";
        next.reasonOther = "";
      }
      if (next.reason !== "other") {
        next.reasonOther = "";
      }
      return { ...prev, [newsId]: next };
    });
    setDiscoveredSaveState((prev) => ({ ...prev, [newsId]: { ...prev[newsId], ok: false, error: "" } }));
  }, []);

  const saveDiscoveredFeedback = useCallback(
    async (newsId: number) => {
      const draft = discoveredDrafts[newsId] ?? { score: "", reason: "", reasonOther: "" };
      if (draft.score !== "1" && draft.score !== "2" && draft.score !== "3") {
        setDiscoveredSaveState((prev) => ({ ...prev, [newsId]: { saving: false, ok: false, error: "Выберите оценку 1–3." } }));
        return;
      }
      if ((draft.score === "1" || draft.score === "2") && !draft.reason) {
        setDiscoveredSaveState((prev) => ({
          ...prev,
          [newsId]: { saving: false, ok: false, error: "Для оценки ниже 3 выберите причину." },
        }));
        return;
      }
      if (draft.reason === "other" && draft.reasonOther.trim().length < 3) {
        setDiscoveredSaveState((prev) => ({
          ...prev,
          [newsId]: { saving: false, ok: false, error: "Для причины «другое» добавьте комментарий (минимум 3 символа)." },
        }));
        return;
      }
      setDiscoveredSaveState((prev) => ({ ...prev, [newsId]: { saving: true, ok: false, error: "" } }));
      try {
        const saved = await api.saveStep1DiscoveredFeedback(digestId, newsId, {
          score: Number(draft.score) as 1 | 2 | 3,
          reason: draft.score === "3" ? undefined : (draft.reason || undefined),
          reason_other: draft.reason === "other" ? draft.reasonOther.trim() : undefined,
        });
        const exportPath = typeof saved?.ratings_export_path === "string" ? saved.ratings_export_path : "";
        if (exportPath) setRatingsExportPath(exportPath);
        setDiscoveredSaveState((prev) => ({
          ...prev,
          [newsId]: { saving: false, ok: true, error: "", exportPath },
        }));
        await loadDigest({ skipProgress: true, preserveError: true });
      } catch (e) {
        setDiscoveredSaveState((prev) => ({
          ...prev,
          [newsId]: { saving: false, ok: false, error: (e as Error).message || "Не удалось сохранить оценку." },
        }));
      }
    },
    [digestId, discoveredDrafts, loadDigest],
  );

  const downloadManualRatings = useCallback(async () => {
    setRatingsDownloadBusy(true);
    setRatingsDownloadError("");
    try {
      await api.downloadStep1ManualRatings();
    } catch (e) {
      setRatingsDownloadError((e as Error).message || "Не удалось скачать файл оценок.");
    } finally {
      setRatingsDownloadBusy(false);
    }
  }, []);

  const scrollToOrderEdit = useCallback(() => {
    setOrderEditMode(true);
    requestAnimationFrame(() => {
      step2OrderCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  const manualUrlList = useMemo(
    () =>
      manualUrls
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
    [manualUrls],
  );

  const run = async (label: string, fn: () => Promise<unknown>) => {
    if (label.includes("Шаг 1:")) {
      pendingScrollToStep2Ref.current = true;
    }
    const rk = parseRunningStepFromLabel(label);
    if (rk !== null) setRunningStepKey(rk);
    if (rk === "1") {
      setStep1Elapsed(0);
      setStep1Live(null);
      requestAnimationFrame(() => {
        step1CardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        step2CardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
    if (rk === "3") {
      step3ModeRef.current =
        label.includes("2–3") || label.includes("2-3") ? "combined" : "analytics";
      setStep3Elapsed(0);
      requestAnimationFrame(() => {
        step3CardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    if (rk === "4") {
      setStep4Elapsed(0);
      requestAnimationFrame(() => {
        step4CardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    setProgressLabel(label);
    setLoading(true);
    try {
      setError("");
      await fn();
      if (rk === "4") {
        setProgressLabel("Шаг 4: обновление результата на экране…");
      } else {
        setProgressLabel("Обновление данных…");
      }
      await loadDigest({ skipProgress: true });
      if (label.includes("порядок") || label.includes("оптимальн")) {
        setOrderEditMode(false);
      }
      if (label.includes("оптимальный порядок по мнению ИИ")) {
        requestAnimationFrame(() => {
          step2OrderCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
      if (rk === "2pick") {
        requestAnimationFrame(() => {
          step2OrderCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
      if (pendingScrollToStep2Ref.current) {
        pendingScrollToStep2Ref.current = false;
        requestAnimationFrame(() => {
          step2CardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
      }
    } catch (e) {
      pendingScrollToStep2Ref.current = false;
      const errMsg = (e as Error).message;
      try {
        await loadDigest({ skipProgress: true, preserveError: true, preserveSelection: true });
      } catch {
        /* игнорируем вторичную ошибку загрузки */
      }
      setError(errMsg);
    } finally {
      setLoading(false);
      setProgressLabel("");
      setRunningStepKey(null);
      setStep1Stopping(false);
      step1AbortRef.current = null;
    }
  };

  const cancelStep1Collection = useCallback(async () => {
    if (step1Stopping) return;
    setStep1Stopping(true);
    setError("");
    try {
      await api.step1Cancel(digestId);
    } catch (e) {
      setStep1Stopping(false);
      setError((e as Error).message || "Не удалось отправить запрос на остановку.");
    }
  }, [digestId, step1Stopping]);

  const runStep1 = (rebuild: boolean) => {
    const keepIds = rebuild && selected.length > 0 ? [...selected] : [];
    const label = rebuild
      ? step1RebuildProgressLabel(selected.length)
      : "Шаг 1: поиск новостей, проверка источников и оценка кандидатов (обычно 3–5 мин)…";
    if (rebuild) {
      const ok = window.confirm(step1RebuildConfirmMessage(selected.length, pastStep2ForRebuild));
      if (!ok) return;
    }
    const keptUrls = keepIds
      .map((id) => candidatesSorted.find((c) => c.id === id)?.url)
      .filter((url): url is string => Boolean(url));
    void run(label, async () => {
      if (keptUrls.length > 0) {
        pendingPreselectKeptUrlsRef.current = keptUrls;
        lastSelectedUrlsRef.current = keptUrls;
      } else if (manualUrlList.length > 0) {
        pendingManualUrlsRef.current = [...manualUrlList];
      }
      const ac = new AbortController();
      step1AbortRef.current = ac;
      await api.step1Run(digestId, manualUrlList, {
        rebuild,
        keep_candidate_ids: keepIds,
        news_window_days: newsWindowDays,
        news_window_day_kind: newsWindowDayKind,
        signal: ac.signal,
      });
    });
  };

  const openStep1FilterSettings = useCallback(async () => {
    setShowStep1FilterSettings(true);
    setStep1FilterLoading(true);
    setStep1FilterError("");
    try {
      const [data] = await Promise.all([
        api.getStep1Filters(digestId),
        loadDigest({ skipProgress: true }),
      ]);
      setStep1FilterCatalog(Array.isArray(data?.catalog) ? data.catalog : []);
      setStep1FilterStates(Array.isArray(data?.config?.filters) ? data.config.filters : []);
      setStep1FilterCounters((data?.counters && typeof data.counters === "object" ? data.counters : {}) as Record<string, number>);
      const jt = data?.journal_totals;
      if (jt && typeof jt === "object") {
        setStep1JournalTotalsApi({
          total: Number(jt.total) || 0,
          in_pool: Number(jt.in_pool) || 0,
          rejected: Number(jt.rejected) || 0,
        });
      } else {
        setStep1JournalTotalsApi(null);
      }
      setStep1FiltersAppliedLastRun(
        Array.isArray(data?.filters_applied_last_run) ? data.filters_applied_last_run : [],
      );
      const minPages = Number(data?.config?.min_discovered_pages);
      if (Number.isFinite(minPages) && minPages >= 10) setStep1MinDiscoveredPages(minPages);
      const minIters = Number(data?.config?.min_collection_iterations);
      if (Number.isFinite(minIters) && minIters >= 1) setStep1MinCollectionIterations(minIters);
    } catch (e) {
      setStep1FilterError((e as Error).message || "Не удалось загрузить настройки фильтров.");
    } finally {
      setStep1FilterLoading(false);
    }
  }, [digestId, loadDigest]);

  const openAppConfigModal = useCallback(async () => {
    setShowAppConfigModal(true);
    setAppConfigLoading(true);
    setAppConfigError("");
    try {
      const data = await api.getAppConfig();
      setAppConfig(data);
    } catch (e) {
      setAppConfigError((e as Error).message || "Не удалось загрузить настройки сервера.");
      setAppConfig(null);
    } finally {
      setAppConfigLoading(false);
    }
  }, []);

  const saveStep1FilterSettings = useCallback(async () => {
    setStep1FilterSaving(true);
    setStep1FilterError("");
    try {
      const payload = {
        version: 1,
        filters: [...step1FilterStates]
          .sort((a, b) => a.order - b.order)
          .map((x, idx) => ({ id: x.id, enabled: Boolean(x.enabled), order: idx + 1 })),
        min_discovered_pages: Math.max(10, Math.min(200, Number(step1MinDiscoveredPages) || 20)),
        min_collection_iterations: Math.max(1, Math.min(50, Number(step1MinCollectionIterations) || 5)),
      };
      const data = await api.saveStep1Filters(digestId, payload);
      setStep1FilterCatalog(Array.isArray(data?.catalog) ? data.catalog : []);
      setStep1FilterStates(Array.isArray(data?.config?.filters) ? data.config.filters : []);
      setStep1FilterCounters((data?.counters && typeof data.counters === "object" ? data.counters : {}) as Record<string, number>);
      const savedMin = Number(data?.config?.min_discovered_pages);
      if (Number.isFinite(savedMin)) setStep1MinDiscoveredPages(savedMin);
      const savedIters = Number(data?.config?.min_collection_iterations);
      if (Number.isFinite(savedIters)) setStep1MinCollectionIterations(savedIters);
      setStep1FilterError("Настройки сохранены. Они применятся при следующем запуске шага 1.");
    } catch (e) {
      setStep1FilterError((e as Error).message || "Не удалось сохранить настройки фильтров.");
    } finally {
      setStep1FilterSaving(false);
    }
  }, [digestId, step1FilterStates, step1MinDiscoveredPages, step1MinCollectionIterations]);

  const selectedMap = useMemo(() => {
    const map = new Map<number, any>();
    for (const item of digest?.selected || []) {
      map.set(item.candidate_id, item);
    }
    return map;
  }, [digest]);

  const orderedSelectedRows = selected.map((id, index) => ({
    candidate_id: id,
    output_position: index + 1,
    ...selectedMap.get(id),
  }));

  const step2StubsShown = useMemo(() => {
    if (digest?.candidates_are_demo_fallback === true) return true;
    if (digest?.candidates_are_demo_fallback === false) return false;
    return candidatesSorted.length > 0 && candidatesSorted.every((c: any) => looksLikeDemoCandidate(c));
  }, [digest?.candidates_are_demo_fallback, candidatesSorted]);

  const poolCollection = digest?.pool_collection_stats as PoolCollectionStatsPayload | undefined;
  const step1CollectionMeta = (digest?.step1_collection_meta || null) as
    | {
        iterations?: number;
        min_collection_iterations?: number;
        stop_reason?: string;
        elapsed_sec?: number;
        target_max_candidates?: number;
        collection_target_pages?: number;
        batch_size?: number;
        urls_raw_merged?: number;
        urls_raw_unique?: number;
        urls_sent_to_http?: number;
        urls_prefilter_rejected?: number;
        verified_total?: number;
        conversion_e2e_pct?: number;
        conversion_http_pct?: number;
        conversion_prefilter_pct?: number;
        conversion_e2e_baseline?: number | null;
        estimated_raw_for_10?: number;
        estimated_raw_for_10_run?: number;
      }
    | null;

  const poolStats = useMemo(() => {
    const total = candidatesSorted.length;
    if (!total) {
      return {
        total: 0,
        pressCount: 0,
        pressShare: 0,
        ruCount: 0,
        ruShare: 0,
        maxPerSource: 0,
        foreignAgentCount: 0,
        forbiddenCount: 0,
      };
    }
    const sourceCount = new Map<string, number>();
    let pressCount = 0;
    let ruCount = 0;
    let foreignAgentCount = 0;
    let forbiddenCount = 0;
    for (const c of candidatesSorted as any[]) {
      const host = hostFromUrl(String(c.url || ""));
      const key = (String(c.source || "").trim() || host || "unknown").toLowerCase();
      sourceCount.set(key, (sourceCount.get(key) || 0) + 1);
      if (isPressLikeCandidate(c)) pressCount += 1;
      if (isRussianHost(host)) ruCount += 1;
      if (Boolean(c.is_foreign_agent)) foreignAgentCount += 1;
      if (Boolean(c.is_aggregator) || String(c.reliability_status || "") === "❗ без подтверждения") forbiddenCount += 1;
    }
    const maxPerSource = Math.max(...Array.from(sourceCount.values()));
    return {
      total,
      pressCount,
      pressShare: pressCount / total,
      ruCount,
      ruShare: ruCount / total,
      maxPerSource,
      foreignAgentCount,
      forbiddenCount,
    };
  }, [candidatesSorted]);

  const step1CollectionInProgress = loading && runningStepKey === "1";

  const step3InProgress = loading && runningStepKey === "3";
  const step4InProgress = loading && (runningStepKey === "4" || runningStepKey === "4img" || runningStepKey === "4txt");
  const step4ImagesInProgress = loading && runningStepKey === "4img";
  const step4TextsInProgress = loading && runningStepKey === "4txt";

  const step1DisplayElapsed = step1Live?.elapsed_sec ?? step1Elapsed;
  const step1PhaseText = step1CollectionInProgress
    ? step1Live?.phase?.trim() || getStep1PhaseText(step1DisplayElapsed)
    : "";

  useEffect(() => {
    if (!step1CollectionInProgress) {
      setStep1Elapsed(0);
      return;
    }
    const start = Date.now();
    const id = setInterval(() => {
      setStep1Elapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [step1CollectionInProgress]);

  useEffect(() => {
    if (!step1CollectionInProgress) {
      let cancelled = false;
      void (async () => {
        try {
          const data = await api.getStep1Progress(digestId);
          if (!cancelled && (data.urls_raw > 0 || data.verified_pool > 0 || data.web_search_api_calls > 0 || (data.links_found_total ?? 0) > 0 || data.rejected_total > 0 || !data.running)) {
            setStep1Live(data);
          }
        } catch {
          /* нет снимка */
        }
      })();
      return () => {
        cancelled = true;
      };
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await api.getStep1Progress(digestId);
        if (!cancelled) setStep1Live(data);
      } catch {
        /* сервер ещё поднимает прогресс */
      }
    };
    void poll();
    const id = setInterval(() => void poll(), 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [step1CollectionInProgress, digestId]);

  useEffect(() => {
    if (step1CollectionInProgress) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.getStep1Progress(digestId);
        if (
          !cancelled &&
          !data.running &&
          (data.urls_raw > 0 || data.verified_pool > 0 || data.web_search_api_calls > 0 || (data.links_found_total ?? 0) > 0 || data.rejected_total > 0)
        ) {
          setStep1Live(data);
        }
      } catch {
        /* нет сохранённого снимка */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [digestId, step1CollectionInProgress, hasStep1StatisticsData]);

  useEffect(() => {
    if (!step3InProgress) {
      setStep3Elapsed(0);
      return;
    }
    const start = Date.now();
    const id = setInterval(() => {
      setStep3Elapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [step3InProgress]);

  useEffect(() => {
    if (!step4InProgress) {
      setStep4Elapsed(0);
      return;
    }
    const start = Date.now();
    const id = setInterval(() => {
      setStep4Elapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [step4InProgress]);

  const step3PhaseText = useMemo(() => {
    if (!step3InProgress) return "";
    if (progressLabel.includes("обновление") || progressLabel.includes("Обновление")) {
      return "Обновляем экран: подгружаем готовую аналитику…";
    }
    return getStep3PhaseText(step3ModeRef.current, step3Elapsed);
  }, [step3InProgress, step3Elapsed, progressLabel]);

  const step4PhaseText = useMemo(() => {
    if (!step4InProgress) return "";
    const refreshing =
      progressLabel.includes("обновление результата") || progressLabel.includes("Обновление");
    if (runningStepKey === "4img") return getStep4ImagesPhaseText(step4Elapsed, refreshing);
    if (runningStepKey === "4txt") return getStep4TextsPhaseText(step4Elapsed, refreshing);
    return getStep4PhaseText(step4Elapsed, refreshing);
  }, [step4InProgress, step4Elapsed, progressLabel, runningStepKey]);

  const step4StatusHeadline = useMemo(() => {
    if (runningStepKey === "4img") return "Шаг 4: обложки";
    if (runningStepKey === "4txt") return "Шаг 4: тексты";
    return "Шаг 4: финальная сборка";
  }, [runningStepKey]);

  const step3StatusHeadline =
    step3ModeRef.current === "combined" ? "Шаг 2–3: порядок и аналитика" : "Шаг 3: аналитика";

  const asyncProgressLabel = useMemo(() => {
    if (step1CollectionInProgress && step1PhaseText) {
      return `Шаг 1 — ${step1PhaseText}`;
    }
    if (step3InProgress && step3PhaseText) {
      return `${step3StatusHeadline} — ${step3PhaseText}`;
    }
    if (step4InProgress && step4PhaseText) {
      return `${step4StatusHeadline} — ${step4PhaseText}`;
    }
    return progressLabel;
  }, [
    step1CollectionInProgress,
    step1PhaseText,
    step3InProgress,
    step3PhaseText,
    step3StatusHeadline,
    step4InProgress,
    step4PhaseText,
    step4StatusHeadline,
    progressLabel,
  ]);

  const step1PoolBlocksRerun = hasCandidatePool && digestStatus === "step_1_candidates";

  const proxyapiBudgetText = useMemo(() => {
    if (digest?.proxyapi_budget_message) return String(digest.proxyapi_budget_message);
    if (error && isProxyapiBudgetError(error)) return error;
    return null;
  }, [digest?.proxyapi_budget_message, error]);

  const budgetNoticesWithoutProxyapi = useMemo(() => {
    const list = digest?.budget_notices ?? [];
    if (!proxyapiBudgetText) return list;
    return list.filter((msg: string) => !isProxyapiBudgetError(msg));
  }, [digest?.budget_notices, proxyapiBudgetText]);

  const handleSelectImageVariant = useCallback(
    async (variant: number) => {
      setSelectedImageVariant(variant);
      setLoading(true);
      setProgressLabel("Шаг 4: сохранение выбранной обложки…");
      try {
        setError("");
        await api.selectStep4Image(digestId, variant);
        await loadDigest({ skipProgress: true });
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
        setProgressLabel("");
        setRunningStepKey(null);
      }
    },
    [digestId, loadDigest],
  );

  /** Шаг 2 показываем сразу после шага 0 (и дальше), чтобы под шагом 1 всегда было место списка; в draft карточка не нужна. */
  const showStep2Section = useMemo(() => {
    if (step1CollectionInProgress) return true;
    if (candidatesSorted.length > 0) return true;
    const st = digest?.digest?.status as string | undefined;
    if (st && st !== "draft") return true;
    return false;
  }, [candidatesSorted.length, digest?.digest?.status, step1CollectionInProgress]);

  const isStyleTopic = digestTopic === "style";
  const stepThemeAccent = isStyleTopic ? "#ec4899" : "#38bdf8";

  const StepTopicDot = () => (
    <span
      aria-hidden="true"
      title={isStyleTopic ? "Тема: Стиль" : "Тема: ИИ"}
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        borderRadius: "50%",
        marginRight: 8,
        background: stepThemeAccent,
        boxShadow: `0 0 0 2px ${isStyleTopic ? "rgba(236,72,153,0.25)" : "rgba(56,189,248,0.25)"}`,
        verticalAlign: "middle",
      }}
    />
  );

  const step0Active = useMemo((): "serious" | "curious" | "default" | null => {
    const row = digest?.digest;
    if (!row?.digest_type) return null;
    if (row.digest_type_via_default) return "default";
    if (row.digest_type === "serious") return "serious";
    if (row.digest_type === "curious") return "curious";
    return null;
  }, [digest?.digest]);

  const sourceTiersDigestType = useMemo((): "serious" | "curious" | null => {
    const t = digest?.digest?.digest_type;
    return t === "serious" || t === "curious" ? t : null;
  }, [digest?.digest?.digest_type]);

  const step0ApiOpts = () => ({
    digest_topic: digestTopic,
    news_window_days: newsWindowDays,
    news_window_day_kind: newsWindowDayKind,
  });

  const runStep0 = (
    label: string,
    payload: {
      digest_type?: "serious" | "curious";
      digest_topic?: "ai" | "style";
      news_window_days?: number;
      news_window_day_kind?: "calendar" | "working";
    },
  ) =>
    run(label, async () => {
      const resp = await api.step0(digestId, payload);
      const nextId = Number(resp?.digest_id || 0);
      if (nextId > 0 && nextId !== digestId) {
        window.location.href = `/digests/${nextId}`;
        return;
      }
    });

  const step0BtnStyle = (key: "serious" | "curious" | "default"): CSSProperties => {
    const on = step0Active === key;
    return {
      padding: "10px 16px",
      borderRadius: 8,
      cursor: loading ? "not-allowed" : "pointer",
      opacity: step0Active && !on ? 0.55 : 1,
      border: on ? "2px solid #38bdf8" : "1px solid #475569",
      background: on ? "#1e3a5f" : "#1e293b",
      color: "#e2e8f0",
      fontWeight: on ? 600 : 400,
    };
  };

  const onDrop = (targetId: number) => {
    if (draggedId === null || draggedId === targetId) return;
    setSelected((prev) => {
      const next = [...prev];
      const from = next.indexOf(draggedId);
      const to = next.indexOf(targetId);
      if (from < 0 || to < 0) return prev;
      next.splice(from, 1);
      next.splice(to, 0, draggedId);
      return next;
    });
    setDraggedId(null);
  };

  return (
    <div className="grid">
      <AsyncProgress active={loading} label={asyncProgressLabel} />

      <div className="card">
        <div className="wizard-header-top">
          <h2>Мастер дайджеста · {formatDigestDateLabel(digest?.digest?.date)}</h2>
          <Link href="/" className="wizard-home-btn" title="Вернуться на панель выпусков">
            На главную
          </Link>
        </div>
        <div style={{ fontSize: "0.88rem", color: "#94a3b8", marginBottom: 8 }}>
          Текущий статус: <strong style={{ color: "#e2e8f0" }}>{digest?.digest?.status ?? "…"}</strong>
          {" · "}
          По выпуску (накопительно):{" "}
          {digest?.total_cost_rub != null ? (
            <>
              <strong style={{ color: releaseCostFinalized ? "#4ade80" : "#e2e8f0" }}>
                {Number(digest.total_cost_rub).toFixed(2)} ₽
              </strong>
              {releaseCostFinalized ? " · зафиксировано" : " · до фиксации"}
            </>
          ) : (
            "—"
          )}
          {" · "}
          Сегодня в приложении:{" "}
          {digest?.tracked_spend_today_rub != null ? (
            <strong style={{ color: "#e2e8f0" }}>
              {Number(digest.tracked_spend_today_rub).toFixed(2)} ₽
            </strong>
          ) : (
            "—"
          )}
          {digest?.proxyapi_spent_today_rub != null ? (
            <>
              {" · "}
              Ключ ProxyAPI (все сервисы за день):{" "}
              <strong style={{ color: "#94a3b8" }}>
                {Number(digest.proxyapi_spent_today_rub).toFixed(2)} ₽
              </strong>
            </>
          ) : null}
        </div>
        <p className="wizard-hint-do">
          Идите по шагам сверху вниз: <strong>0 → 1</strong> → <strong>2</strong> (сначала выбор пятёрки, затем порядок) →{" "}
          <strong>3 → 4</strong>. Статус в шапке показывает, на каком этапе выпуск на сервере.
        </p>
        <WizardWhy summary="Что означают статус и суммы в рублях">
          <p>
            <strong>Статус</strong> — этап конвейера на сервере. <strong>По выпуску (накопительно)</strong> — все
            списания ProxyAPI с момента шага 0 по этому выпуску до фиксации кнопкой в блоке «Шаг 4 — результат» (включая шаг 1: веб-поиск,
            Telegram). <strong>Сегодня в приложении</strong> — учтённые запросы только этого мастера дайджеста за
            календарный день (МСК). <strong>Ключ ProxyAPI</strong> — все списания с API-ключом за день, в том числе
            Cursor, другие проекты и инструменты с тем же ключом.
          </p>
        </WizardWhy>
        <WizardWhy summary="Панель, ссылка и кнопка «Создать на сегодня» — в чём разница">
          <p>
            Логотип <strong>ExTellect Daily Digest</strong> ведёт на{" "}
            <Link href="/" style={{ color: "#7dd3fc" }}>
              панель выпусков
            </Link>
            . Если выпуск на сегодня уже создан, его можно открыть из списка («Открыть мастер») или по закладке{" "}
            <code>/digests/…</code>. Кнопка на панели «Создать или открыть сегодняшний дайджест» при уже существующей дате{" "}
            <strong>не создаёт дубликат</strong> — сервер возвращает тот же выпуск и перенаправляет в тот же мастер.
          </p>
        </WizardWhy>
        <p className="wizard-hint-anchor">
          Полная памятка по шагам и привязка к коду —{" "}
          <a href="#digest-hints" style={{ color: "#7dd3fc" }}>
            блок ниже
          </a>{" "}
          (по умолчанию свёрнута — клик по строке «Памятка: шаги и «под капотом»…», как у журнала проверки ссылок).
        </p>
      </div>

      {proxyapiBudgetText ? <ProxyapiBudgetAlert message={proxyapiBudgetText} /> : null}

      {error && !proxyapiBudgetText ? (
        <div className="card" role="alert" style={{ borderColor: "#f87171", color: "#fecaca" }}>
          <h3 style={{ marginTop: 0, fontSize: "1.05rem", color: "#fca5a5" }}>Ошибка</h3>
          <p style={{ margin: 0, lineHeight: 1.55 }}>{error}</p>
        </div>
      ) : null}

      {budgetNoticesWithoutProxyapi.length > 0 ? (
        <div
          className="card"
          role="status"
          style={{
            borderColor: "#d97706",
            background: "rgba(217, 119, 6, 0.14)",
            color: "#fde68a",
          }}
        >
          <h3 style={{ marginTop: 0, fontSize: "1.05rem", color: "#fcd34d" }}>Лимит расходов</h3>
          <p className="wizard-hint-do" style={{ color: "#fef3c7", fontSize: "0.98rem" }}>
            Если лимит исчерпан, часть шагов с ИИ выполняется упрощённо или пропускается. При необходимости увеличьте лимиты в{" "}
            <code style={{ color: "#fef3c7" }}>backend/.env</code> и перезапустите backend.
          </p>
          <WizardWhy summary="Подробнее про лимиты и список ниже">
            <p style={{ color: "#fde68a" }}>
              На шагах с ИИ действует лимит в рублях (настройки сервера). Сообщения в списке — что именно сократили или
              пропустили на этой сессии.
            </p>
          </WizardWhy>
          <ul style={{ margin: "0 0 0 1.2rem", padding: 0, lineHeight: 1.55, fontSize: "0.95rem" }}>
            {budgetNoticesWithoutProxyapi.map((msg: string, i: number) => (
              <li key={i} style={{ marginBottom: i < budgetNoticesWithoutProxyapi.length - 1 ? 10 : 0 }}>
                {msg}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {hasStep1StatisticsData || step1CollectionInProgress ? (
        <div className="card" role="status">
          <p className="wizard-hint-do" style={{ margin: 0, fontSize: "0.95rem" }}>
            {step1CollectionInProgress && step1Live?.running ? (
              <>
                Сбор идёт: итерация <strong>{step1Live.iteration || "—"}</strong>, найдено{" "}
                <strong>{step1Live.links_found_total ?? step1Live.urls_raw}</strong> ссылок, проверено{" "}
                <strong>{step1Live.links_processed ?? step1Live.rejected_total}</strong>, в пуле{" "}
                <strong style={{ color: "#4ade80" }}>{step1Live.verified_pool}</strong>, отбраковано{" "}
                <strong style={{ color: "#fca5a5" }}>{step1Live.rejected_total}</strong>.
              </>
            ) : (
              <>
                Журнал шага 1: проверено <strong>{step1AuditCounts.total || step1ModalJournal.total}</strong>, в списке
                кандидатов <strong style={{ color: "#4ade80" }}>{step1AuditCounts.inPool || step1ModalJournal.inPool}</strong>
                , отбраковано <strong style={{ color: "#fca5a5" }}>{step1AuditCounts.rejected || step1ModalJournal.rejected}</strong>
                . Подробная аналитика — кнопка <strong>«Статистика»</strong> в блоке шага 1.
              </>
            )}
          </p>
        </div>
      ) : null}

      <DigestHintsAccordion />

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
          <h3 style={{ margin: 0 }}>
            <StepTopicDot />
            Шаг 0 — тематика, тон и окно новостей
          </h3>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              disabled={loading || !sourceTiersDigestType || isStyleTopic}
              title={
                isStyleTopic
                  ? "Для темы «Стиль» используется отдельный файл source_tiers_style.txt"
                  : sourceTiersDigestType
                    ? "Домены источников и счётчики за 30 дней"
                    : "Сначала выберите тип дайджеста (серьёзный или курьёзный)"
              }
              onClick={() => setShowSourceTiersModal(true)}
            >
              Источники
            </button>
            <button type="button" disabled={loading} onClick={() => void openAppConfigModal()}>
              Настройки
            </button>
          </div>
        </div>
        <StepProgressBar active={runningStepKey === "0" || runningStepKey === "init"} />
        <p className="wizard-hint-do">
          Выберите <strong>тематику</strong> выпуска и окно по дате публикации.
          {isStyleTopic ? (
            <>
              {" "}
              Для темы <strong>«Стиль»</strong> по умолчанию ставится окно{" "}
              <strong>7 календарных дней</strong>; тон общий нейтральный — кнопки тона скрыты. Нажмите{" "}
              <strong>«Сохранить тематику»</strong> и дождитесь полоски загрузки.
            </>
          ) : (
            <>
              {" "}
              Затем нажмите <strong>одну</strong> кнопку тона и дождитесь полоски загрузки.
            </>
          )}{" "}
          Если в выпуске уже есть собранные шаги, смена тематики создаст/откроет отдельный выпуск этой же даты под новую тему.
          После успеха статус в шапке станет{" "}
          <code>step_0</code> — откроется шаг 1.
        </p>
        <label style={{ display: "block", marginBottom: 12 }}>
          Тематика:{" "}
          <select
            value={digestTopic}
            disabled={loading}
            onChange={(e) => {
              const next = e.target.value === "style" ? "style" : "ai";
              setDigestTopic(next);
              if (next === "style") {
                setNewsWindowDays(7);
                setNewsWindowDayKind("calendar");
              } else {
                setNewsWindowDays(3);
                setNewsWindowDayKind("working");
              }
            }}
            style={{ marginLeft: 6, padding: "6px 8px", borderRadius: 6 }}
          >
            <option value="ai">Искусственный интеллект</option>
            <option value="style">Стиль (мода)</option>
          </select>
        </label>
        <label style={{ display: "block", marginBottom: 8 }}>
          Окно поиска (дней от даты выпуска):{" "}
          <input
            type="number"
            min={1}
            max={90}
            value={newsWindowDays}
            disabled={loading}
            onChange={(e) => setNewsWindowDays(Math.max(1, Math.min(90, Number(e.target.value) || 3)))}
            style={{ width: 64, marginLeft: 6 }}
          />
        </label>
        <fieldset className="wizard-radio-group">
          <legend>Тип дней в окне</legend>
          <label className="wizard-radio-option">
            <input
              type="radio"
              name="newsWindowDayKind"
              value="working"
              checked={newsWindowDayKind === "working"}
              disabled={loading}
              onChange={() => setNewsWindowDayKind("working")}
            />
            Рабочие (пн–пт)
          </label>
          <label className="wizard-radio-option">
            <input
              type="radio"
              name="newsWindowDayKind"
              value="calendar"
              checked={newsWindowDayKind === "calendar"}
              disabled={loading}
              onChange={() => setNewsWindowDayKind("calendar")}
            />
            Календарные
          </label>
        </fieldset>
        <WizardWhy summary="Зачем тематика и тон важны">
          <p>
            <strong>Тематика</strong> определяет, какие источники и ключевые слова использует поиск на шаге 1, и как
            формируются тексты на шагах 3–4. <strong>ИИ</strong> — текущий режим про искусственный интеллект.{" "}
            <strong>Стиль</strong> — отдельный пул fashion-источников и фильтры по моде/одежде.
          </p>
          {!isStyleTopic ? (
            <p>
              <strong>Серьёзный</strong> — нейтральный деловой тон. <strong>Курьёзный</strong> — легче формулировки.{" "}
              <strong>По умолчанию</strong> — тип из файла настроек сервера (<code>digest_defaults.json</code>).
            </p>
          ) : (
            <p>
              Для темы <strong>Стиль</strong> тон один — общий нейтральный; кнопки «Серьёзный/Курьёзный» не
              показываются.
            </p>
          )}
          <p>
            <strong>Окно новостей</strong> ограничивает шаг 1: в пул попадают только материалы с датой публикации не раньше N
            дней от даты выпуска (календарных или рабочих). Слишком старые URL отсекаются с причиной{" "}
            <code>published_before_window</code>. Отдельно сохранять окно не нужно: при каждом{" "}
            <strong>запуске или пересборке шага 1</strong> сервер берёт текущие значения из полей шага 0.
          </p>
        </WizardWhy>
        {isStyleTopic ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
            <button
              type="button"
              disabled={loading}
              style={step0BtnStyle("serious")}
              onClick={() => runStep0("Сохранение тематики: стиль…", { digest_type: "serious", ...step0ApiOpts() })}
            >
              Сохранить тематику
            </button>
          </div>
        ) : (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("serious")}
            aria-pressed={step0Active === "serious"}
            title="Деловой нейтральный тон; рекомендуется для будничных выпусков вручную."
            onClick={() => runStep0("Сохранение типа дайджеста: серьёзный…", { digest_type: "serious", ...step0ApiOpts() })}
          >
            Серьёзный
          </button>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("curious")}
            aria-pressed={step0Active === "curious"}
            title="Курьёзный выпуск: поиск и отбор забавных/неожиданных историй про ИИ (без делового официоза). Перед шагом 1 сохраните тип."
            onClick={() => runStep0("Сохранение типа дайджеста: курьёзный…", { digest_type: "curious", ...step0ApiOpts() })}
          >
            Курьёзный
          </button>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("default")}
            aria-pressed={step0Active === "default"}
            title="Тип из digest_defaults.json на сервере (сейчас — серьёзный)."
            onClick={() => runStep0("Сохранение типа дайджеста по умолчанию…", step0ApiOpts())}
          >
            По умолчанию
          </button>
        </div>
        )}
      </div>

      <div className="card" ref={step1CardRef}>
        <h3>
          <StepTopicDot />
          Шаг 1 — кандидаты
        </h3>
        <StepProgressBar active={runningStepKey === "1"} />
        <p className="wizard-hint-do" style={{ fontSize: "0.95rem", margin: "0 0 10px" }}>
          Окно новостей (из шага 0):{" "}
          <strong>
            {newsWindowDays} {newsWindowDayKind === "working" ? "рабочих" : "календарных"}
          </strong>{" "}
          дн. — будет применено при запуске или пересборке сбора.
        </p>
        <ul className="wizard-hint-do-list">
          <li>
            При необходимости вставьте важные URL в поле ниже (каждая строка — отдельная ссылка; такие материалы должны
            попасть в итоговую пятёрку).
          </li>
          <li>
            Нажмите <strong>«Запустить сбор кандидатов»</strong> и дождитесь появления списка в блоке «Шаг 2» — не закрывайте
            вкладку до завершения.
          </li>
        </ul>
        <WizardWhy summary="Что делает сервер на шаге 1 и про повторный запуск">
          <p>
            Сервер сначала ищет реальные URL через ProxyAPI web_search (если включён), при нехватке — модели CrewAI, затем для
            каждого кандидата загружает страницу,
            проверяет статью про ИИ/нейросети, согласует заголовок с HTML и оценивает баллы — поэтому шаг занимает заметное
            время.
          </p>
          <p>
            <strong>Поле URL</strong> — для обязательных материалов; оставьте пустым, если дополнительных ссылок нет. Когда список
            кандидатов уже есть в шаге 2, для повторного сбора используйте кнопку в шаге 2:{" "}
            <strong>«Дополнить пул кандидатов»</strong> или, с галочками,{" "}
            <strong>«Пересобрать пул (оставить N)»</strong>.
          </p>
          <p>Результат сбора отображается в блоке «Шаг 2» ниже после завершения запроса.</p>
        </WizardWhy>
        {isDraft ? (
          <p className="wizard-hint-warn">
            Сначала выполните шаг 0 — пока статус <code>draft</code>, сервер не примет запуск сбора.
          </p>
        ) : null}
        {step1CollectionInProgress ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10, width: "100%" }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-start" }}>
              <WizardStepStatus
                headline="Идёт сбор кандидатов"
                phase={step1PhaseText}
                elapsedSec={step1DisplayElapsed}
                hint={
                  step1Stopping || step1Live?.cancel_requested
                    ? "Останавливаем после текущей проверки URL…"
                    : "Итеративный сбор обычно занимает 3–5 минут. Можно остановить — сохранится частичный результат."
                }
              />
              <button
                type="button"
                className="btn-rebuild"
                disabled={step1Stopping}
                title="Остановить поиск и проверку; уже найденные кандидаты сохранятся"
                onClick={() => void cancelStep1Collection()}
              >
                {step1Stopping ? "Останавливаем…" : "Остановить сбор"}
              </button>
            </div>
            <Step1LivePanel live={step1Live} />
          </div>
        ) : step1Live ? (
          <Step1LivePanel live={step1Live} finished />
        ) : null}
        {proxyapiBudgetText ? <ProxyapiBudgetAlert message={proxyapiBudgetText} compact /> : null}
        <textarea
          rows={4}
          placeholder="Необязательно: важные URL (каждый с новой строки). Эти материалы должны попасть в итоговые 5 новостей — проверьте, что ссылки открываются и ведут на статьи про ИИ."
          value={manualUrls}
          onChange={(e) => setManualUrls(e.target.value)}
        />
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
          <button
            type="button"
            disabled={!canRunStep1 || loading || step1PoolBlocksRerun}
            title={
              !canRunStep1
                ? isDraft
                  ? "Сначала шаг 0"
                  : pastStep2ForRebuild
                    ? "Пересборка пула — в шаге 2, когда виден список кандидатов"
                    : "Сбор недоступен для текущего статуса"
                : step1PoolBlocksRerun
                  ? "Пул уже собран — пересоберите в шаге 2"
                  : "Запуск поиска, проверки страниц и скоринга (без лимита по времени в браузере)"
            }
            onClick={() => runStep1(false)}
          >
            Запустить сбор кандидатов → результат в «Шаг 2» ниже
          </button>
          <button
            type="button"
            disabled={!hasStep1StatisticsData || step1CollectionInProgress}
            title="Воронка, расходы ProxyAPI, отбраковка и журнал по каждой ссылке"
            onClick={() => void openStep1Statistics()}
          >
            Статистика
          </button>
          <button
            type="button"
            disabled={discoveredNewsSorted.length === 0}
            title="Открыть полный пул новостей, найденных на шаге 1, для ручной оценки отбраковки"
            onClick={() => setShowAllFoundNews(true)}
          >
            Все найденные новости ({discoveredNewsSorted.length})
          </button>
          <button
            type="button"
            title="Порядок и включение фильтров новостей, порог воронки и число итераций поиска"
            onClick={() => void openStep1FilterSettings()}
          >
            Настройки фильтра новостей
          </button>
          <button
            type="button"
            disabled={ratingsDownloadBusy}
            title="Скачать JSON со всеми ручными оценками по всем выпускам (дата пула → запуск → оценки)"
            onClick={() => void downloadManualRatings()}
          >
            {ratingsDownloadBusy ? "Скачивание…" : "Скачать"}
          </button>
        </div>
        {ratingsDownloadError ? (
          <p className="wizard-hint-do" style={{ marginTop: 8, color: "#fca5a5" }}>
            {ratingsDownloadError}
          </p>
        ) : null}
        {showRebuildPoolButton && hasCandidatePool ? (
          <p className="wizard-hint-do" style={{ marginTop: 10, fontSize: "0.92rem" }}>
            Список кандидатов уже в блоке <strong>«Шаг 2»</strong> ниже. Чтобы обновить ленту —{" "}
            <strong>«Дополнить пул кандидатов»</strong> (все карточки остаются) или отметьте галочками нужные и нажмите{" "}
            <strong>«Пересобрать пул (оставить N)»</strong>.
          </p>
        ) : null}
      </div>

      {showStep1Statistics ? (
        <div className="step1-stats-modal-overlay" role="presentation" onClick={() => setShowStep1Statistics(false)}>
          <div
            className="step1-stats-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="step1-stats-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="step1-stats-modal-header">
              <div>
                <h3 id="step1-stats-modal-title" style={{ margin: 0 }}>
                  Статистика шага 1
                </h3>
                <p className="wizard-hint-do" style={{ margin: "6px 0 0", fontSize: "0.92rem" }}>
                  Воронка, расходы, отбраковка и журнал по каждой ссылке — для разбора, почему мало кандидатов.
                </p>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  type="button"
                  disabled={step1StatsLoading}
                  title="Пересобрать снимок из журнала в базе"
                  onClick={() => {
                    setStep1StatsLoading(true);
                    setStep1StatsError("");
                    void api
                      .getStep1Statistics(digestId, true)
                      .then((data) => setStep1StatsData(data))
                      .catch((e) =>
                        setStep1StatsError(e instanceof Error ? e.message : "Не удалось обновить статистику"),
                      )
                      .finally(() => setStep1StatsLoading(false));
                  }}
                >
                  {step1StatsLoading ? "Загрузка…" : "Обновить"}
                </button>
                <button type="button" onClick={() => setShowStep1Statistics(false)}>
                  Закрыть
                </button>
              </div>
            </div>

            {step1StatsError ? (
              <p className="wizard-hint-do" style={{ color: "#fca5a5" }}>
                {step1StatsError}
              </p>
            ) : null}

            <div className="step1-stats-modal-summary">
              <span className="news-chip ok">
                В списке: {step1StatsData?.summary?.in_pool ?? step1AuditCounts.inPool}
              </span>
              <span className="news-chip warn">
                Отбраковано: {step1StatsData?.summary?.rejected ?? step1AuditCounts.rejected}
              </span>
              <span className="news-chip">
                Проверено URL: {step1StatsData?.summary?.total_links ?? step1AuditCounts.total}
              </span>
              {step1StatsData?.generated_at ? (
                <span className="news-chip">Снимок: {formatRunWhen(step1StatsData.generated_at)}</span>
              ) : null}
            </div>

            {step1StatsData?.insights ? (
              <div className="step1-stats-insights" role="status">
                <div className="step1-stats-section-title" style={{ marginTop: 0 }}>
                  Разбор и что делать
                </div>
                <p className="step1-stats-insights-headline">{step1StatsData.insights.headline}</p>
                {Array.isArray(step1StatsData.insights.dominant_rejects) &&
                step1StatsData.insights.dominant_rejects.length > 0 ? (
                  <div className="step1-stats-dominant-list">
                    {step1StatsData.insights.dominant_rejects
                      .filter((d: { is_dominant?: boolean }) => d.is_dominant)
                      .map((d: { code: string; label: string; count: number; share_pct: number }) => (
                        <span key={d.code} className="news-chip warn step1-stats-dominant-chip" title={d.label}>
                          <strong>{d.share_pct}%</strong> · {REJECT_REASON_LABELS[d.code] ?? d.label}: {d.count}
                        </span>
                      ))}
                  </div>
                ) : null}
                {Array.isArray(step1StatsData.insights.efficiency_notes) &&
                step1StatsData.insights.efficiency_notes.length > 0 ? (
                  <ul style={{ margin: "0 0 12px", paddingLeft: "1.2rem", fontSize: "0.88rem", color: "#94a3b8" }}>
                    {step1StatsData.insights.efficiency_notes.map((note: string, i: number) => (
                      <li key={i}>{note}</li>
                    ))}
                  </ul>
                ) : null}
                {Array.isArray(step1StatsData.insights.recommendations) &&
                step1StatsData.insights.recommendations.length > 0 ? (
                  <ul className="step1-stats-rec-list">
                    {step1StatsData.insights.recommendations.map(
                      (rec: { priority: string; title: string; detail: string }, i: number) => (
                        <li
                          key={`${rec.title}-${i}`}
                          className={`step1-stats-rec-item priority-${rec.priority || "medium"}`}
                        >
                          <div className="step1-stats-rec-title">
                            {rec.priority === "high" ? "⚠ " : rec.priority === "low" ? "· " : "→ "}
                            {rec.title}
                          </div>
                          <p className="step1-stats-rec-detail">{rec.detail}</p>
                        </li>
                      ),
                    )}
                  </ul>
                ) : null}
              </div>
            ) : null}

            {(step1StatsData?.pool_collection_stats ?? poolCollection)?.last_run ||
            (step1StatsData?.pool_collection_stats ?? poolCollection)?.pool?.total ? (
              <PoolCollectionStatsPanel
                stats={(step1StatsData?.pool_collection_stats ?? poolCollection) as PoolCollectionStatsPayload}
                showHistory={Boolean(
                  ((step1StatsData?.pool_collection_stats ?? poolCollection)?.history?.length ?? 0) > 1,
                )}
              />
            ) : null}

            {((step1StatsData?.pool_collection_stats ?? poolCollection)?.step1_usage ||
              step1StatsData?.step1_collection_meta ||
              step1CollectionMeta) ? (
              <Step1UsageStatsPanel
                usage={
                  (step1StatsData?.pool_collection_stats ?? poolCollection)?.step1_usage ?? poolCollection?.step1_usage
                }
                stopReasonLabel={formatStep1StopReason(
                  (step1StatsData?.step1_collection_meta ?? step1CollectionMeta)?.stop_reason,
                )}
                releaseCostRub={digest?.release_cost_rub}
                releaseCostFinalized={releaseCostFinalized}
                topRejectReasons={(
                  Object.entries(
                    (step1StatsData?.rejected_reasons_summary ?? digest?.rejected_reasons_summary ?? {}) as Record<
                      string,
                      number
                    >,
                  ).filter(([, c]) => Number(c) > 0) as [string, number][]
                ).slice(0, 5)}
                rejectSamples={rejectAuditSamples}
              />
            ) : null}

            {rejectReasonSummary.entries.length > 0 ? (
              <>
                <div className="step1-stats-section-title">Статистика отбраковки по причинам</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {rejectReasonSummary.entries.map(([code, count]) => (
                    <button
                      key={code}
                      type="button"
                      className={step1StatsReasonFilter === code ? "news-chip warn" : "news-chip"}
                      title="Фильтр журнала по этой причине"
                      onClick={() => setStep1StatsReasonFilter((prev) => (prev === code ? "" : code))}
                    >
                      {REJECT_REASON_LABELS[code] ?? code}: {String(count)}
                    </button>
                  ))}
                  {step1StatsReasonFilter ? (
                    <button type="button" className="news-chip" onClick={() => setStep1StatsReasonFilter("")}>
                      Сбросить фильтр
                    </button>
                  ) : null}
                </div>
              </>
            ) : null}

            {step1RejectBreakdown.length > 0 ? (
              <>
                <div className="step1-stats-section-title">Счётчики фильтров шага 1</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {step1RejectBreakdown.map((x) => (
                    <span key={x.id} className="news-chip warn" title={x.label}>
                      {x.label}: {x.count}
                    </span>
                  ))}
                </div>
              </>
            ) : null}

            {step1StatsData?.curious_tone_audit &&
            Object.keys(step1StatsData.curious_tone_audit as Record<string, unknown>).length > 0 ? (
              <>
                <div className="step1-stats-section-title">Доп. аудит фильтров</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {Object.entries(step1StatsData.curious_tone_audit as Record<string, number>)
                    .filter(([, v]) => Number(v) > 0)
                    .map(([key, count]) => (
                      <span key={key} className="news-chip warn">
                        {key}: {count}
                      </span>
                    ))}
                </div>
              </>
            ) : null}

            {step1StatsData?.registry_buckets &&
            Object.keys(step1StatsData.registry_buckets as Record<string, number>).length > 0 ? (
              <>
                <div className="step1-stats-section-title">Реестр URL (общий, 90 дн.)</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {Object.entries(step1StatsData.registry_buckets as Record<string, number>).map(([bucket, count]) => (
                    <span key={bucket} className="news-chip">
                      {bucket}: {count}
                    </span>
                  ))}
                </div>
              </>
            ) : null}

            <div className="step1-stats-section-title">
              Журнал по ссылкам · показано {step1StatsLinkRows.length}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 4 }}>
              <button
                type="button"
                className={step1AuditFilter === "all" ? "news-chip ok" : "news-chip"}
                onClick={() => setStep1AuditFilter("all")}
              >
                Все ({step1StatsData?.summary?.total_links ?? step1AuditCounts.total})
              </button>
              <button
                type="button"
                className={step1AuditFilter === "in_pool" ? "news-chip ok" : "news-chip"}
                onClick={() => setStep1AuditFilter("in_pool")}
              >
                В списке ({step1StatsData?.summary?.in_pool ?? step1AuditCounts.inPool})
              </button>
              <button
                type="button"
                className={step1AuditFilter === "rejected" ? "news-chip warn" : "news-chip"}
                onClick={() => setStep1AuditFilter("rejected")}
              >
                Отбраковано ({step1StatsData?.summary?.rejected ?? step1AuditCounts.rejected})
              </button>
              <button type="button" className="news-chip" onClick={() => void copyStep1JournalSummary()}>
                {step1SummaryCopied ? "Скопировано" : "Копировать сводку"}
              </button>
            </div>
            <div className="step1-stats-links">
              {step1StatsLinkRows.map((row: any) => {
                const codes =
                  Array.isArray(row.reject_codes) && row.reject_codes.length
                    ? row.reject_codes
                    : rejectReasonCodes(String(row.verification_comment || ""));
                const labels =
                  Array.isArray(row.reject_labels) && row.reject_labels.length
                    ? row.reject_labels
                    : codes.map((x: string) => REJECT_REASON_LABELS[x] ?? x);
                const inPool = Boolean(row.in_candidate_pool || row.outcome === "in_pool");
                const verifiedOnly = row.outcome === "verified_only" || (row.page_verification_passed && !inPool);
                const rowClass = inPool ? "in-pool" : verifiedOnly ? "verified-only" : "rejected";
                return (
                  <div key={row.id ?? row.url} className={`step1-stats-link-row ${rowClass}`}>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 6 }}>
                      {inPool ? (
                        <span className="news-chip ok">В списке кандидатов</span>
                      ) : verifiedOnly ? (
                        <span className="news-chip">Проверено, не в списке</span>
                      ) : (
                        <span className="news-chip warn">Не прошло проверку</span>
                      )}
                      {row.source_stage ? <span className="news-chip">{row.source_stage}</span> : null}
                      {row.source ? <span className="news-chip">{row.source}</span> : null}
                      <span className="news-chip">{formatNewsPublishedAt(row.published_at)}</span>
                    </div>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>{row.title || "—"}</div>
                    <a
                      href={row.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ wordBreak: "break-all", fontSize: "0.88rem" }}
                    >
                      {row.url}
                    </a>
                    {!inPool && labels.length > 0 ? (
                      <p style={{ margin: "8px 0 0", fontSize: "0.88rem", color: "#e9d5ff", lineHeight: 1.45 }}>
                        <strong>Причина:</strong> {labels.join("; ")}
                      </p>
                    ) : null}
                    {row.verification_comment ? (
                      <details style={{ marginTop: 8, fontSize: "0.82rem", color: "#94a3b8" }}>
                        <summary>Технический комментарий</summary>
                        <pre style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{row.verification_comment}</pre>
                      </details>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}

      {showAllFoundNews ? (
        <div
          role="dialog"
          aria-modal="true"
          className="card"
          style={{
            position: "fixed",
            inset: 20,
            zIndex: 60,
            overflow: "auto",
            background: "rgba(15, 23, 42, 0.98)",
            borderColor: "#334155",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>Все найденные новости ({discoveredNewsSorted.length})</h3>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                disabled={ratingsDownloadBusy}
                title="JSON со всеми ручными оценками по всем выпускам"
                onClick={() => void downloadManualRatings()}
              >
                {ratingsDownloadBusy ? "Скачивание…" : "Скачать"}
              </button>
              <button type="button" onClick={() => setShowAllFoundNews(false)}>
                Закрыть
              </button>
            </div>
          </div>
          <p className="wizard-hint-do" style={{ marginTop: 0 }}>
            Оценивайте пригодность новости по шкале 1–3. Для оценок ниже 3 выберите причину — эти данные сохраняются для
            последующей калибровки поиска и фильтрации. Кнопка «Скачать» выгружает все оценки по всем выпускам, не только
            текущий пул.
          </p>
          {ratingsDownloadError ? (
            <p className="wizard-hint-do" style={{ marginTop: 0, color: "#fca5a5" }}>
              {ratingsDownloadError}
            </p>
          ) : null}
          {ratingsExportPath ? (
            <p className="wizard-hint-do" style={{ marginTop: 0, fontSize: "0.9rem" }}>
              Файл оценок: <code style={{ color: "#e2e8f0" }}>{ratingsExportPath}</code>
            </p>
          ) : null}
          <div style={{ display: "grid", gap: 10 }}>
            {discoveredNewsSorted.map((row: any) => {
              const draft = discoveredDrafts[row.id] ?? { score: "", reason: "", reasonOther: "" };
              const state = discoveredSaveState[row.id] ?? {};
              const showReason = draft.score === "1" || draft.score === "2";
              const auditCodes =
                Array.isArray(row.reject_codes) && row.reject_codes.length
                  ? row.reject_codes
                  : rejectReasonCodes(String(row.verification_comment || ""));
              return (
                <div
                  key={row.id}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(360px, 2.3fr) minmax(320px, 1fr)",
                    gap: 12,
                    border: "1px solid #334155",
                    borderRadius: 8,
                    padding: 12,
                    background: "rgba(30, 41, 59, 0.45)",
                  }}
                >
                  <div>
                    <div style={{ fontSize: "0.8rem", color: "#94a3b8", marginBottom: 4 }}>
                      {formatNewsPublishedAt(row.published_at)} · {row.source || "источник не указан"}
                    </div>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>{row.title}</div>
                    <a href={row.url} target="_blank" rel="noopener noreferrer" style={{ wordBreak: "break-all" }}>
                      {row.url}
                    </a>
                    {row.in_candidate_pool ? (
                      <span className="news-chip ok" style={{ display: "inline-block", marginTop: 8 }}>
                        В пуле кандидатов
                      </span>
                    ) : (
                      <span className="news-chip warn" style={{ display: "inline-block", marginTop: 8 }}>
                        Не в пуле
                      </span>
                    )}
                    {!row.in_candidate_pool && auditCodes.length > 0 ? (
                      <p style={{ margin: "8px 0 0", fontSize: "0.86rem", color: "#e9d5ff" }}>
                        <strong>Причина:</strong>{" "}
                        {auditCodes.map((x: string) => REJECT_REASON_LABELS[x] ?? x).join("; ")}
                      </p>
                    ) : null}
                  </div>
                  <div style={{ display: "grid", gap: 8 }}>
                    <label style={{ display: "grid", gap: 4 }}>
                      <span style={{ fontSize: "0.86rem", color: "#cbd5e1" }}>Оценка пригодности</span>
                      <select
                        value={draft.score}
                        onChange={(e) =>
                          updateDiscoveredDraft(row.id, {
                            score: e.target.value as "" | "1" | "2" | "3",
                          })
                        }
                      >
                        <option value="">— выберите —</option>
                        <option value="3">3 — подходит</option>
                        <option value="2">2 — спорно</option>
                        <option value="1">1 — не подходит</option>
                      </select>
                    </label>
                    {showReason ? (
                      <label style={{ display: "grid", gap: 4 }}>
                        <span style={{ fontSize: "0.86rem", color: "#cbd5e1" }}>Причина оценки ниже 3</span>
                        <select
                          value={draft.reason}
                          onChange={(e) =>
                            updateDiscoveredDraft(row.id, {
                              reason: e.target.value as "" | ManualScoreReason,
                            })
                          }
                        >
                          <option value="">— выберите причину —</option>
                          {MANUAL_SCORE_REASON_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : null}
                    {draft.reason === "other" ? (
                      <textarea
                        rows={2}
                        placeholder="Кратко опишите причину"
                        value={draft.reasonOther}
                        onChange={(e) => updateDiscoveredDraft(row.id, { reasonOther: e.target.value })}
                      />
                    ) : null}
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <button type="button" disabled={state.saving} onClick={() => void saveDiscoveredFeedback(row.id)}>
                        {state.saving ? "Сохраняем..." : "Сохранить оценку"}
                      </button>
                      {state.ok ? <span style={{ color: "#4ade80", fontSize: "0.86rem" }}>Сохранено</span> : null}
                    </div>
                    {state.error ? <div style={{ color: "#fca5a5", fontSize: "0.85rem" }}>{state.error}</div> : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {sourceTiersDigestType ? (
        <SourceTiersModal
          digestType={sourceTiersDigestType}
          open={showSourceTiersModal}
          onClose={() => setShowSourceTiersModal(false)}
        />
      ) : null}

      {showAppConfigModal ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="app-config-modal-title"
          className="card"
          style={{
            position: "fixed",
            inset: 24,
            zIndex: 72,
            overflow: "auto",
            background: "rgba(15, 23, 42, 0.98)",
            borderColor: "#334155",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, gap: 8, flexWrap: "wrap" }}>
            <h3 id="app-config-modal-title" style={{ margin: 0 }}>
              Настройки сервера
            </h3>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" disabled={appConfigLoading} onClick={() => void openAppConfigModal()}>
                {appConfigLoading ? "Обновляем…" : "Обновить"}
              </button>
              <button type="button" onClick={() => setShowAppConfigModal(false)}>
                Закрыть
              </button>
            </div>
          </div>
          <p className="wizard-hint-do" style={{ marginTop: 0 }}>
            Только просмотр. В <code style={{ color: "#e2e8f0" }}>backend/.env</code> обычно только{" "}
            <code style={{ color: "#e2e8f0" }}>PROXYAPI_API_KEY</code>; остальные параметры — из JSON и{" "}
            <code style={{ color: "#e2e8f0" }}>config.py</code>. Колонки «Почему так» и «Другие значения» поясняют текущий
            выбор.
          </p>
          {appConfigError ? <div style={{ color: "#fca5a5", marginBottom: 12 }}>{appConfigError}</div> : null}
          {appConfigLoading && !appConfig ? <p style={{ color: "#94a3b8" }}>Загрузка…</p> : null}
          {appConfig?.env_overrides?.length ? (
            <p style={{ color: "#94a3b8", fontSize: "0.88rem", lineHeight: 1.45 }}>
              Переопределено через <code style={{ color: "#e2e8f0" }}>.env</code>:{" "}
              {appConfig.env_overrides.join(", ")}
            </p>
          ) : null}
          {appConfig?.sections?.map((section) => (
            <section key={section.id} style={{ marginBottom: 20 }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline", marginBottom: 8 }}>
                <h4 style={{ margin: 0 }}>{section.title}</h4>
                <span style={{ color: "#64748b", fontSize: "0.85rem" }}>{section.file}</span>
              </div>
              <div style={{ overflowX: "auto", border: "1px solid #334155", borderRadius: 8 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
                  <thead>
                    <tr style={{ background: "rgba(30, 41, 59, 0.65)", textAlign: "left" }}>
                      <th style={{ padding: "8px 10px", fontWeight: 600 }}>Параметр</th>
                      <th style={{ padding: "8px 10px", fontWeight: 600 }}>Значение</th>
                      <th style={{ padding: "8px 10px", fontWeight: 600 }}>Почему так</th>
                      <th style={{ padding: "8px 10px", fontWeight: 600 }}>Другие значения</th>
                      <th style={{ padding: "8px 10px", fontWeight: 600, whiteSpace: "nowrap" }}>Источник</th>
                    </tr>
                  </thead>
                  <tbody>
                    {section.items.map((row) => (
                      <tr key={`${section.id}-${row.label}`} style={{ borderTop: "1px solid #334155" }}>
                        <td style={{ padding: "8px 10px", verticalAlign: "top", color: "#cbd5e1" }}>{row.label}</td>
                        <td style={{ padding: "8px 10px", verticalAlign: "top", wordBreak: "break-word" }}>
                          {row.value}
                          {row.hint ? (
                            <div style={{ color: "#64748b", fontSize: "0.82rem", marginTop: 4 }}>{row.hint}</div>
                          ) : null}
                        </td>
                        <td style={{ padding: "8px 10px", verticalAlign: "top", color: "#94a3b8", fontSize: "0.86rem", lineHeight: 1.45 }}>
                          {row.why_chosen || "—"}
                        </td>
                        <td style={{ padding: "8px 10px", verticalAlign: "top", color: "#94a3b8", fontSize: "0.86rem", lineHeight: 1.45 }}>
                          {row.alternatives || "—"}
                        </td>
                        <td style={{ padding: "8px 10px", verticalAlign: "top", color: "#94a3b8", whiteSpace: "nowrap" }}>
                          {row.source}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
          {appConfig?.note ? (
            <p style={{ color: "#94a3b8", fontSize: "0.88rem", marginBottom: 0 }}>{appConfig.note}</p>
          ) : null}
        </div>
      ) : null}

      {showStep1FilterSettings ? (
        <div
          role="dialog"
          aria-modal="true"
          className="card"
          style={{
            position: "fixed",
            inset: 24,
            zIndex: 70,
            overflow: "auto",
            background: "rgba(15, 23, 42, 0.98)",
            borderColor: "#334155",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>
              Настройки фильтра новостей
              {digest?.digest?.digest_type === "curious"
                ? " (курьёзный выпуск)"
                : digest?.digest?.digest_type === "serious"
                  ? " (серьёзный выпуск)"
                  : ""}
            </h3>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" disabled={step1FilterSaving || step1FilterLoading} onClick={() => void saveStep1FilterSettings()}>
                {step1FilterSaving ? "Сохраняем..." : "Сохранить"}
              </button>
              <button
                type="button"
                disabled={step1FilterSaving}
                onClick={() => {
                  setShowStep1FilterSettings(false);
                  setDraggedFilterId(null);
                }}
              >
                Закрыть
              </button>
            </div>
          </div>
          <p className="wizard-hint-do" style={{ marginTop: 0 }}>
            Перетаскивайте плашки, чтобы менять приоритет применения. У каждого фильтра есть переключатель и счётчик
            отбраковок в рамках текущего выпуска. Порог «минимум найденных страниц» задаёт воронку до отбора финальных 10
            кандидатов. Изменения вступят в силу при следующем запуске шага 1.
          </p>
          <div
            style={{
              marginBottom: 12,
              padding: "12px 14px",
              border: "1px solid #334155",
              borderRadius: 8,
              background: "rgba(30, 41, 59, 0.45)",
              display: "grid",
              gap: 8,
              maxWidth: 520,
            }}
          >
            <label style={{ display: "grid", gap: 4 }}>
              <span style={{ fontWeight: 600 }}>Минимум итераций web-поиска</span>
              <span style={{ color: "#94a3b8", fontSize: "0.88rem" }}>
                Сколько раз подряд запускать батч поиска (по 20 URL), даже если прогресс замедлился. До этого порога не
                срабатывают soft-таймаут и остановка «нет прогресса».
              </span>
              <input
                type="number"
                min={1}
                max={50}
                step={1}
                value={step1MinCollectionIterations}
                disabled={step1FilterSaving || step1FilterLoading}
                onChange={(e) =>
                  setStep1MinCollectionIterations(Math.max(1, Math.min(50, Number(e.target.value) || 5)))
                }
              />
            </label>
            <label style={{ display: "grid", gap: 4 }}>
              <span style={{ fontWeight: 600 }}>Минимум найденных страниц (воронка)</span>
              <span style={{ color: "#94a3b8", fontSize: "0.88rem" }}>
                Сколько конкретных проверенных статей нужно собрать до формирования пула из 10. Значение хранится в{" "}
                <code style={{ color: "#e2e8f0" }}>backend/app/step1_filter_settings.json</code> (общий конфиг для всех
                выпусков).
              </span>
              <input
                type="number"
                min={10}
                max={200}
                step={1}
                value={step1MinDiscoveredPages}
                disabled={step1FilterSaving || step1FilterLoading}
                onChange={(e) => setStep1MinDiscoveredPages(Math.max(10, Math.min(200, Number(e.target.value) || 20)))}
              />
            </label>
            <span style={{ color: "#94a3b8", fontSize: "0.86rem", lineHeight: 1.45, display: "block" }}>
              Журнал <strong>последнего</strong> запуска шага 1: проверено URL <strong>{step1ModalJournal.total}</strong>, в списке
              кандидатов (шаг 2): <strong>{step1ModalJournal.inPool}</strong>
              {step1ModalJournal.rejected > 0 ? (
                <>
                  , не в списке <strong>{step1ModalJournal.rejected}</strong>
                </>
              ) : null}
              . Галочки ниже — настройка на <strong>следующий</strong> сбор; цифры в колонке — что записано в журнале{" "}
              <strong>уже после последнего</strong> запуска.
            </span>
            {step1FiltersAppliedLastRun.length > 0 ? (
              <p style={{ margin: 0, fontSize: "0.84rem", color: "#cbd5e1", lineHeight: 1.45 }}>
                При <strong>последнем</strong> сборе «Дата вне окна» была{" "}
                <strong>
                  {step1FiltersAppliedLastRun.find((x) => x.id === "published_before_window")?.enabled
                    ? "включена"
                    : "выключена"}
                </strong>
                . Если сейчас выключена, а в журнале всё ещё 20 по дате — перезапустите backend после обновления кода и снова
                «Дополнить пул» или «Пересобрать пул (оставить N)».
              </p>
            ) : null}
            {step1RejectBreakdown.length > 0 ? (
              <div style={{ marginTop: 4, fontSize: "0.84rem", color: "#e2e8f0", lineHeight: 1.5 }}>
                <strong>Почему не в пул ({step1ModalJournal.rejected} URL):</strong>
                <ul style={{ margin: "6px 0 0", paddingLeft: "1.2rem" }}>
                  {step1RejectBreakdown.map((row) => (
                    <li key={row.id}>
                      {row.label}: <strong>{row.count}</strong>
                    </li>
                  ))}
                </ul>
                {step1CountersSum !== step1ModalJournal.rejected ? (
                  <span style={{ color: "#94a3b8", display: "block", marginTop: 6 }}>
                    Сумма по причинам ({step1CountersSum}) может отличаться от числа URL ({step1ModalJournal.rejected}): у одной
                    ссылки бывает несколько кодов отказа.
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
          {step1FilterError ? (
            <p className="wizard-hint-do" style={{ marginTop: 0, color: step1FilterError.includes("сохранены") ? "#86efac" : "#fca5a5" }}>
              {step1FilterError}
            </p>
          ) : null}
          {step1FilterLoading ? (
            <p className="wizard-hint-do">Загружаем фильтры…</p>
          ) : (
            <div style={{ display: "grid", gap: 10 }}>
              {step1FiltersOrdered.map((row) => (
                <div
                  key={row.id}
                  draggable
                  onDragStart={() => setDraggedFilterId(row.id)}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (draggedFilterId) reorderStep1Filters(draggedFilterId, row.id);
                    setDraggedFilterId(null);
                  }}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1.8fr minmax(150px, 0.6fr) minmax(90px, 0.4fr)",
                    gap: 10,
                    padding: 12,
                    border: "1px solid #334155",
                    borderRadius: 8,
                    background: "rgba(30, 41, 59, 0.45)",
                    cursor: "grab",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 4 }}>
                      <strong>{row.order}. {row.label_ru}</strong>
                      <span className="news-chip">{row.stage === "pre_http" ? "до HTTP" : row.stage === "verify" ? "проверка страницы" : "пул"}</span>
                    </div>
                    <div style={{ color: "#cbd5e1", fontSize: "0.9rem", lineHeight: 1.35 }}>{row.description_ru}</div>
                  </div>
                  <div style={{ display: "grid", alignContent: "start", gap: 6 }}>
                    <span style={{ color: "#94a3b8", fontSize: "0.85rem" }}>В журнале последнего сбора</span>
                    <strong
                      style={{
                        fontSize: "1.15rem",
                        color:
                          row.count > 0 && row.enabled ? "#fca5a5" : row.count > 0 && !row.enabled ? "#94a3b8" : "#86efac",
                      }}
                    >
                      {row.count}
                    </strong>
                    {row.count > 0 && !row.enabled ? (
                      <span style={{ color: "#64748b", fontSize: "0.78rem", lineHeight: 1.3 }}>
                        фильтр сейчас выкл — при следующем шаге 1 не применится
                      </span>
                    ) : null}
                  </div>
                  <div style={{ display: "grid", alignContent: "start", gap: 6 }}>
                    <span style={{ color: "#94a3b8", fontSize: "0.85rem" }}>Вкл/выкл</span>
                    <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <input
                        type="checkbox"
                        checked={row.enabled}
                        disabled={step1FilterSaving}
                        onChange={() => toggleStep1Filter(row.id)}
                      />
                      <span>{row.enabled ? "Включен" : "Выключен"}</span>
                    </label>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {showStep2Section && (
        <div className="card" ref={step2CardRef}>
          <div
            className="news-pick-toolbar"
            style={{ flexWrap: "wrap", gap: 10, alignItems: "center", justifyContent: "space-between" }}
          >
            <h3 style={{ margin: 0 }}>
              <StepTopicDot />
              Шаг 2 — выбор 5 новостей
            </h3>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
              <span className="news-pick-counter">
                Выбрано: <strong>{selected.length}</strong> / 5
                {selected.length >= 5 ? " — снимите галочку, чтобы заменить новость" : ""}
              </span>
              {showRebuildPoolButton ? (
                <button
                  type="button"
                  className="btn-rebuild"
                  disabled={loading}
                  title={step1RebuildButtonTitle(selected.length, pastStep2ForRebuild)}
                  onClick={() => runStep1(true)}
                >
                  {step1RebuildButtonLabel(selected.length)}
                </button>
              ) : null}
            </div>
          </div>
          <StepProgressBar active={runningStepKey === "2pick"} />
          {showRebuildPoolButton ? (
            <p className="wizard-hint-do" style={{ marginTop: 0, marginBottom: 12, fontSize: "0.92rem" }}>
              <strong>Дополнить пул</strong> (кнопка без галочек) — все текущие карточки остаются, ищутся новые тем же
              поиском, что при первом запуске.{" "}
              <strong>Пересобрать с закреплением</strong> — отметьте галочками, что оставить; остальные места в пуле
              заполнятся заново, снятые URL не вернутся.
              {pastStep2ForRebuild
                ? " После выбора или аналитики любой повторный сбор сбросит шаги 2–4."
                : null}
            </p>
          ) : null}
          {step1CollectionInProgress && candidatesSorted.length === 0 ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
              <p className="wizard-hint-do" style={{ fontSize: "0.95rem", margin: 0 }}>
                Идёт сбор и проверка кандидатов. Списки появятся здесь сразу после завершения — при необходимости прокрутите к
                шагу 1 или остановите сбор.
              </p>
              <button
                type="button"
                className="btn-rebuild"
                disabled={step1Stopping}
                title="Остановить поиск и проверку; уже найденные кандидаты сохранятся"
                onClick={() => void cancelStep1Collection()}
              >
                {step1Stopping ? "Останавливаем…" : "Остановить сбор"}
              </button>
            </div>
          ) : proxyapiBudgetText ? (
            <ProxyapiBudgetAlert message={proxyapiBudgetText} compact />
          ) : !canSelect && hasCandidatePool && !hasSelectableInPool ? (
            <p className="wizard-hint-wait">
              В пуле нет строк с меткой «Можно в топ‑5» — отметьте только карточки с зелёными чипами «Читаемый заголовок» и «Ссылка рабочая».
              Ослабьте фильтры в <strong>«Настройках фильтра новостей»</strong> или пересоберите пул.
            </p>
          ) : !canSelect && candidatesSorted.length === 0 && digestStatus !== "draft" ? (
            <p className="wizard-hint-wait">
              Список появится здесь после <strong>успешного</strong> шага 1 (в шапке статус <code>step_1_candidates</code>).
              Чекбоксы станут активны вместе со строками. Если выше красное предупреждение про бюджет ключа ProxyAPI —
              пополните счёт или измените лимит бюджета ключа в личном кабинете; при другой ошибке исправьте настройки и
              запустите сбор снова.
            </p>
          ) : null}
          {canSelect && candidatesSorted.length > 0 ? (
            <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
              {selectionWasSaved ? (
                <>
                  Пятёрку можно изменить в любой момент: отметьте новые галочки и нажмите{" "}
                  <strong>«Подтвердить 5 новостей»</strong>. Если аналитика или финал уже были — они сбросятся. Дальше —
                  порядок и шаг 3.
                </>
              ) : (
                <>
                  Отметьте ровно <strong>пять</strong> чекбоксов у строк с «Можно в топ‑5», затем{" "}
                  <strong>«Подтвердить 5 новостей»</strong> (ваш выбор) или <strong>«Оставь топ‑5»</strong> (лучшие по
                  баллу). Это только сохраняет состав — аналитика пойдёт после порядка ниже.
                </>
              )}
            </p>
          ) : null}
          {canAddStep2ManualUrl ? (
            <div style={{ marginBottom: 14 }}>
              <p className="wizard-hint-do" style={{ fontSize: "0.95rem", margin: "0 0 8px" }}>
                Не хватает новостей в пуле? Вставьте прямую ссылку на статью — она пройдёт ту же проверку, что и на шаге 1,
                и <strong>обязательно</strong> попадёт в итоговую пятёрку при подтверждении.
              </p>
              <textarea
                rows={2}
                placeholder="Ссылка на статью (каждая с новой строки). Должна открываться и вести на материал про ИИ."
                value={step2ManualUrls}
                onChange={(e) => setStep2ManualUrls(e.target.value)}
              />
              <div style={{ marginTop: 8 }}>
                <button
                  type="button"
                  disabled={loading || !step2ManualUrls.trim()}
                  title="Проверить страницу и добавить в список кандидатов без пересборки пула"
                  onClick={() => void addStep2ManualUrls()}
                >
                  Добавить ссылку в пул
                </button>
              </div>
            </div>
          ) : null}
          {candidatesSorted.length > 0 ? (
            poolCollection?.pool?.total || poolCollection?.last_run ? (
              <PoolCollectionStatsPanel stats={poolCollection} showHistory />
            ) : (
              <div className="wizard-hint-why-body" style={{ marginBottom: 12 }}>
                <strong>Статистика пула:</strong>{" "}
                {poolStats.total} новостей, пресс-релизы/официальные заявления — {poolStats.pressCount} (
                {(poolStats.pressShare * 100).toFixed(0)}%), российские источники — {poolStats.ruCount} (
                {(poolStats.ruShare * 100).toFixed(0)}%), максимум на источник — {poolStats.maxPerSource}, иноагенты —{" "}
                {poolStats.foreignAgentCount}, запрещённые источники — {poolStats.forbiddenCount}.
              </div>
            )
          ) : null}
          {canSelect && candidatesSorted.length > 0 ? (
            <WizardWhy summary="Как читать карточку и метки (читаемый заголовок, ссылка, топ‑5)">
              <p>
                Каждая карточка — одна новость. Карточки <strong>сгруппированы по сайту</strong> (домену): в пуле не
                больше {STEP1_POOL_MAX_PER_DOMAIN} статей с одного сайта; при выборе топ‑5 — не больше {STEP2_MAX_PER_DOMAIN}.
                У чекбокса три метки: <strong>Читаемый заголовок</strong>, <strong>Ссылка рабочая</strong>,{" "}
                <strong>Можно в топ‑5</strong> — все условия выполнены. В пятёрку отмечайте только такие строки.
              </p>
            </WizardWhy>
          ) : null}
          {step2StubsShown ? (
            <div
              role="alert"
              style={{
                marginBottom: 14,
                padding: "12px 14px",
                borderRadius: 8,
                border: "1px solid #f59e0b",
                background: "rgba(245, 158, 11, 0.12)",
                color: "#fde68a",
                fontSize: "0.92rem",
                lineHeight: 1.45,
              }}
            >
              <strong>Это не реальные новости из интернета</strong>, а учебные строки (или старый такой же набор в базе
              после сбоя разбора JSON). Запросы к LLM при этом могли выполняться — деньги списываются за ответ модели,
              даже если список в БД остался заглушкой. Чтобы увидеть настоящие материалы: проверьте{" "}
              <code style={{ color: "#e2e8f0" }}>ENABLE_WEB_FETCH=true</code> и <code style={{ color: "#e2e8f0" }}>PROXYAPI_API_KEY</code> в{" "}
              <code style={{ color: "#e2e8f0" }}>backend/.env</code>, перезапустите backend и нажмите{" "}
              <strong>«Дополнить пул кандидатов»</strong> в этом блоке (или отметьте нужные галочками — «Пересобрать пул»).
              Либо вставьте не менее 10 прямых URL на статьи (при{" "}
              <code style={{ color: "#e2e8f0" }}>ENABLE_WEB_FETCH=false</code>
              ) и снова тот же шаг.
            </div>
          ) : null}
          <div className="news-pick-list">
            {(() => {
              let listIndex = 0;
              return candidatesGroupedByDomain.map(({ host, items }) => (
                <section key={host} className="news-pick-domain-group" aria-label={`Сайт ${host}`}>
                  <div className="news-pick-domain-header">
                    <span className="news-pick-domain-name">{host}</span>
                    <span className="news-pick-domain-count">
                      {items.length} {items.length === 1 ? "статья" : items.length < 5 ? "статьи" : "статей"}
                      {items.length >= STEP1_POOL_MAX_PER_DOMAIN ? " · лимит пула" : ""}
                    </span>
                  </div>
                  <div className="news-pick-domain-items">
                    {items.map((c) => {
                      listIndex += 1;
                      const rowIndex = listIndex;
              const checked = selected.includes(c.id);
              const atMax = selected.length >= 5;
              const selectable = candidateSelectableForStep2(c);
              const hostKey = publisherHostForCandidate(c);
              const hostSelected = countSelectedOnHost(hostKey, selected, candidatesSorted);
              const domainCapReached = selectable && !checked && hostSelected >= STEP2_MAX_PER_DOMAIN;
              const canPickMore = canAddCandidateToSelection(c, selected, candidatesSorted);
              const disabled =
                (!canSelect && !checked) ||
                (atMax && !checked) ||
                (!selectable && !checked) ||
                (selectable && !checked && !canPickMore);
              const rejectCodes = rejectReasonCodes(String(c.verification_comment || ""));
              const inputId = `news-candidate-${c.id}`;
              const warnRel = String(c.reliability_status || "").includes("⚠️") || String(c.reliability_status || "").includes("сомн");
              const demoRow = looksLikeDemoCandidate(c);
              return (
                <div key={c.id} className={`news-pick-row ${checked ? "is-selected" : ""}`}>
                  <input
                    id={inputId}
                    type="checkbox"
                    disabled={disabled}
                    checked={checked}
                    title={
                      domainCapReached
                        ? `С сайта ${hostKey || "этого домена"} уже выбрано ${STEP2_MAX_PER_DOMAIN} новости — максимум для топ‑5`
                        : undefined
                    }
                    onChange={() => toggleSelected(c.id)}
                  />
                  <div className="news-pick-main">
                    <div className="news-pick-eyebrow">№{rowIndex} в списке</div>
                    <label htmlFor={inputId} className="news-pick-title-label">
                      <span className="news-pick-title">{displayCandidateTitle(c.title)}</span>
                    </label>
                    {c.url ? (
                      <div className="news-pick-url-line">
                        <a href={c.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
                          {truncateText(String(c.url), 96)}
                        </a>
                      </div>
                    ) : null}
                    <div className="news-pick-meta">
                      <span
                        className={editorialAngleChipClass(c.editorial_angle)}
                        title="Редакционный угол: деловой или курьёзный"
                      >
                        {editorialAngleLabel(c.editorial_angle)}
                      </span>
                      {c.source ? <span className="news-chip">{c.source}</span> : null}
                      {c.category || c.verification_comment || c.description ? (
                        <span className="news-chip" title="Как материал попал в пул">
                          {categoryLabel(c)}
                        </span>
                      ) : null}
                      {c.material_form && c.material_form !== "article" ? (
                        <span className="news-chip" title="Тип страницы по содержимому и URL">
                          {MATERIAL_FORM_CHIP_LABELS[String(c.material_form)] || `Форма: ${c.material_form}`}
                        </span>
                      ) : null}
                      {c.not_ad_disclosure ? (
                        <span className="news-chip ok" title="Образовательный или сервисный материал, не рекламный пост">
                          Не реклама
                        </span>
                      ) : null}
                      {c.tier ? <span className="news-chip">{c.tier}</span> : null}
                      <span className="news-chip">Балл: {c.total_score}</span>
                      <span className="news-chip">{formatNewsPublishedAt(c.published_at)}</span>
                      {headlineEditorialOk(c) ? (
                        <span className="news-chip ok">Читаемый заголовок</span>
                      ) : (
                        <span className="news-chip warn">Заголовок не готов</span>
                      )}
                      {linkOkForStep1(c) ? (
                        <span className="news-chip ok">Ссылка рабочая</span>
                      ) : (
                        <span className="news-chip warn">Ссылка не подтверждена</span>
                      )}
                      {selectable ? (
                        <span className="news-chip ok">Можно в топ‑5</span>
                      ) : (
                        <span className="news-chip warn">Нельзя выбрать в топ‑5</span>
                      )}
                      {domainCapReached ? (
                        <span className="news-chip warn" title="С одного сайта в топ‑5 можно выбрать не более 2 новостей">
                          Лимит домена ({STEP2_MAX_PER_DOMAIN})
                        </span>
                      ) : null}
                      {c.is_foreign_agent ? (
                        <span className="news-chip warn">Иноагент</span>
                      ) : (
                        <span className="news-chip ok">Не иноагент</span>
                      )}
                      {isTier5ForbiddenMedia(c) ? (
                        <span className="news-chip warn">Запрещён в РФ</span>
                      ) : c.is_aggregator ? (
                        <span className="news-chip warn">Агрегатор</span>
                      ) : String(c.reliability_status || "") === "❗ без подтверждения" ? (
                        <span className="news-chip warn">Источник без подтверждения</span>
                      ) : (
                        <span className="news-chip ok">Не запрещён в РФ</span>
                      )}
                      {c.reliability_status ? (
                        <span className={`news-chip ${warnRel ? "warn" : ""}`}>{c.reliability_status}</span>
                      ) : null}
                      {c.url ? (
                        <a href={c.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
                          Открыть в новой вкладке
                        </a>
                      ) : null}
                    </div>
                    {(c.description || c.verification_comment) && (
                      <details className="news-pick-details" open={demoRow} onClick={(e) => e.stopPropagation()}>
                        <summary>Подробнее: описание и проверка</summary>
                        {c.description ? <p className="news-pick-desc">{c.description}</p> : null}
                        {rejectCodes.length > 0 ? (
                          <p className="news-pick-verify">
                            Причины отбраковки:{" "}
                            {rejectCodes.map((x) => (REJECT_REASON_LABELS[x] ? REJECT_REASON_LABELS[x] : `\`${x}\``)).join(", ")}
                          </p>
                        ) : null}
                        {c.verification_comment ? (
                          <p className="news-pick-verify">{c.verification_comment}</p>
                        ) : null}
                      </details>
                    )}
                  </div>
                </div>
              );
                    })}
                  </div>
                </section>
              ));
            })()}
          </div>
          {showRebuildPoolButton ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 14, marginTop: 4 }}>
              <button
                type="button"
                className="btn-rebuild"
                disabled={loading}
                title={step1RebuildButtonTitle(selected.length, pastStep2ForRebuild)}
                onClick={() => runStep1(true)}
              >
                {step1RebuildButtonLabel(selected.length)}
              </button>
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              disabled={!canSelect || selected.length !== 5 || loading}
              title="Сохраняет именно те пять новостей, чьи чекбоксы отмечены"
              onClick={() => void confirmSelectedFive()}
            >
              Подтвердить 5 новостей
            </button>
            <button
              type="button"
              disabled={!canSelect || loading}
              title="Отмечает пять лучших по баллу и сохраняет состав на сервере"
              onClick={() => applyTop5AndSelect()}
            >
              Оставь топ-5
            </button>
          </div>
          <WizardWhy summary="Разница между «Подтвердить 5» и «Оставь топ‑5»">
            <p>
              <strong>Подтвердить 5</strong> — сохраняет ровно те строки, где стоят галочки. <strong>Оставь топ‑5</strong> —
              отмечает пять лучших по баллу и сразу сохраняет на сервере. Обе кнопки только фиксируют состав; блок{" "}
              <strong>порядка</strong> ниже — для расстановки и запуска аналитики.
            </p>
          </WizardWhy>
        </div>
      )}

      {digest?.selected?.length > 0 && (
        <div className="card" ref={step2OrderCardRef}>
          <h3>
            <StepTopicDot />
            Шаг 2 — порядок новостей (drag-and-drop)
          </h3>
          <StepProgressBar active={runningStepKey === "2order"} />
          <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
            {orderEditMode && canChangeOrderFromLaterSteps ? (
              <>
                Режим изменения порядка: перетащите карточки и нажмите <strong>«Применить порядок»</strong> или{" "}
                <strong>«Оптимально по мнению ИИ»</strong>. Аналитика и финал пересоберутся заново.
              </>
            ) : canChangeOrderFromLaterSteps && !canOrder ? (
              <>
                Порядок уже сохранён. Нажмите <strong>«Изменить порядок»</strong> ниже, чтобы снова перетаскивать карточки.
              </>
            ) : (
              <>
                Перетащите карточки в нужной последовательности (1 — верхняя новость выпуска) и сохраните порядок одной из
                кнопок ниже. Кнопка «Оптимально по мнению ИИ» только переставляет шаг 2; шаг 3 запускается после «Применить
                порядок».
              </>
            )}
          </p>
          {!canOrder && !canChangeOrderFromLaterSteps ? (
            <p className="wizard-hint-wait">
              Сначала сохраните пятёрку кнопкой «Подтвердить 5 новостей» или «Оставь топ‑5» — затем станет доступно
              перетаскивание.
            </p>
          ) : null}
          {digest?.step2_order_rationale ? (
            <div
              className="wizard-hint-why-body"
              style={{ marginBottom: 12, padding: "10px 12px", borderRadius: 8, background: "#0f172a" }}
            >
              <strong>Аргументация ИИ за этот порядок:</strong>
              <p style={{ margin: "8px 0 0", lineHeight: 1.45 }}>{digest.step2_order_rationale}</p>
            </div>
          ) : null}
          <WizardWhy summary="Кнопки порядка и аргументация ИИ">
            <p>
              <strong>«Оптимально по мнению ИИ»</strong> — ProxyAPI расставляет пятёрку под интерес читателя (сильный заход,
              ритм, финал) и пишет общую аргументацию плюс пояснение к каждой позиции.{" "}
              <strong>«Применить порядок»</strong> — сохраняет порядок после вашего drag-and-drop.{" "}
              <strong>«Изменить порядок»</strong> — включает редактирование после шага 3 (аналитика и финал сбросятся при
              сохранении нового порядка). Шаг 3 запускается только после кнопки <strong>«Применить порядок»</strong>.
            </p>
          </WizardWhy>
          {orderedSelectedRows.map((s: any) => (
            <div
              key={s.candidate_id}
              draggable={canOrder}
              onDragStart={() => setDraggedId(s.candidate_id)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => onDrop(s.candidate_id)}
              style={{
                marginBottom: 8,
                padding: 8,
                border: "1px dashed #334155",
                borderRadius: 8,
                background: draggedId === s.candidate_id ? "#1e293b" : "transparent",
              }}
            >
              <strong>{s.output_position}.</strong> {displayCandidateTitle(s.title)}
              {s.ordering_reason ? (
                <div style={{ marginTop: 4, fontSize: "0.88rem", color: "#94a3b8", lineHeight: 1.4 }}>
                  {s.ordering_reason}
                </div>
              ) : null}
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
            <button
              type="button"
              disabled={!canOrder || selected.length !== 5 || loading}
              title="ProxyAPI gpt-4.1-mini: порядок для удержания читателя (сильный заход, ритм, финал)"
              onClick={() =>
                run("Шаг 2: оптимальный порядок по мнению ИИ…", async () => {
                  await api.orderNewsAiOptimal(digestId);
                })
              }
            >
              Оптимально по мнению ИИ
            </button>
            <button
              type="button"
              disabled={!canOrder || selected.length !== 5 || loading}
              title="Сохранить текущий порядок карточек после перетаскивания"
              onClick={() =>
                run("Шаг 2–3: сохраняем ваш порядок и запускаем аналитику…", () =>
                  api.orderNews(
                    digestId,
                    orderedSelectedRows.map((s: { candidate_id: number }) => s.candidate_id),
                  ),
                )
              }
            >
              Применить порядок
            </button>
            {canChangeOrderFromLaterSteps && !orderEditMode ? (
              <button
                type="button"
                onClick={scrollToOrderEdit}
                disabled={loading}
                title="Снова перетаскивать карточки; при сохранении порядка аналитика и финал пересоберутся"
              >
                Изменить порядок
              </button>
            ) : null}
          </div>
        </div>
      )}

      <div ref={step3CardRef} className="card" style={{ opacity: canAnalytics ? 1 : 0.55 }}>
        <h3>
          <StepTopicDot />
          Шаг 3 — аналитика
        </h3>
        <StepProgressBar active={runningStepKey === "3"} />
        {step3InProgress ? (
          <WizardStepStatus
            headline={step3StatusHeadline}
            phase={step3PhaseText}
            elapsedSec={step3Elapsed}
            combinedWithOrder={step3ModeRef.current === "combined"}
          />
        ) : null}
        <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
          Аналитика — материал <strong>для редактора</strong> (суть, заметки, разбор по каждой новости). Обычно запускается
          после кнопки «Применить порядок» на шаге 2; дождитесь заполнения блоков ниже (несколько минут).
        </p>
        {!canOrder && !analyticsDone ? (
          <p className="wizard-hint-wait">
            Сначала сохраните пятёрку и порядок на шаге 2 — тогда аналитика стартует автоматически или её можно запустить
            кнопкой ниже.
          </p>
        ) : null}
        {analyticsDone && !loading ? (
          <p className="wizard-hint-do" style={{ fontSize: "0.95rem" }}>
            Аналитика готова. «Повторить аналитику» — пересобрать блоки по текущей пятёрке и порядку. Порядок меняют в
            блоке drag-and-drop на шаге 2 («Изменить порядок»).
          </p>
        ) : null}
        <WizardWhy summary="Что появится в результате аналитики">
          <p>
            По каждой из пяти новостей: <strong>суть</strong>, необязательная <strong>заметка</strong>,{" "}
            <strong>развёрнутый анализ</strong> для редакции; внизу — общий контекст выпуска и хэштеги. Простые тексты для
            читателей на площадках (до 450 символов без заголовка) формируются на <strong>шаге 4</strong>.
          </p>
        </WizardWhy>
        <button
          type="button"
          disabled={!canAnalytics || loading}
          title={
            analyticsDone
              ? "Пересобрать аналитику по текущей пятёрке"
              : "Запуск вручную, если автозапуск после порядка не сработал"
          }
          onClick={() =>
            run("Шаг 3: аналитика по выбранным новостям (AI, может занять несколько минут)…", () => api.confirmReady(digestId, ""))
          }
        >
          {analyticsDone ? "Повторить аналитику" : "Запустить аналитику вручную"}
        </button>
        {digest?.analytics?.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <WizardWhy summary="Как читать блоки по новостям и хэштеги">
              <p>
                Блоки шага 3 — для редактора: <strong>суть</strong>, <strong>заметка</strong>, <strong>анализ</strong> (можно
                развёрнуто). Тексты для публикации читателям — на шаге 4: коротко, простым языком, до 450 символов под
                заголовком. Хэштеги внизу — для соцсетей.
              </p>
            </WizardWhy>
            {digest.analytics.map((a: any) => (
              <div className="card" key={a.candidate_id}>
                <div>
                  <strong>{a.source_name}</strong>
                  <span> · {formatNewsPublishedAt(a.published_at)}</span>
                </div>
                <div>
                  <small style={{ color: "#94a3b8" }}>Суть (редактор)</small>
                  <div>{a.essence}</div>
                </div>
                {a.comment ? (
                  <div>
                    <small style={{ color: "#94a3b8" }}>Заметка редактора</small>
                    <div>{a.comment}</div>
                  </div>
                ) : null}
                <div>
                  <small style={{ color: "#94a3b8" }}>Анализ (редактор)</small>
                  <div>{a.analysis}</div>
                </div>
                {a.reader_text ? (
                  <div style={{ marginTop: 8 }}>
                    <small style={{ color: "#94a3b8" }}>Текст для читателя</small>
                    <div>{a.reader_text}</div>
                  </div>
                ) : null}
              </div>
            ))}
            <div>Хэштеги: {(digest.hashtags || []).join(" ")}</div>
          </div>
        )}
      </div>

      <div ref={step4CardRef} className="card" style={{ opacity: canStep4 ? 1 : 0.55 }}>
        <h3>
          <StepTopicDot />
          Шаг 4 — обложки и тексты
        </h3>
        <StepProgressBar active={step4InProgress} />
        {step4InProgress ? (
          <WizardStepStatus
            headline={step4StatusHeadline}
            phase={step4PhaseText}
            elapsedSec={step4Elapsed}
            hint={
              step4ImagesInProgress
                ? "Четыре варианта обложки — обычно 2–5 минут. Не закрывайте вкладку."
                : step4TextsInProgress
                  ? "Тексты выбранных площадок и QC — обычно 2–4 минуты. Не закрывайте вкладку."
                  : "Обложки, тексты и QC — обычно 3–6 минут. Не закрывайте вкладку."
            }
          />
        ) : null}
        {!canStep4 ? (
          <p className="wizard-hint-wait">Сначала завершите аналитику (шаг 3); затем вернитесь сюда.</p>
        ) : (
          <>
            <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
              Сначала обложки (если включены на сервере), затем тексты площадок. Текст поста и обложка публикуются
              отдельно: скопируйте текст кнопками в строке над полями в блоке «результат», обложку — скачайте или возьмите JPG из папки{" "}
              <code>images/</code> по типу выпуска. Под каждым заголовком — 2–4 простых предложения для читателей (до 450
              символов, без учёта заголовка).
            </p>
            <WizardWhy summary="Зачем раздельные действия">
              <p>
                Обложки и тексты — отдельные запросы к ИИ: можно перегенерировать картинки, не трогая посты, и наоборот.
                Выбранная AI-обложка попадает в .docx. Тексты для читателей — через ReaderCopyAgent; финальную вёрстку
                (markdown Telegram, HTML MAX/Дzen, plain VK) собирает сервер в{" "}
                <code>platform_assembly.py</code>.
              </p>
            </WizardWhy>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
              <select
                value={hookVariant}
                onChange={(e) => setHookVariant(e.target.value as "A" | "B" | "V" | "")}
                title="Тон первого абзаца для обложки и текстов"
              >
                <option value="">Авто-ротация крючка</option>
                <option value="A">A — риск/перегрев</option>
                <option value="B">B — деньги/прибыль</option>
                <option value="V">V — дефицит/ограничения</option>
              </select>
            </div>
            {digest?.enable_step4_image_generation !== true ? (
              <div className="card" style={{ marginTop: 12, padding: 12, color: "#94a3b8" }}>
                <h4>4.1 — Обложки</h4>
                <p style={{ margin: 0, fontSize: "0.92rem" }}>
                  Генерация обложек на сервере отключена. Тексты площадок (блок 4.2) доступны без AI-обложки. Для
                  публикации используйте файлы <strong>Дайджест новостей Серьезный.jpg</strong> /{" "}
                  <strong>Курьезный.jpg</strong> из папки <code>images/</code> проекта — загрузите их в редактор площадки
                  вручную, отдельно от текста.
                </p>
              </div>
            ) : (
            <div className="card" style={{ marginTop: 12, padding: 12 }}>
              <h4>4.1 — Обложки</h4>
              <button
                type="button"
                disabled={!canStep4 || loading}
                onClick={() =>
                  run("Шаг 4: обложки — генерация 4 вариантов (AI, может занять несколько минут)…", () =>
                    api.generateStep4Images(digestId, hookVariant || undefined),
                  )
                }
              >
                Сгенерировать 4 варианта обложки
              </button>
              {hasStep4Images ? (
                <div style={{ marginTop: 12 }}>
                  <p style={{ fontSize: "0.95rem", marginBottom: 8 }}>Выберите одну обложку для всех площадок:</p>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
                      gap: 12,
                    }}
                  >
                    {(digest?.image_variants || []).map((v: { variant: number; available?: boolean }) => {
                      if (v.available === false) return null;
                      const n = v.variant;
                      const selected = selectedImageVariant === n;
                      return (
                        <label
                          key={n}
                          style={{
                            cursor: loading ? "wait" : "pointer",
                            border: selected ? "2px solid #a78bfa" : "1px solid #334155",
                            borderRadius: 8,
                            padding: 6,
                            background: selected ? "rgba(167,139,250,0.12)" : "transparent",
                          }}
                        >
                          <input
                            type="radio"
                            name="cover-variant"
                            checked={selected}
                            disabled={loading}
                            onChange={() => void handleSelectImageVariant(n)}
                            style={{ marginRight: 6 }}
                          />
                          <span>Вариант {n}</span>
                          <img
                            src={assetUrl(digestId, "image", n)}
                            alt={`Обложка вариант ${n}`}
                            style={{ width: "100%", marginTop: 6, borderRadius: 4, display: "block" }}
                          />
                        </label>
                      );
                    })}
                  </div>
                  {digest?.step4_selected_image_variant ? (
                    <p style={{ marginTop: 8, color: "#a78bfa", fontSize: "0.9rem" }}>
                      Выбран вариант {digest.step4_selected_image_variant} — скачайте ниже на шаге «результат» и
                      загрузите в редактор площадки отдельно от текста.
                    </p>
                  ) : (
                    <p style={{ marginTop: 8, color: "#fbbf24", fontSize: "0.9rem" }}>
                      Обложка ещё не выбрана — отметьте вариант или используйте JPG из <code>images/</code>.
                    </p>
                  )}
                </div>
              ) : null}
            </div>
            )}

            <div className="card" style={{ marginTop: 12, padding: 12 }}>
              <h4>4.2 — Тексты площадок</h4>
              <p className="wizard-hint-do" style={{ fontSize: "0.9rem", marginTop: 0 }}>
                Сервер собирает финальный текст: Telegram (markdown), MAX и Дzen (HTML), ВК (plain text). Копирование —
                на шаге «результат»; обложка не входит в буфер.
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 10 }}>
                {PLATFORM_ORDER.map((p) => (
                  <label key={p} style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={Boolean(step4Platforms[p])}
                      disabled={loading}
                      onChange={() => setStep4Platforms((prev) => ({ ...prev, [p]: !prev[p] }))}
                    />
                    {PLATFORM_LABELS[p]}
                  </label>
                ))}
              </div>
              {digest?.enable_step4_image_generation !== false &&
              !digest?.step4_selected_image_variant &&
              hasStep4Images ? (
                <p style={{ color: "#fbbf24", fontSize: "0.9rem", marginBottom: 8 }}>
                  Рекомендуем выбрать обложку выше — она попадёт в .docx; на площадку загружайте её отдельно от текста.
                </p>
              ) : null}
              <button
                type="button"
                disabled={!canStep4 || loading || selectedPlatformsList.length === 0}
                onClick={() =>
                  run("Шаг 4: тексты — генерация для выбранных площадок (AI, может занять несколько минут)…", () =>
                    api.generateStep4Texts(digestId, [...selectedPlatformsList], hookVariant || undefined),
                  )
                }
              >
                Сгенерировать тексты ({selectedPlatformsList.length}{" "}
                {selectedPlatformsList.length === 1 ? "площадка" : "площадок"})
              </button>
            </div>
          </>
        )}

      </div>

      {showStep4Results && (
        <div className="card">
          <h3>
            <StepTopicDot />
            Шаг 4 — результат
          </h3>
          <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
            Все кнопки «Скопировать текст для …» — одной строкой над полями; вставляйте в редактор площадки (Ctrl+V).
            Обложку загружайте отдельно — ссылка ниже или <code>images/</code>. Telegram — markdown; MAX и Дzen — HTML
            (только через кнопку); ВК — plain text.
          </p>
          <div className="step4-results-toolbar">
            <div className="step4-results-toolbar-links">
              {digest?.step4_selected_image_variant || digest?.image_path ? (
                <a href={assetUrl(digestId, "image")} target="_blank" rel="noopener noreferrer">
                  Скачать обложку
                </a>
              ) : null}
              {digest?.docx_path ? (
                <a href={assetUrl(digestId, "docx")} target="_blank" rel="noopener noreferrer">
                  Скачать .docx
                </a>
              ) : null}
            </div>
            {isFinal ? (
              <div className="step4-results-finalize">
                {releaseCostFinalized ? (
                  <span className="step4-results-finalize--done">
                    Зафиксировано: {Number(digest?.total_cost_rub ?? 0).toFixed(2)} ₽
                    {digest?.release_cost_finalized_at
                      ? ` (${formatRunWhen(digest.release_cost_finalized_at)})`
                      : null}
                  </span>
                ) : (
                  <span className="wizard-hint-do" style={{ fontSize: "0.88rem", margin: 0 }}>
                    По выпуску: {Number(digest?.release_cost_rub ?? digest?.total_cost_rub ?? 0).toFixed(2)} ₽
                  </span>
                )}
                <button
                  type="button"
                  className="btn-primary"
                  disabled={releaseCostFinalized || loading}
                  onClick={() => void handleFinalizeRelease()}
                >
                  {releaseCostFinalized ? "Выпуск зафиксирован" : "Зафиксировать"}
                </button>
              </div>
            ) : null}
          </div>
          {sortedOutputs.length === 0 ? (
            <p className="wizard-hint-wait">Тексты площадок ещё не сгенерированы — отметьте площадки в блоке 4.2.</p>
          ) : null}
          {sortedOutputs.length > 0 ? (
            <div className="step4-copy-all-bar">
              <div className="step4-copy-all-buttons">
                {sortedOutputs.map((o: any) => {
                  const label = PLATFORM_LABELS[o.platform] ?? String(o.platform).toUpperCase();
                  return (
                    <button
                      key={o.platform}
                      type="button"
                      className="step4-copy-btn"
                      onClick={() => void handleCopyPlatform(o.platform, String(o.content ?? ""))}
                    >
                      Скопировать для {label}
                    </button>
                  );
                })}
                <Link href="/" className="wizard-home-btn step4-home-btn" title="Вернуться на панель выпусков">
                  На главную
                </Link>
              </div>
              {sortedOutputs.map((o: any) => {
                const label = PLATFORM_LABELS[o.platform] ?? String(o.platform).toUpperCase();
                const st = copyStatus[o.platform] ?? "idle";
                if (st === "idle") return null;
                return (
                  <p
                    key={`${o.platform}-feedback`}
                    className="step4-copy-all-feedback"
                    style={{ color: st === "ok" ? "#4ade80" : "#f87171", margin: 0 }}
                  >
                    {st === "ok"
                      ? o.platform === "max" || o.platform === "dzen"
                        ? `${label}: скопировано с форматированием — вставьте в редактор (Ctrl+V).`
                        : `${label}: скопировано — вставьте в редактор (Ctrl+V).`
                      : `${label}: не удалось записать в буфер — выделите текст в поле ниже и Ctrl+C.`}
                  </p>
                );
              })}
            </div>
          ) : null}
          {sortedOutputs.map((o: any) => {
            const label = PLATFORM_LABELS[o.platform] ?? String(o.platform).toUpperCase();
            return (
              <div key={o.platform} className="card step4-platform-card">
                <h4>{label}</h4>
                {o.platform === "max" || o.platform === "dzen" ? (
                  <p className="wizard-hint-do" style={{ fontSize: "0.9rem", marginTop: 0 }}>
                    HTML для веб-редактора {label}: жирная шапка, кликабельные заголовки, отступы между абзацами.
                    Копируйте кнопкой «Скопировать для {label}» в строке выше и вставьте в пост (Ctrl+V) — не копируйте
                    вручную из поля, иначе форматирование не сохранится.
                    {/\*\*|\\]\(/.test(String(o.content ?? "")) && !/<a\s+href=/i.test(String(o.content ?? "")) ? (
                      <span style={{ display: "block", color: "#fbbf24", marginTop: 6 }}>
                        В поле markdown вместо HTML — обновите страницу выпуска (сервер пересоберёт вёрстку) или
                        перезапустите «Шаг 4 — тексты».
                      </span>
                    ) : null}
                  </p>
                ) : o.platform === "telegram" ? (
                  <p className="wizard-hint-do" style={{ fontSize: "0.9rem", marginTop: 0 }}>
                    Markdown: жирная шапка, ссылки в заголовках новостей. Копируйте кнопкой в строке выше и вставляйте в
                    Telegram (Ctrl+V).
                  </p>
                ) : o.platform === "vk" ? (
                  <p className="wizard-hint-do" style={{ fontSize: "0.9rem", marginTop: 0 }}>
                    Plain text: заголовки CAPS, после каждой новости строка «Подробности: URL». Без markdown.
                  </p>
                ) : null}
                {(o.platform === "max" || o.platform === "dzen") && (
                  <p className="wizard-hint-wait" style={{ fontSize: "0.85rem", margin: "0 0 6px" }}>
                    {String(o.content ?? "").length} / {o.platform === "max" ? "4000" : "4096"} символов · подпись и
                    хэштеги в конце текста
                  </p>
                )}
                <textarea
                  readOnly
                  value={o.content ?? ""}
                  rows={14}
                  className={
                    o.platform === "max" || o.platform === "dzen" ? "platform-output-preview" : undefined
                  }
                  style={{
                    width: "100%",
                    boxSizing: "border-box",
                    whiteSpace: "pre-wrap",
                    fontFamily: "inherit",
                    fontSize: "0.9rem",
                    lineHeight: 1.45,
                    padding: 10,
                    borderRadius: 8,
                    border: "1px solid #334155",
                    background: "#0f172a",
                    color: "#e2e8f0",
                    resize: "vertical",
                  }}
                  spellCheck={false}
                />
              </div>
            );
          })}
          <div className="card">
            <h4>Автопроверки качества (QC)</h4>
            <WizardWhy summary="Как читать QC и что делать при предупреждениях">
              <p>
                Короткие проверки сгенерированных текстов: имя проверки, статус и комментарий модели. Если статус не «ok» —
                перечитайте текст на площадке вручную или перезапустите шаг 4 после правок в исходных данных (может потребоваться
                заново пройти цепочку с нужного шага).
              </p>
            </WizardWhy>
            {digest.checks?.map((c: any, idx: number) => (
              <div key={idx}>
                {c.check_name}: {c.status} ({c.comment})
              </div>
            ))}
          </div>
        </div>
      )}

      {digest?.llm_costs?.length > 0 && (
        <div className="card">
          <h3>Стоимость запросов</h3>
          <WizardWhy summary="Зачем эта таблица">
            <p>
              Списание по шагам (где записано): разница баланса ProxyAPI до и после операции. Полная сумма выпуска — в шапке
              «По выпуску (накопительно)» и после кнопки «Зафиксировать».
            </p>
          </WizardWhy>
          <div style={{ overflowX: "auto" }}>
            <table className="wizard-table" style={{ width: "100%", fontSize: "0.88rem" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>Агент</th>
                  <th style={{ textAlign: "left" }}>Операция</th>
                  <th style={{ textAlign: "left" }}>Модель</th>
                  <th style={{ textAlign: "right" }}>Стоимость</th>
                </tr>
              </thead>
              <tbody>
                {digest.llm_costs.map((row: any, idx: number) => (
                  <tr key={idx}>
                    <td>
                      <span style={{ color: "#94a3b8", fontSize: "0.8rem" }}>{row.agent_name}</span>
                      <br />
                      {row.agent_title_ru || row.agent_name}
                    </td>
                    <td>{row.operation_title_ru || row.request_label}</td>
                    <td>{row.model}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {row.cost_rub != null ? `${Number(row.cost_rub).toFixed(4)} ₽` : "н/д"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {digest?.model_recommendations?.length > 0 && (
        <div className="card">
          <h3>Рекомендованные модели агентов</h3>
          <WizardWhy summary="Что означают рекомендации сервера">
            <p>
              Подсказка от сервера: какие модели сейчас считаются рациональными по цене/качеству для каждого агента. Сами
              вызовы всё равно идут по фактической конфигурации backend.
            </p>
          </WizardWhy>
          {digest.model_recommendations.map((m: any, idx: number) => (
            <div key={idx} style={{ marginBottom: 8 }}>
              <strong>{m.agent_name}</strong>: {m.recommended_model} (ввод {m.input_rub_per_1m} ₽/1M, вывод{" "}
              {m.output_rub_per_1m} ₽/1M) — {m.rationale}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
