# PR-M' Reframe Brief — F18 observation_instants: why "drop v1" was wrong

**Date**: 2026-05-18
**Replaces**: original PR-M brief ("drop legacy observation_instants")

## Finding summary

The original PR-M brief assumed `observation_instants` (v1) was legacy and
could be dropped once readers were migrated to `observation_instants_v2`.
Four investigation runs disproved this:

**v1 has 5 runtime consumers and cannot be dropped:**

1. `src/engine/ddd_wiring.py` — `fetch_directional_coverage()` reads v2 for
   coverage calculation in DDD decisions.
2. `src/data/observation_instants_v2_writer.py` — writer's own upsert SELECT
   queries v2 to check existing rows before insertion.
3. `src/state/schema/v2_schema.py` — defines the VIEW `observation_instants_current`
   over v2 (VIEW definition references v2 directly).
4. Ingest scripts (e.g. `scripts/hko_ingest_tick.py`) — read v2 directly for
   source-specific ingest logic.
5. Audit/diagnostic scripts — read v2 directly to bypass the view and inspect
   raw data.

Additionally, `observation_instants_v2` is NOT a universal replacement for v1:
- v2 is **native-source canonical** (WU/Ogimet/HKO), A1/A2/A6 gated, covers
  only tier-1 settlement cities.
- v1 is **OpenMeteo filler**, ungated, 100% city coverage including markets
  where WU/Ogimet/HKO have not been activated.
- Row counts at investigation time: v1 ≈ 906k, v2 ≈ 1.8M — the asymmetry
  is **by design**, not a data loss signal.

## Reframe: what PR-M' actually does

The goal shifts from "drop v1" to **lock the dual-write contract**:

1. **Dual-writer freshness antibody** (`test_dual_writer_observation_instants_invariant.py`):
   For every tier-1 city in `EXPECTED_SOURCE_BY_CITY`, assert v2 has a row
   within 4h of v1's latest row. Catches a stalled v2 writer before it
   silently corrupts DDD coverage calculations.

2. **Contract documentation** (`architecture/db_table_ownership.yaml`):
   Full dual-write rationale block; v1 and v2 notes with ownership, role,
   and retirement-deferred status.

3. **FIX_PLAN retraction** (`FIX_PLAN.md`): PR-M original brief retracted;
   this document cited as the reframe basis.

## What PR-M' does NOT do (deferred)

**View cutover** (`observation_instants_current`) is deferred to a separate
operator-mediated ops migration:
- The view is currently **INACTIVE** — `init_schema` seeds `zeus_meta` with
  `observation_data_version='v0'`, which matches no rows in v2 (no rows carry
  `data_version='v0'`).
- Routing any runtime reader through the view before activation causes `cov=0`
  for every city, silently halting all DDD-gated trade decisions.
- Activation requires: (a) operator `UPDATE zeus_meta SET value='v1.wu-native'
  WHERE key='observation_data_version'`, (b) downstream consumer audit.
- Pre-activation guard: `tests/state/test_observation_view_consumer_safety.py`
  fails immediately if any runtime code in `src/engine/` or `src/execution/`
  reads the inactive view.

## Reference docs

- `src/state/schema/v2_schema.py:640-683` — VIEW definition + design notes
- `src/data/tier_resolver.py` — `EXPECTED_SOURCE_BY_CITY` (canonical city→source)
- `src/main.py:1583` — `S1_S2_SLA_HOURS = 4`
- `docs/operations/task_2026-05-17_post_karachi_remediation/F44_INVESTIGATION.md`
  — historical context on v2 design
