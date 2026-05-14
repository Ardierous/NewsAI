"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AsyncProgress } from "./AsyncProgress";
import { api } from "../lib/api";

export function Dashboard() {
  const [digests, setDigests] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [progressLabel, setProgressLabel] = useState("");

  const load = useCallback(async () => {
    setProgressLabel("Загрузка списка выпусков…");
    setLoading(true);
    try {
      setError("");
      const data = await api.listDigests();
      setDigests(data);
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
          <summary>Пояснение к полям списка</summary>
          <div className="wizard-hint-why-body">
            <p>
              Один ряд = один календарный выпуск. <strong>Дата</strong> — за какой день дайджест; <strong>Статус</strong> и{" "}
              <strong>Шаг</strong> — на каком этапе конвейера остановились (совпадают с полем «Текущий статус» в мастере). Данные
              на сервере уже сохранены — мастер просто продолжает с того же места.
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
          <div>
            <strong>Дата выпуска: {d.date}</strong>
          </div>
          <p className="wizard-hint-do" style={{ fontSize: "0.95rem", margin: "8px 0 0" }}>
            Статус: <strong>{d.status}</strong>
            {" · "}
            шаг: <strong>{d.current_step}</strong>
          </p>
          <details className="wizard-hint-why" style={{ marginTop: 6 }}>
            <summary>Что означают статус и шаг</summary>
            <div className="wizard-hint-why-body">
              <p>
                <strong>Статус</strong> — этап пайплайна (черновик, кандидаты, выбрано, аналитика, готово).{" "}
                <strong>Текущий шаг (метка)</strong> — для отладки; в мастере смотрите то же поле «Текущий статус».
              </p>
            </div>
          </details>
          <p style={{ margin: "12px 0 0", fontSize: "0.95rem", lineHeight: 1.45 }}>
            <Link href={`/digests/${d.id}`}>Открыть мастер ({d.date})</Link> — продолжить работу с этим днём.
          </p>
        </div>
      ))}
    </div>
  );
}
