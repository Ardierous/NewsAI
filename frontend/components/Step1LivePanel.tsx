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
  rejected_links?: number;
  reject_reason_events?: number;
  collection_target: number;
  cancel_requested: boolean;
  pool_carried_over?: number;
  pool_added_this_run?: number;
  links_found_paid?: number;
  links_found_free?: number;
  links_found_total?: number;
  links_processed?: number;
  links_checked?: number;
  pool_yield_pct?: number | null;
  recheck_only?: boolean;
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

function fmtPct(value: number | null | undefined): string | null {
  if (value == null || Number.isNaN(value)) return null;
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
}

export function Step1LivePanel({ live, finished = false }: Props) {
  if (!live) return null;
  const isFinished = finished || !live.running;

  const linksFoundPaid = live.links_found_paid ?? live.web_search_citation_urls ?? live.urls_raw;
  const linksFoundFree = live.links_found_free ?? 0;
  const linksFoundTotal =
    live.links_found_total ??
    Math.max(linksFoundPaid + linksFoundFree, live.urls_sent_to_http, live.urls_raw_merged);

  const poolCarried = live.pool_carried_over ?? 0;
  const poolAdded = live.pool_added_this_run ?? Math.max(0, live.verified_pool - poolCarried);
  const rejectedLinks = live.rejected_links ?? live.rejected_total;
  const linksChecked = live.links_checked ?? live.links_processed ?? rejectedLinks + poolAdded;
  const yieldPct = live.pool_yield_pct ?? (linksChecked > 0 ? (100 * poolAdded) / linksChecked : null);
  const yieldLabel = fmtPct(yieldPct);

  const costNote =
    live.web_search_cost_est_rub > 0 ? `≈${live.web_search_cost_est_rub.toFixed(1)} ₽` : undefined;

  const poolSubParts: string[] = [`цель ${live.collection_target}`];
  if (poolCarried > 0) {
    poolSubParts.push(`${poolCarried} с прошлого запуска`);
  }
  if (poolAdded > 0) {
    poolSubParts.push(`+${poolAdded} новых`);
  }
  if (yieldLabel && poolAdded > 0) {
    poolSubParts.push(`${yieldLabel} новых прошли проверку`);
  }

  const foundSub =
    linksFoundTotal > 0
      ? `платно ${linksFoundPaid} · бесплатно ${linksFoundFree}`
      : linksChecked > 0
        ? "новый поиск не дал ссылок"
        : "платно 0 · бесплатно 0";

  const rejectSub =
    live.reject_reason_events != null && live.reject_reason_events > rejectedLinks
      ? `из ${linksChecked} проверенных · ${live.reject_reason_events} срабатываний фильтров`
      : `из ${linksChecked} проверенных`;

  const showRecheckNote =
    Boolean(live.recheck_only) || (linksChecked > linksFoundTotal && linksFoundTotal > 0 && poolCarried > 0);
  const showNoNewSearchNote = linksFoundTotal === 0 && linksChecked > 0;

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
        <span className="step1-live-elapsed">{live.elapsed_human || `${live.elapsed_sec} с`}</span>
      </div>

      <div className="step1-live-grid step1-live-grid--primary">
        <StatCard
          label="Итерации поиска"
          value={live.iteration > 0 ? live.iteration : "—"}
          sub={
            live.web_search_api_calls > 0
              ? `${live.web_search_api_calls} платн. запрос${live.web_search_api_calls === 1 ? "" : live.web_search_api_calls < 5 ? "а" : "ов"}${costNote ? ` · ${costNote}` : ""}`
              : "без платного поиска"
          }
        />
        <StatCard label="Найдено в поиске" value={linksFoundTotal} sub={foundSub} />
        <StatCard label="Отбраковано" value={rejectedLinks} sub={rejectSub} />
        <StatCard label="В пуле" value={live.verified_pool} sub={poolSubParts.join(" · ")} />
      </div>

      <div className="step1-live-funnel" aria-label="Воронка отбора ссылок">
        <div className="step1-live-funnel-step">
          <span className="step1-live-funnel-num">{linksFoundTotal}</span>
          <span className="step1-live-funnel-caption">найдено</span>
        </div>
        <span className="step1-live-funnel-arrow" aria-hidden>
          →
        </span>
        <div className="step1-live-funnel-step">
          <span className="step1-live-funnel-num">{linksChecked}</span>
          <span className="step1-live-funnel-caption">проверено</span>
        </div>
        <span className="step1-live-funnel-arrow" aria-hidden>
          →
        </span>
        <div className="step1-live-funnel-step step1-live-funnel-step--reject">
          <span className="step1-live-funnel-num">{rejectedLinks}</span>
          <span className="step1-live-funnel-caption">отбраковано</span>
        </div>
        <span className="step1-live-funnel-arrow" aria-hidden>
          →
        </span>
        <div className="step1-live-funnel-step step1-live-funnel-step--pool">
          <span className="step1-live-funnel-num">{poolAdded}</span>
          <span className="step1-live-funnel-caption">новых в пуле</span>
        </div>
      </div>

      {showNoNewSearchNote ? (
        <p className="step1-live-summary">
          В этом запуске <strong>новых ссылок из поиска не было</strong> — проверялись материалы из реестра, лент и
          прошлого сбора. Отбраковано <strong>{rejectedLinks}</strong>, в пуле сейчас <strong>{live.verified_pool}</strong>
          {poolCarried > 0 ? (
            <>
              {" "}
              (из них <strong>{poolCarried}</strong> остались с прошлого раза)
            </>
          ) : null}
          .
        </p>
      ) : poolAdded > 0 && linksChecked > 0 ? (
        <p className="step1-live-summary">
          Из <strong>{linksChecked}</strong> проверенных ссылок в пул добавлено <strong>{poolAdded}</strong> новых
          {yieldLabel ? ` (${yieldLabel})` : ""}. Всего в пуле <strong>{live.verified_pool}</strong>, цель —{" "}
          <strong>{live.collection_target}</strong>.
        </p>
      ) : linksChecked > 0 ? (
        <p className="step1-live-summary">
          Проверено <strong>{linksChecked}</strong> ссылок, в пуле <strong>{live.verified_pool}</strong> (цель —{" "}
          <strong>{live.collection_target}</strong>).
        </p>
      ) : null}

      {showRecheckNote && !showNoNewSearchNote ? (
        <p className="step1-live-summary step1-live-summary--muted">
          Часть ссылок перепроверялась с прошлого запуска — поэтому «проверено» может быть больше, чем «найдено в
          поиске».
        </p>
      ) : null}
    </div>
  );
}
