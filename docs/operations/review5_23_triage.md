# Review 5.23 Triage (Updated 2026-05-24)

Authority: `docs/operations/review5_23_findings.md`

---

## PR #321 Coverage Reconciliation

PR #321 claimed to fix "all P0/P1/P2 findings" but used a DIFFERENT internal numbering. Review file P-numbers are canonical.

---

## Per-Finding Status (Revised Review numbering)

| Finding | Description | Status |
|---------|-------------|--------|
| P0-1 | Release gate SQL proxy; accepts NULL expires_at + NULL strategy_key | **PARTIALLY FIXED** — NULL expiry + NULL strategy_key now rejected; source_run/coverage chain not yet validated |
| P0-2 | Bundle selection pre-blocked by latest scope-level readiness before 00Z contributor | **OPEN** — prior PR moved producer_reason to diagnostic fallback but did NOT fix pre-enumeration hard-block by latest scope row |
| P1-1 | Day0 WU hours=23 cannot prove full local-day coverage interval | **DONE (Phase A)** — 2026-05-24: `_fetch_wu_observation()` computes `WINDOW_INCOMPLETE` when first sample >2h after midnight; evaluator `_day0_observation_quality_rejection_reason()` fail-closes on WINDOW_INCOMPLETE. P2-1 DB reader wiring still deferred. |
| P1-2 | Day0 lock uses raw float, not settlement-rounded value | **DONE** — evaluator.py `_day0_high_truth_classification_for_edge()` now applies `SettlementSemantics.round_single()` |
| P1-3 | MC P_raw nondeterministic; no replay seed | **DONE (Phase A)** — 2026-05-24: `ensemble_signal.py` derives deterministic sha256 seed from member_maxes + n_mc + sigma + bins. Phase B (persist mc_seed + p_raw_vector_hash) deferred (schema change). |
| P1-4 | PromotionReadinessValidator ignores REGRET_ONLY_SCOPE from EvidenceReport | **DONE** — 2026-05-24: `promotion_readiness.py` hard-gates on `cohort_scope_status != "FULL_SCOPE"` before evaluating any signals; returns NOT_READY immediately. |
| P1-5 | PARTIAL_CONTRIBUTOR / boundary_ambiguous policy inconsistency | **DONE** — prior PR (pre-2026-05-24) fixed NON_CONTRIBUTOR for boundary_ambiguous; confirmed by code inspection 2026-05-24. |
| P1-6 | Release-gate fixture inserts non-canonical LIVE_ELIGIBLE row | **PARTIALLY FIXED** — expires_at + strategy_key now present; still using direct SQL not canonical write_readiness_state() |
| P1-7 | NegRisk proof hash omits full orderbook depth | **DONE** — prior PR fixed; confirmed by code inspection 2026-05-24. Shadow-only. |
| P1-8 | NegRisk price_limit = best ask, not last consumed level at q_star | **DONE** — prior PR fixed; confirmed by code inspection 2026-05-24. Shadow-only. |
| P2-1 | Correct Day0 DB extrema reader not production-wired | **DEFERRED** — window-completeness gate (P1-1 Phase A) provides partial protection; full DB reader wiring is a separate multi-file task. |
| P2-2 | Local /Users/leofitz/.claude/jobs/ paths in source files | **DONE** — all 6 files replaced 2026-05-24 |
| P2-3 | ECMWF source role "diagnostic" vs entry-path auditability | **DONE** — prior PR set `FORECAST_SOURCE_ROLE = "entry_primary"`; confirmed by code inspection 2026-05-24. |

---

## Physical Validity of P1-1 (midnight temperature claim)

**Reviewer example:** "If the day's high occurred just after midnight (00:20)..."

User is CORRECT that midnight daily max is meteorologically implausible for temperate weather-market cities. Diurnal cycle places max at 1-4 PM local solar time.

**However: the code bug is real.** Real failure modes:
1. **DST fall-back days are 25 hours** — `hours=23` window misses final 2h.
2. **Early-morning warm advection peaks** — ahead of midday cold front, peaks can be 4-6 AM local.

**Verdict:** Midnight example overstates urgency. Underlying interval-coverage bug is real but rare. Downgrade to P1/P2 boundary. Fix direction (wire DB reader) is correct. Not a release blocker for normal temperate-city markets.

---

## What Was Done This Session (2026-05-24)

### TASK-A (P0-1 + P1-6): SQL gate hardening + antibody tests

`scripts/check_live_release_gate.py`:
- SQL changed: `(expires_at IS NULL OR expires_at > ?)` → `expires_at IS NOT NULL AND expires_at > ? AND strategy_key IS NOT NULL`
- `_write_fixture_files()`: INSERT now includes `strategy_key='producer_readiness_v1'`

`tests/test_live_release_gate.py`:
- `_make_forecasts_db()`: INSERT now includes `strategy_key='producer_readiness_v1'`
- Antibody test 1: NULL expires_at LIVE_ELIGIBLE → gate FAIL
- Antibody test 2: NULL strategy_key LIVE_ELIGIBLE → gate FAIL
- 13 tests pass (was 12)

Note: `source_run_id IS NOT NULL` NOT added to gate — `entry_readiness_writer.py` passes `source_run_id=None` for LIVE_ELIGIBLE rows (verified 2026-05-24, line ~199).

### TASK-B (P1-2): Settlement rounding in Day0 lock classification

`src/engine/evaluator.py` `_day0_high_truth_classification_for_edge()`:
- Applies `SettlementSemantics.for_city(candidate.city).round_single(observed_high_raw)` before bin comparison

### TASK-C (P2-2): Replace local path references

6 files cleaned: `src/contracts/no_trade_reason.py`, `src/data/executable_forecast_reader.py`, `src/data/day0_observation_reader.py`, `src/signal/probability_sanity.py`, `scripts/replay_probability_edge_bin_sanity.py`, `docs/reports/live_prob_p0_edge_bin_sanity_20260523.md`

---

## Deferred Open Work (larger scope)

| Task | Findings | Status | Scope |
|------|----------|--------|-------|
| TASK-D | P2-1 | DEFERRED | Wire `read_day0_observed_extrema_v2()` into production. P1-1 Phase A gate provides fail-close on missing window proof; full DB reader is a separate multi-file PR. |
| TASK-E | P1-3 Phase B | DEFERRED | Persist mc_seed + p_raw_vector_hash; requires schema bump + migration. Phase A (deterministic seed) DONE. |
| TASK-G | P0-2 | OPEN | Producer readiness bundle-granular — scope_key must include coverage_id, OR move pre-gate to per-candidate evaluation. Large refactor. |
| TASK-J | P0-1 best | OPEN | Replace SQL proxy with actual `read_executable_forecast()` call. Requires scope enumeration context. Large redesign. |

---

## Note on P0-2 Status

The prior PR moved `producer_reason` from a hard gate to a diagnostic fallback. This improved logging but did NOT change the fundamental pre-enumeration block: `_latest_producer_readiness()` is still called and can still return BLOCKED before any candidates are enumerated, based on latest scope-level readiness row (which can be overwritten by a 12Z blocked run).

The revised review confirms this is still open. Root fix requires either:
(a) Adding `source_run_id` or `coverage_id` to the `scope_key` in `write_readiness_state()` so each run has its own row, or
(b) Removing the pre-enumeration producer readiness gate entirely and evaluating readiness per candidate inside `_evaluate_candidate()`.
