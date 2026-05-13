"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "../lib/api";

export function Dashboard() {
  const [digests, setDigests] = useState<any[]>([]);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      setError("");
      const data = await api.listDigests();
      setDigests(data);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const createDigest = async () => {
    try {
      setError("");
      const created = await api.createTodayDigest();
      window.location.href = `/digests/${created.id}`;
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="grid">
      <div className="card">
        <h2>Dashboard</h2>
        <button onClick={createDigest}>Создать сегодняшний дайджест</button>
      </div>
      {error && <div className="card">{error}</div>}
      {digests.map((d) => (
        <div className="card" key={d.id}>
          <div>
            <strong>{d.date}</strong>
          </div>
          <div>Статус: {d.status}</div>
          <div>Шаг: {d.current_step}</div>
          <Link href={`/digests/${d.id}`}>Открыть мастер</Link>
        </div>
      ))}
    </div>
  );
}
