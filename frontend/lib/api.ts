const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/** Длинные AI-операции (шаги 1, 3, 4): браузерный fetch по умолчанию не ограничен, но явный таймаут защищает от «вечного» зависания. */
const LONG_POST_MS = 15 * 60 * 1000;

type RequestOptions = RequestInit & { timeoutMs?: number };

function createTimeoutSignal(ms: number): { signal: AbortSignal; cancel: () => void } {
  const controller = new AbortController();
  const id = setTimeout(() => {
    controller.abort(new Error("Превышено время ожидания ответа сервера (15 мин)."));
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
  step1Run: (id: number, manual_urls: string[], opts?: { rebuild?: boolean }) =>
    request<any[]>(`/digests/${id}/step1/run`, {
      method: "POST",
      body: JSON.stringify({ manual_urls, rebuild: opts?.rebuild ?? false }),
      timeoutMs: LONG_POST_MS,
    }),
  selectNews: (id: number, selected_ids: number[], top5: boolean) =>
    request<any>(`/digests/${id}/step2/select`, { method: "POST", body: JSON.stringify({ selected_ids, top5 }) }),
  orderNews: (id: number, ordered_candidate_ids: number[]) =>
    request<any>(`/digests/${id}/step2/order`, { method: "POST", body: JSON.stringify({ ordered_candidate_ids }) }),
  orderNewsAiOptimal: (id: number) =>
    request<{ ordered: { candidate_id: number; output_position: number; ordering_reason: string; title?: string }[] }>(
      `/digests/${id}/step2/order/ai-optimal`,
      { method: "POST", body: "{}" },
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
};

export const assetUrl = (id: number, type: "docx" | "image", variant?: number) => {
  if (type === "image" && variant != null) {
    return `${API_BASE}/digests/${id}/image?variant=${variant}`;
  }
  return `${API_BASE}/digests/${id}/${type}`;
};
