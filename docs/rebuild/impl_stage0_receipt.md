# Stage 0 — Decision Receipt Spine (implementation report)

Created: 2026-06-14
Authority basis: docs/rebuild/consult_build_spec.md Stage 0 (lines 994-1033, field list
1008-1027, one-invariant 5-12) + docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md

## Goal (spec 996-998)

Make every CURRENT live candidate reconstructable from source inputs to decision —
`fresh inputs -> predictive distribution -> Omega -> normalized joint q -> uncertainty
sample -> executable family book -> payoff-vector decision -> liquidation-aware lifecycle`
— BEFORE any behavior change. Stage 0 changes NO decision, sizing, or submit behavior.

## What I built

### 1. `src/decision/__init__.py` (new package)

The `src/decision/` package did not exist (drift ledger row: directory drift). Created with
`__init__.py` re-exporting `DecisionReceipt`, `ForecastSpine`, `QSpine`, `RouteSpine`,
`SizeSpine`.

### 2. `src/decision/decision_receipt.py` (new)

The `DecisionReceipt` dataclass plus four sub-dataclasses (`ForecastSpine`, `QSpine`,
`RouteSpine`, `SizeSpine`) carrying the spec's receipt fields with EXACT names. Key symbols:

- `DecisionReceipt.from_q_build(...)` — the only Stage-0 constructor. It DERIVES the
  coherence fields rather than accepting them as free inputs:
  - `member_min_native = min(raw_members_native)`, `member_max_native = max(...)`
  - `debiased_member_{min,max}_native = min/max(debiased_members_native)`
  - `q_sum = sum(q_vector)`
- `reconstruct_forecast_q_route_and_size()` — the contract surface: replays the receipt into
  forecast / q / route / size legs.
- `has_forecast_spine()`, `has_q_spine()`, `envelope_is_coherent()` — make the spec's live
  verification signal (line 1033: "no candidate receipt lacks mu/sigma/member
  envelope/q_source/route") queryable.
- `to_row()` / `from_row()` — flatten to / rebuild from the 19/20 receipt columns.
- `RECEIPT_SPINE_COLUMNS` — the single column vocabulary, imported by the schema module and
  the test.

CORRECTED-TRANSFORMATION (operator law, no detector/gate/clamp): the receipt's
reconstruction-correctness is not checked-then-flagged; it is made
*unconstructable-when-wrong*. Because the envelope is `min()/max()` of the same member array
the q-build integrated, `member_min <= member_max` holds by construction; because `q_sum` is
the literal sum of the q vector, a receipt whose `q_sum` disagrees with its q vector cannot be
built through `from_q_build`. A later stage that emits members [20..23] but a receipt claiming
reconstruction at 26 cannot smuggle it past this object — the derived envelope can only
describe the array that was actually integrated.

### 3. `src/state/schema/no_trade_events_schema.py` (modified — REAL schema extended)

Added the 19/20 receipt-spine columns to the EXISTING `no_trade_events` table (no parallel
schema). All are NULLABLE, no DEFAULT, no CHECK -> additive, observability-only, can NEVER
gate/size/submit. Concretely:
- `_RECEIPT_SPINE_COLUMNS_SQL` injected into both `CREATE_TABLE_SQL` and
  `_CREATE_TABLE_REBUILD_SQL`.
- `_RECEIPT_SPINE_COLUMN_DEFS` — (name, sql_type) authority for migration; REAL for
  native-unit floats/probabilities, TEXT for ids/labels.
- `_ensure_receipt_spine_columns(conn)` — additive ALTER-ADD for a pre-Stage-0 table where
  `CREATE TABLE IF NOT EXISTS` is a no-op; called from `ensure_table` and
  `migrate_no_trade_events_schema`. Idempotent (only missing columns are added), non-
  destructive, no hot-path table rebuild.
- The CHECK-driven rebuild's INSERT-SELECT now dynamically carries any spine columns present
  on the source table, so a later reason/version-driven rebuild never DROPS spine data that
  `ensure_table` already populated.

### 4. `src/engine/event_reactor_adapter.py` (modified — READ-ONLY emission only)

Three additive, read-only edits; ZERO change to any decision/sizing/submit path:
- In `_market_analysis_from_event_snapshot`: stash the already-computed predictive
  center/dispersion (mu/sigma per branch — EMOS analytic, honest-raw floored, else member
  mean), the raw + final member arrays, and the FINAL calibrated point distribution `p_cal`
  onto the THREADED `payload` under `payload["_edli_spine_*"]`. These locals never feed
  `p_raw`/`p_cal`/`sampler`/`members` — they only observe.
- In `_generate_candidate_proofs` (same site that captures the belief): lift those threaded-
  payload spine values onto `provenance_capture["decision_receipt_spine_inputs"]` (the
  threaded payload is the only object that carries them; the wrapper's `_payload(event)` re-
  parses fresh JSON and would NOT see them).
- `_build_decision_receipt_spine(spine_inputs, receipt)` (new helper) + a call in
  `build_event_bound_no_submit_receipt` that assembles the `DecisionReceipt` and attaches the
  flattened 19/20-column row to `provenance_capture["decision_receipt_spine"]`. Fail-soft:
  mirrors the existing envelope-wrapper contract (any error leaves the decision untouched).

### 5. `tests/decision/test_live_receipt_contract.py` (new) + `tests/decision/__init__.py`

The spec-named RED-on-revert test
`test_candidate_receipt_reconstructs_forecast_q_route_and_size` plus 9 supporting tests
(q_sum-derivation, envelope min<=max, debiased-envelope consistency, spine-only-None,
to_row/from_row round-trip + schema-column equivalence, NULLABLE columns, pre-Stage-0 ALTER
backfill, the live emission helper, and the gate-reject None path).

### 6. `architecture/_schema_fingerprint.txt` (re-pinned)

The additive columns are intentional schema drift; re-pinned via
`scripts/check_schema_fingerprint.py --write-pin` (governance gate).

## Drift resolved (toward the live type)

| Spec / brief | Live truth | Resolution |
|---|---|---|
| Schema at `src/events/no_trade_events_schema.py` (spec 1002) | path does not exist; real path is `src/state/schema/no_trade_events_schema.py` | extended the REAL schema; never created `src/events/...` |
| `src/decision/` package | did not exist | created with `__init__.py` first |
| "19 receipt fields (1008-1027)" | the list at 1008-1027 is 20 lines (20 fields) | implemented ALL 20 (the live list), not 19; `RECEIPT_SPINE_COLUMNS` has 20 |
| spine-only fields not computed by current path | `predictive_distribution_id`, `q_band_basis`, `route_id`, `payoff_vector_hash`, `edge_lcb`, `delta_u`, `market_implied_q` | present as Optional/None now; each later stage wires its own |
| `applied_debias_native` / `debias_artifact_id` source | `edli_bias_correction_enabled` default OFF in current path | `applied_debias_native` DERIVED from raw-vs-debiased member means when a shift ran; `debias_artifact_id` stays None until Stage 2 DebiasAuthority |
| persistence site | the no_submit ledger insert lives in `src/events/reactor.py`, not the adapter | wired the READ-ONLY spine onto `provenance_capture` at the adapter's existing observability seam (same place the decision-provenance envelope and belief are emitted); no change to the ledger insert behavior |

## RED-on-revert proof

Reverting the q_sum derivation to the broken behavior the spec replaces (a fabricated
`q_sum = 1.0` regardless of the vector — the raw-per-bin incoherence of the old
`_build_fused_q_bounds` lifted into the receipt) makes
`test_q_sum_is_derived_from_the_q_vector_not_a_free_field` FAIL (Obtained 1.0, Expected 1.6);
restoring the derivation makes all 10 pass. Verified by patch-and-restore.

## Test results

Stage-0 contract (`tests/decision/test_live_receipt_contract.py`): 10 passed.

Money-path baseline (`tests/money_path tests/strategy/live_inference`): 331 passed; combined
with the contract test, 341 passed. ZERO behavior change.

Reactor acceptance (`tests/engine/test_event_reactor_no_bypass.py` +
`test_emos_seam_serve_loud.py`): 119 passed, 1 xfailed.

Pre-existing failures in `tests/state` (stale `NoTradeReason` members
`PHYSICAL_INTERVAL_DATA_GATED` / `IMMINENT_CALIBRATION_UNAVAILABLE`, missing
`day0_oracle_anomaly_flags` table, registry drift) are UNRELATED to Stage 0: stashing my
changes, the same suite shows 15 failures (more than the 7 with my change present) — my spine
columns introduce no new failure and in fact satisfy some column-shape checks that were
already failing.
