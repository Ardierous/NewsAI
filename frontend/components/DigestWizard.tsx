"use client";

import type { CSSProperties, ReactNode } from "react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AsyncProgress, StepProgressBar } from "./AsyncProgress";
import { DigestHintsAccordion } from "./DigestHintsAccordion";
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

function truncateText(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
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
  url_redirect_mismatch:
    "ссылка ведёт на другую страницу (редирект на главную или другой материал), не на заявленную новость",
  unknown_reject: "точная причина в данных не указана",
};

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

type RunningStepKey = "init" | "0" | "1" | "2pick" | "2order" | "3" | "4";

/** Соответствие текста прогресса карточке шага (для полосы у шага). */
function parseRunningStepFromLabel(label: string): RunningStepKey | null {
  const t = label.trim();
  if (!t) return null;
  if (t.includes("Загрузка выпуска")) return "init";
  if (t.includes("Сохранение типа") || t.includes("типа дайджеста")) return "0";
  if (t.includes("Шаг 1:")) return "1";
  if (t.includes("Шаг 2: применение порядка")) return "2order";
  if (t.includes("Шаг 2:")) return "2pick";
  if (t.includes("Шаг 3:")) return "3";
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
  const [finalCommand, setFinalCommand] = useState("");
  const [hookVariant, setHookVariant] = useState<"A" | "B" | "V" | "">("");
  const [draggedId, setDraggedId] = useState<number | null>(null);
  const [copyStatus, setCopyStatus] = useState<Record<string, "idle" | "ok" | "err">>({});
  const copyTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const step2CardRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollToStep2Ref = useRef(false);

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
    async (opts?: { skipProgress?: boolean; label?: string }) => {
      if (opts?.label) {
        setProgressLabel(opts.label);
        const rk = parseRunningStepFromLabel(opts.label);
        if (rk !== null) setRunningStepKey(rk);
      }
      if (!opts?.skipProgress) setLoading(true);
      try {
        setError("");
        const data = await api.getDigest(digestId);
        setDigest(data);
        if (data.selected?.length) {
          setSelected(data.selected.map((s: any) => s.candidate_id));
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
    const list = digest?.candidates as
      | { id: number; page_verified?: boolean; link_status?: boolean; headline_editorial_ok?: boolean }[]
      | undefined;
    if (!list?.length) return;
    setSelected((prev) => prev.filter((id) => {
      const row = list.find((x) => x.id === id);
      return row ? candidateSelectableForStep2(row) : false;
    }));
  }, [digest?.candidates]);

  const digestStatus = digest?.digest?.status as string | undefined;
  const isDraft = digestStatus === "draft";
  const canRunStep1 = digestStatus === "step_0" || digestStatus === "step_1_candidates";
  const canSelect = digestStatus === "step_1_candidates";
  const canOrder = digestStatus === "selected";
  const canAnalytics = digestStatus === "selected";
  const canFinal = digestStatus === "analytics_ready";
  const isFinal = digestStatus === "final_ready";

  const candidatesSorted = useMemo(
    () => [...(digest?.candidates || [])].sort((a, b) => a.original_number - b.original_number),
    [digest],
  );

  const toggleSelected = (id: number) => {
    const row = candidatesSorted.find((x: any) => x.id === id);
    if (row && !candidateSelectableForStep2(row)) return;
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 5) return prev;
      return [...prev, id];
    });
  };

  const run = async (label: string, fn: () => Promise<unknown>) => {
    if (label.includes("Шаг 1:")) {
      pendingScrollToStep2Ref.current = true;
    }
    const rk = parseRunningStepFromLabel(label);
    if (rk !== null) setRunningStepKey(rk);
    setProgressLabel(label);
    setLoading(true);
    try {
      setError("");
      await fn();
      setProgressLabel("Обновление данных…");
      await loadDigest({ skipProgress: true });
      if (pendingScrollToStep2Ref.current) {
        pendingScrollToStep2Ref.current = false;
        requestAnimationFrame(() => {
          step2CardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
      }
    } catch (e) {
      pendingScrollToStep2Ref.current = false;
      setError((e as Error).message);
      try {
        await loadDigest({ skipProgress: true });
      } catch {
        /* игнорируем вторичную ошибку загрузки */
      }
    } finally {
      setLoading(false);
      setProgressLabel("");
      setRunningStepKey(null);
    }
  };

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

  const step1CollectionInProgress = loading && progressLabel.includes("Шаг 1:");

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
      <AsyncProgress active={loading} label={progressLabel} />

      <div className="card">
        <h2 style={{ marginBottom: 6 }}>Мастер дайджеста · {formatDigestDateLabel(digest?.digest?.date)}</h2>
        <div style={{ fontSize: "0.88rem", color: "#94a3b8", marginBottom: 8 }}>
          Текущий статус: <strong style={{ color: "#e2e8f0" }}>{digest?.digest?.status ?? "…"}</strong>
          {" · "}
          Суммарно AI: {digest?.total_cost_rub != null ? `${Number(digest.total_cost_rub).toFixed(4)} ₽` : "—"}
        </div>
        <p className="wizard-hint-do">
          Идите по шагам сверху вниз: <strong>0 → 1 → 2</strong> (выбор и при желании порядок) → <strong>3 → 4</strong>. Поле
          статуса в шапке показывает, на каком этапе вы сейчас.
        </p>
        <WizardWhy summary="Что означают статус и «Суммарно AI»">
          <p>
            <strong>Статус</strong> — этап конвейера на сервере: от черновика до готового выпуска. Меняется только после
            успешных действий (кнопок). <strong>Суммарно AI</strong> — ориентировочная стоимость вызовов моделей по этому выпуску
            (учёт ProxyAPI); растёт по мере шагов с ИИ.
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

      {error && <div className="card">{error}</div>}

      {digest?.budget_notices && digest.budget_notices.length > 0 ? (
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
            {digest.budget_notices.map((msg: string, i: number) => (
              <li key={i} style={{ marginBottom: i < digest.budget_notices!.length - 1 ? 10 : 0 }}>
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

      <DigestHintsAccordion />

      <div className="card">
        <h3>Шаг 0 — тип дайджеста</h3>
        <StepProgressBar active={runningStepKey === "0" || runningStepKey === "init"} />
        <p className="wizard-hint-do">
          Нажмите <strong>одну</strong> кнопку тона и дождитесь окончания полоски загрузки. После успеха статус в шапке станет{" "}
          <code>step_0</code> — откроется шаг 1.
        </p>
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
        </WizardWhy>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("serious")}
            aria-pressed={step0Active === "serious"}
            title="Деловой нейтральный тон; рекомендуется для будничных выпусков вручную."
            onClick={() => run("Сохранение типа дайджеста: серьёзный…", () => api.step0(digestId, "serious"))}
          >
            Серьёзный
          </button>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("curious")}
            aria-pressed={step0Active === "curious"}
            title="Более лёгкий тон под курьёзные заметки; выберите осознанно."
            onClick={() => run("Сохранение типа дайджеста: курьёзный…", () => api.step0(digestId, "curious"))}
          >
            Курьёзный
          </button>
          <button
            type="button"
            disabled={loading}
            style={step0BtnStyle("default")}
            aria-pressed={step0Active === "default"}
            title="Будни → серьёзный, выходные → курьёзный; сервер решает по календарю Москвы."
            onClick={() => run("Сохранение типа дайджеста по умолчанию…", () => api.step0(digestId, undefined))}
          >
            По умолчанию
          </button>
        </div>
      </div>

      <div className="card">
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
            <strong>Поле URL</strong> — для обязательных материалов; оставьте пустым, если дополнительных ссылок нет. Повторный
            запуск шага 1 <strong>удалит</strong> прежних кандидатов, выбор и аналитику и соберёт всё заново — используйте при
            смене .env или для обновления ленты.
          </p>
          <p>Результат сбора отображается в блоке «Шаг 2» ниже после завершения запроса.</p>
        </WizardWhy>
        {isDraft ? (
          <p className="wizard-hint-warn">
            Сначала выполните шаг 0 — пока статус <code>draft</code>, сервер не примет запуск сбора.
          </p>
        ) : null}
        <textarea
          rows={4}
          placeholder="Необязательно: важные URL (каждый с новой строки). Эти материалы должны попасть в итоговые 5 новостей — проверьте, что ссылки открываются и ведут на статьи про ИИ."
          value={manualUrls}
          onChange={(e) => setManualUrls(e.target.value)}
        />
        <button
          type="button"
          disabled={!canRunStep1 || loading}
          title={
            !canRunStep1
              ? "Сначала шаг 0"
              : "Запуск поиска, проверки страниц и скоринга (долго). Повтор затирает старых кандидатов."
          }
          onClick={() =>
            run(
              "Шаг 1: поиск новостей, проверка источников и оценка кандидатов (AI, обычно 1–5 мин)…",
              () =>
                api.step1Run(
                  digestId,
                  manualUrls
                    .split("\n")
                    .map((x) => x.trim())
                    .filter(Boolean),
                ),
            )
          }
        >
          Запустить сбор кандидатов → результат в «Шаг 2» ниже
        </button>
      </div>

      {showStep2Section && (
        <div className="card" ref={step2CardRef}>
          <div className="news-pick-toolbar">
            <h3 style={{ margin: 0 }}>Шаг 2 — выбор 5 новостей</h3>
            <span className="news-pick-counter">
              Выбрано: <strong>{selected.length}</strong> / 5
              {selected.length >= 5 ? " — снимите галочку, чтобы заменить новость" : ""}
            </span>
          </div>
          <StepProgressBar active={runningStepKey === "2pick"} />
          {step1CollectionInProgress && candidatesSorted.length === 0 ? (
            <p className="wizard-hint-do" style={{ fontSize: "0.95rem" }}>
              Идёт сбор и проверка кандидатов (обычно 1–5 минут). Списки появятся здесь сразу после завершения — не уходите со
              страницы, при необходимости прокрутите к этому блоку.
            </p>
          ) : !canSelect && candidatesSorted.length === 0 && digestStatus !== "draft" ? (
            <p className="wizard-hint-wait">
              Список появится здесь после <strong>успешного</strong> шага 1 (в шапке статус <code>step_1_candidates</code>).
              Чекбоксы станут активны вместе со строками. Если выше показана ошибка (например 502) — исправьте URL или .env и
              снова запустите шаг 1.
            </p>
          ) : null}
          {canSelect && candidatesSorted.length > 0 ? (
            <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
              Отметьте ровно <strong>пять</strong> чекбоксов у строк с зелёной меткой «Можно в топ‑5», затем нажмите{" "}
              <strong>«Подтвердить 5 новостей»</strong> или <strong>«Оставь топ‑5»</strong>.
            </p>
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
              <code style={{ color: "#e2e8f0" }}>backend/.env</code>, перезапустите backend и снова нажмите{" "}
              <strong>«Запустить сбор кандидатов»</strong> в шаге 1. Либо вставьте 5–10 прямых URL на статьи (при{" "}
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
                      {c.category ? <span className="news-chip">{c.category}</span> : null}
                      {c.tier ? <span className="news-chip">{c.tier}</span> : null}
                      <span className="news-chip">Балл: {c.total_score}</span>
                      {c.published_at ? (
                        <span className="news-chip">{String(c.published_at).replace("T", " ").slice(0, 16)}</span>
                      ) : null}
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
              Порядок задаёт очередь тем в выпуске (1 — самая верхняя). После «Применить порядок» ИИ пересоберёт краткие
              обоснования позиций. Это не обязательно для кнопки «Шаг 3», но влияет на читаемость итогового текста.
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
            title="Вызов OrderingAgent: краткое обоснование порядка по каждой позиции"
            onClick={() => run("Шаг 2: применение порядка новостей (AI)…", () => api.orderNews(digestId, selected))}
          >
            Применить порядок
          </button>
        </div>
      )}

      <div className="card" style={{ opacity: canAnalytics ? 1 : 0.55 }}>
        <h3>Шаг 3 — аналитика</h3>
        <StepProgressBar active={runningStepKey === "3"} />
        <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
          После подтверждения пятёрки на шаге 2 нажмите <strong>«Запустить аналитику (шаг 3)»</strong> и дождитесь заполнения
          блоков ниже. Отдельно вводить «готово» не нужно.
        </p>
        {!canAnalytics ? (
          <p className="wizard-hint-wait">
            Сначала шаг 2 — «Подтвердить 5 новостей» или «Оставь топ-5», пока статус не станет <code>selected</code>.
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
          disabled={!canAnalytics || loading}
          title="Запуск аналитики по пяти выбранным (долго, списывается в сумму AI)"
          onClick={() =>
            run("Шаг 3: аналитика по выбранным новостям (AI, может занять несколько минут)…", () => api.confirmReady(digestId, ""))
          }
        >
          Запустить аналитику (шаг 3)
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
                  <strong>{a.source_name}</strong> | {a.published_at}
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

      <div className="card" style={{ opacity: canFinal ? 1 : 0.55 }}>
        <h3>Шаг 4 — финальная сборка</h3>
        <StepProgressBar active={runningStepKey === "4"} />
        <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
          После шага 3 выберите при желании вариант «крючка», в поле введите <strong>Ок</strong> (как в placeholder) и нажмите{" "}
          <strong>«Запустить финальную сборку (шаг 4)»</strong>.
        </p>
        {!canFinal ? (
          <p className="wizard-hint-wait">Сначала завершите аналитику (шаг 3); затем вернитесь сюда.</p>
        ) : null}
        <WizardWhy summary="Зачем именно «Ок» и когда блок активен">
          <p>
            Сбор готовых постов под площадки, обложки и проверки качества. Доступно после шага 3 (статус{" "}
            <code>analytics_ready</code>). Текст в поле — по редакционному контракту пайплайна, не произвольная фраза.
          </p>
        </WizardWhy>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
          <select
            value={hookVariant}
            onChange={(e) => setHookVariant(e.target.value as "A" | "B" | "V" | "")}
            title="Тон первого абзаца: риск, деньги или дефицит; «Авто» — ротация по выпускам"
          >
            <option value="">Авто-ротация крючка</option>
            <option value="A">A — риск/перегрев</option>
            <option value="B">B — деньги/прибыль</option>
            <option value="V">V — дефицит/ограничения</option>
          </select>
        </div>
        <input value={finalCommand} onChange={(e) => setFinalCommand(e.target.value)} placeholder="Введите: Ок" />
        <button
          type="button"
          disabled={!canFinal || loading}
          title="Долгий запрос: тексты платформ, изображение, QC"
          onClick={() =>
            run("Шаг 4: финальная сборка текста, изображения и проверки (AI, может занять несколько минут)…", () =>
              api.confirmFinal(digestId, finalCommand, hookVariant || undefined),
            )
          }
        >
          Запустить финальную сборку (шаг 4)
        </button>
      </div>

      {isFinal && (
        <div className="card">
          <h3>Шаг 4 — результат</h3>
          <p className="wizard-hint-do" style={{ fontSize: "0.98rem" }}>
            Скачайте файлы по ссылкам и для каждой площадки нажмите <strong>«Скопировать текст»</strong>, затем вставьте в
            редактор (Ctrl+V). При ошибке буфера выделите текст в поле и Ctrl+C.
          </p>
          <div style={{ marginBottom: 10 }}>
            <a href={assetUrl(digestId, "image")} target="_blank" rel="noopener noreferrer">
              Скачать изображение
            </a>{" "}
            |{" "}
            <a href={assetUrl(digestId, "docx")} target="_blank" rel="noopener noreferrer">
              Скачать .docx
            </a>
          </div>
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
              Детализация по вызовам моделей на этом выпуске: шаг, агент, метка запроса, модель и оценка в рублях по тарифам
              ProxyAPI. Полезно сверять с «Суммарно AI» в шапке и с лимитами в .env.
            </p>
          </WizardWhy>
          {digest.llm_costs.map((row: any, idx: number) => (
            <div key={idx} style={{ marginBottom: 6 }}>
              [{row.step}] {row.agent_name} / {row.request_label} / {row.model} :{" "}
              {row.cost_rub != null ? `${Number(row.cost_rub).toFixed(6)} ₽` : "н/д"}
            </div>
          ))}
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
