# legacy_archived drop-safety audit (2026-07-01)

Created: 2026-07-01
Authority basis: operator request "另两项冗余处置" (execute the two redundancy dispositions).
Method: row-probe on the live canonical DBs (read-only) + src/scripts reference count per table.

## Verdict: the legacy_archived layer is NOT blind-droppable. Audit-first is mandatory.

141 `legacy_archived` entries across world/forecasts/trade. Classification:

| bucket | count | disposition |
|---|---|---|
| already absent (dropped / never created) | 32 | done |
| **0-row + 0 src refs → provably dead** | **2** | `tail_stress_scenarios` (world+trade) — drop-safe |
| 0-row but name-referenced (ghost of live canonical) | 78 | needs per-table connection-target proof |
| **NON-ZERO rows (holds data)** | **29** | NOT droppable without canonical-copy verification |

## The sanctioned drain is essentially complete

`scripts/task_2026-06-09_drop_dead_tables.py` (operator-authorized 2026-06-09, backed by
`.omc/research/dead_table_live_read_proof.md`, 21/21 DEAD-SAFE) is **20/21 done** — dry-run shows
only `platt_models` (forecasts, 0 rows) remaining. Its `--execute` is a destructive live-DB DROP
(classifier-gated; needs explicit operator OK, no `--vacuum-world` so no daemon pause required).

## Why the remaining 109 cannot be blind-dropped

- **29 hold live data** — e.g. `world execution_feasibility_evidence` 12.9M rows, `world
  hourly_observations` 1.8M, `trade probability_trace_fact` 33k, `forecasts settlements` 8336.
  "legacy_archived" ≠ empty. Dropping these is data loss.
- **78 are 0-row ghost copies** whose NAME is referenced for the live canonical copy on a *different*
  DB (`trade forecasts` 0-row/235-refs → canonical on world; `world/trade settlements` 0-row/95-refs
  → canonical is `settlement_outcomes`). A name-based reader count cannot prove which DB each caller
  opens — that requires the same per-connection-target proof the 21-table audit did. Reference count
  alone is NOT drop-safety.

## Recommendation

1. **Retired decoys (item 1): resolved by design, no action.** The `zero_byte_state_cleanup` organ
   (`maintenance_worker/rules/zero_byte_state_cleanup.py`) deliberately excludes every `.db` file
   (`_is_sqlite_companion`, corruption-safety). The 4 zero-byte decoys (`zeus_world.db`,
   `zeus_forecasts.db`, `zeus-trades.db`, `zeus_live.db` — wrong-separator variants) are already
   neutralized by the Owner-Routed Writes guard. Manual `rm` bypasses a deliberate safety rule for
   zero benefit.
2. **Legacy drain (item 2): audited, not wholesale-executable.** The tractable proven-dead subset is
   already drained (sanctioned tool, 20/21). The only trivially-safe additions are
   `tail_stress_scenarios` (×2) + the tool's `platt_models` — all 0-row, all gated destructive
   live-DB drops requiring explicit operator OK. The 78 ghosts need a per-connection-target proof
   before any drop; the 29 data-holding tables are not drop candidates.

Net: the "redundancy layer" here is mostly ghost-shells of live tables + still-populated legacy —
its safe removal is a careful per-table proof + gated migration, not a blind DROP. The registry
`legacy_archived` label marks *intent*, not verified drop-safety.
