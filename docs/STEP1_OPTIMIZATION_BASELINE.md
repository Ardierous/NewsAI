# Baseline шага 1 (до оптимизации шагов 0–6)

Снято из `digest.db` → `assets.type = step1_collection_meta` (июнь 2026).

## Digest 20 (типичный «дорогой» прогон)

| Метрика | Значение |
|---------|----------|
| `elapsed_sec` | 1310 (~22 мин) |
| `iterations` | 1 |
| `verified_total` | 9 |
| `urls_raw_merged` | 42 |
| `urls_prefilter_rejected` | 8 |
| `urls_sent_to_http` | 34 |
| `stop_reason` | `hard_timeout` |
| `conversion_e2e_pct` | 21.4% |

## Digest 21

| Метрика | Значение |
|---------|----------|
| `elapsed_sec` | 1196 |
| `verified_total` | 8 (в пул попал раздел vc.ru/ai до фикса) |
| `urls_sent_to_http` | 31 |
| `stop_reason` | `hard_timeout` |
| `conversion_e2e_pct` | 22.9% |

## Целевые метрики после шагов 1–6 (без шага 7)

| Метрика | Цель |
|---------|------|
| `elapsed_sec` | 8–14 мин (при soft/hard 150/240) |
| `verified_total` | 10 или честный 502 |
| `urls_sent_to_http` | ≤ 28 за прогон |
| ProxyAPI шаг 1 | ~35–55 ₽ |
| `stop_reason` | `target_min_met` / `soft_timeout_target_met`, не `hard_timeout` на нормальном выпуске |

Повторный замер: один прогон шага 1 после деплоя, сравнить с таблицей выше.
