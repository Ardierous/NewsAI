"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AsyncProgress } from "./AsyncProgress";
import { api } from "../lib/api";

type DigestTop5Item = {
  position: number;
  title: string;
  source?: string | null;
};

type DigestListRow = {
  id: number;
  date: string;
  status: string;
  status_label_ru: string;
  summary_title: string;
  top5: DigestTop5Item[];
  total_cost_rub: number;
};

function formatDigestDateLabel(iso: string): string {
  const d = String(iso || "").split("T")[0];
  const parts = d.split("-").map((x) => parseInt(x, 10));
  if (parts.length !== 3 || parts.some((n) => Number.isNaN(n))) return iso;
  const [y, m, day] = parts;
  const dt = new Date(y, m - 1, day);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

export function Dashboard() {
  const [digests, setDigests] = useState<DigestListRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [progressLabel, setProgressLabel] = useState("");

  const load = useCallback(async () => {
    setProgressLabel("Загрузка списка выпусков…");
    setLoading(true);
    try {
      setError("");
      const data = (await api.listDigests()) as DigestListRow[] | { value?: DigestListRow[] };
      const rows = Array.isArray(data) ? data : Array.isArray(data?.value) ? data.value : [];
      setDigests(rows);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
      setProgressLabel("");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const createDigest = async () => {
    setProgressLabel("Создание выпуска на сегодня…");
    setLoading(true);
    try {
      setError("");
      const created = await api.createTodayDigest();
      window.location.href = `/digests/${created.id}`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
      setProgressLabel("");
    }
  };

  return (
    <div className="grid">
      <AsyncProgress active={loading} label={progressLabel} />

      <div className="card">
        <h2>Панель выпусков</h2>
        <p className="wizard-hint-do" style={{ marginBottom: 12 }}>
          Чтобы продолжить работу: откройте мастер по ссылке в строке выпуска <strong>или</strong> нажмите кнопку ниже — для
          сегодняшней даты это приведёт к <strong>тому же</strong> выпуску (дубликат по дате не создаётся).
        </p>
        <details className="wizard-hint-why">
          <summary>Пояснение к карточкам выпусков</summary>
          <div className="wizard-hint-why-body">
            <p>
              <strong>Заголовок карточки</strong> — краткая суть выпуска (общий вывод после аналитики) или подпись по дате, если
              аналитика ещё не готова. <strong>Статус</strong> — этап работы понятным языком. <strong>Расход</strong> — сумма всех
              учтённых запросов к ИИ по этому выпуску, включая повторные сборы пула. В раскрывающемся блоке — финальная пятёрка
              новостей, если она уже выбрана.
            </p>
          </div>
        </details>
        <button type="button" disabled={loading} onClick={createDigest} style={{ marginTop: 12 }}>
          Создать или открыть сегодняшний дайджест
        </button>
      </div>
      {error && <div className="card">{error}</div>}
      {digests.length === 0 && !loading && !error ? (
        <div className="card" style={{ fontSize: "0.95rem", color: "#cbd5e1", lineHeight: 1.55 }}>
          <p className="wizard-hint-do" style={{ marginBottom: 10 }}>
            Список пуст — нажмите кнопку выше, чтобы создать первый выпуск на сегодня.
          </p>
          <p style={{ margin: 0, fontSize: "0.8rem", color: "#64748b" }}>
            После создания выпуск появится в списке; дальнейшая работа — в мастере по ссылке «Открыть мастер».
          </p>
        </div>
      ) : null}
      {digests.map((d) => (
        <div className="card" key={d.id}>
          <div style={{ fontSize: "0.88rem", color: "#94a3b8", marginBottom: 6 }}>{formatDigestDateLabel(d.date)}</div>
          <h3 style={{ margin: "0 0 10px", fontSize: "1.12rem", lineHeight: 1.4, fontWeight: 600, color: "#f1f5f9" }}>
            {d.summary_title || `Выпуск ${formatDigestDateLabel(d.date)}`}
          </h3>
          <p className="wizard-hint-do" style={{ fontSize: "0.95rem", margin: "0 0 6px" }}>
            Статус: <strong>{d.status_label_ru || d.status}</strong>
            {" · "}
            Расход по выпуску:{" "}
            <strong>{Number(d.total_cost_rub || 0) > 0 ? `${Number(d.total_cost_rub).toFixed(2)} ₽` : "—"}</strong>
          </p>
          <details className="wizard-hint-why" style={{ marginTop: 8 }}>
            <summary>{d.top5?.length ? `Пятёрка новостей (${d.top5.length})` : "Пятёрка ещё не выбрана"}</summary>
            <div className="wizard-hint-why-body">
              {d.top5?.length ? (
                <ol style={{ margin: "0 0 0 1.1rem", padding: 0 }}>
                  {(Array.isArray(d.top5) ? d.top5 : []).map((item) => (
                    <li key={`${d.id}-${item.position}`} style={{ marginBottom: 8 }}>
                      <strong>{item.position}.</strong> {item.title}
                      {item.source ? (
                        <span style={{ color: "#94a3b8", fontSize: "0.88rem" }}> — {item.source}</span>
                      ) : null}
                    </li>
                  ))}
                </ol>
              ) : (
                <p style={{ margin: 0 }}>
                  После шага 2 в мастере здесь появится список из пяти материалов в порядке выпуска.
                </p>
              )}
            </div>
          </details>
          <p style={{ margin: "12px 0 0", fontSize: "0.95rem", lineHeight: 1.45 }}>
            <Link href={`/digests/${d.id}`}>Открыть мастер</Link> — продолжить работу с этим выпуском.
          </p>
        </div>
      ))}
    </div>
  );
}
