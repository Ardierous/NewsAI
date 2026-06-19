"use client";

export type Step1LiveProgress = {
  running: boolean;
  phase: string;
  phase_key: string;
  elapsed_sec: number;
  elapsed_human: string;
  iteration: number;
  web_search_api_calls: number;
  web_search_citation_urls: number;
  web_search_cost_est_rub: number;
  urls_raw: number;
  urls_raw_merged: number;
  urls_prefilter_rejected: number;
  urls_sent_to_http: number;
  verified_pool: number;
  rejected_total: number;
  collection_target: number;
  cancel_requested: boolean;
};

type Props = {
  live: Step1LiveProgress | null;
  finished?: boolean;
};

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="step1-live-stat">
      <span className="step1-live-stat-label">{label}</span>
      <span className="step1-live-stat-value">{value}</span>
      {sub ? <span className="step1-live-stat-sub">{sub}</span> : null}
    </div>
  );
}

export function Step1LivePanel({ live, finished = false }: Props) {
  if (!live) return null;
  const isFinished = finished || !live.running;

  const httpChecked = live.urls_sent_to_http;
  const costNote =
    live.web_search_cost_est_rub > 0 ? `≈${live.web_search_cost_est_rub.toFixed(1)} ₽` : undefined;

  return (
    <div
      className={`step1-live-panel${isFinished ? " step1-live-panel--finished" : ""}`}
      role="status"
      aria-live={isFinished ? "off" : "polite"}
      aria-label={isFinished ? "Итог сбора шага 1" : "Онлайн-статистика шага 1"}
    >
      <div className="step1-live-header">
        <span className="step1-live-title">{isFinished ? "Итог сбора" : "Сейчас на сервере"}</span>
        <span className="step1-live-phase">{live.phase || (isFinished ? "Сбор завершён" : "Выполняется…")}</span>
      </div>
      <div className="step1-live-grid">
        <StatCard label="Прошло" value={live.elapsed_human || `${live.elapsed_sec} с`} />
        <StatCard label="Итерация" value={live.iteration > 0 ? live.iteration : "—"} />
        <StatCard
          label="Запросы web_search"
          value={live.web_search_api_calls}
          sub={
            live.web_search_citation_urls > 0
              ? `citations ${live.web_search_citation_urls}${costNote ? ` · ${costNote}` : ""}`
              : costNote
          }
        />
        <StatCard label="Сырые URL" value={live.urls_raw} />
        <StatCard label="На HTTP" value={httpChecked} sub={live.urls_prefilter_rejected > 0 ? `−${live.urls_prefilter_rejected} до HTTP` : undefined} />
        <StatCard label="Отбраковано" value={live.rejected_total} />
        <StatCard
          label="В пуле"
          value={live.verified_pool}
          sub={`цель ${live.collection_target}`}
        />
      </div>
      <div className="step1-live-funnel" aria-hidden>
        <span>{live.urls_raw} сырых</span>
        <span className="step1-live-funnel-arrow">→</span>
        <span>{httpChecked} HTTP</span>
        <span className="step1-live-funnel-arrow">→</span>
        <span className="step1-live-funnel-accent">{live.verified_pool} в пуле</span>
        {live.rejected_total > 0 ? (
          <>
            <span className="step1-live-funnel-muted"> · отбр. {live.rejected_total}</span>
          </>
        ) : null}
      </div>
    </div>
  );
}
