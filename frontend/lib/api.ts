const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/** Длинные AI-операции (шаги 3, 4): опциональный таймаут. Шаг 1 без лимита — сбор пула может идти долго. */
const LONG_POST_MS = 60 * 60 * 1000;

type RequestOptions = RequestInit & { timeoutMs?: number };

function createTimeoutSignal(ms: number): { signal: AbortSignal; cancel: () => void } {
  const controller = new AbortController();
  const min = Math.max(1, Math.round(ms / 60_000));
  const id = setTimeout(() => {
    controller.abort(new Error(`Превышено время ожидания ответа сервера (${min} мин).`));
  }, ms);
  return {
    signal: controller.signal,
    cancel: () => clearTimeout(id),
  };
}

async function readErrorMessage(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const j = JSON.parse(text) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string" && d.trim()) return d.trim();
    if (Array.isArray(d)) {
      const msgs = d
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item && typeof (item as { msg?: string }).msg === "string") {
            return (item as { msg: string }).msg;
          }
          return typeof item === "string" ? item : JSON.stringify(item);
        })
        .filter(Boolean);
      if (msgs.length) return msgs.join("; ");
    }
    if (d != null && typeof d !== "object") return String(d);
  } catch {
    /* не JSON */
  }
  const cut = text.trim().slice(0, 400);
  return cut || `Ошибка API (${res.status})`;
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { timeoutMs, signal: userSignal, ...restInit } = init ?? {};
  let cancelTimeout: (() => void) | undefined;
  let signal = userSignal;
  if (timeoutMs && timeoutMs > 0 && !userSignal) {
    const t = createTimeoutSignal(timeoutMs);
    signal = t.signal;
    cancelTimeout = t.cancel;
  }
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...restInit,
      signal,
      headers: {
        "Content-Type": "application/json",
        ...(restInit.headers || {}),
      },
      cache: "no-store",
    });
    if (!res.ok) {
      const msg = await readErrorMessage(res);
      throw new Error(msg);
    }
    return res.json() as Promise<T>;
  } finally {
    cancelTimeout?.();
  }
}

export const api = {
  listDigests: () => request<any[]>("/digests"),
  getAppConfig: () =>
    request<{
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
    }>("/config"),
  createTodayDigest: () => request<any>("/digests/create", { method: "POST" }),
  getDigest: (id: number) => request<any>(`/digests/${id}`),
  step0: (
    id: number,
    opts?: {
      digest_type?: "serious" | "curious";
      news_window_days?: number;
      news_window_day_kind?: "calendar" | "working";
    },
  ) =>
    request<any>(`/digests/${id}/step0`, {
      method: "POST",
      body: JSON.stringify({
        digest_type: opts?.digest_type,
        news_window_days: opts?.news_window_days ?? 3,
        news_window_day_kind: opts?.news_window_day_kind ?? "working",
      }),
    }),
  patchNewsWindow: (
    id: number,
    opts: { news_window_days: number; news_window_day_kind: "calendar" | "working" },
  ) =>
    request<any>(`/digests/${id}/news-window`, {
      method: "PATCH",
      body: JSON.stringify(opts),
    }),
  step1Run: (
    id: number,
    manual_urls: string[],
    opts?: {
      rebuild?: boolean;
      keep_candidate_ids?: number[];
      news_window_days?: number;
      news_window_day_kind?: "calendar" | "working";
      signal?: AbortSignal;
    },
  ) =>
    request<any[]>(`/digests/${id}/step1/run`, {
      method: "POST",
      body: JSON.stringify({
        manual_urls,
        rebuild: opts?.rebuild ?? false,
        keep_candidate_ids: opts?.keep_candidate_ids ?? [],
        news_window_days: opts?.news_window_days ?? 3,
        news_window_day_kind: opts?.news_window_day_kind ?? "working",
      }),
      signal: opts?.signal,
      // Без timeoutMs: шаг 1 верифицирует десятки URL — обрыв на 15 мин давал ложный сбой при живом backend.
    }),
  step1Cancel: (id: number) =>
    request<{ ok: boolean; detail: string }>(`/digests/${id}/step1/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  getStep1Filters: (id: number) =>
    request<any>(`/digests/${id}/step1/filters`),
  saveStep1Filters: (
    id: number,
    payload: {
      version: number;
      filters: Array<{ id: string; enabled: boolean; order: number }>;
      min_discovered_pages: number;
      min_collection_iterations: number;
    },
  ) =>
    request<any>(`/digests/${id}/step1/filters`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  saveStep1DiscoveredFeedback: (
    id: number,
    newsId: number,
    payload: {
      score: 1 | 2 | 3;
      reason?: "published_out_of_range" | "http_unreachable" | "url_redirect_mismatch" | "off_topic_not_ai" | "other";
      reason_other?: string;
    },
  ) =>
    request<any>(`/digests/${id}/step1/discovered/${newsId}/feedback`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  downloadStep1ManualRatings: async () => {
    const res = await fetch(`${API_BASE}/digests/step1/manual-ratings/export`, { cache: "no-store" });
    if (!res.ok) {
      const msg = await readErrorMessage(res);
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "step1_manual_ratings.json";
      anchor.rel = "noopener";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } finally {
      URL.revokeObjectURL(url);
    }
  },
  selectNews: (id: number, selected_ids: number[], top5: boolean) =>
    request<any>(`/digests/${id}/step2/select`, {
      method: "POST",
      body: JSON.stringify({ selected_ids, top5 }),
    }),
  step2AddManualUrls: (id: number, urls: string[]) =>
    request<{
      added: Array<{
        id: number;
        url: string;
        title: string;
        page_verified?: boolean;
        headline_editorial_ok?: boolean;
        link_status?: boolean;
      }>;
      skipped_duplicates: string[];
      pool_count: number;
      detail?: string;
    }>(`/digests/${id}/step2/manual-url`, {
      method: "POST",
      body: JSON.stringify({ urls }),
      timeoutMs: LONG_POST_MS,
    }),
  orderNews: (id: number, ordered_candidate_ids: number[]) =>
    request<any>(`/digests/${id}/step2/order`, {
      method: "POST",
      body: JSON.stringify({ ordered_candidate_ids }),
      timeoutMs: LONG_POST_MS,
    }),
  orderNewsAiOptimal: (id: number) =>
    request<{ ordered: { candidate_id: number; output_position: number; ordering_reason: string; title?: string }[] }>(
      `/digests/${id}/step2/order/ai-optimal`,
      { method: "POST", body: "{}", timeoutMs: LONG_POST_MS },
    ),
  confirmReady: (id: number, command: string) =>
    request<any>(`/digests/${id}/step3/confirm-ready`, {
      method: "POST",
      body: JSON.stringify({ command }),
      timeoutMs: LONG_POST_MS,
    }),
  generateStep4Images: (id: number, hook_variant?: "A" | "B" | "V") =>
    request<any>(`/digests/${id}/step4/generate-images`, {
      method: "POST",
      body: JSON.stringify({ hook_variant }),
      timeoutMs: LONG_POST_MS,
    }),
  selectStep4Image: (id: number, variant: number) =>
    request<any>(`/digests/${id}/step4/select-image`, {
      method: "POST",
      body: JSON.stringify({ variant }),
    }),
  generateStep4Texts: (id: number, platforms: string[], hook_variant?: "A" | "B" | "V") =>
    request<any>(`/digests/${id}/step4/generate-texts`, {
      method: "POST",
      body: JSON.stringify({ platforms, hook_variant }),
      timeoutMs: LONG_POST_MS,
    }),
  confirmFinal: (id: number, hook_variant?: "A" | "B" | "V") =>
    request<any>(`/digests/${id}/step4/confirm-final`, {
      method: "POST",
      body: JSON.stringify({ hook_variant }),
      timeoutMs: LONG_POST_MS,
    }),
  finalizeRelease: (id: number) =>
    request<{
      digest_id: number;
      finalized: boolean;
      already_finalized: boolean;
      release_cost_rub: number;
      finalized_at: string | null;
    }>(`/digests/${id}/finalize`, { method: "POST", body: "{}" }),
};

export const assetUrl = (id: number, type: "docx" | "image", variant?: number) => {
  if (type === "image" && variant != null) {
    return `${API_BASE}/digests/${id}/image?variant=${variant}`;
  }
  return `${API_BASE}/digests/${id}/${type}`;
};
