# Phase 2 Audit Log — Adjacent Fix Discovery

Created: 2026-04-26
Authority basis: phase 2 plan §2 audit method
Source HEAD at audit: `cec0185` (post-phase-1 + 4 review-fix commits)
Audit window: 2026-04-26, 10-min window before plan write

---

## Method

Three audit queries against the phase-1-completed branch state:

- **Q1**: Repo-wide grep for `get_pairs_for_bucket|get_pairs_count|get_decision_group_count` calls outside `src/calibration/manager.py` (the only caller now passing metric).
- **Q2**: Diff between `CREATE TABLE ensemble_snapshots` schema and writer SQL UPDATE column expectations.
- **Q3**: Repo-wide grep for `getattr(position, "temperature_metric", "high")` outside `src/state/chain_reconciliation.py` (the only canonical resolver after phase 1).

---

## Q1 — Adjacent calibration_pairs read sites lacking metric

Verified hits:

- **`src/engine/evaluator.py:1000-1024` (approx)** — K4 belt-and-suspenders gate. Inside `evaluate_candidate`. Reads `_get_pairs(conn, city.cluster, _cal_season, authority_filter='UNVERIFIED')` to detect contamination. Does not pass `metric`. Phase 1 slice A2 routed `manager.py`'s reads through metric but missed this guard layer.

- **`src/engine/monitor_refresh.py:177-184`** — twin K4 belt-and-suspenders gate at the monitor cycle. Same pattern.

- **`src/engine/monitor_refresh.py:371-378`** — second twin gate further down monitor_refresh, same pattern.

- **`src/strategy/market_fusion.py:97`** — comment-only reference (no actual call).

Status: 3 actual call sites without `metric` pass-through. Phase 2 P2-A addresses them.

---

## Q2 — ensemble_snapshots schema gap

Schema declared in `src/state/db.py` `CREATE TABLE ensemble_snapshots`:

```
snapshot_id, city, target_date, issue_time, valid_time, available_at,
fetch_time, lead_hours, members_json, p_raw_json, spread, is_bimodal,
model_version, data_version, authority, temperature_metric
+ UNIQUE(city, target_date, issue_time, data_version)
```

Writer at `src/engine/evaluator.py:1928`:

```sql
UPDATE {snapshots_table} SET p_raw_json = ?, bias_corrected = ? WHERE snapshot_id = ?
```

`bias_corrected` is REFERENCED but not DECLARED. Production DB likely has it via a past undocumented ALTER TABLE; fresh in-memory DBs from `init_schema(conn)` do NOT.

Test failure surface (during phase 1):
- `tests/test_runtime_guards.py::test_store_ens_snapshot_routes_to_attached_world_db` — fails with `world_row["p_raw_json"]` returning None because the UPDATE silently catches OperationalError ("no such column: bias_corrected") and the row's p_raw_json remains NULL.
- `tests/test_runtime_guards.py::test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly` — different failure mode but same root: writer's silent-error swallows the schema gap.

Status: Real schema bug. Phase 2 P2-B fixes via additive ADD COLUMN.

---

## Q3 — Position metric defensive defaults outside canonical resolver

Verified hits:

- **`src/engine/lifecycle_events.py:100`** — `"temperature_metric": getattr(position, "temperature_metric", "high"),` inside event payload dict construction. Writes the silent default to the lifecycle event JSON without UNVERIFIED tagging. Downstream analytics can't tell which rows were materialized vs defaulted.

- **`src/engine/monitor_refresh.py:140`** — **CRITICAL**:
  ```python
  cal, cal_level = get_calibrator(
      conn, city, position.target_date,
      temperature_metric=getattr(position, "temperature_metric", "high"),
  )
  ```
  Phase 9C L3 hardened `get_calibrator` to be metric-aware specifically to prevent LOW positions from receiving HIGH Platt models. This silent `getattr(..., "high")` default UNDOES that hardening at the entry seam: a LOW position with no metric attribute silently gets HIGH calibration.

- **`src/engine/monitor_refresh.py:298`** — reads `getattr(position, "temperature_metric", "high")` for downstream signal computation.

- **`src/engine/monitor_refresh.py:334`** — passes `getattr(position, "temperature_metric", "high")` into another helper.

Comparison with canonical:

`src/state/chain_reconciliation.py:resolve_rescue_authority` (phase 1 slice A4 anchor):
```python
def resolve_rescue_authority(position) -> tuple[str, str, str]:
    _raw_metric = getattr(position, "temperature_metric", None)
    if _raw_metric in ("high", "low"):
        return (_raw_metric, "VERIFIED", "position_materialized")
    return ("high", "UNVERIFIED", f"position_missing_metric:{_raw_metric!r}")
```

The canonical resolver:
- Uses `None` (not `"high"`) as `getattr` default to distinguish missing-attribute from explicit-HIGH.
- Returns the (metric, authority, source) tuple so downstream consumers can filter on authority.
- Handles the 4 invalid input shapes (None, empty, garbage, non-string).

The 4 Q3 hits all degrade to `getattr(..., "high")` — losing both signals.

Status: 4 silent-HIGH sites. Phase 2 P2-C consolidates them through a peer resolver `resolve_position_metric`.

---

## Summary

| Cluster | Hits | Severity | Phase 2 slice |
|---|---|---|---|
| Q1 (adjacent calibration_pairs reads) | 3 sites | Medium (defense-in-depth) | P2-A1 + P2-A2 |
| Q2 (ensemble_snapshots schema gap) | 1 schema + 1 writer | High (test infra + prod fragility) | P2-B1 |
| Q3 (position metric silent defaults) | 4 sites | High (1 directly undermines Phase 9C L3) | P2-C1 + P2-C2 |

Total: 8 distinct sites → 5 slices → 3 structural decisions.

---

## Premise rot disclaimer

This audit was conducted at HEAD `cec0185`. If new commits land on the branch before phase 2 implementation (e.g., other operators merging fixes), each slice MUST re-grep the cited file:line within 10 minutes before edit per memory `feedback_grep_gate_before_contract_lock.md`.

End of audit log.
