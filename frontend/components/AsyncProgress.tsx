"use client";

type Props = {
  active: boolean;
  label: string;
};

/**
 * Индикатор длительных операций: неопределённый прогресс + подпись этапа.
 */
export function AsyncProgress({ active, label }: Props) {
  if (!active) return null;
  return (
    <div className="async-progress" role="status" aria-live="polite" aria-busy="true">
      <div className="async-progress-label">{label || "Выполняется операция…"}</div>
      <div className="async-progress-track" aria-hidden>
        <div className="async-progress-bar async-progress-bar--indeterminate" />
      </div>
    </div>
  );
}

type StepProgressBarProps = {
  /** Идёт длительная операция, привязанная к этому шагу мастера */
  active: boolean;
};

/**
 * Компактная полоса у карточки шага: неактивная дорожка или неопределённый прогресс.
 */
export function StepProgressBar({ active }: StepProgressBarProps) {
  return (
    <div
      className="step-progress"
      role={active ? "progressbar" : undefined}
      aria-busy={active ? true : undefined}
      aria-valuetext={active ? "Выполняется" : undefined}
      aria-hidden={!active}
    >
      <div className={`step-progress-track${active ? "" : " step-progress-track--idle"}`}>
        {active ? <div className="async-progress-bar async-progress-bar--indeterminate" /> : null}
      </div>
    </div>
  );
}
