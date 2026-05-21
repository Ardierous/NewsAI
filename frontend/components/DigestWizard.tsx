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
};

function formatStep1StopReason(reason: string | undefined): string {
  const r = String(reason || "").trim();
  if (!r) return "—";
  const map: Record<string, string> = {
    target_reached: "цель набрана",
    soft_timeout_target_met: "soft-лимит, минимум набран",
    soft_timeout_final_attempt: "soft-лимит, финальная попытка",
    hard_timeout: "hard-лимит времени",
    budget_limit: "лимит бюджета",
    no_progress_target_met: "минимум набран, прогресс остановился",
    no_progress: "нет нового прогресса",
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

const CATEGORY_LABELS: Record<string, string> = {
  manual: "Ручная ссылка (поле URL)",
  telegram_seed: "Из Telegram",
  search: "Web-поиск",
  llm_crew: "LLM-добор",
  technology: "LLM-добор",
  analytics: "LLM-добор",
};

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
  return String(c.tier || "") === "Tier-5" && !Boolean(c.is_aggregator);
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
  const [selected, setSelected] = useState<number[]>([]);
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
  const step3CardRef = useRef<HTMLDivElement | null>(null);
  const step4CardRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollToStep2Ref = useRef(false);
  const step3ModeRef = useRef<Step3ProgressMode>("analytics");
  const [step1Elapsed, setStep1Elapsed] = useState(0);
  const [step3Elapsed, setStep3Elapsed] = useState(0);
  const [step4Elapsed, setStep4Elapsed] = useState(0);
  const [newsWindowDays, setNewsWindowDays] = useState(3);
  const [newsWindowDayKind, setNewsWindowDayKind] = useState<"calendar" | "working">("working");
  const [showAllFoundNews, setShowAllFoundNews] = useState(false);
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
        await copyPlainTextToClipboard(text);
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
    async (opts?: { skipProgress?: boolean; label?: string; preserveError?: boolean }) => {
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
        } else {
          setSelected([]);
        }
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
    const d = digest?.digest;
    if (!d) return;
    if (d.news_window_days != null) setNewsWindowDays(Number(d.news_window_days) || 3);
    const st = d.status as string | undefined;
    if (!st || st === "draft" || st === "step_0") {
      setNewsWindowDayKind("working");
      return;
    }
    const kind = d.news_window_day_kind;
    if (kind === "working" || kind === "calendar") {
      setNewsWindowDayKind(kind);
    }
  }, [digest?.digest?.news_window_days, digest?.digest?.news_window_day_kind, digest?.digest?.status]);

  useEffect(() => {
    const list = digest?.candidates as
      | { id: number; page_verified?: boolean; link_status?: boolean; headline_editorial_ok?: boolean }[]
      | undefined;
    if (!list?.length) return;
    setSelected((prev) => prev.filter((id) => {
      const row = list.find((x) => x.id === id);
      return row ? candidateSelectableForStep2(row) : false;
    }));
  }, [digest?.candidates]);

  const candidatesSorted = useMemo(
    () => [...(digest?.candidates || [])].sort((a, b) => a.original_number - b.original_number),
    [digest],
  );
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
  const canSelect =
    digestStatus === "step_1_candidates" ||
    (digestStatus === "step_0" && hasCandidatePool && hasSelectableInPool);
  const canOrder = digestStatus === "selected";
  const canAnalytics = digestStatus === "selected" || digestStatus === "analytics_ready";
  const analyticsDone = digestStatus === "analytics_ready" || digestStatus === "final_ready";
  const canStep4 = analyticsDone;
  const isFinal = digestStatus === "final_ready";
  const hasStep4Images = (digest?.image_variants?.length ?? 0) > 0;
  const hasStep4Outputs = (digest?.outputs?.length ?? 0) > 0;
  const showStep4Results = isFinal || hasStep4Images || hasStep4Outputs;

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
  const step1AuditRows = useMemo(() => {
    const rows = [...(digest?.discovered_news || [])].sort((a: any, b: any) => {
      const ap = a.in_candidate_pool ? 0 : 1;
      const bp = b.in_candidate_pool ? 0 : 1;
      if (ap !== bp) return ap - bp;
      return String(b.published_at || "").localeCompare(String(a.published_at || ""));
    });
    if (step1AuditFilter === "in_pool") return rows.filter((r: any) => r.in_candidate_pool);
    if (step1AuditFilter === "rejected") return rows.filter((r: any) => !r.in_candidate_pool);
    return rows;
  }, [digest?.discovered_news, step1AuditFilter]);

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
    if (row && !candidateSelectableForStep2(row)) return;
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 5) return prev;
      return [...prev, id];
    });
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
        await loadDigest({ skipProgress: true, preserveError: true });
      } catch {
        /* игнорируем вторичную ошибку загрузки */
      }
      setError(errMsg);
    } finally {
      setLoading(false);
      setProgressLabel("");
      setRunningStepKey(null);
    }
  };

  const runStep1 = (rebuild: boolean) => {
    const keepIds = rebuild && selected.length > 0 ? [...selected] : [];
    const partialKeep = keepIds.length > 0;
    const label = rebuild
      ? partialKeep
        ? `Шаг 1: пересборка пула (сохранить ${keepIds.length}, добрать остальные; подождите)…`
        : "Шаг 1: полная пересборка пула (поиск, проверка, оценка; подождите)…"
      : "Шаг 1: поиск новостей, проверка источников и оценка кандидатов (обычно 3–5 мин)…";
    if (rebuild) {
      const ok = window.confirm(
        partialKeep
          ? pastStep2ForRebuild
            ? `Пересобрать пул, оставив ${keepIds.length} отмеченных новостей?\n\n` +
                "Остальные позиции в списке кандидатов будут заменены: веб-поиск, проверка страниц, скоринг.\n" +
                "Сотрутся подтверждённая пятёрка, порядок, аналитика и финал — их нужно пройти заново.\n\n" +
                "Тип дайджеста (шаг 0) сохранится. Продолжить?"
            : `Пересобрать пул, оставив ${keepIds.length} отмеченных новостей?\n\n` +
                "Остальные позиции в списке кандидатов будут заменены: веб-поиск, проверка страниц, скоринг.\n" +
                "Отмеченные галочки сохранятся. Тип дайджеста (шаг 0) сохранится. Продолжить?"
          : pastStep2ForRebuild
            ? "Пересобрать пул кандидатов с нуля?\n\n" +
                "Будет заново: веб-поиск, проверка страниц, скоринг.\n" +
                "Сотрутся: выбранные 5 новостей, порядок, аналитика (шаг 3) и финальная сборка (шаг 4).\n\n" +
                "Тип дайджеста (шаг 0) сохранится. Продолжить?"
            : "Пересобрать пул кандидатов с нуля?\n\n" +
                "Текущий список в шаге 2 будет заменён: снова веб-поиск, проверка страниц и скоринг.\n" +
                "Отмеченные галочки сбросятся. Тип дайджеста (шаг 0) сохранится. Продолжить?",
      );
      if (!ok) return;
    }
    void run(label, () =>
      api.step1Run(digestId, manualUrlList, {
        rebuild,
        keep_candidate_ids: keepIds,
        news_window_days: newsWindowDays,
        news_window_day_kind: newsWindowDayKind,
      }),
    );
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
        urls_sent_to_http?: number;
        urls_prefilter_rejected?: number;
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

  const step1PhaseText = step1CollectionInProgress ? getStep1PhaseText(step1Elapsed) : "";

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

  const step0Active = useMemo((): "serious" | "curious" | "default" | null => {
    const row = digest?.digest;
    if (!row?.digest_type) return null;
    if (row.digest_type_via_default) return "default";
    if (row.digest_type === "serious") return "serious";
    if (row.digest_type === "curious") return "curious";
    return null;
  }, [digest?.digest]);

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
        <h2 style={{ marginBottom: 6 }}>Мастер дайджеста · {formatDigestDateLabel(digest?.digest?.date)}</h2>
        <div style={{ fontSize: "0.88rem", color: "#94a3b8", marginBottom: 8 }}>
          Текущий статус: <strong style={{ color: "#e2e8f0" }}>{digest?.digest?.status ?? "…"}</strong>
          {" · "}
          По выпуску: {digest?.total_cost_rub != null ? `${Number(digest.total_cost_rub).toFixed(2)} ₽` : "—"}
          {" · "}
          Сегодня ProxyAPI:{" "}
          {digest?.proxyapi_spent_today_rub != null
            ? `${Number(digest.proxyapi_spent_today_rub).toFixed(2)} ₽`
            : digest?.tracked_spend_today_rub != null
              ? `~${Number(digest.tracked_spend_today_rub).toFixed(2)} ₽ (учёт приложения)`
              : "—"}
        </div>
        <p className="wizard-hint-do">
          Идите по шагам сверху вниз: <strong>0 → 1 → 2</strong> (выбор и при желании порядок) → <strong>3 → 4</strong>. Поле
          статуса в шапке показывает, на каком этапе вы сейчас.
        </p>
        <WizardWhy summary="Что означают статус и суммы в рублях">
          <p>
            <strong>Статус</strong> — этап конвейера на сервере. <strong>По выпуску</strong> — разница баланса ProxyAPI
            с начала работы над выпуском до последнего шага (снимки до и после каждого запуска шагов 1–4).{" "}
            <strong>Сегодня ProxyAPI</strong> — разница баланса с первого запуска backend за календарный день (МСК) до
            текущего момента.
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
          (по умолчанию свёрнут — нажмите заголовок «Памятка…», чтобы раскрыть).
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

      {digest?.rejected_reasons_summary && Object.keys(digest.rejected_reasons_summary).length > 0 ? (
        <div className="card" role="status" style={{ borderColor: "#7c3aed", background: "rgba(124, 58, 237, 0.12)" }}>
          <h3 style={{ marginTop: 0, fontSize: "1.05rem", color: "#c4b5fd" }}>Статистика отбраковки ссылок (шаг 1)</h3>
          <WizardWhy summary="Как читать эти цифры">
            <p style={{ color: "#e9d5ff" }}>
              Здесь суммы по причинам, по которым ссылка не попала в итоговый пул проверенных кандидатов (поиск дал мусор,
              агрегатор, не статья, не тема ИИ и т.д.). Если одна причина доминирует — сузьте ручные URL или проверьте .env для
              поиска.
            </p>
          </WizardWhy>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {Object.entries(digest.rejected_reasons_summary).map(([code, count]) => (
              <span key={code} className="news-chip warn" title="Причина отбраковки">
                {REJECT_REASON_LABELS[code] ?? code}: {String(count)}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {step1AuditCounts.total > 0 ? (
        <div className="card" style={{ borderColor: "#475569" }}>
          <h3 style={{ marginTop: 0, fontSize: "1.05rem" }}>Журнал проверки ссылок (шаг 1)</h3>
          <p className="wizard-hint-do" style={{ marginTop: 0 }}>
            Проверено URL: <strong>{step1AuditCounts.total}</strong> · в пул кандидатов:{" "}
            <strong style={{ color: "#4ade80" }}>{step1AuditCounts.inPool}</strong> · отбраковано:{" "}
            <strong style={{ color: "#fca5a5" }}>{step1AuditCounts.rejected}</strong>
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            <button
              type="button"
              className={step1AuditFilter === "all" ? "news-chip ok" : "news-chip"}
              onClick={() => setStep1AuditFilter("all")}
            >
              Все ({step1AuditCounts.total})
            </button>
            <button
              type="button"
              className={step1AuditFilter === "in_pool" ? "news-chip ok" : "news-chip"}
              onClick={() => setStep1AuditFilter("in_pool")}
            >
              В пуле ({step1AuditCounts.inPool})
            </button>
            <button
              type="button"
              className={step1AuditFilter === "rejected" ? "news-chip warn" : "news-chip"}
              onClick={() => setStep1AuditFilter("rejected")}
            >
              Отбраковано ({step1AuditCounts.rejected})
            </button>
            <button type="button" onClick={() => setShowAllFoundNews(true)}>
              Оценки и комментарии →
            </button>
          </div>
          <div style={{ display: "grid", gap: 8, maxHeight: 420, overflow: "auto" }}>
            {step1AuditRows.map((row: any) => {
              const codes =
                Array.isArray(row.reject_codes) && row.reject_codes.length
                  ? row.reject_codes
                  : rejectReasonCodes(String(row.verification_comment || ""));
              return (
                <div
                  key={row.id}
                  style={{
                    border: "1px solid #334155",
                    borderRadius: 8,
                    padding: "10px 12px",
                    background: row.in_candidate_pool ? "rgba(34, 197, 94, 0.08)" : "rgba(248, 113, 113, 0.06)",
                  }}
                >
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 6 }}>
                    {row.in_candidate_pool ? (
                      <span className="news-chip ok">В пуле кандидатов</span>
                    ) : (
                      <span className="news-chip warn">Не в пуле</span>
                    )}
                    {row.source ? <span className="news-chip">{row.source}</span> : null}
                    <span className="news-chip">{formatNewsPublishedAt(row.published_at)}</span>
                  </div>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>{row.title}</div>
                  <a href={row.url} target="_blank" rel="noopener noreferrer" style={{ wordBreak: "break-all", fontSize: "0.88rem" }}>
                    {row.url}
                  </a>
                  {!row.in_candidate_pool && codes.length > 0 ? (
                    <p style={{ margin: "8px 0 0", fontSize: "0.88rem", color: "#e9d5ff", lineHeight: 1.45 }}>
                      <strong>Причина:</strong>{" "}
                      {codes.map((x: string) => REJECT_REASON_LABELS[x] ?? x).join("; ")}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <DigestHintsAccordion />

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
          <h3 style={{ margin: 0 }}>Шаг 0 — тип дайджеста и окно новостей</h3>
          <button type="button" disabled={loading} onClick={() => void openAppConfigModal()}>
            Настройки
          </button>
        </div>
        <StepProgressBar active={runningStepKey === "0" || runningStepKey === "init"} />
        <p className="wizard-hint-do">
          Задайте окно по дате публикации, затем нажмите <strong>одну</strong> кнопку тона и дождитесь полоски загрузки. После
          успеха статус в шапке станет <code>step_0</code> — откроется шаг 1.
        </p>
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
        <WizardWhy summary="Зачем тип важен и как ведут себя кнопки">
          <p>
            От выбора зависят промпты к ИИ на шагах 1–4 — это не просто «стиль оформления». До первого сохранения ни одна
            кнопка не подсвечена — это нормально: подсветка появится у выбранного варианта; остальные станут бледнее (их
            всё ещё можно нажать, чтобы сменить тип до шага 1).
          </p>
          <p>
            <strong>Серьёзный</strong> — нейтральный деловой стиль. <strong>Курьёзный</strong> — легче формулировки.{" "}
            <strong>По умолчанию</strong> — в будни серьёзный, в выходные курьёзный (решает сервер по календарю Москвы).
          </p>
          <p>
            <strong>Окно новостей</strong> ограничивает шаг 1: в пул попадают только материалы с датой публикации не раньше N
            дней от даты выпуска (календарных или рабочих). Слишком старые URL отсекаются с причиной{" "}
            <code>published_before_window</code>. Изменили число дней или тип дней — при следующем{" "}
            <strong>запуске или пересборке шага 1</strong> окно подставится автоматически (сохраняется в выпуск перед поиском).
          </p>
        </WizardWhy>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("serious")}
            aria-pressed={step0Active === "serious"}
            title="Деловой нейтральный тон; рекомендуется для будничных выпусков вручную."
            onClick={() =>
              run("Сохранение типа дайджеста: серьёзный…", () =>
                api.step0(digestId, {
                  digest_type: "serious",
                  news_window_days: newsWindowDays,
                  news_window_day_kind: newsWindowDayKind,
                }),
              )
            }
          >
            Серьёзный
          </button>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("curious")}
            aria-pressed={step0Active === "curious"}
            title="Более лёгкий тон под курьёзные заметки; выберите осознанно."
            onClick={() =>
              run("Сохранение типа дайджеста: курьёзный…", () =>
                api.step0(digestId, {
                  digest_type: "curious",
                  news_window_days: newsWindowDays,
                  news_window_day_kind: newsWindowDayKind,
                }),
              )
            }
          >
            Курьёзный
          </button>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("default")}
            aria-pressed={step0Active === "default"}
            title="Будни → серьёзный, выходные → курьёзный; сервер решает по календарю Москвы."
            onClick={() =>
              run("Сохранение типа дайджеста по умолчанию…", () =>
                api.step0(digestId, {
                  news_window_days: newsWindowDays,
                  news_window_day_kind: newsWindowDayKind,
                }),
              )
            }
          >
            По умолчанию
          </button>
        </div>
      </div>

      <div className="card" ref={step1CardRef}>
        <h3>Шаг 1 — кандидаты</h3>
        <StepProgressBar active={runningStepKey === "1"} />
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
            кандидатов уже есть в шаге 2, для повторного сбора используйте <strong>«Пересобрать пул кандидатов»</strong> там же.
          </p>
          <p>Результат сбора отображается в блоке «Шаг 2» ниже после завершения запроса.</p>
        </WizardWhy>
        {isDraft ? (
          <p className="wizard-hint-warn">
            Сначала выполните шаг 0 — пока статус <code>draft</code>, сервер не примет запуск сбора.
          </p>
        ) : null}
        {step1CollectionInProgress ? (
          <WizardStepStatus
            headline="Идёт сбор кандидатов"
            phase={step1PhaseText}
            elapsedSec={step1Elapsed}
            hint="Итеративный сбор обычно занимает 3–5 минут. Не закрывайте вкладку до завершения."
          />
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
            Список кандидатов уже в блоке <strong>«Шаг 2»</strong> ниже. Чтобы обновить ленту — кнопка{" "}
            <strong>«Пересобрать пул кандидатов»</strong> в шапке шага 2.
          </p>
        ) : null}
        {!step1CollectionInProgress && (poolCollection?.last_run || poolCollection?.pool?.total) ? (
          <PoolCollectionStatsPanel stats={poolCollection} showHistory={Boolean((poolCollection?.history?.length ?? 0) > 1)} />
        ) : null}
        {!step1CollectionInProgress && step1CollectionMeta ? (
          <p className="wizard-hint-do" style={{ marginTop: 8, fontSize: "0.9rem" }}>
            Итераций: <strong>{Number(step1CollectionMeta.iterations ?? 0)}</strong>
            {Number(step1CollectionMeta.min_collection_iterations ?? 0) > 0 ? (
              <>
                {" "}
                (мин. <strong>{Number(step1CollectionMeta.min_collection_iterations)}</strong>)
              </>
            ) : null}
            {" · "}
            остановка: <strong>{formatStep1StopReason(step1CollectionMeta.stop_reason)}</strong>
            {" · "}
            время: <strong>{Math.max(0, Number(step1CollectionMeta.elapsed_sec ?? 0))} c</strong>
            {" · "}
            батч: <strong>{Math.max(1, Number(step1CollectionMeta.batch_size ?? 20))}</strong>
            {" · "}
            цель воронки:{" "}
            <strong>
              {Math.max(
                10,
                Number(
                  step1CollectionMeta.collection_target_pages ??
                    step1CollectionMeta.target_max_candidates ??
                    15,
                ),
              )}
            </strong>
            {Number(step1CollectionMeta.urls_raw_merged ?? 0) > 0 ? (
              <>
                {" "}
                · воронка: сырые URL <strong>{Number(step1CollectionMeta.urls_raw_merged)}</strong>
                {", "}на HTTP <strong>{Number(step1CollectionMeta.urls_sent_to_http ?? 0)}</strong>
                {", "}отсев до HTTP <strong>{Number(step1CollectionMeta.urls_prefilter_rejected ?? 0)}</strong>
              </>
            ) : null}
          </p>
        ) : null}
      </div>

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
            <h3 style={{ margin: 0 }}>Настройки фильтра новостей</h3>
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
              Журнал <strong>последнего</strong> запуска шага 1: проверено URL <strong>{step1ModalJournal.total}</strong>, в пул:{" "}
              <strong>{step1ModalJournal.inPool}</strong>
              {step1ModalJournal.rejected > 0 ? (
                <>
                  , не в пул <strong>{step1ModalJournal.rejected}</strong>
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
                «Пересобрать пул».
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
            <h3 style={{ margin: 0 }}>Шаг 2 — выбор 5 новостей</h3>
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
                  title={
                    selected.length > 0
                      ? `Оставить ${selected.length} отмеченных новостей, остальные слоты пула (до 15) пересобрать`
                      : pastStep2ForRebuild
                        ? "Новый поиск и проверка; сброс выбора, порядка, аналитики и финала"
                        : "Заново собрать список кандидатов (поиск, проверка ссылок, скоринг)"
                  }
                  onClick={() => runStep1(true)}
                >
                  Пересобрать пул кандидатов
                </button>
              ) : null}
            </div>
          </div>
          <StepProgressBar active={runningStepKey === "2pick"} />
          {showRebuildPoolButton && pastStep2ForRebuild ? (
            <p className="wizard-hint-warn" style={{ marginTop: 0, marginBottom: 12, fontSize: "0.92rem" }}>
              Выпуск уже прошёл выбор или аналитику. Пересборка пула сбросит шаги 2–4 — затем снова отметьте пятёрку и пройдите
              порядок и аналитику.
            </p>
          ) : null}
          {step1CollectionInProgress && candidatesSorted.length === 0 ? (
            <p className="wizard-hint-do" style={{ fontSize: "0.95rem" }}>
              Идёт сбор и проверка кандидатов (время не ограничено в браузере). Списки появятся здесь сразу после завершения — не уходите со
              страницы, при необходимости прокрутите к этому блоку.
            </p>
          ) : proxyapiBudgetText ? (
            <ProxyapiBudgetAlert message={proxyapiBudgetText} compact />
          ) : !canSelect && hasCandidatePool && digestStatus === "step_0" && !hasSelectableInPool ? (
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
              Отметьте ровно <strong>пять</strong> чекбоксов у строк с зелёной меткой «Можно в топ‑5», затем нажмите{" "}
              <strong>«Подтвердить 5 новостей»</strong> или <strong>«Оставь топ‑5»</strong>.
            </p>
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
                Каждая карточка — одна новость: заголовок, ссылка, источник, Tier, балл. У чекбокса три метки:{" "}
                <strong>Читаемый заголовок</strong>, <strong>Ссылка рабочая</strong>, <strong>Можно в топ‑5</strong> — все условия
                выполнены (тема ИИ, надёжность источника и т.д.). В пятёрку отмечайте только такие строки; серые чекбоксы —
                смотрите «Подробнее» у строки и цветные чипы.
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
              <strong>«Пересобрать пул кандидатов»</strong> в этом блоке. Либо вставьте не менее 10 прямых URL на статьи (при{" "}
              <code style={{ color: "#e2e8f0" }}>ENABLE_WEB_FETCH=false</code>
              ) и снова тот же шаг.
            </div>
          ) : null}
          <div className="news-pick-list">
            {candidatesSorted.map((c) => {
              const checked = selected.includes(c.id);
              const atMax = selected.length >= 5;
              const selectable = candidateSelectableForStep2(c);
              const disabled = (!canSelect && !checked) || (atMax && !checked) || (!selectable && !checked);
              const rejectCodes = rejectReasonCodes(String(c.verification_comment || ""));
              const inputId = `news-candidate-${c.id}`;
              const warnRel = String(c.reliability_status || "").includes("⚠️") || String(c.reliability_status || "").includes("сомн");
              const host = c.url ? hostFromUrl(String(c.url)) : "";
              const demoRow = looksLikeDemoCandidate(c);
              return (
                <div key={c.id} className={`news-pick-row ${checked ? "is-selected" : ""}`}>
                  <input
                    id={inputId}
                    type="checkbox"
                    disabled={disabled}
                    checked={checked}
                    onChange={() => toggleSelected(c.id)}
                  />
                  <div className="news-pick-main">
                    <div className="news-pick-eyebrow">Кандидат №{c.original_number}</div>
                    <label htmlFor={inputId} className="news-pick-title-label">
                      <span className="news-pick-title">{c.title}</span>
                    </label>
                    {c.url ? (
                      <div
                        style={{
                          marginTop: 6,
                          fontSize: "0.82rem",
                          color: "#93c5fd",
                          wordBreak: "break-word",
                          lineHeight: 1.35,
                        }}
                      >
                        {host ? <span style={{ color: "#cbd5e1" }}>{host}</span> : null}
                        {host ? " · " : null}
                        <a href={c.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
                          {truncateText(String(c.url), 96)}
                        </a>
                      </div>
                    ) : null}
                    <div className="news-pick-meta">
                      {c.source ? <span className="news-chip">{c.source}</span> : null}
                      {c.category || c.verification_comment || c.description ? (
                        <span className="news-chip" title="Как материал попал в пул">
                          {categoryLabel(c)}
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
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              disabled={!canSelect || selected.length !== 5 || loading}
              title="Сохраняет именно те пять новостей, чьи чекбоксы отмечены"
              onClick={() => run("Шаг 2: сохранение выбранных пяти новостей…", () => api.selectNews(digestId, selected, false))}
            >
              Подтвердить 5 новостей
            </button>
            <button
              type="button"
              disabled={!canSelect || loading}
              title="Снимает ручной выбор и берёт пять лучших по баллу из проверенных кандидатов"
              onClick={() => run("Шаг 2: выбор топ-5 по рейтингу…", () => api.selectNews(digestId, [], true))}
            >
              Оставь топ-5
            </button>
          </div>
          <WizardWhy summary="Разница между «Подтвердить 5» и «Оставь топ‑5»">
            <p>
              <strong>Подтвердить 5</strong> активна только при пяти галочках — вы управляете составом.{" "}
              <strong>Оставь топ‑5</strong> — доверить выбор системе по рейтингу. После успеха статус станет <code>selected</code>{" "}
              — откроется блок перетаскивания порядка и шаг 3.
            </p>
          </WizardWhy>
        </div>
      )}

      {digest?.selected?.length > 0 && (
        <div className="card">
          <h3>Шаг 2 — порядок новостей (drag-and-drop)</h3>
          <StepProgressBar active={runningStepKey === "2order"} />
          <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
            Перетащите карточки в нужном порядке и нажмите <strong>«Применить порядок»</strong>.
          </p>
          {!canOrder ? (
            <p className="wizard-hint-wait">
              Кнопка станет активной, когда сервер зафиксирует пятёрку (статус <code>selected</code>) — обычно сразу после
              «Подтвердить 5» / «Оставь топ-5».
            </p>
          ) : null}
          <WizardWhy summary="Зачем менять порядок до шага 3">
            <p>
              Порядок задаёт очередь тем в выпуске (1 — самая верхняя).{" "}
              <strong>«Оптимально по мнению ИИ»</strong> расставит пятёрку с упором на интерес читателя и сохранит
              обоснования позиций, затем <strong>автоматически запускается аналитика (шаг 3)</strong>.{" "}
              <strong>«Применить порядок»</strong> — сохранить ваш порядок после перетаскивания.
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
              <strong>{s.output_position}.</strong> {s.title}
              <div>{s.ordering_reason}</div>
            </div>
          ))}
          <button
            type="button"
            disabled={!canOrder || selected.length !== 5 || loading}
            title="ProxyAPI gpt-4.1-mini: порядок для удержания читателя (сильный заход, ритм, финал)"
            onClick={() =>
              run("Шаг 2–3: оптимальный порядок и аналитика (AI, несколько минут)…", async () => {
                await api.orderNewsAiOptimal(digestId);
              })
            }
          >
            Оптимально по мнению ИИ
          </button>
          <button
            type="button"
            disabled={!canOrder || selected.length !== 5 || loading}
            style={{ marginLeft: 10 }}
            title="Сохранить текущий порядок карточек после перетаскивания"
            onClick={() =>
              run("Шаг 2–3: порядок и аналитика (AI, несколько минут)…", () => api.orderNews(digestId, selected))
            }
          >
            Применить порядок
          </button>
        </div>
      )}

      <div ref={step3CardRef} className="card" style={{ opacity: canAnalytics ? 1 : 0.55 }}>
        <h3>Шаг 3 — аналитика</h3>
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
          После <strong>«Применить порядок»</strong> или <strong>«Оптимально по мнению ИИ»</strong> аналитика запускается
          автоматически — дождитесь заполнения блоков ниже (обычно несколько минут).
        </p>
        {!canOrder && !analyticsDone ? (
          <p className="wizard-hint-wait">
            Сначала подтвердите пятёрку («Подтвердить 5» / «Оставь топ-5»), затем сохраните порядок — аналитика пойдёт сама.
          </p>
        ) : null}
        {analyticsDone && !loading ? (
          <p className="wizard-hint-do" style={{ fontSize: "0.95rem" }}>
            Аналитика уже готова. Кнопка ниже — только если нужно пересобрать блоки заново.
          </p>
        ) : null}
        <WizardWhy summary="Что появится в результате аналитики">
          <p>
            Для каждой из пяти новостей: суть, комментарий редакции, развёрнутый анализ; плюс общий контекст и хэштеги внизу
            блока.
          </p>
        </WizardWhy>
        <button
          type="button"
          disabled={!canAnalytics || loading || (!analyticsDone && canOrder)}
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
                По одному блоку на новость: <strong>суть</strong> (коротко), <strong>комментарий</strong> редакции,{" "}
                <strong>анализ</strong> (развёрнуто). Хэштеги внизу — для соцсетей.
              </p>
            </WizardWhy>
            {digest.analytics.map((a: any) => (
              <div className="card" key={a.candidate_id}>
                <div>
                  <strong>{a.source_name}</strong>
                  <span> · {formatNewsPublishedAt(a.published_at)}</span>
                </div>
                <div>{a.essence}</div>
                <div>{a.comment}</div>
                <div>{a.analysis}</div>
              </div>
            ))}
            <div>Хэштеги: {(digest.hashtags || []).join(" ")}</div>
          </div>
        )}
      </div>

      <div ref={step4CardRef} className="card" style={{ opacity: canStep4 ? 1 : 0.55 }}>
        <h3>Шаг 4 — обложки и тексты</h3>
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
              Сначала сгенерируйте <strong>4 варианта обложки</strong> и выберите одну для всех площадок. Затем отметьте
              нужные площадки и нажмите кнопку генерации текстов.
            </p>
            <WizardWhy summary="Зачем раздельные действия">
              <p>
                Обложки и тексты — отдельные запросы к AI: можно перегенерировать картинки, не трогая посты, и наоборот.
                Выбранная обложка копируется в финальный файл и попадает в .docx.
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
                  Генерация обложек временно отключена на сервере. Шаг 4.2 (тексты площадок) доступен без обложки.
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
                      Выбран вариант {digest.step4_selected_image_variant} — используется для экспорта и .docx.
                    </p>
                  ) : (
                    <p style={{ marginTop: 8, color: "#fbbf24", fontSize: "0.9rem" }}>
                      Обложка ещё не выбрана — выберите вариант перед публикацией.
                    </p>
                  )}
                </div>
              ) : null}
            </div>
            )}

            <div className="card" style={{ marginTop: 12, padding: 12 }}>
              <h4>4.2 — Тексты площадок</h4>
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
                  Рекомендуем выбрать обложку выше — в .docx попадёт выбранный вариант.
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
          <h3>Шаг 4 — результат</h3>
          <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
            Скачайте файлы по ссылкам и для каждой площадки нажмите <strong>«Скопировать текст»</strong>, затем вставьте в
            редактор (Ctrl+V). При ошибке буфера выделите текст в поле и Ctrl+C.
          </p>
          <div style={{ marginBottom: 10 }}>
            {digest?.step4_selected_image_variant || digest?.image_path ? (
              <a href={assetUrl(digestId, "image")} target="_blank" rel="noopener noreferrer">
                Скачать выбранную обложку
              </a>
            ) : null}
            {digest?.step4_selected_image_variant || digest?.image_path ? " | " : null}
            {digest?.docx_path ? (
              <a href={assetUrl(digestId, "docx")} target="_blank" rel="noopener noreferrer">
                Скачать .docx
              </a>
            ) : null}
          </div>
          {sortedOutputs.length === 0 ? (
            <p className="wizard-hint-wait">Тексты площадок ещё не сгенерированы — отметьте площадки в блоке 4.2.</p>
          ) : null}
          {sortedOutputs.map((o: any) => {
            const label = PLATFORM_LABELS[o.platform] ?? String(o.platform).toUpperCase();
            const st = copyStatus[o.platform] ?? "idle";
            return (
              <div key={o.platform} className="card">
                <h4>{label}</h4>
                <textarea
                  readOnly
                  value={o.content ?? ""}
                  rows={14}
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
                <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
                  <button type="button" onClick={() => void handleCopyPlatform(o.platform, String(o.content ?? ""))}>
                    Скопировать текст для {label}
                  </button>
                  {st === "ok" ? (
                    <span style={{ color: "#4ade80", fontSize: "0.9rem" }}>Скопировано — вставьте в редактор площадки (Ctrl+V).</span>
                  ) : null}
                  {st === "err" ? (
                    <span style={{ color: "#f87171", fontSize: "0.9rem" }}>
                      Не удалось записать в буфер. Выделите текст в поле выше и нажмите Ctrl+C.
                    </span>
                  ) : null}
                </div>
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
              Списание по шагам выпуска: разница баланса ProxyAPI до и после операции. Сверяйте с суммой «По выпуску» в шапке.
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
