const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `API error ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listDigests: () => request<any[]>("/digests"),
  createTodayDigest: () => request<any>("/digests/create", { method: "POST" }),
  getDigest: (id: number) => request<any>(`/digests/${id}`),
  step0: (id: number, digest_type?: "serious" | "curious") =>
    request<any>(`/digests/${id}/step0`, { method: "POST", body: JSON.stringify({ digest_type }) }),
  step1Run: (id: number, manual_urls: string[]) =>
    request<any[]>(`/digests/${id}/step1/run`, { method: "POST", body: JSON.stringify({ manual_urls }) }),
  selectNews: (id: number, selected_ids: number[], top5: boolean) =>
    request<any>(`/digests/${id}/select`, { method: "POST", body: JSON.stringify({ selected_ids, top5 }) }),
  orderNews: (id: number, ordered_candidate_ids: number[]) =>
    request<any>(`/digests/${id}/order`, { method: "POST", body: JSON.stringify({ ordered_candidate_ids }) }),
  confirmReady: (id: number, command: string) =>
    request<any>(`/digests/${id}/confirm-ready`, { method: "POST", body: JSON.stringify({ command }) }),
  confirmFinal: (id: number, command: string, hook_variant?: "A" | "B" | "V") =>
    request<any>(`/digests/${id}/confirm-final`, {
      method: "POST",
      body: JSON.stringify({ command, hook_variant }),
    }),
};

export const assetUrl = (id: number, type: "docx" | "image") => `${API_BASE}/digests/${id}/${type}`;
