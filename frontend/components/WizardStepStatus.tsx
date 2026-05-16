"use client";

export type Step3ProgressMode = "combined" | "analytics";

const STEP1_PHASES: { atSec: number; text: string }[] = [
  { atSec: 0, text: "Запрос принят: снимок баланса ProxyAPI…" },
  { atSec: 3, text: "Веб-поиск URL статей (ProxyAPI / SerpAPI / Tavily)…" },
  { atSec: 25, text: "Загрузка и проверка страниц кандидатов…" },
  { atSec: 55, text: "Добор ссылок и фильтрация по теме ИИ…" },
  { atSec: 90, text: "ИИ: поиск, проверка источников, скоринг (если нужен добор)…" },
  { atSec: 150, text: "Сохранение проверенного пула в базу…" },
  { atSec: 200, text: "Почти готово — дожидаемся ответа сервера…" },
];

export function getStep1PhaseText(elapsedSec: number): string {
  return phaseFromList(STEP1_PHASES, elapsedSec);
}

const STEP3_PHASES: Record<Step3ProgressMode, { atSec: number; text: string }[]> = {
  combined: [
    { atSec: 0, text: "Сохраняем порядок пяти новостей…" },
    { atSec: 8, text: "Фиксируем обоснования позиций (OrderingAgent)…" },
    { atSec: 25, text: "Запускаем аналитику по выбранным материалам…" },
    { atSec: 45, text: "ИИ готовит суть и комментарии (примерно 1–2 из 5)…" },
    { atSec: 75, text: "ИИ пишет развёрнутый анализ (3–4 из 5)…" },
    { atSec: 105, text: "Общий вывод выпуска и хэштеги…" },
    { atSec: 135, text: "Почти готово: сохраняем результаты в базу…" },
  ],
  analytics: [
    { atSec: 0, text: "Запуск аналитики (AnalyticsAgent, ProxyAPI)…" },
    { atSec: 5, text: "Читаем материалы и готовим контекст…" },
    { atSec: 25, text: "Суть и комментарии редакции (примерно 1–2 из 5)…" },
    { atSec: 55, text: "Развёрнутый анализ по темам (3–4 из 5)…" },
    { atSec: 85, text: "Общий вывод выпуска и хэштеги…" },
    { atSec: 115, text: "Сохранение результатов в базу…" },
  ],
};

export function getStep3PhaseText(mode: Step3ProgressMode, elapsedSec: number): string {
  const phases = STEP3_PHASES[mode];
  let current = phases[0]?.text ?? "Выполняется…";
  for (const p of phases) {
    if (elapsedSec >= p.atSec) current = p.text;
  }
  return current;
}

const STEP4_IMAGES_PHASES: { atSec: number; text: string }[] = [
  { atSec: 0, text: "Промпт для обложки (ImagePromptAgent)…" },
  { atSec: 12, text: "Генерация варианта 1 из 4 (ProxyAPI)…" },
  { atSec: 45, text: "Генерация варианта 2 из 4…" },
  { atSec: 78, text: "Генерация варианта 3 из 4…" },
  { atSec: 111, text: "Генерация варианта 4 из 4…" },
  { atSec: 140, text: "Сохранение вариантов в базу…" },
];

const STEP4_TEXTS_PHASES: { atSec: number; text: string }[] = [
  { atSec: 0, text: "Тексты для выбранных площадок (PlatformWriterAgent)…" },
  { atSec: 40, text: "Проверка качества (QualityControlAgent)…" },
  { atSec: 75, text: "Сборка .docx и сохранение в базу…" },
  { atSec: 110, text: "Почти готово — дожидаемся ответа сервера…" },
];

const STEP4_PHASES: { atSec: number; text: string }[] = [
  { atSec: 0, text: "Промпт для обложки (ImagePromptAgent)…" },
  { atSec: 12, text: "Генерация изображения (ProxyAPI, обычно 30–90 с)…" },
  { atSec: 55, text: "Тексты для площадок (PlatformWriterAgent)…" },
  { atSec: 95, text: "Проверка качества (QualityControlAgent)…" },
  { atSec: 125, text: "Сборка .docx и сохранение в базу…" },
  { atSec: 160, text: "Почти готово — дожидаемся ответа сервера…" },
];

function phaseFromList(phases: { atSec: number; text: string }[], elapsedSec: number): string {
  let current = phases[0]?.text ?? "Выполняется…";
  for (const p of phases) {
    if (elapsedSec >= p.atSec) current = p.text;
  }
  return current;
}

export function getStep4ImagesPhaseText(elapsedSec: number, refreshing: boolean): string {
  if (refreshing) return "Обновляем экран: подгружаем варианты обложек…";
  return phaseFromList(STEP4_IMAGES_PHASES, elapsedSec);
}

export function getStep4TextsPhaseText(elapsedSec: number, refreshing: boolean): string {
  if (refreshing) return "Обновляем экран: подгружаем посты и проверки…";
  return phaseFromList(STEP4_TEXTS_PHASES, elapsedSec);
}

export function getStep4PhaseText(elapsedSec: number, refreshing: boolean): string {
  if (refreshing) {
    return "Обновляем экран: подгружаем посты, обложку и проверки…";
  }
  return phaseFromList(STEP4_PHASES, elapsedSec);
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec} с`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s > 0 ? `${m} мин ${s} с` : `${m} мин`;
}

type Props = {
  headline: string;
  phase: string;
  elapsedSec: number;
  /** true = шаг 2 (порядок) + шаг 3 в одном запросе */
  combinedWithOrder?: boolean;
  hint?: string;
};

/**
 * Панель длительной операции у карточки шага: заголовок, текущая фаза, таймер, полоса.
 */
export function WizardStepStatus({ headline, phase, elapsedSec, combinedWithOrder, hint: hintProp }: Props) {
  const hint =
    hintProp ??
    (combinedWithOrder
      ? "Сначала порядок, затем аналитика — обычно 2–5 минут. Не закрывайте вкладку."
      : "Аналитика по пяти новостям — обычно 2–4 минуты. Не закрывайте вкладку.");

  return (
    <div className="wizard-step-status" role="status" aria-live="polite" aria-busy="true">
      <div className="wizard-step-status-headline">{headline}</div>
      <div className="wizard-step-status-phase">{phase}</div>
      <div className="wizard-step-status-meta">
        Прошло {formatElapsed(elapsedSec)} · {hint}
      </div>
      <div className="async-progress-track wizard-step-status-track" aria-hidden>
        <div className="async-progress-bar async-progress-bar--indeterminate" />
      </div>
    </div>
  );
}
