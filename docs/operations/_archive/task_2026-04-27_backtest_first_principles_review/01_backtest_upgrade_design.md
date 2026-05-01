# 01 — Backtest Upgrade Design (Structural)

Created: 2026-04-27
Last reused/audited: 2026-04-27
Authority basis: `zeus/AGENTS.md` §1 (probability chain), `docs/reports/authority_history/zeus_live_backtest_shadow_boundary.md`, `architecture/invariants.yaml` (INV-06 point-in-time, INV-13 multiplier provenance, INV-15 forecast cycle identity)
Status: planning evidence; not authority.

---

## 1. The problem in one sentence

The current `src/engine/replay.py` (2382 lines) is a single state machine that simultaneously claims to do **forecast skill scoring**, **trading economics validation**, and **diagnostic counterfactual auditing**, while in reality only the first is honest, the second is structurally impossible (no market price data), and the third is implicit. Consumers therefore cannot tell what a replay run proves; the boundary doc says `diagnostic_non_promotion` and the code agrees, but the route to lift that label is not enumerable because the module conflates three goals with three different prerequisites.

This is a Fitz Constraint #1 violation: 39 forensic + adversarial findings against the backtest substrate are symptoms of **K=4 structural decisions** that have not been executed:

1. **D1**. What backtest is *for* is not typed; it lives in docstrings (Constraint #2 violation).
2. **D2**. PnL availability uses runtime-computed limitation flags; it should be a precondition the runtime cannot get past.
3. **D3**. Sizing & selection use silent defaults (`flat $5`, `no FDR`); they should be sentinels that fail closed.
4. **D4**. Decision-time-truth provenance is enforced via comments (`DIAGNOSTIC_REPLAY_REFERENCE_SOURCES = frozenset({...})` at [replay.py:42-46](../../../src/engine/replay.py:42)) but not via type — `INV-06` is doc-level, not structure-level.

---

## 2. The three purposes (decomposed)

### 2.A `SKILL` — forecast probability quality (no PnL)

**Question answered.** Given `ensemble_snapshots` of P_raw at decision time and `settlements.winning_bin` as ground truth, how good are the probabilities? Output: Brier, log-loss, accuracy, climatology skill score, calibration buckets.

**Inputs required.**

| Input | Today's status (2026-04-27) | Why required |
|---|---|---|
| Decision-time P_raw vector | `ensemble_snapshots`: 0 rows ; `forecasts`: 23,466 rows (synthetic decision-time fallback) | The probability under test |
| `settlements.winning_bin` | 1,469 VERIFIED rows | The ground truth |
| `bin_labels` per (city, target_date) | reconstructable from `_typed_bins_for_city_date()` | Bin geometry |

**Inputs NOT required.**

- Market price (Polymarket bid/ask) — SKILL is about probability quality, not trading economics
- Kelly cascade — no sizing
- FDR / bootstrap — no multi-comparison correction
- `market_events_v2` rows — bin labels come from city/unit metadata, not from market identity

**Verdict.** This lane is **runnable today** and is the only honest output of the current replay stack. It maps to `WU_SWEEP_LANE` ([replay.py:1721](../../../src/engine/replay.py:1721)) but is currently mixed into the same `ReplaySummary` struct as the others.

### 2.B `ECONOMICS` — historical PnL with full parity (PROMOTION-grade)

**Question answered.** If the current code had been live during \[start, end\], what would the realized PnL trajectory have been, with statistical significance against a control? Output: $ PnL curve, Sharpe, drawdown, FDR-adjusted alpha vs control.

**Inputs required.**

| Input | Today's status | Gap |
|---|---|---|
| Decision-time P_posterior (post-Platt, post-fusion) | partial: P_cal stored in `shadow_signals` & `decision_log` | OK as far as it goes |
| Decision-time market price vector | **0 rows** in `market_events`, `market_events_v2`, `market_price_history` | **STRUCTURAL BLOCKER** |
| Polymarket fee + tick + neg_risk | not captured at decision time historically | Need historical capture |
| Realized fill price + slippage | only on `position_events` rows; `position_events`: **0 rows** | No execution truth |
| Active sizing (Kelly cascade with bootstrap CI) | `_missing_parity_dimensions()` says `False` ([replay.py:160-163](../../../src/engine/replay.py:160)) | Currently uses flat $5 |
| Selection-family (BH FDR or equivalent) | `_missing_parity_dimensions()` says `False` | Not applied |

**Verdict.** This lane is **structurally impossible until the data-layer blockers in `03_data_layer_issues.md` §1+§3 are resolved**. The forensic ruling explicitly listed "Exact Polymarket market replay" as UNSAFE NOW. Implementation in code without data → false confidence.

### 2.C `DIAGNOSTIC` — code-vs-history decision divergence (NOT PnL)

**Question answered.** Given a candidate code change (calibration tweak, alpha rule, exit threshold), at what fraction of historical decisions would the new code have made a different decision (trade vs no-trade, different bin, different size class) than what was historically logged? Output: divergence matrix per cohort, surfacing of unintended regressions.

**Inputs required.**

| Input | Today's status | Note |
|---|---|---|
| Historical `trade_decisions` rows | **0 rows in zeus_trades.db** today | But `decision_log.artifact_json` may have historical replay-able records |
| Decision-time P_raw / P_cal / P_market vectors | partial via `decision_log.trade_cases` and `shadow_signals` | Already used by current `get_decision_reference_for()` ([replay.py:393-583](../../../src/engine/replay.py:393)) |
| Current evaluator code (in-process) | available | The thing under test |

**Verdict.** This lane is **runnable when `decision_log` or `shadow_signals` has historical records**. Today both substrates are largely empty in the canonical DB but oracle/instrumentation captures may exist. Worth one explicit probe before declaring impossible.

**Critical distinction.** DIAGNOSTIC is *not* economics. It does not compute PnL. It surfaces "would the new code have made a different decision than the old code on this snapshot?" This is the **antibody** Zeus actually needs to prevent silent calibration regressions — not a PnL fairy tale.

---

## 3. Structural fix — the typed contract

### 3.A `BacktestPurpose` enum

New file: `src/engine/backtest_purpose.py`

```python
from enum import Enum
from dataclasses import dataclass
from typing import Literal

class BacktestPurpose(str, Enum):
    SKILL = "skill"           # forecast probability quality
    ECONOMICS = "economics"   # historical PnL with full parity
    DIAGNOSTIC = "diagnostic" # code-vs-history decision divergence

# What each purpose may emit
SKILL_FIELDS = frozenset({"brier", "log_loss", "accuracy", "calibration_buckets",
                          "climatology_skill_score", "majority_baseline"})
ECONOMICS_FIELDS = frozenset({"realized_pnl", "sharpe", "max_drawdown",
                              "fdr_adjusted_alpha", "win_rate", "kelly_size_distribution"})
DIAGNOSTIC_FIELDS = frozenset({"decision_divergence_count", "divergence_by_cohort",
                               "edge_sign_flips", "size_class_changes",
                               "unintended_regression_subjects"})

@dataclass(frozen=True)
class PurposeContract:
    purpose: BacktestPurpose
    required_inputs: frozenset[str]
    forbidden_inputs: frozenset[str]
    permitted_outputs: frozenset[str]
    promotion_authority: bool  # True ONLY for ECONOMICS at full parity, otherwise False
```

`run_replay(..., purpose: BacktestPurpose)` becomes mandatory. Calling without it is `TypeError`. Calling with a purpose whose required inputs are missing is `ReplayPreflightError` — not a runtime fallback, not a `limitations` flag, a hard fail at boot.

### 3.B File decomposition

Replace the 2382-line monolith with three modules + a thin orchestrator:

```
src/backtest/
├── __init__.py
├── purpose.py              # BacktestPurpose, PurposeContract, sentinels
├── skill.py                # SKILL lane (formerly run_wu_settlement_sweep + skill summarizers)
├── economics.py            # ECONOMICS lane (currently a stub that raises until data-layer P4.A unblocks)
├── diagnostic.py           # DIAGNOSTIC lane (formerly fragments of run_replay + run_trade_history_audit)
├── decision_time_truth.py  # Provenance-typed snapshot loader (D4 antibody)
├── orchestrator.py         # public run_replay() entry, dispatches by purpose
└── reporting.py            # ReplaySummary, per-purpose serializers
```

The CLI at `scripts/run_replay.py` becomes a thin wrapper that requires `--purpose {skill|economics|diagnostic}` and validates inputs before dispatching.

### 3.C `economics.py` is intentionally a tombstone (today)

Until data-layer blockers resolve, the body of `economics.py` is:

```python
def run_economics(...) -> NoReturn:
    raise ReplayPreflightError(
        "ECONOMICS purpose requires populated market_events_v2 + market_price_history. "
        "See docs/operations/task_2026-04-27_backtest_first_principles_review/03_data_layer_issues.md "
        "§1 for the unblock plan."
    )
```

This is **deliberate**. The current `run_replay()` "supports" economics by silently returning `pnl_available: False` while still running the loop and writing to `zeus_backtest.db`. That is theatre — the module pretends to do work that yields nothing useful. The tombstone surfaces the fact that the work is impossible today, in a place every consumer must see.

When data-layer P4.A unblocks (`market_events_v2` populated), the tombstone's body fills in — and only then.

---

## 4. D3 antibody — sentinel sizing + selection

### Current state ([replay.py:156-164](../../../src/engine/replay.py:156)):

```python
def _missing_parity_dimensions(full_linkage: bool) -> list[str]:
    return [
        dim for dim, present in [
            ("market_price_linkage", full_linkage),
            ("active_sizing_parity", False),   # replay uses flat $5, not Kelly
            ("selection_family_parity", False), # replay has no bootstrap/FDR
        ] if not present
    ]
```

`False` is hardcoded. The "limitation" is reported in the run output but does not prevent the run. So a consumer of the output can read "missing: active_sizing_parity" and shrug, while the loop has already produced numbers that look plausible.

### Replacement (D3):

In `src/backtest/purpose.py`:

```python
class Sizing(Enum):
    NONE = "none"               # SKILL: no sizing emitted
    FLAT_DIAGNOSTIC = "flat_5"  # DIAGNOSTIC: explicit $5 marker, never PnL-meaningful
    KELLY_BOOTSTRAP = "kelly"   # ECONOMICS: real Kelly cascade with bootstrap CI

class Selection(Enum):
    NONE = "none"           # SKILL / DIAGNOSTIC
    BH_FDR = "bh_fdr"       # ECONOMICS: matches live entry contract

@dataclass(frozen=True)
class ParityContract:
    sizing: Sizing
    selection: Selection
    market_price_linkage: Literal["full","partial","none"]

# Per-purpose canonical contracts
SKILL_CONTRACT = ParityContract(Sizing.NONE, Selection.NONE, "none")
DIAGNOSTIC_CONTRACT = ParityContract(Sizing.FLAT_DIAGNOSTIC, Selection.NONE, "none")
ECONOMICS_CONTRACT = ParityContract(Sizing.KELLY_BOOTSTRAP, Selection.BH_FDR, "full")
```

If a purpose's runtime contract is not satisfied, the orchestrator raises **before any output is written**. No more silent "limitations" flags.

### Antibody tests (relationship-grade)

`tests/test_backtest_purpose_contract.py`:

1. "Calling `run_replay(purpose=SKILL)` with `Sizing.KELLY_BOOTSTRAP` raises `PurposeContractViolation`."
2. "Calling `run_replay(purpose=ECONOMICS)` with `market_price_linkage='none'` raises before opening any DB."
3. "Output JSON for `purpose=SKILL` has zero overlap with `ECONOMICS_FIELDS`."
4. "Output JSON for `purpose=DIAGNOSTIC` includes `decision_divergence_count` and excludes `realized_pnl`."

These tests are the antibody: a future maintainer cannot write code that emits PnL under SKILL, because the contract refuses to construct.

---

## 5. D4 antibody — decision-time provenance is typed

### Current state

`DIAGNOSTIC_REPLAY_REFERENCE_SOURCES = frozenset({"shadow_signals", "ensemble_snapshots.available_at", "forecasts_table_synthetic"})` at [replay.py:42-46](../../../src/engine/replay.py:42) is a comment-level convention. `_replay_provenance_limitations()` ([replay.py:167-196](../../../src/engine/replay.py:167)) computes a count of "diagnostic_replay_subjects" and emits the rate as a metric — but no path actually rejects a subject because of it. The fallback chain at [replay.py:502-579](../../../src/engine/replay.py:502) descends through three increasingly speculative layers:

1. `decision_log.trade_cases` / `no_trade_cases` (real decision)
2. `shadow_signals` (instrumentation, not real decision)
3. `ensemble_snapshots.available_at` (any snapshot before that timestamp)
4. `forecasts_table_synthetic` (reconstructed midday)

INV-06 ("point-in-time truth beats hindsight truth") is the law. The chain at L502-579 violates the spirit by allowing layer 3+4 silently in `allow_snapshot_only_reference=True` mode.

### Disk reality (verified 2026-04-27)

`forecasts.forecast_issue_time = NULL` on every one of 23,466 rows. That means even the legacy table cannot prove decision-time truth without inferring `available_at = forecast_basis_date + 12h` or similar — which IS hindsight reconstruction. F11 from the forensic audit. **The risk is not theoretical; it is on disk now.**

### Replacement (D4)

New module: `src/backtest/decision_time_truth.py`

```python
from enum import Enum

class AvailabilityProvenance(str, Enum):
    FETCH_TIME = "fetch_time"           # raw fetch_time from ensemble_snapshots, never reconstructed
    RECORDED = "recorded"               # forecast_available_at recorded by an authoritative writer
    DERIVED_FROM_DISSEMINATION = "derived_dissemination"  # base_time + source-specific dissemination lag; ECMWF ENS = +6h40min + lead_day×4min (confluence.ecmwf.int verbatim)
    RECONSTRUCTED = "reconstructed"     # heuristic; HISTORY-ONLY, never enters training/economics

@dataclass(frozen=True)
class DecisionTimeTruth:
    snapshot_id: str
    available_at: datetime           # the time the data became usable
    provenance: AvailabilityProvenance
    member_extrema: np.ndarray       # the actual data
    p_raw: np.ndarray                # the probability vector at decision time

    def is_promotion_grade(self) -> bool:
        return self.provenance in (AvailabilityProvenance.FETCH_TIME, AvailabilityProvenance.RECORDED)

    def is_diagnostic_only(self) -> bool:
        return self.provenance in (AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
                                    AvailabilityProvenance.RECONSTRUCTED)
```

`load_decision_time_truth(city, target_date, purpose)`:
- `purpose=SKILL`: accepts FETCH_TIME, RECORDED, DERIVED_FROM_DISSEMINATION
- `purpose=DIAGNOSTIC`: accepts all four
- `purpose=ECONOMICS`: accepts only FETCH_TIME, RECORDED — anything else raises `HindsightLeakageRefused`

**Reality-grounded note (CORRECTED 2026-04-27 — see [04 §C1](04_corrections_2026-04-27.md#c1-ecmwf-ens-dissemination-lag)).** The `DERIVED_FROM_DISSEMINATION` tier is genuinely safer than `RECONSTRUCTED` because ECMWF's dissemination schedule (verified verbatim at https://confluence.ecmwf.int/display/DAC/Dissemination+schedule on 2026-04-27) lets us compute `available_at` deterministically for any ENS row:

```
ECMWF ENS Day N forecast available at:
    base_time + 6h40min + (N × 4min)
# Day 0 = +6h40m, Day 1 = +6h44m, Day 15 = +7h40m
# Verified for base times 00 / 06 / 12 / 18 UTC
```

So that derivation is schedule-grounded, not heuristic. The heuristic in `forecasts_append.py` (F11) is `RECONSTRUCTED`. (My earlier "+40min" claim was a misread of the 2017 news article that said "40 minutes earlier than before" — a delta, not the absolute lag. See 04 §C1 for the audit trail.)

Each forecast source needs its own dissemination-derivation function. Today only ECMWF is verified at primary-source level. The other four sources in `forecasts.source` (`openmeteo_previous_runs`, `gfs_previous_runs`, `icon_previous_runs`, `ukmo_previous_runs`) need their own primary-source verification before `DERIVED_FROM_DISSEMINATION` can be applied — flagged as 04 §3 U5.

### Antibody tests

1. `test_load_decision_time_truth_economics_rejects_reconstructed` — fixture row with provenance="reconstructed" + purpose=ECONOMICS raises.
2. `test_dissemination_derivation_matches_published_lag` — assert that `derive_availability("ECMWF_ENS", base, lead_day) == base + timedelta(hours=6, minutes=40 + 4*lead_day)`, and the test docstring cites `https://confluence.ecmwf.int/display/DAC/Dissemination+schedule` so a future maintainer who changes the formula must justify against the wiki.
3. `test_no_consumer_silently_calls_with_reconstructed_provenance` — semgrep / ast-grep antibody equivalent to INV-06's existing rule.

---

## 6. Three-module migration sequence

### Slice S1 — `src/backtest/purpose.py` + `decision_time_truth.py` (additive only, **lowest blast radius**)

- Net new files. No existing code touched.
- Sentinel/contract types, decision-time loader.
- Relationship tests #1-#4 above land here.
- Old `src/engine/replay.py` keeps working unchanged.

### Slice S2 — `src/backtest/skill.py` (move SKILL lane, behavior preserved)

- Cut `run_wu_settlement_sweep()` + `_summarize_binary_samples()` + `_summarize_forecast_skill()` out of `replay.py` into `skill.py`.
- Add `purpose=SKILL` plumbing.
- Old entry point in `replay.py` becomes a deprecation shim that redirects, with a `DeprecationWarning`.
- Regression: existing `tests/test_backtest_outcome_comparison.py` and `tests/test_run_replay_cli.py` still pass without modification.

### Slice S3 — `src/backtest/diagnostic.py` (probe `decision_log` first)

- Probe whether `decision_log` table is populated; if so, port `run_trade_history_audit()` semantics over.
- If not, this slice is structured but tombstoned similar to economics.
- New relationship test: "DIAGNOSTIC purpose output excludes ECONOMICS_FIELDS even when the snapshot is rich enough to compute them."

### Slice S4 — `src/backtest/economics.py` tombstone

- Stub raises `ReplayPreflightError` with explicit pointer to data-layer blockers.
- Test: "ECONOMICS purpose raises until `market_events_v2` is non-empty."

### Slice S5 — `replay.py` removal

- Once S2/S3/S4 are green and one full release cycle has passed without consumer issues, `src/engine/replay.py` is deleted, and consumers re-pointed to `src/backtest/orchestrator.run_replay`.
- Memory L20: every grep target verified at deletion time, not at planning time.

---

## 7. Out-of-scope (explicit)

- Polymarket data ingestion, archive subscription, or websocket capture pipeline. That is data engineering / operator-owned, addressed in `02_blocker_handling_plan.md`.
- LOW-track settlement writer.
- TIGGE local rsync.
- WU empty-provenance backfill.
- Live trading or paper trading code paths.
- `BACKTEST_AUTHORITY_SCOPE` lifting from `diagnostic_non_promotion` — this packet does NOT propose lifting it. Lifting requires economics-grade backtest to be runnable, which requires data-layer unblocks first. The scope label is an honest constraint and stays.

---

## 8. Acceptance criteria for the design (not implementation)

This design doc is "accepted" when:

1. Operator has answered Q1, Q2 from `plan.md` §5.
2. Critic-opus has reviewed the proposed `BacktestPurpose` / `Sizing` / `Selection` / `AvailabilityProvenance` enum membership for completeness.
3. The relationship test list in §3.B + §4 + §5 has been confirmed to be expressible as pytest assertions (per Fitz: if you can't express the relationship as a test, you don't understand it).
4. `architecture/source_rationale.yaml` has a tentative entry for the new `src/backtest/` zone with hazards and write routes identified — **as a follow-on packet**, not in this doc.

---

## 9. Lessons applied from existing system

- **Memory L20 (grep-gate)**: every replay.py file:line citation in this doc was Read or grep'd within 30 minutes of writing. Citations may rot; the implementation packets must re-verify.
- **Memory L22 (commit boundary)**: implementation packets MUST NOT autocommit before critic dispatch.
- **Memory L24 (no `git add -A` with co-tenant)**: stage only files inside this packet folder.
- **Fitz Constraint #1**: 39 findings → 4 structural decisions (D1-D4).
- **Fitz Constraint #2**: every "rule" is encoded as a type or a test, not a comment. The comment-frozenset at `replay.py:42` is the anti-pattern this design replaces.
- **Fitz Constraint #3** (immune system): every D{n} antibody is a test that catches a category of error, not a single instance.
- **Fitz Constraint #4** (data provenance): D4 makes provenance a typed field, not metadata.
