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
  step0: (id: number, digest_type?: "serious" | "curious") =>
    request<any>(`/digests/${id}/step0`, { method: "POST", body: JSON.stringify({ digest_type }) }),
  step1Run: (id: number, manual_urls: string[]) =>
    request<any[]>(`/digests/${id}/step1/run`, {
      method: "POST",
      body: JSON.stringify({ manual_urls }),
      timeoutMs: LONG_POST_MS,
    }),
  selectNews: (id: number, selected_ids: number[], top5: boolean) =>
    request<any>(`/digests/${id}/step2/select`, { method: "POST", body: JSON.stringify({ selected_ids, top5 }) }),
  orderNews: (id: number, ordered_candidate_ids: number[]) =>
    request<any>(`/digests/${id}/step2/order`, { method: "POST", body: JSON.stringify({ ordered_candidate_ids }) }),
  confirmReady: (id: number, command: string) =>
    request<any>(`/digests/${id}/step3/confirm-ready`, {
      method: "POST",
      body: JSON.stringify({ command }),
      timeoutMs: LONG_POST_MS,
    }),
  confirmFinal: (id: number, command: string, hook_variant?: "A" | "B" | "V") =>
    request<any>(`/digests/${id}/step4/confirm-final`, {
      method: "POST",
      body: JSON.stringify({ command, hook_variant }),
      timeoutMs: LONG_POST_MS,
    }),
};

export const assetUrl = (id: number, type: "docx" | "image") => `${API_BASE}/digests/${id}/${type}`;
