"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type SourceTiersEditor } from "../lib/api";

type Props = {
  digestType: "serious" | "curious";
  open: boolean;
  onClose: () => void;
};

type DragRef = { groupId: string; index: number } | null;

type EditableHost = {
  marker: string;
  locked: boolean;
  stats: { raw_count: number; pool_count: number; selected_count: number };
};

type EditableGroup = {
  id: string;
  label: string;
  priority: number;
  is_blacklist: boolean;
  hosts: EditableHost[];
};

function cloneEditor(data: SourceTiersEditor): EditableGroup[] {
  return data.groups.map((g) => ({
    id: g.id,
    label: g.label,
    priority: g.priority,
    is_blacklist: g.is_blacklist,
    hosts: g.hosts.map((h) => ({
      marker: h.marker,
      locked: h.locked,
      stats: { ...h.stats },
    })),
  }));
}

export function SourceTiersModal({ digestType, open, onClose }: Props) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ window_days: number; file_name: string } | null>(null);
  const [groups, setGroups] = useState<EditableGroup[]>([]);
  const [dragged, setDragged] = useState<DragRef>(null);
  const [newHostByGroup, setNewHostByGroup] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getSourceTiersEditor(digestType, 30);
      setMeta({ window_days: data.window_days, file_name: data.file_name });
      setGroups(cloneEditor(data));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить источники");
    } finally {
      setLoading(false);
    }
  }, [digestType]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const reorderHost = (from: { groupId: string; index: number }, toGroupId: string, toIndex: number) => {
    setGroups((prev) => {
      const next = prev.map((g) => ({ ...g, hosts: [...g.hosts] }));
      const fromGroup = next.find((g) => g.id === from.groupId);
      const toGroup = next.find((g) => g.id === toGroupId);
      if (!fromGroup || !toGroup) return prev;
      const [item] = fromGroup.hosts.splice(from.index, 1);
      if (!item || item.locked) return prev;
      let insertAt = toIndex;
      if (from.groupId === toGroupId && from.index < toIndex) insertAt -= 1;
      toGroup.hosts.splice(Math.max(0, insertAt), 0, item);
      return next;
    });
  };

  const updateMarker = (groupId: string, index: number, marker: string) => {
    setGroups((prev) =>
      prev.map((g) =>
        g.id !== groupId
          ? g
          : {
              ...g,
              hosts: g.hosts.map((h, i) => (i === index && !h.locked ? { ...h, marker } : h)),
            },
      ),
    );
  };

  const removeHost = (groupId: string, index: number) => {
    setGroups((prev) =>
      prev.map((g) =>
        g.id !== groupId
          ? g
          : { ...g, hosts: g.hosts.filter((h, i) => i !== index || h.locked) },
      ),
    );
  };

  const addHost = (groupId: string) => {
    const raw = (newHostByGroup[groupId] || "").trim().toLowerCase().replace(/^www\./, "");
    if (!raw) return;
    setGroups((prev) =>
      prev.map((g) =>
        g.id !== groupId
          ? g
          : {
              ...g,
              hosts: [
                ...g.hosts,
                { marker: raw, locked: false, stats: { raw_count: 0, pool_count: 0, selected_count: 0 } },
              ],
            },
      ),
    );
    setNewHostByGroup((m) => ({ ...m, [groupId]: "" }));
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const data = await api.saveSourceTiersEditor({
        digest_type: digestType,
        groups: groups.map((g) => ({
          id: g.id,
          hosts: g.hosts.filter((h) => h.marker.trim()).map((h) => ({ marker: h.marker.trim() })),
        })),
      });
      setMeta({ window_days: data.window_days, file_name: data.file_name });
      setGroups(cloneEditor(data));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  const title =
    digestType === "curious"
      ? "Источники — курьёзный выпуск"
      : "Источники — серьёзный выпуск";

  return (
    <div className="source-tiers-modal-overlay" role="dialog" aria-modal="true" aria-labelledby="source-tiers-title">
      <div className="source-tiers-modal card">
        <div className="source-tiers-modal-header">
          <div>
            <h3 id="source-tiers-title" style={{ margin: 0 }}>
              {title}
            </h3>
            {meta ? (
              <p className="source-tiers-modal-sub">
                Файл: <code>{meta.file_name}</code> · группы сверху вниз — порядок поиска на шаге 1
                (сначала ленты и Telegram, затем Tier‑1…4) · домены из Tier автоматически попадают в
                «Ленты, Telegram и seed-URL» · счётчики за {meta.window_days} дн.
              </p>
            ) : null}
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" disabled={saving || loading} onClick={() => void save()}>
              {saving ? "Сохраняем…" : "Сохранить"}
            </button>
            <button type="button" disabled={saving} onClick={onClose}>
              Закрыть
            </button>
          </div>
        </div>

        {error ? <p className="source-tiers-error">{error}</p> : null}
        {loading ? <p style={{ color: "#94a3b8" }}>Загрузка…</p> : null}

        {!loading ? (
          <div className="source-tiers-groups">
            {groups.map((group) => (
              <section
                key={group.id}
                className={`source-tiers-group${group.is_blacklist ? " source-tiers-group--blacklist" : ""}`}
              >
                <h4 className="source-tiers-group-title">
                  {group.label}
                  {group.is_blacklist ? (
                    <span className="source-tiers-badge">чёрный список</span>
                  ) : (
                    <span className="source-tiers-badge source-tiers-badge--prio">приоритет {group.priority}</span>
                  )}
                </h4>
                <div className="source-tiers-table-wrap">
                  <table className="source-tiers-table">
                    <thead>
                      <tr>
                        <th aria-label="Порядок" />
                        <th>Домен / маркер</th>
                        <th title="Для доменов — по сайту статьи; для Telegram и seed-лент — по источнику, откуда ссылка пришла">Найдено</th>
                        <th title="Для доменов — по сайту статьи; для Telegram и seed-лент — по источнику сбора">В пуле</th>
                        <th title="Для доменов — по сайту статьи; для Telegram и seed-лент — по источнику сбора">В топ‑5</th>
                        <th aria-label="Действия" />
                      </tr>
                    </thead>
                    <tbody>
                      {group.hosts.map((host, index) => (
                        <tr
                          key={`${group.id}-${host.marker}-${index}`}
                          draggable={!host.locked}
                          onDragStart={() => !host.locked && setDragged({ groupId: group.id, index })}
                          onDragEnd={() => setDragged(null)}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={() => {
                            if (dragged) reorderHost(dragged, group.id, index);
                            setDragged(null);
                          }}
                          className={host.locked ? "source-tiers-row--locked" : "source-tiers-row--draggable"}
                        >
                          <td className="source-tiers-drag" title={host.locked ? "Автоблок — не перетаскивать" : "Перетащить"}>
                            {host.locked ? "🔒" : "⋮⋮"}
                          </td>
                          <td>
                            {host.locked ? (
                              <code>{host.marker}</code>
                            ) : (
                              <input
                                className="source-tiers-input"
                                value={host.marker}
                                onChange={(e) => updateMarker(group.id, index, e.target.value)}
                              />
                            )}
                          </td>
                          <td>{host.stats.raw_count}</td>
                          <td>{host.stats.pool_count}</td>
                          <td>{host.stats.selected_count}</td>
                          <td>
                            {!host.locked ? (
                              <button
                                type="button"
                                className="source-tiers-remove"
                                title="Удалить"
                                onClick={() => removeHost(group.id, index)}
                              >
                                ×
                              </button>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                      <tr
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => {
                          if (dragged) reorderHost(dragged, group.id, group.hosts.length);
                          setDragged(null);
                        }}
                      >
                        <td colSpan={6} className="source-tiers-add-row">
                          <input
                            className="source-tiers-input"
                            placeholder="новый домен, например habr.com"
                            value={newHostByGroup[group.id] || ""}
                            onChange={(e) =>
                              setNewHostByGroup((m) => ({ ...m, [group.id]: e.target.value }))
                            }
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                addHost(group.id);
                              }
                            }}
                          />
                          <button type="button" onClick={() => addHost(group.id)}>
                            Добавить
                          </button>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
