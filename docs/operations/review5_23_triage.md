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
| P1-1 | Day0 WU hours=23 cannot prove full local-day coverage interval | **OPEN** |
| P1-2 | Day0 lock uses raw float, not settlement-rounded value | **DONE** — evaluator.py `_day0_high_truth_classification_for_edge()` now applies `SettlementSemantics.round_single()` |
| P1-3 | MC P_raw nondeterministic; no replay seed | **OPEN** |
| P1-4 | PromotionReadinessValidator ignores REGRET_ONLY_SCOPE from EvidenceReport | **OPEN** |
| P1-5 | PARTIAL_CONTRIBUTOR / boundary_ambiguous policy inconsistency | **OPEN** — prior PR set NON_CONTRIBUTOR for boundary_ambiguous but read_executable_forecast_snapshot() still has conflicting pass-through comments |
| P1-6 | Release-gate fixture inserts non-canonical LIVE_ELIGIBLE row | **PARTIALLY FIXED** — expires_at + strategy_key now present; still using direct SQL not canonical write_readiness_state() |
| P1-7 | NegRisk proof hash omits full orderbook depth | **OPEN** (shadow-only severity) |
| P1-8 | NegRisk price_limit = best ask, not last consumed level at q_star | **OPEN** (shadow-only severity) |
| P2-1 | Correct Day0 DB extrema reader not production-wired | **OPEN** (same as P1-1) |
| P2-2 | Local /Users/leofitz/.claude/jobs/ paths in source files | **DONE** — all 6 files replaced 2026-05-24 |
| P2-3 | ECMWF source role "diagnostic" vs entry-path auditability | **OPEN** — "scheduled_collector" is not a valid ForecastSourceRole Literal; needs new role value or documented separation |

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

| Task | Findings | Scope |
|------|----------|-------|
| TASK-D | P1-1/P2-1 | Wire `read_day0_observed_extrema_v2()` into production; add interval coverage proof to observation_client.py. Multi-file, needs planning. |
| TASK-E | P1-3 | Deterministic MC seed via sha256(snapshot_id, source_run_id, city_id, ...); persist seed + p_raw_vector_hash. Schema change required. |
| TASK-F | P1-4 | `experiment_decisions` table; PromotionReadinessValidator blocks REGRET_ONLY_SCOPE. Schema change. |
| TASK-G | P0-2 | Producer readiness bundle-granular (scope_key include coverage_id; or move pre-gate into per-candidate evaluation). Large refactor. |
| TASK-H | P1-5 | Pick one PARTIAL_CONTRIBUTOR live policy (strict: always block; or haircut: allow with strategy profile flag). |
| TASK-I | P1-7/P1-8 | NegRisk basket: hash full orderbook depth; price_limit = last consumed level at q_star. Shadow-only, lower urgency. |
| TASK-J | P0-1 best | Replace SQL proxy with actual `read_executable_forecast()` call. Requires scope enumeration context. Large redesign. |

---

## Note on P0-2 Status

The prior PR moved `producer_reason` from a hard gate to a diagnostic fallback. This improved logging but did NOT change the fundamental pre-enumeration block: `_latest_producer_readiness()` is still called and can still return BLOCKED before any candidates are enumerated, based on latest scope-level readiness row (which can be overwritten by a 12Z blocked run).

The revised review confirms this is still open. Root fix requires either:
(a) Adding `source_run_id` or `coverage_id` to the `scope_key` in `write_readiness_state()` so each run has its own row, or
(b) Removing the pre-enumeration producer readiness gate entirely and evaluating readiness per candidate inside `_evaluate_candidate()`.
