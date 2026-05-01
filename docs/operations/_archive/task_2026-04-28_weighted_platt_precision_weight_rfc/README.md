# task_2026-04-28_weighted_platt_precision_weight_rfc

| Item | Path |
|---|---|
| RFC | `rfc.md` |
| Evidence (PoC v4 numbers) | `evidence/poc_summary.md` |
| PoC source (out-of-repo) | `/Users/leofitz/.openclaw/workspace-venus/_poc_weighted_platt_2026-04-28/` |

## What this packet proposes

Replace zeus's binary `training_allowed: bool` gate (which discards 78% of TIGGE LOW snapshots) with a continuous `precision_weight: float ∈ [0, 1]` across the calibration pipeline.

## Status

DRAFT — pending operator review. See `rfc.md` §10 for required decisions.

## Sibling packets

- `task_2026-04-28_settlements_physical_quantity_migration` — fixes the 1561-row HIGH `physical_quantity` drift; precondition for any future LOW settlements writer parameterization
- LOW settlements backfill — out of scope here; gated on Polymarket LOW market data availability (operator decision)

## Reading order

1. `rfc.md` §1-3 (problem framing + first-principles)
2. `evidence/poc_summary.md` (numbers behind the claim)
3. `rfc.md` §4-5 (migration plan + acceptance criteria)
4. `rfc.md` §10 (decisions required)
