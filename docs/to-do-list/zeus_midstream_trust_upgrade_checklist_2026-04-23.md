# Zeus Midstream Trust Upgrade Checklist

Created: 2026-04-23
Revision: v2 (supersedes v1; v2 adds T7 INV-22 canonicality + N1 harvester
lineage hygiene; restructures T2 / T3 for cleaner separation; substance
and verdict unchanged)
Branch: `data-improve` (commit `e69352fd955b`)
Status: operational evidence / workbook (NOT authority)
Authority basis: joint converged verdict from structured pro/con Opus
debate 2026-04-23 on Zeus midstream trust (signal → calibration → fusion
→ edge/FDR → Kelly → pre-CLOB execution → monitor/exit). Three rounds +
Round 3 convergence + v2 correction. Zero residual disagreement between
participants. Operator directive enforced: **any test without a dated
provenance header AND recent passing-run evidence is UNTRUSTED and
cannot be cited as protection for any invariant.**

**This is a task queue, not durable law. Do not promote items here to
authority by reference. When an item closes, record its closure in the
owning task packet and, if durable, extract into machine manifest /
test / contract / lore card.**

## Scope

- **In scope (midstream):** `src/signal/`, `src/calibration/`,
  `src/strategy/`, `src/engine/evaluator.py`,
  `src/engine/cycle_runner.py`, `src/engine/monitor_refresh.py`,
  `src/execution/exit_triggers.py`, `src/execution/executor.py`
  (pre-CLOB construction), `src/contracts/`.
- **Out of scope:** upstream data collection (`src/data/*`; currently
  being rebuilt per the live-readiness verdict); downstream position
  management and storage.

## Executive verdict

**Midstream is UNTRUSTED today.**
**→ CONDITIONAL** after T1 + T2 + T3 + T4 + T5 close green.
**→ TRUSTWORTHY** after T6 + T7 promote MITIGATED to full antibody.

The two parties' apparent labels (con-nyx: UNTRUSTED; pro-vega:
CONDITIONAL) collapsed in Round 2 onto the same substance at different
timestamps. The test corpus that is supposed to keep midstream
invariants current has **7.1 % provenance-header coverage** and **19
active test failures** (10 midstream-relevant). Until test currency is
restored and the midstream test reds close, no typed-atom architecture
can be cited as load-bearing — currency, per operator directive, is
what makes code reliable across time, not correctness at a point.

## Structural framing — Fitz K<<N (K=4 roots)

### K1 — Test-currency collapse

- `grep -l "^# Created:" tests/test_*.py | wc -l = 11` out of **156**
  test files: **7.1 %** coverage. The remaining 92.9 % are UNTRUSTED
  regardless of pass/fail (operator directive + `~/CLAUDE.md`
  §"File-header provenance rule").
- **Structural skip debt** — invariants that look guarded but aren't:
  - `tests/test_dual_track_law_stubs.py:1-7` self-labels as
    "skeleton that skips with a message indicating which Phase will
    activate the real enforcement";
    `tests/test_dual_track_law_stubs.py:67` calls
    `pytest.skip("pending: enforced in Phase 7 rebuild")`. This file is
    the most-cited invariant guardian for INV-18/19/20/22.
  - `tests/test_live_safety_invariants.py` carries 7
    `@pytest.mark.skip` markers.
  - `tests/test_provenance_enforcement.py` skips all 4 INV-13 tests on
    `not REGISTRY_YAML.exists()` — INV-13 (Kelly multiplier provenance
    registry) **has no live enforcement today**.
  - `tests/test_cross_module_invariants.py` + `test_cross_module_relationships.py`
    silent-skip on empty `calibration_pairs_v2` and missing canonical
    tables — which is today's state (v2 empty).

### K2 — Active midstream test failures (10 of 19 total fails)

Today's `pytest -q` run: 19 failed / 344 passed / 34 skipped / 1 xfailed.

| Count | Failure class | Routed to |
|---|---|---|
| 2 | `test_calibration_bins_canonical::R14` — quarantine partition drift (M2) | T2 |
| 1 | `test_market_analysis::TestComputePosterior::test_sparse_monitor_market_vector_imputes_missing_sibling_prices` — D5 sparse-vig residual (M3) | T2 |
| 3 | `test_fdr::TestSelectionFamilySubstrate` — fail-closed FDR paths red (M4) | T2 |
| 2 | `test_architecture_contracts::execute_discovery_phase_entry_path_*` — signature drift (`TypeError: execute_discovery_phase() missing 1 required keyword-only argument: 'env'`) (M6) | T3 |
| 1 | `test_architecture_contracts::cycle_runtime_entry_dual_write_helper_skips_when_canonical_schema_absent` (M6) | T3 |
| 2 | `test_architecture_contracts` INV-08 atomicity — `test_canonical_transaction_boundary_helper_is_atomic`, `test_append_many_and_project_is_atomic` (cross-cutting) | T3 |

### K3 — D1–D6 cross-layer epistemic fragmentation residuals

Per `docs/operations/known_gaps.md:344-383`.

- **D3 OPEN** — `ExecutionPrice` is typed unconditionally at
  `src/engine/evaluator.py:267,276-277` (the `EXECUTION_PRICE_SHADOW`
  seam is retired; this is pro-vega's firmest load-bearing antibody in
  midstream) but it **does not propagate past evaluator into
  `executor.py` CLOB-send**. Residual: tick-size, neg-risk, realized
  fill / slippage reconciliation still bare-float. Routed to **T5**.
- **D4 OPEN (critical)** — entry requires BH FDR α = 0.10 + bootstrap CI
  + `ci_lower > 0`; exit requires 2-cycle confirmation. `DecisionEvidence`
  contract exists (`src/contracts/decision_evidence.py`) and
  `test_entry_exit_symmetry.py` passes 15 tests, but **production entry
  and exit paths have not converged on it** — contract is declared, not
  consumed symmetrically. Routed to **T4**.
- **D1, D2, D5, D6 MITIGATED** with residuals. Real
  "category-more-impossible" narrowings in
  `src/strategy/market_fusion.py:11-13, 47, 82-148, 185`:
  - `AlphaDecision(optimization_target='risk_cap')` — D1 mitigation;
    residual: no EV-target α policy.
  - `DEFAULT_TAIL_TREATMENT(serves='calibration_accuracy')` — D2
    mitigation; residual: no profit-validated tail policy.
  - `VigTreatment.from_raw()` — D5 mitigation; residual: sparse monitor
    vectors.
  - `HoldValue.compute(fee=0, time=0)` — D6 mitigation; residual: real
    nonzero funding / correlation cost pricing.
  - MITIGATED labels are earned, distinct from OPEN, but not antibody
    strength. All routed to **T6**.

### K4 — Systemic signal (absorbed into T3)

`test_cross_module_invariants::test_structural_linter_gate` red on raw
local-hour leakage at `src/data/observation_instants_v2_writer.py:122,288`
and `src/data/wu_hourly_client.py:81`. The offending files live upstream
and are covered by the data-readiness packet, but the leak flows through
v2 rows into `src/signal/diurnal.py::get_peak_hour_context()` and into
Day0 α + peak_confidence — trust signal and acceptance criterion are
midstream's. T3 carries the midstream acceptance; T3 blocks on upstream
packet closure for the root-cause fix.

## Architecture credit (load-bearing today, code-wise)

- **`ExecutionPrice` typed at money seam, unconditional.**
  `src/engine/evaluator.py:267` — "only path. No feature flag;
  `assert_kelly_safe()` runs unconditionally". `src/strategy/kelly.py:48-49`
  same. Firmest load-bearing antibody in midstream — and it still needs
  T1 to re-certify its guardian tests.
- **Typed atoms deployed.** `SettlementSemantics.for_city()`,
  `Bin.unit`, `TemperatureDelta`, `MetricIdentity`. Prevent whole
  categories (unit / rounding / track-mixing) at construction time.
- **Recent code wins** — MC n=5000 entry/monitor parity (FIXED
  2026-03-31), bin-width-aware Platt (FIXED 2026-03-31), CI-aware exit,
  hard divergence kill-switch at 0.30. All real code; untrusted-by-header
  only until T1 closes.
- **2026-04-13 D1/D2/D5/D6 mitigation wave** is real narrowing of
  failure categories. MITIGATED ≠ antibody but ≠ pure prose either.

## Axis convergence

| Axis | Status today | Gating remediation |
|---|---|---|
| M1 Signal construction | UNTRUSTED | T1 + T3 (K4 spillover via `diurnal.py`) |
| M2 Calibration | UNTRUSTED + 2 fails (R14) | T1 + T2 |
| M3 Market fusion / α | UNTRUSTED + 1 fail + D1/D2/D5 residuals | T1 + T2 + T6 |
| M4 Edge / FDR | RED — 3 `SelectionFamilySubstrate` fails; INV-22 untrusted | T1 + T2 + T7 |
| M5 Kelly | UNTRUSTED — INV-13 dormant (`provenance_registry.yaml` missing); D3 pipe incomplete | T1 + T5 |
| M6 Risk gate | UNTRUSTED + orchestration signature drift | T1 + T3 |
| M7 Executor pre-CLOB | UNTRUSTED + D3 residual past evaluator | T1 + T5 |
| M8 Monitor → exit | UNTRUSTED + D4 OPEN in production | T1 + T4 |
| M9 Test currency | structural RED | T1 |

## Upgrade checklist (consensus — v2)

**T1** is the master prerequisite — without it, every other antibody
cited here is currency-uncertified and the verdict cannot progress past
UNTRUSTED.

### BLOCKERs — required to move from UNTRUSTED → CONDITIONAL

| ID | Item | Files / tests | Acceptance | Priority |
|---|---|---|---|---|
| **T1** | Test-currency restoration for midstream invariant panel | Dated provenance headers on 15-file midstream-guardian panel: `test_dual_track_law_stubs`, `test_cross_module_invariants`, `test_architecture_contracts`, `test_live_safety_invariants`, `test_provenance_enforcement`, `test_kelly_cascade_bounds`, `test_kelly_live_safety_cap`, `test_execution_price`, `test_ensemble_signal`, `test_calibration_bins_canonical`, `test_alpha_target_coherence`, `test_fdr`, `test_entry_exit_symmetry`, `test_day0_exit_gate`, `test_platt`. Deploy `config/provenance_registry.yaml` so INV-13 enforcement actually runs (no more `skipif REGISTRY_YAML missing`). Audit 7 `@pytest.mark.skip` markers in `test_live_safety_invariants.py`: keep legitimate phase-outs; rewrite the rest. Audit `test_dual_track_law_stubs.py` Phase-7 skip stubs: each one must either activate or be reclassified. | 15 files carry `# Created:` / `# Last reused/audited:` / `# Authority basis:` headers; `config/provenance_registry.yaml` exists so INV-13 tests run without skip; skip-inventory justified per marker with a recorded decision | **P0 — wave 1** |
| **T2** | Close 6 active midstream failures (calibration + posterior + FDR) | Fix 2× R14 (`test_calibration_bins_canonical`), 1× sparse-monitor posterior (`test_market_analysis::test_sparse_monitor_market_vector_imputes_missing_sibling_prices`), 3× `test_fdr::TestSelectionFamilySubstrate` | `pytest -q tests/test_calibration_bins_canonical.py tests/test_market_analysis.py tests/test_fdr.py` → zero failures | **P0 — wave 2** |
| **T3** | Architecture-contract re-certification + structural linter (absorbs K4) | Fix 9 `test_architecture_contracts.py` failures (2 `execute_discovery_phase` signature drift on `env` kwarg, 1 `cycle_runtime_entry_dual_write_helper`, 2 INV-08 atomicity helpers, plus remaining contract reds) **and** `test_cross_module_invariants::test_structural_linter_gate` green (requires upstream closure of raw local-hour leakage at `src/data/observation_instants_v2_writer.py:122,288` + `src/data/wu_hourly_client.py:81` — blocks on upstream remediation packet for root-cause fix, but the acceptance lives midstream because the leak reaches `src/signal/diurnal.py::get_peak_hour_context()` and the Day0 α + peak_confidence path) | `pytest -q tests/test_architecture_contracts tests/test_cross_module_invariants` → 0 failures | **P0 — wave 2** |
| **T4** | D4 closure (entry/exit epistemic symmetry, production path) | Production entry (`src/engine/evaluator.py`) AND exit (`src/execution/exit_triggers.py`) both consume `DecisionEvidence` with symmetric statistical burden. Re-headered `test_entry_exit_symmetry.py` extended to assert both call sites invoke the contract (not just that the contract exists). | Extended test asserts `evaluator.py` and `exit_triggers.py` both use symmetric-burden path; `known_gaps.md:367-371` D4 OPEN → CLOSED | **P0 — wave 3** |
| **T5** | D3 typed-pipeline extension past evaluator | `ExecutionPrice` propagates past `src/engine/evaluator.py:277` into `src/execution/executor.py` order construction boundary. Tick-size, neg-risk, realized fill / slippage reconciliation typed to CLOB-send boundary. | Dated test asserts `ExecutionPrice` type survives to `executor.py` CLOB-send; `known_gaps.md:360-365` D3 residual CLOSED | **P0 — wave 3** |

### CONDITIONALs — required to move CONDITIONAL → TRUSTWORTHY

| ID | Item | Acceptance | Priority |
|---|---|---|---|
| **T6** | D1 / D2 / D5 / D6 residual closures (MITIGATED → antibody) | **D1:** `AlphaDecision(optimization_target='ev')` policy replaces risk-cap blend; profit-validated (not Brier-validated); policy + test + receipt. **D2:** profit-validated tail policy, direction/objective-aware; buy_no P&L evidence; policy + test + receipt. **D5:** sparse-monitor vig with explicit provenance; complete monitor market-family vectors; overlaps T2 `test_sparse_monitor_market_vector_imputes_missing_sibling_prices`. **D6:** nonzero funding / correlation cost pricing in `HoldValue.compute()`; portfolio context at exit authority; replay / live validation receipt. Each `known_gaps.md` entry → CLOSED. | Per-slice acceptance above; each closes its own `known_gaps.md` entry | P1 — parallelizable post-CONDITIONAL |
| **T7** | INV-22 `make_family_id()` canonicality verification | Re-headered `tests/test_dual_track_law_stubs.py::test_fdr_family_key_is_canonical` + grep verification that every call site delegates to the canonical helper (`grep -rn "make_family_id\\|family_key" src/` reviewed against `architecture/invariants.yaml` INV-22 enforcement). Today's skip / Phase-7 stub is why this needs a deliberate check, not a re-run. | Grep-verified canonicality at every call site; dated test header; receipt doc | P1 — coincides with T2 fix-pass (FDR family identity) |

### NICE-TO-HAVE

| ID | Item | Acceptance | Priority |
|---|---|---|---|
| **N1** | Bias-correction lineage harvester legacy-path migration; persistence_anomaly full MITIGATED → FIXED promotion | Per `known_gaps.md` residual entries: harvester legacy bias-correction path migrated to canonical lineage (the `bias_corrected` column + snapshot persistence is in place per known_gaps.md:121-129; this is the remaining legacy-path cleanup); `persistence_anomaly` (minimum-n=30, confidence scaling, 3-day window) promoted to full FIXED status with dated header + receipt. | Packet closure + dated headers | P2 — background hygiene |

## Sequencing

Five waves. Each wave is independently deployable and CI-testable; waves
only cross once predecessors are green.

**Wave 1 — test-currency restoration (~3 engineering days, parallelizable)**
- **T1**: 15-file header wave, `provenance_registry.yaml` deployment,
  skip-marker audit.
- No green test-currency claim elsewhere is defensible without T1.

**Wave 2 — close midstream reds (~5 engineering days)**
- **T2** (6 fails: calibration + posterior + FDR) and
  **T3** (9 architecture-contract fails + structural linter) in
  parallel. T3 blocks on upstream packet closure for the structural
  linter root-cause fix, but in-midstream contract fixes proceed
  independently.

**Wave 3 — cross-layer closures (~5 engineering days each, parallel)**
- **T4**: D4 symmetric contract wired to both call sites in production.
- **T5**: D3 typed pipe extended past evaluator into `executor.py`.

After Wave 3 green with current CI evidence, midstream earns
**CONDITIONAL** — entry to the live-readiness gate set per the separate
live-readiness verdict.

**Wave 4 — MITIGATED → antibody promotions (parallelizable)**
- **T6**: D1 EV-optimized α; D2 profit-validated tail; D5 sparse-vig
  complete; D6 real hold cost.
- **T7**: INV-22 canonical verification (overlaps T2 FDR fix-pass).

After Wave 4 green, midstream earns **TRUSTWORTHY**.

**Wave 5 — background hygiene**
- **N1**: harvester bias-correction legacy cleanup; persistence_anomaly
  full FIXED promotion.

## Residual disagreement

None. The joint verdict recorded zero residual disagreement between
pro-vega (PRO, MIDSTREAM-CONDITIONAL thesis) and con-nyx (CON,
MIDSTREAM-UNTRUSTED thesis) at Round 3 close. Both parties confirmed
their apparent label split collapsed in Round 2 onto the same substance
at different timestamps. v2 correction supersedes v1 for completeness
only; substance and verdict unchanged.

## Closure path

When each item ships and tests go green, record closure in:

- The owning task packet (new packet recommended: "Zeus Midstream Trust
  Restoration" or absorbed into existing packets as appropriate; T1
  belongs in its own test-currency packet; T2 items map to their
  respective subsystem packets; T3's structural linter blocks on the
  upstream data-readiness packet; T4/T5/T6 overlap the Cross-Layer
  Epistemic Fragmentation work at `known_gaps.md:344-383`).
- If the item produces durable law (a new INV, a new typed atom, a new
  manifest clause, a new antibody test with headers), extract into the
  appropriate machine manifest / test / contract / lore card. Do **not**
  leave durable law inside this workbook.
- Mark the row "CLOSED YYYY-MM-DD — packet/receipt link".

When this workbook is fully closed, record closure in
`docs/operations/current_state.md` and demote this file to evidence per
`docs/authority/zeus_current_delivery.md §10` (demotion, not deletion).

## Cross-references

- Live-readiness upgrade checklist (peer workbook):
  `docs/to-do-list/zeus_live_readiness_upgrade_checklist_2026-04-23.md`
- D1–D6 Cross-Layer Epistemic Fragmentation:
  `docs/operations/known_gaps.md:344-383`
- Entry/Exit Epistemic Asymmetry section:
  `docs/operations/known_gaps.md:52-80`
- Harvester bias-correction lineage (N1):
  `docs/operations/known_gaps.md:121-129`
- Runtime semantic law:
  `docs/authority/zeus_current_architecture.md`
- File-header provenance rule (operator directive):
  `/Users/leofitz/CLAUDE.md` §"File-header provenance rule"
- Machine invariants:
  `architecture/invariants.yaml` (INV-08, INV-13, INV-18, INV-19,
  INV-20, INV-21, INV-22)
- Test topology registry:
  `architecture/test_topology.yaml`
- Fatal misreads antibodies:
  `architecture/fatal_misreads.yaml`

## Provenance

- Debate team: `zeus-live-readiness-debate` (native Claude Code team,
  reused from live-readiness debate; config at
  `~/.claude/teams/zeus-live-readiness-debate/config.json`).
- Participants: `pro-vega` (Opus architect, MIDSTREAM-CONDITIONAL
  thesis), `con-nyx` (Opus architect, MIDSTREAM-UNTRUSTED thesis).
- Judge: team-lead (this session).
- Mode: read-only architectural inquiry. No source, state, or graph
  mutation occurred during the debate. Tests were executed read-only
  via `pytest -q` for evidence.
- Rounds: Round 0 openings → Round 1 rebuttals → Round 2 closes →
  Round 3 convergence, all peer-to-peer with zero residual
  disagreement at close. A v2 correction was dispatched after Round 3
  to restore T7 + N1 items from Round 2 closes that were missed in the
  v1 dispatch; v2 supersedes v1.
- Operator directive enforced throughout: dateless test = UNTRUSTED.
- Structural finding (headline): 7.1 % test-file provenance-header
  coverage across the `tests/` directory; the test corpus that Zeus
  relies on to keep midstream invariants current is itself
  uncertified.
