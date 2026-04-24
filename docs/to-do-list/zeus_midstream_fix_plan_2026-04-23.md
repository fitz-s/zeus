# Zeus Midstream Remediation — Joint Implementation Plan

Created: 2026-04-23
Revision: v2 (supersedes v1; v2 integrates 4 granularity deltas from
Phase B cross-review that were missed in v1: T3.1 hour bump 3→4,
T4.2 split into two rows Phase1/Phase2, T3.2b + T4.3b broken out as
standalone rows, P2 timeline sharpened to "months not days". Slice
count moves 33 → 36 via splits; engineer-hours unchanged at ~126;
substance and sequencing unchanged.)
Branch: `data-improve` (commit `e69352fd955b`)
Status: operational evidence / workbook (NOT authority)
Authority basis: midstream verdict v2 dispatched 2026-04-23 (peer
workbook `zeus_midstream_trust_upgrade_checklist_2026-04-23.md`);
decomposition into slices produced by pro-vega (T1/T2/T5/T7) +
con-nyx (T3/T4/T6/N1) via three-phase collaborative protocol
(parallel deep-decompose → adversarial cross-review → joint merge);
Phase B cross-review recorded zero residual findings unaddressed;
Phase C joint plan signed by both parties 2026-04-23; v2 correction
applied to restore granularity deltas omitted in v1 dispatch.

**This is a task queue, not durable law. Do not promote items here to
authority by reference. When a slice closes, record its closure in the
owning task packet and, if durable, extract into machine manifest /
test / contract / lore card.**

## Closure snapshot (2026-04-24 end-of-session)

**Wave 1-4 CLOSED — CONDITIONAL gate materially achieved.** Wave 5
substrate-deferred slices remain blocked on non-plan dependencies;
two out-of-plan "follow-up" feature slices shipped opportunistically
beyond the original 36-slice workbook.

| Wave | Planned | Shipped | Retroactive | Upstream-blocked |
|---|---|---|---|---|
| W1 | 6 | 6 ✅ | - | - |
| W2 | 10 | 10 ✅ | - | - |
| W3 | 12 | 12 ✅ | - | - |
| W4 | 5 | 4 ✅ | N1.2 retroactively accounted (already FIXED 2026-03-31) | T3.4-observe (upstream K4) |
| W5 | 4 | 0 | - | T6.1 / T6.2 / N1.1 (live-B3 + ≥30 settlements); T4.2-Phase2 (7-day audit clean) |

**Out-of-plan shipped this session**:
- **T6.4-phase2** (correlation crowding via ExitContext portfolio threading) — promotes D6 category immunity 0.7/1.0 → 1.0/1.0 when `exit.correlation_crowding_rate > 0`. Feature-flag-safe default 0.0 until operator flip.
- **Day0-canonical-event feature slice** — new `build_day0_window_entered_canonical_write` + `DAY0_WINDOW_ENTERED` event_type in position_events CHECK + legacy-DB migration helper. Closes T1.c-followup L875 OBSOLETE_PENDING_FEATURE.

**Plan-premise corrections accumulated during execution** (numbered
session-running, not plan-slice IDs):

| # | Slice | Correction |
|---|---|---|
| #1 | T4.0 | `decision_log` has no `decision_snapshot_id` column |
| #2 | T1.b | `config/provenance_registry.yaml` pre-existed |
| #3 | T3.3 | bootstrap already creates 31 canonical columns |
| #4 | T3.2b | AST-walk `projection.py` vacuous |
| #5 | T7.a | skip at L67 is a comment |
| #6 | T3.1 | 7 callers → 5 patches |
| #7 | T5.a | `place_buy_order` does not exist |
| #8 | T4.1b | accept path is L1700+ not L753 rejection |
| #9 | T4.2-Phase1 | `exit_triggers.py` is test-only |
| #10 | T4.3b | `Day0Signal → Day0Router.route` refactor |
| #11 | T4.3 | `assert_symmetric_with` not `..._or_stronger` |
| #12 | T5.c | NegRisk passthrough verified → skip |
| #13 | T1.c | skip lines +3 shifted by T1.a header prepend |
| #14 | T2.d.1 | Day0Signal → Day0Router.route echo + DT7 monkeypatch redundant |
| #15 | T6.3 | `VigTreatment` + `from_raw` pre-existed; slice = extension not greenfield |
| #18 | T6.4 | `exit_triggers.py` has ZERO production callers; real seam is `Position.evaluate_exit` in `src/state/portfolio.py` (planning-locked) |
| #19 | T6.3 | B086 archive + T2.c xfail body + plan L109 sibling_snapshot are 3-way colliding; operator chose Option C (sibling primary + p_cal fallback with typed provenance) |
| #20 | T6.3 | `test_sparse_vs_complete_held_bin_stability` silently RED on master since B086; T1 currency audit missed it (checked headers, not pytest green-ness) |
| #21 | T6.4 | `_buy_no_exit` path bypassed HoldValue entirely (bare math); T6.4 brought both directions through contract for parity |
| #22 | T6.4 | `HoldValue.compute` accepted `extra_costs` dict per docstring but `__post_init__` validator ignored extras — latent ValueError surfaced + fixed with trailing `extra_costs_total` field |
| #23 | N1.2 | Already FIXED 2026-03-31; plan row is retroactive accounting, no new code |
| #24 | Day0-canonical | `position_events.event_type` CHECK is a hard gate; new event types require SQL CHECK update + legacy-DB migration helper |

## Slice closure status (W1–W4)

All slices below are CLOSED unless annotated. Commit hashes are the
primary reference; packet receipts (`T1_receipt.json`,
`T2_receipt.json`, `T6_receipt.json`) carry the detailed audit trail
for multi-slice families.

### Wave 1 (foundation)
- T1.a ✅ `67b5908`
- T1.b ✅ `4943d0d`
- T3.1 ✅ `716bfdd`
- T3.3 ✅ `36f0189`
- T7.b ✅ `beea8a9`
- T4.0 ✅ `9365b20`

### Wave 2 (testpath unblocked)
- T1.d ✅ `979eb3b` (keep NC-12 L70 until Phase-7 v2 substrate rebuild)
- T1.e ✅ `692a3af`
- T2.a/T2.b ✅ `c4ee26a`
- T2.d/e/f ✅ `7d064be` (T2.d.1 substitute slice per critic prompt fix)
- T3.2+T3.2b ✅ `566a48f`
- T3.4 **BLOCKED — upstream K4**
- T7.a ✅ (pre-session activation confirmed; plan citation was wrong line)
- T4.1 → **T4.1a** `547bcdd` + **T4.1b** `1d541a3` (split into primitive + wiring)
- T5.a ✅ `abd5bb6`

### Wave 3 (residual fails resolved)
- T1.c ✅ `9b3e4bd` (+ T1.c-followup `480e4f3` for P9 rewrites; L1536/L1569 OBSOLETE_BY_ARCHITECTURE kept pending operator decision)
- T1.f ✅ `9b3e4bd`
- T2.c ✅ closed via **T6.3 Option C** `6f53ef2` (xfail(strict=True) XPASS removed)
- T4.2-Phase1 ✅ `0206428`
- T4.3b ✅ `abd04ad`
- T4.3 ✅ `dc027bb`
- T5.b ✅ `c5c916b`
- T5.c ✅ `63c5c36` (SDK-passthrough audit; no typed contract per plan-premise #12)
- **T6.3** ✅ `6f53ef2` (Option C) + `d83d5ff` (b+j hardening) + `38bcba2` (d+e+partial-f followups)

### Wave 4 (CONDITIONAL milestone)
- T2.g ✅ `6fbdabb` (Day0TemporalContext fixture built; un-monkeypatched real-Day0Router integration passes)
- T5.d ✅ `782d2af`
- **T6.4** ✅ `96fd850` (minimal) + `6b4455f` (surrogate HIGH+MED hardening) + `4edd4c8` (con-nyx post-edit hardening) + **`ebdfb2d` T6.4-phase2** (correlation crowding → D6 full 1.0/1.0 when rate > 0)
- N1.2 ✅ retroactively accounted (plan-premise #23; already FIXED 2026-03-31)
- T3.4-observe **BLOCKED — upstream K4**

### Wave 5 (TRUSTWORTHY milestone — NOT achievable this session)

See "Wave 5 remaining blockers" section below for structured reasons
each slice cannot be ground through the regular per-slice ritual.

## Wave 5 remaining blockers (2026-04-24 snapshot)

The four Wave 5 slices share one structural property: they depend on
data / time substrate that cannot be manufactured by engineering work.
This is NOT a pre-emptive deferral — plan L171 (P2 risk card) predicted
"concretely months, not days" and the session's execution bore that
out. Each row below is a **tracked open item** with its specific gate,
NOT a bug.

| Slice | Hrs | Plan gate | Concrete blocker | What unblocks |
|---|---|---|---|---|
| **T6.1** | 16 | live-B3 + ≥ 30 settlements | `portfolio_position_count = 0` in `state/status_summary.json`; no realized P&L corpus exists to derive EV-optimal α from regression | Zeus resumes live trading + accumulates ≥ 30 settled positions with realized P&L data points |
| **T6.2** | 12 | live-B3 + ≥ 30 buy_no settlements | Same corpus absence; additionally narrower (buy_no-direction subset) | Same + `buy_no`-direction share reaches ≥ 30 |
| **N1.1** | 6 | live-B3 | Bias-correction harvester legacy-path Stage-2 skip cannot flip to `canonical_only_enforced` until live-B3 substrate populates | Live-readiness B3 closes |
| **T4.2-Phase2** | 2 | Phase1 ≥ 7 days audit clean with `audit_log_false_positive_rate ≤ 0.05` | Phase1 audit-only path shipped 2026-04-23 (`0206428`); needs 7 consecutive days of live cycles emitting clean audits | Zeus runs ≥ 7 continuous days in live + audit log metric computable |
| **T3.4** | 1 | Upstream K4 closure | Upstream data-readiness packet owns the raw-hour-leakage root cause; midstream owns only the acceptance signal | Upstream packet closes K4 |

### Why this session cannot push Wave 5 further

None of these gates are code-addressable in isolation:

1. **Capital corpus gates (T6.1, T6.2, N1.1)** require settled realized
   P&L from live Polymarket weather trades. Synthesized backtest data
   was explicitly rejected as the live-readiness workbook evidence
   source. Writing more code does not manufacture settled positions.
2. **Time-window gate (T4.2-Phase2)** requires a 7-day operational
   window of Phase1 audit-only output with FP rate ≤ 0.05. Phase1 went
   live 2026-04-23; a 7-day clean window would earliest end
   2026-04-30, only if Zeus runs continuously. Current status: auto-
   paused (`state/auto_pause_failclosed.tombstone` exists).
3. **Upstream packet gate (T3.4)** requires the data-readiness team to
   close K4. Midstream cannot force that closure.

**Operator choice points**:
- Resume Zeus live trading to start accumulating the capital corpus.
- OR accept that midstream stays at CONDITIONAL indefinitely, with
  TRUSTWORTHY blocked until operational substrate materializes.

## Out-of-plan deferrals surfaced during execution

These items are NOT in the 36-slice workbook but emerged from
execution and require tracking. Each has a defined scope + owner.

### T6.3-followup-1 — Bootstrap CI delta audit (production corpus)

**Origin**: con-nyx post-edit review of T6.3 Option C (finding f).
**Scope**: Replay harness on frozen monitor_refresh corpus including
sparse-monitor events; compare T6.3 p_cal_fallback branch vs pre-T6.3
no-impute branch under identical seeds; report `|delta|` on
`should_trade` rate per direction (Y/N) at each fixed alpha.
**Partial landed**: `test_sparse_p_market_bootstrap_produces_finite_ci`
(tests/test_k3_slice_p.py) asserts no NaN/Inf CI on sparse p_market —
regression guard, not comparative delta audit.
**Blocker**: needs frozen production monitor_refresh corpus (not
available in engineering env).
**Gate for**: live-readiness B3 closure (pre-requisite for flipping
`feature_flags.HOLD_VALUE_EXIT_COSTS=true`).
**Estimated size**: ~3h.

### T6.4 pre-flag-flip operator checklist

**Origin**: con-nyx T6.4 post-edit review — items (a) + (j remainder).
Code-enforced safeguards are in place (bounds validators on
`exit.fee_rate` / `exit.daily_hurdle_rate` / `exit.correlation_
crowding_rate`; `_compute_exit_correlation_crowding` defensive zeros;
HoldValue extra_costs_total field; authority-gap breadcrumb when
`hours_to_settlement` is None). Two items remain operator-governance:

1. **`(a)` polymarket_fee currency verify**: Operator re-verifies
   Polymarket fee schedule at https://docs.polymarket.com/trading/fees
   against `src/contracts/execution_price.py:130` formula `rate × p ×
   (1-p)` with `fee_rate=0.05`. Last verification timestamp should be
   recorded in receipt addendum. Current last-verified stamp is
   2026-03 per git log on `execution_price.py`.
2. **`(j remainder)` replay-receipt enforcement**: Currently the flag-
   note at `config/settings.json:147` documents T6.3-followup-1 as a
   flag-flip prerequisite in prose only. A future hardening slice
   could add a file-presence assertion inside
   `hold_value_exit_costs_enabled()` pointing at
   `docs/operations/.../T6.3-followup-1_replay_receipt.json`. Deferred
   pending operator policy decision on whether code-enforcement is
   preferred over operator-checklist governance.

### Day0-canonical-event production DB migration

**Origin**: Day0-canonical-event feature slice (2026-04-24).
**Scope**: The `_ensure_day0_window_entered_event_type` migration in
`src/state/ledger.py` detects legacy DBs missing `DAY0_WINDOW_ENTERED`
in the CHECK constraint and rebuilds the `position_events` table
(rename → re-create via kernel SQL → copy rows → drop). Fresh tmp-path
DBs get the new CHECK via `CREATE TABLE IF NOT EXISTS`. **Production
daemon requires `init_schema(conn)` call on the live
`state/zeus-world.db`** to apply the migration. Not done by the slice
per Zeus runtime-safety convention (daemon restart + snapshot + verify
pattern, same as prior migration batches T3.3 / REOPEN-1 / S2.1 /
S2.2).
**Pre-migration snapshot**: operator should generate a pre-migration
snapshot with SHA-256 sidecar before invoking `init_schema(conn)`,
following the REOPEN-2 pattern documented in `docs/operations/task_
2026-04-23_midstream_remediation/work_log.md` S2.2 row.
**Estimated size**: ~1h (mechanical, follows proven pattern).

### T1.c-followup L1536 / L1569 — OBSOLETE_BY_ARCHITECTURE

**Origin**: T1.c-followup (2026-04-23) — 2 skipped tests
(`test_contamination_guard_blocks_wrong_env` +
`test_empty_env_positions_pass_guard`) validate the deleted
`_load_portfolio_from_json_data` contamination guard. Post-DB-first
`load_portfolio` architecture, env is carried on each row so the
"run loader in paper mode, expect RuntimeError on live position"
scenario does not exist.
**Operator decision needed**: `OBSOLETE_DELETE` (physically remove the
two test functions) vs `KEEP` (documentation antibody of the removed
contract, following the L589 paper-mode precedent).
**Blocker**: governance preference, not code.
**Estimated size**: ~0.5h either direction.

### T1.d NC-12 L70 — KEEP until Phase-7 v2 substrate rebuild

**Origin**: T1.d audit 2026-04-23 (`979eb3b`). One skip marker at
`tests/test_dual_track_law_stubs.py:70`
(`test_no_high_low_mix_in_platt_or_bins`) classified
`KEEP_LEGITIMATE`.
**Fact**: INV-16 Day0 LOW causality enforcement IS coded at
`src/engine/evaluator.py:922-944`, but NC-12 is multi-surface (Platt +
calibration pairs + bin lookup + settlement identity) and full
enforcement awaits Phase-7 v2 substrate rebuild — `ensemble_snapshots_v2`
+ `calibration_pairs_v2` + `platt_models_v2` currently empty.
**Blocker**: Phase-7 v2 substrate rebuild (external).
**Estimated size**: revisit after Phase-7 ships.

### T6.4 surrogate MED-5 — buy_no `best_bid` kwarg naming approximation

**Origin**: surrogate code-reviewer@opus T6.4 post-edit MED finding.
**Scope**: `_buy_no_exit` passes `current_market_price` as the
`best_bid=` kwarg to `HoldValue.compute_with_exit_costs`. Numerically
harmless (polymarket_fee p*(1-p) is symmetric), but semantically
approximate — capital-locked semantic for buy_no is slightly
miscalibrated vs buy_yes because native-space sell price differs from
mark-to-market capital.
**Full fix**: thread `best_bid_no` separately through `ExitContext`
(touches same architectural layer as T6.4-phase2 portfolio threading;
natural phase2 extension).
**Current mitigation**: docstring comment at portfolio.py buy_no sites
documents the approximation; operator accepts until a dedicated
phase2+ slice lifts the kwarg.
**Estimated size**: ~2h as part of a T6.4-phase3 scope.

### Day0TemporalContext fixture promotion

**Origin**: T2.g closure (2026-04-24, `6fbdabb`).
**Scope**: The ad-hoc Dallas/2026-04-12/CDT Day0TemporalContext
fixture built inline in `tests/test_fdr.py:
test_evaluate_candidate_exercises_real_day0_router_on_fixture_db` is a
candidate for promotion to a shared helper (e.g., `tests/fixtures/
day0_temporal_context.py::build_dallas_cdt_fixture(target_date)`).
Other tests using `SimpleNamespace(current_utc_timestamp=now)` stubs
(`tests/test_fdr.py:616,823,1038`) could migrate to the shared helper
for consistent integration coverage.
**Blocker**: none technical; scope + opportunity cost vs other work.
**Estimated size**: ~1h (promote helper) + ~1h per stub migration.

### T2.g clearance of related SimpleNamespace stubs

**Origin**: T2.d/e/f closure used `SimpleNamespace(current_utc_
timestamp=now)` as temporary stubs in the same test file.
**Scope**: The 3 sibling tests (L616 / L823 / L1038) still use the
minimal stub even after T2.g built the real fixture. They pass the
DT7 gate via the `_seed_ensemble_snapshots_v2_row` path (co-tenant
S1.3 commit `36c5f1d`) so they do not hit the Day0HighSignal
temporal_context code path — but they would benefit from the real
fixture for consistency.
**Blocker**: none; scope decision.
**Estimated size**: ~1h.

## Executive plan summary

**36 slices, ~126 engineer-hours, 5 sequenced waves.**

- **Wave 1–4 (~10 working days for 1 engineer, ~5 days with 2-engineer
  parallelism)** close the BLOCKER set
  (T1 + T2 + T3 + T4.2-Phase1 + T5) and earn midstream **CONDITIONAL**
  status.
- **Wave 5** closes T4.2-Phase2 + T6 + T7 + N1 to earn **TRUSTWORTHY**.
  Wave 5 is substrate-deferred — concretely months, not days (see P2).

Two classes of hard upstream dependency:

- **T3.4** awaits the upstream `src/data/*` raw-hour-leakage fix covered
  by the data-readiness packet. Midstream owns the acceptance signal
  (structural linter gate green) but not the root-cause fix.
- **T6.1, T6.2, N1.1** are doubly blocked on (a) live-readiness **B3**
  (calibration substrate populated) AND (b) a ≥ 30-settlement realized
  P&L corpus that does not exist today (`portfolio_position_count = 0`
  per `state/status_summary.json:151`). Plan flags these as Wave 5
  "deferred-conditional", not blocking the CONDITIONAL milestone.

## Slice catalog

### T1 — Test-currency restoration (6 slices)

| ID | Slice | Hrs | Deps |
|---|---|---|---|
| **T1.a** | Provenance header wave on 15-file panel; `git log --follow --reverse --format=%cs <file> \| head -1` for true Created date. Includes `test_architecture_contracts.py`; T3.5 dropped as redundant | 2 | none |
| **T1.b** | Registry schema + content audit; verify YAML schema at `tests/test_provenance_enforcement.py:29-39`; remove redundant `skipif`s; assert content covers all `dynamic_kelly_mult` cascade multipliers in `src/strategy/kelly.py` | 2 | none |
| **T1.c** | Audit 8 skip markers in `test_live_safety_invariants.py` (211/265/332/586/872/1224/1533/1566). 1× KEEP (Phase2 paper-mode 586); 5× P9 REWRITE against canonical path; 2× P4 contamination guard relocate. Acceptance: `--collect-only SKIPPED` ≤ 1 | 4 | T3.3 |
| **T1.d** | Audit `test_dual_track_law_stubs.py` Phase-N skip stubs (line 67 + any others). Remove activatable; document residuals | 2 | T7.b |
| **T1.e** | Currency-CI integration via `scripts/test_currency_audit.py` + panel definition in `architecture/test_topology.yaml::midstream_guardian_panel` | 2 | T1.a |
| **T1.f** | Currency receipt at `docs/operations/task_2026-04-23_midstream_remediation/T1_receipt.json` | 0.5 | T1.a-e |

### T2 — Six midstream test failures (7 slices)

| ID | Slice | Hrs | Deps |
|---|---|---|---|
| **T2.a** | `test_R14_quarantine_allows_replacement_tag`; read `architecture/data_rebuild_topology.yaml` for direction (test vs code stale) | 2 | T1.a |
| **T2.b** | `test_R14_filter_allowed_partitions_rows`; same direction as T2.a, mechanical | 1 | T2.a |
| **T2.c** | `test_sparse_monitor_market_vector_imputes_missing_sibling_prices` — **PAIR with T6.3** (test boundary only; T6.3 implements; T2.c transitions red → green) | 1 | T1.a |
| **T2.d** | `SelectionFamilySubstrate::test_evaluate_candidate_materializes_selection_facts`; `evaluator.py:951` calls `Day0Router.route()`. Replace monkeypatch target to `src.signal.day0_router.Day0Router.route` | 2 | T1.a |
| **T2.e** | `..._fails_closed_when_full_family_scan_unavailable`; same root + fix | 0.5 | T2.d |
| **T2.f** | `..._fails_closed_when_full_family_returns_empty`; same root + fix | 0.5 | T2.e |
| **T2.g** | Un-monkeypatched fail-closed verification — run T2.d/e/f assertions against real `Day0Router` on fixture DB | 3 | T2.f, T3.3 |

### T3 — Architecture-contract re-certification (5 slices; T3.2b now standalone)

| ID | Slice | Hrs | Deps |
|---|---|---|---|
| **T3.1** | Signature drift wave — patch ALL **7** `execute_discovery_phase(` callers (`test_phase10e_closeout.py:352`, `test_day0_runtime_observation_context.py:41,84`, `test_runtime_guards.py:717,4877,5027`, `test_architecture_contracts.py:3552`) + audit `materialize_position` callers at `test_runtime_guards.py:2010,2057` (also has `env: str` keyword-only at `src/engine/cycle_runtime.py:209`) | **4** | none |
| **T3.2** | INV-14 `temperature_metric` projection-payload drift — patch 5 fixtures in `test_architecture_contracts.py` | 2 | T1.a |
| **T3.2b** | **(standalone row)** Antibody meta-test `tests/test_projection_dict_construction_always_carries_metric_identity.py` — AST-walk `src/state/projection.py` builders; assert every return-dict carries `temperature_metric` key. Dated header per T1.a policy | 0.5 | T1.a |
| **T3.3** | Canonical `position_current` schema bootstrap fix. Diff `apply_architecture_kernel_schema()` at `src/state/ledger.py:150` against `CANONICAL_POSITION_CURRENT_COLUMNS`; add missing columns. Verify production DB unaffected via `sqlite3 state/zeus-world.db "PRAGMA table_info(position_current)"` first | 4 | none |
| **T3.4** | Structural linter gate green — observe-only; depends on UPSTREAM packet K4 | 1 | UPSTREAM K4 |

### T4 — D4 entry/exit symmetric `DecisionEvidence` (5 slices, two-phase deployment)

| ID | Slice | Hrs | Deps |
|---|---|---|---|
| **T4.0** | Persistence-mechanism decision pinned: `decision_log` row keyed on `decision_snapshot_id` (option b; no schema migration). Design doc at `docs/operations/task_2026-04-23_midstream_remediation/T4_persistence.md` | 1 | none |
| **T4.1** | Entry call-site wiring at `src/engine/evaluator.py:724, 1307`. Construct `DecisionEvidence(evidence_type="entry", statistical_method="bootstrap_ci_bh_fdr", sample_size=bootstrap_ci.n_samples, confidence_level=0.10, fdr_corrected=True, consecutive_confirmations=1)`; persist to `decision_log` | 4 | T4.0 |
| **T4.2-Phase1** | **(standalone row)** Exit call-site wiring at `src/execution/exit_triggers.py:49,158,218` — **audit-only**. `try: assert_symmetric_or_stronger; except EvidenceAsymmetryError: log_audit_event('exit_evidence_asymmetry'); continue`. Ships to Wave 3. D4 stays MITIGATED after Phase1 | **6** | T4.1 |
| **T4.2-Phase2** | **(standalone row)** Remove try/except; let `EvidenceAsymmetryError` propagate and kill weak-burden exits. **D4 CLOSES here, not at Phase1.** Gate: Phase1 audit ≥ 7 days with `audit_log_false_positive_rate ≤ 0.05` | **2** | T4.2-Phase1 + 7d clean audit |
| **T4.3** | Static AST-walk call-site presence test in `tests/test_entry_exit_symmetry.py`: asserts `DecisionEvidence(evidence_type="entry")` + `assert_symmetric_or_stronger` string literals | 2 | T4.1, T4.2-Phase1 |
| **T4.3b** | **(standalone row)** Runtime-mock test — `unittest.mock.patch('src.contracts.decision_evidence.DecisionEvidence.__init__', wraps=original_init)` during one full `evaluate_candidate` cycle on fixture DB; assert mock called with `evidence_type="entry"` at least once. Catches dead-code-path gap | 1 | T4.1 |

### T5 — D3 typed-pipeline extension (4 slices)

| ID | Slice | Hrs | Deps |
|---|---|---|---|
| **T5.a** | Import `ExecutionPrice` into `src/execution/executor.py`; refactor `place_buy_order` / `place_sell_order` (real entrypoints at `:191+`) to accept `ExecutionPrice`; assert `assert_kelly_safe()` at executor boundary as defense-in-depth. NEW `tests/test_executor_typed_boundary.py` | 4 | T1.a |
| **T5.b** | `TickSize` typed contract (NEW `src/contracts/tick_size.py`); replace `0.01` magic with `TickSize.for_market(market_id).value` | 2 | T5.a |
| **T5.c** | `NegRiskMarket` typed flag (NEW `src/contracts/neg_risk.py`); verify py-clob-client behavior first — may reduce to typed passthrough | 2 | T5.a |
| **T5.d** | `RealizedFill(execution_price: ExecutionPrice, expected_price: ExecutionPrice, slippage: SlippageBps)` typed record. **Fresh `SlippageBps` semantic type** (NEW `src/contracts/slippage_bps.py`), NOT aliased on `TemperatureDelta` | 3 | T5.a, T5.b |

### T6 — D1 / D2 / D5 / D6 MITIGATED → antibody promotion (4 slices)

| ID | Slice | Hrs | Deps |
|---|---|---|---|
| **T6.1** | D1 EV-optimized α — replaces risk-cap blend; derived from realized-P&L regression. **Doubly blocked**: live-readiness B3 + ≥ 30-settlement P&L corpus | 16 | live-B3 + ≥ 30 settlements |
| **T6.2** | D2 profit-validated `TailTreatment` — direction-aware, buy_no P&L evidence. Same double blocker | 12 | live-B3 + ≥ 30 buy_no settlements |
| **T6.3** | D5 sparse-monitor `VigTreatment` with provenance — `VigTreatment.from_raw(p_market, *, sibling_snapshot=None)` records `imputed_bins` + `imputation_source`. **PAIR with T2.c** | 10 | T2.c (boundary) |
| **T6.4** | D6 nonzero funding + correlation cost in `HoldValue.compute()`; wires existing `src/strategy/correlation.py` + `config/city_correlation_matrix.json` into exit authority | 14 | none |

### T7 — INV-22 `make_family_id()` canonicality (2 slices)

| ID | Slice | Hrs | Deps |
|---|---|---|---|
| **T7.a** | Activate `test_fdr_family_key_is_canonical`; remove `pytest.skip` at line 67; verify body matches current helper signatures | 1 | T7.b, T1.a |
| **T7.b** | AST-walk guard test catches BOTH `ast.Attribute` (`obj.make_family_id`) AND `ast.Name(id="make_family_id")` (direct import); excludes `tests/` | 2 | T1.a |

### N1 — Hygiene promotions (2 slices)

| ID | Slice | Hrs | Deps |
|---|---|---|---|
| **N1.1** | Bias-correction lineage harvester legacy-path cleanup — Stage-2 skip → `canonical_only_enforced` | 6 | live-B3 |
| **N1.2** | `persistence_anomaly` MITIGATED → FIXED: `min_samples ≥ 30`, 3-day window, confidence-scaled discount | 5 | none |

**Total: 36 slices, ~126 engineer-hours.**

## Sequencing dependency graph

```
WAVE 1 (no deps):     T1.a, T1.b, T3.1, T3.3, T7.b, T4.0
WAVE 2 (W1 deps):     T1.d, T1.e, T2.a, T2.d, T3.2, T3.2b, T3.4* [upstream],
                      T7.a, T4.1, T5.a
WAVE 3 (W2 deps):     T1.c (← T3.3), T1.f, T2.b, T2.c [PAIR T6.3],
                      T2.e, T2.f,
                      T4.2-Phase1, T4.3, T4.3b,
                      T5.b, T5.c, T6.3
WAVE 4 (W3 deps):     T2.g (← T2.f + T3.3), T5.d, T6.4, N1.2, T3.4-observe
                      — earns CONDITIONAL
WAVE 5 (substrate-deferred):
                      T4.2-Phase2 (← N ≥ 7d audit clean),
                      T6.1, T6.2, N1.1 (← live-readiness B3 + ≥ 30 settlements)
                      — earns TRUSTWORTHY
```

**CONDITIONAL at end of Wave 4** (T1 + T2 + T3 + T4.2-Phase1 + T5 green; T3.4 awaits upstream).
**TRUSTWORTHY at end of Wave 5** (T4.2-Phase2 enforces D4; T6 + T7 + N1 closed).

## Wave-by-wave execution chart

| Wave | Days | Slice count | Engineer-hrs | Milestone |
|---|---|---|---|---|
| **W1** | Day 1-2 | 6 | 14 | foundation |
| **W2** | Day 3-4 | 10 | ~20 | testpath unblocked |
| **W3** | Day 5-7 | 12 | ~28 | residual fails resolved |
| **W4** | Day 8-10 | 5 | ~28 | **CONDITIONAL** |
| **W5** | Day 11+ (substrate-deferred) | 4 | ~36 | **TRUSTWORTHY** |

Wave 1–4 ≈ **10 working days** single engineer or **~5 days with
2-engineer parallelism**.

## Top-level pre-mortem (5 risks)

**P1 — T1.c rewrites surface latent production defects.** Rewriting 7
P9/P4 skip-markers against the canonical write path may reveal that
canonical `position_current` writes don't carry expected fields
(separate from T3.3 schema bootstrap). Mitigation: file each new failure
as its own micro-slice; do not let surprises gate T1.c.

**P2 — T6.1 / T6.2 / N1.1 blocked months, not days.** Wave-5
substrate-deferred slices require ≥ 30 settled positions with realized
P&L. Today: `portfolio_position_count = 0`. Path-to-substrate:
live-readiness B3 closes + Zeus resumes live + ~30 days of settled
positions accumulate. **Effective wait: concretely months, not days.**
Worst case: Zeus does not resume live (operator decision) and Wave 5
never starts. Mitigation: TRUSTWORTHY milestone is contingent on live
operations resuming; document in plan-level acceptance.

**P3 — T4 stays MITIGATED if Phase2 deferred.** T4.2-Phase1 ships
audit-only; D4 only CLOSES on T4.2-Phase2 enforce-and-block. If audit
phase reveals high false-positive rate, Phase2 deferred, D4 stays
MITIGATED. Mitigation: T4.2-Phase1 acceptance includes
`audit_log_false_positive_rate ≤ 0.05` over 7 days as gating metric;
T4.2-Phase2 blocked on this metric, not on calendar time.

**P4 — T5 typed-boundary refactor breaks unannounced callers.**
`place_buy_order` / `place_sell_order` signature change from bare-float
to `ExecutionPrice` could break callers in evaluator / monitor / CLI
scripts. Mitigation: T5.a ships against ONE call site first; broader
run only after `pytest -q` full-suite green; explicit
`grep -rn "place_buy_order(" src/ scripts/` audit before refactor.

**P5 — T3.4 perpetual upstream block.** Structural linter gate depends
on upstream packet K4 closure. If upstream packet stalls, T3.4 stays
red, midstream cannot achieve full-suite green. Mitigation: CONDITIONAL
milestone explicit about T3.4 exception; do not let upstream timing
block W4 close.

## Plan acceptance (overall)

Joint plan considered DONE when:

1. All Wave 1–4 acceptance commands return green.
2. `pytest -q tests/` zero midstream failures (T2 + T3 closed).
3. `python scripts/test_currency_audit.py` exit 0 (T1 closed).
4. `grep -rn "make_family_id\b" src/` returns only deprecated-wrapper
   definition (T7 closed).
5. `grep -n "ExecutionPrice" src/execution/executor.py` ≥ 3 (T5 closed).
6. `docs/operations/task_2026-04-23_midstream_remediation/` carries
   T1.f + T4 + T5 + T7 receipts.
7. `docs/operations/known_gaps.md` updates: D5 → CLOSED, D3 → CLOSED,
   D4 → MITIGATED (CLOSED post-Phase2).

Wave 5 acceptance separately gated on live-substrate availability.

## Closure path

When each slice ships and tests go green, record closure in:

- The owning task packet — the canonical home is
  `docs/operations/task_2026-04-23_midstream_remediation/` (not yet
  created; opening this packet is the first real-work step).
- If a slice produces durable law (a new INV, a new typed atom, a new
  manifest clause, a new antibody test with dated headers), extract
  into the appropriate machine manifest / test / contract / lore card.
  Candidates: `TickSize`, `NegRiskMarket`, `SlippageBps`, `RealizedFill`,
  `VigTreatment` fields, `DecisionEvidence` fields; INV-22 AST-walk
  guard; `test_projection_dict_construction_always_carries_metric_identity`.
  Do **not** leave durable law inside this workbook.
- Mark the row "CLOSED YYYY-MM-DD — packet/receipt link".

When this workbook is fully closed, record closure in
`docs/operations/current_state.md` and demote this file to evidence per
`docs/authority/zeus_current_delivery.md §10` (demotion, not deletion).

## Cross-references

- Midstream trust verdict (peer workbook, upstream authority for this
  plan): `docs/to-do-list/zeus_midstream_trust_upgrade_checklist_2026-04-23.md`
- Live-readiness workbook (peer, substrate dependency for T6.1 / T6.2 /
  N1.1): `docs/to-do-list/zeus_live_readiness_upgrade_checklist_2026-04-23.md`
- D1–D6 Cross-Layer Epistemic Fragmentation source:
  `docs/operations/known_gaps.md:344-383`
- File-header provenance rule (operator directive enforced throughout):
  `/Users/leofitz/CLAUDE.md` §"File-header provenance rule"
- Machine invariants relevant to this plan:
  `architecture/invariants.yaml` (INV-08, INV-13, INV-14, INV-18,
  INV-19, INV-20, INV-21, INV-22)
- Test topology registry (extended by T1.e):
  `architecture/test_topology.yaml`

## Provenance

- Debate team: `zeus-live-readiness-debate` (native Claude Code team,
  reused from earlier debates; config at
  `~/.claude/teams/zeus-live-readiness-debate/config.json`).
- Participants: `pro-vega` (Opus architect) owned T1 / T2 / T5 / T7;
  `con-nyx` (Opus architect) owned T3 / T4 / T6 / N1.
- Protocol: Phase A parallel deep-decompose (peer-independent) →
  Phase B adversarial cross-review (1 DM round each way) → Phase C
  joint merge. A v2 supersede was dispatched post-Phase-C to restore
  granularity deltas from Phase B that were omitted from the v1
  dispatch (T3.1 hour bump, T4.2 two-row split, T3.2b + T4.3b
  standalone rows, P2 timeline sharpening). v2 supersedes v1.
- Con-nyx accepted all 9 of pro-vega's Phase B findings; pro-vega
  accepted con-nyx's Phase B findings. Zero residual disagreement at
  Phase C close.
- Judge: team-lead (this session). Ruling: accept, no override. Plan
  persisted unmodified to this workbook.
- Mode: read-only investigation throughout. No source, state, or graph
  mutation occurred during plan production. Tests were executed
  read-only via `pytest -q` for evidence.
