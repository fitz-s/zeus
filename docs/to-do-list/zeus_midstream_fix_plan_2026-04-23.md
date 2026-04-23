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
