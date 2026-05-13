"use client";

import { useEffect, useMemo, useState } from "react";

import { api, assetUrl } from "../lib/api";

type Props = { digestId: number };

export function DigestWizard({ digestId }: Props) {
  const [digest, setDigest] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [manualUrls, setManualUrls] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [readyCommand, setReadyCommand] = useState("");
  const [finalCommand, setFinalCommand] = useState("");
  const [hookVariant, setHookVariant] = useState<"A" | "B" | "V" | "">("");
  const [draggedId, setDraggedId] = useState<number | null>(null);

  const loadDigest = async () => {
    setLoading(true);
    try {
      setError("");
      const data = await api.getDigest(digestId);
      setDigest(data);
      if (data.selected?.length) {
        setSelected(data.selected.map((s: any) => s.candidate_id));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadDigest();
  }, [digestId]);

  const canRunStep1 = digest?.digest?.status === "step_0" || digest?.digest?.status === "step_1_candidates";
  const canSelect = digest?.digest?.status === "step_1_candidates";
  const canOrder = digest?.digest?.status === "selected";
  const canAnalytics = digest?.digest?.status === "selected";
  const canFinal = digest?.digest?.status === "analytics_ready";
  const isFinal = digest?.digest?.status === "final_ready";

  const candidatesSorted = useMemo(
    () => [...(digest?.candidates || [])].sort((a, b) => a.original_number - b.original_number),
    [digest],
  );

  const toggleSelected = (id: number) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const run = async (fn: () => Promise<unknown>) => {
    setLoading(true);
    try {
      setError("");
      await fn();
      await loadDigest();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
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
      <div className="card">
        <h2>Digest Wizard #{digestId}</h2>
        <div>Текущий статус: {digest?.digest?.status ?? "..."}</div>
      </div>

      {error && <div className="card">{error}</div>}
      {loading && <div className="card">Загрузка...</div>}

      <div className="card">
        <h3>Шаг 0 — тип дайджеста</h3>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => run(() => api.step0(digestId, "serious"))}>Серьезный</button>
          <button onClick={() => run(() => api.step0(digestId, "curious"))}>Курьезный</button>
          <button onClick={() => run(() => api.step0(digestId, undefined))}>По умолчанию</button>
        </div>
      </div>

      <div className="card">
        <h3>Шаг 1 — кандидаты</h3>
        <textarea
          rows={4}
          placeholder="Опционально: вставьте 5-10 URL, каждый с новой строки"
          value={manualUrls}
          onChange={(e) => setManualUrls(e.target.value)}
        />
        <button
          disabled={!canRunStep1}
          onClick={() =>
            run(() =>
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
          Запустить сбор кандидатов
        </button>
      </div>

      {candidatesSorted.length > 0 && (
        <div className="card">
          <h3>Candidate Selection (ровно 5)</h3>
          {candidatesSorted.map((c) => (
            <label key={c.id} style={{ display: "block", marginBottom: 8 }}>
              <input
                type="checkbox"
                disabled={!canSelect && !selected.includes(c.id)}
                checked={selected.includes(c.id)}
                onChange={() => toggleSelected(c.id)}
              />
              {` #${c.original_number} ${c.title} | ${c.source} | ${c.tier} | ${c.total_score} | ${c.reliability_status}`}
            </label>
          ))}
          <div style={{ display: "flex", gap: 8 }}>
            <button disabled={!canSelect || selected.length !== 5} onClick={() => run(() => api.selectNews(digestId, selected, false))}>
              Подтвердить 5 новостей
            </button>
            <button disabled={!canSelect} onClick={() => run(() => api.selectNews(digestId, [], true))}>
              Оставь топ-5
            </button>
          </div>
        </div>
      )}

      {digest?.selected?.length > 0 && (
        <div className="card">
          <h3>Ordering Screen (drag-and-drop)</h3>
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
          <button disabled={!canOrder || selected.length !== 5} onClick={() => run(() => api.orderNews(digestId, selected))}>
            Применить порядок
          </button>
        </div>
      )}

      <div className="card">
        <h3>Шаг 2 — аналитика</h3>
        <input value={readyCommand} onChange={(e) => setReadyCommand(e.target.value)} placeholder='Введите: готово' />
        <button disabled={!canAnalytics} onClick={() => run(() => api.confirmReady(digestId, readyCommand))}>
          Запустить Step 2
        </button>
        {digest?.analytics?.length > 0 && (
          <div style={{ marginTop: 12 }}>
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

      <div className="card">
        <h3>Шаг 3 — финал</h3>
        <div style={{ display: "flex", gap: 8 }}>
          <select value={hookVariant} onChange={(e) => setHookVariant(e.target.value as "A" | "B" | "V" | "")}>
            <option value="">Авто-ротация крючка</option>
            <option value="A">A — риск/перегрев</option>
            <option value="B">B — деньги/прибыль</option>
            <option value="V">V — дефицит/ограничения</option>
          </select>
        </div>
        <input value={finalCommand} onChange={(e) => setFinalCommand(e.target.value)} placeholder='Введите: Ок' />
        <button disabled={!canFinal} onClick={() => run(() => api.confirmFinal(digestId, finalCommand, hookVariant || undefined))}>
          Запустить Step 3
        </button>
      </div>

      {isFinal && (
        <div className="card">
          <h3>Final Screen</h3>
          <div style={{ marginBottom: 10 }}>
            <a href={assetUrl(digestId, "image")} target="_blank">
              Скачать изображение
            </a>{" "}
            |{" "}
            <a href={assetUrl(digestId, "docx")} target="_blank">
              Скачать .docx
            </a>
          </div>
          {digest.outputs?.map((o: any) => (
            <div key={o.platform} className="card">
              <h4>{o.platform.toUpperCase()}</h4>
              <pre style={{ whiteSpace: "pre-wrap" }}>{o.content}</pre>
              <button onClick={() => navigator.clipboard.writeText(o.content)}>Копировать</button>
            </div>
          ))}
          <div className="card">
            <h4>Quality checks</h4>
            {digest.checks?.map((c: any, idx: number) => (
              <div key={idx}>
                {c.check_name}: {c.status} ({c.comment})
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
