# Phase 9B Contract — Risk-critical DT closure (DT#2 + DT#5 + DT#7)

**Written**: 2026-04-18 post P9A close (`2e138d6` on `origin/data-improve`).
**Branch**: `data-improve`.
**Mode**: Gen-Verifier. critic-carol cycle 3 (final before rotation to dave).
**Predecessor**: P9A closed with 3-consecutive-first-try PASS streak. carol's cycle-2 learnings recommend ADVERSARIAL opening for cycle 3.
**User ruling 2026-04-18**: "同 commit 就行... 按照你和 critic 的建议继续推进 P9B".

## Scope — ONE commit delivers

All three DT items in same commit per user ruling. Scout-verified all three are genuine gaps with clear code locations.

### S1 — DT#2 RED force-exit sweep (marker-based)

**Law** (`zeus_current_architecture.md §17` L364): "sweep active positions toward exit"; **forbids** entry-block-only RED scope.

**Pre-P9B state** (scout-verified):
- `src/engine/cycle_runner.py:246-250`: `force_exit=True` sets `force_exit_review_scope="entry_block_only"` and logs warning. No sweep.
- No function named `force_exit_sweep` / `force_close_active` / `emergency_exit` anywhere in `src/`.
- `test_red_triggers_active_position_sweep` stub at `tests/test_dual_track_law_stubs.py:162-164` currently `pytest.skip("pending: enforced in risk phase before Phase 9")`.

**P9B delivery**:
- New function `_execute_force_exit_sweep(portfolio, clob, conn, tracker) -> dict` in `src/engine/cycle_runner.py`.
- Iterates `portfolio.positions`; for each non-terminal position, sets `pos.exit_reason = "red_force_exit"` + marks `portfolio_dirty=True`.
- Does NOT post sell orders in-cycle — the `exit_lifecycle.py` machinery (`handle_exit_pending_missing`, DEFERRED_SELL_FILL path) is already in place and consumes `exit_reason` on the next monitor_refresh cycle.
- Summary returned: `{"attempted": N, "already_exiting": M, "skipped_terminal": T}`.
- `force_exit` branch (L246-250) upgrades: scope string changes to `"sweep_active_positions"`; sweep function called; summary merged; positions persisted via existing `save_portfolio` at L333.
- Pre-P9B log message updated: no longer "Scope: entry-block only".

**Antibody R-BV** (un-skip + flesh out `test_red_triggers_active_position_sweep`):
- Given: portfolio with 3 active positions + `get_force_exit_review()→True`.
- When: `cycle_runner.run_cycle` executes the force_exit branch.
- Then: all 3 positions have `exit_reason="red_force_exit"`; `summary["force_exit_sweep"]["attempted"]==3`; `summary["force_exit_review_scope"]=="sweep_active_positions"`.

### S2 — DT#5 Kelly executable-price strict enforcement

**Law** (`zeus_current_architecture.md §20` L414-415): "Sizing inputs at the Kelly boundary must describe an executable price distribution, not a single static `entry_price`". INV-21 / NC-14.

**Pre-P9B state** (scout-verified):
- `src/strategy/kelly.py:24-30` signature: `def kelly_size(p_posterior: float, entry_price: float, bankroll, kelly_mult, safety_cap_usd)`. Bare float.
- `src/contracts/execution_price.py` L23-92: `ExecutionPrice` dataclass EXISTS with `assert_kelly_safe()` method enforcing three rules (price_type != implied_probability; fee_deducted=True; currency="probability_units").
- Callers at `src/engine/evaluator.py:187-205`: two call sites. L187 passes `ep_fee_adjusted.value` (extracts scalar from ExecutionPrice — lossy). L199 passes bare `raw_entry_price` (shadow comparison lane, P9-D3 transition residue).
- `test_kelly_input_carries_distributional_info` stub at `tests/test_dual_track_law_stubs.py:125-127` currently `pytest.skip("pending: enforced pre-Phase 9 activation")`.

**P9B delivery**:
- `kelly_size` signature strict-type-changed: `entry_price: ExecutionPrice` (import from `src.contracts.execution_price`). Inside function body: `entry_price.assert_kelly_safe()` at top. All internal math uses `entry_price.value`.
- Caller update evaluator.py L187: pass `ep_fee_adjusted` (the full ExecutionPrice object, not `.value`).
- Caller update evaluator.py L199 (shadow raw-compare lane): simplest option — remove the `kelly_size(..., raw_entry_price, ...)` call. P9-D3 shadow served its transition purpose; P9B enforces strict. Shadow log at L207-209 continues to log the fee_adjusted size vs an informational raw-compare (computed without going through kelly_size).
- If removing shadow raw call is too invasive, fallback: wrap `raw_entry_price` as ExecutionPrice but this would fail `assert_kelly_safe()` — violates the new contract on purpose. Cleaner: remove.

**Antibody R-BW** (un-skip + flesh out `test_kelly_input_carries_distributional_info`):
- (a) `kelly_size(p_posterior=0.6, entry_price=0.5, bankroll=1000.0)` (bare float) → `TypeError` (type mismatch).
- (b) `kelly_size(..., entry_price=ExecutionPrice(value=0.5, price_type="implied_probability", fee_deducted=False, currency="probability_units"))` → `ExecutionPriceContractError`.
- (c) `kelly_size(..., entry_price=ExecutionPrice(value=0.5, price_type="fee_adjusted", fee_deducted=True, currency="probability_units"))` → returns positive size.

### S3 — DT#7 boundary-day runtime contract function

**Law** (`zeus_current_architecture.md §22`): "reduce leverage on boundary-candidate positions; isolate oracle penalty for the affected city; refuse to treat boundary-ambiguous forecasts as confirmatory signal".

**Pre-P9B state** (scout-verified):
- `src/contracts/snapshot_ingest_contract.py:60-61` reads `boundary_ambiguous` from ingest payload.
- `src/state/schema/v2_schema.py:144-145` schema has `boundary_ambiguous INTEGER NOT NULL DEFAULT 0`.
- **ZERO runtime consumer** in `src/engine/evaluator.py` — scout confirmed.
- Oracle penalty is per-city (`src/strategy/oracle_penalty.py`) with no boundary-day isolation.
- No test for DT#7 runtime policy.

**P9B delivery** (minimal — code-ready, activation-deferred to P9C):
- New module `src/contracts/boundary_policy.py` (or inline in `src/contracts/snapshot_ingest_contract.py` if preferred):
  - `def boundary_ambiguous_refuses_signal(snapshot_dict) -> bool`: returns `bool(snapshot_dict.get("boundary_ambiguous", False))`.
  - Docstring cites §22 law + clarifies that "refusal" is the P9B policy (reject candidate); leverage-reduction + oracle-isolation variants are P9C scope.
- Evaluator integration — **deferred to P9C**. Scout confirmed `boundary_ambiguous` does not reach evaluator's candidate flow today; plumbing lands with monitor_refresh LOW wiring (P9C). P9B delivers the NAMED function so P9C has a stable seam to wire.
- The P9B deliverable is the **contract** (function + antibody); P9C is the **enforcement** (wire into evaluator's candidate decision).

**Antibody R-BX**:
- `boundary_ambiguous_refuses_signal({"boundary_ambiguous": True})` → `True`.
- `boundary_ambiguous_refuses_signal({"boundary_ambiguous": False})` → `False`.
- `boundary_ambiguous_refuses_signal({})` → `False` (absence = permissive default; safe fallback).
- `boundary_ambiguous_refuses_signal({"boundary_ambiguous": "truthy_string"})` → `True` (bool coercion).

## Acceptance gates

1. **Full regression ≤ baseline**: post-P9A baseline is `2e138d6`: 144 failed / 1851 passed / 95 skipped / 7 subtests. Post-P9B: ≤144 failed, ≥1854 passed (+3 minimum from R-BV/R-BW/R-BX; may be more if un-skipping adds sub-assertions), zero new failures.
2. **R-BV / R-BW / R-BX**: all GREEN. Two un-skipped stubs (`test_red_triggers_active_position_sweep` + `test_kelly_input_carries_distributional_info`) must now actually run and pass.
3. **P5/P6/P7A/P7B/P8/P9A targeted suites unchanged-green**.
4. **critic-carol cycle 3 PASS** (she opens in adversarial-mode 15 min per her cycle-2 recommendation).
5. **Hard constraints preserved**: no TIGGE import, no v2 writes, no DDL, no evaluator metric-routing changes, Golden Window intact.

## Hard constraints (forbidden moves)

- **NOT scope**: DT#7 full enforcement (leverage reduction + oracle penalty isolation + evaluator wiring) — P9C, blocks on monitor_refresh LOW data flow.
- **NOT scope**: monitor_refresh LOW wiring (P9C).
- **NOT scope**: B093 half-2 replay migration (P9C, blocks on Golden Window lift).
- **NOT scope**: `Day0LowNowcastSignal.p_vector` proper impl (P9C, Gate F prep).
- **NOT scope**: `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` mapping re-audit (P9C or later).
- **NOT scope**: `--temperature-metric` CLI flag (deferred hygiene).
- No TIGGE data import.
- No v2 table population (all zero-row unchanged).
- No SQL DDL.
- No evaluator metric-routing changes.

## R-letter range

P9B uses **R-BV + R-BW + R-BX** (new). P9B un-skips 2 pre-existing stubs (`test_red_triggers_active_position_sweep` + `test_kelly_input_carries_distributional_info`) and fleshes them into full antibodies bearing the R-letters.

## P3.1 guard-removal vocab check

- DT#2 sweep UPGRADES an entry-block scope to a sweep scope — not a guard removal. P3.1 check: grep `_entry_block_only|_forced_exit_forbidden|_sweep_not_implemented` in tests. Expected: empty (pre-P9B law-stub tests use skip, not antibody).
- DT#5 strict-type-change RESTRICTS the `kelly_size` interface (narrows accepted types). Not a guard removal; it ADDS a guard (type check + assert_kelly_safe). P3.1 check: grep for any test passing bare float to kelly_size — will become type-error, needing update.
- DT#7 adds a new contract function — no guard removal.

## Critic-carol cycle 3 brief (explicit adversarial-mode opening)

Per critic-carol cycle 2 learning L6: "3-pass streak is real complacency warning". Her explicit recommendation:
> "For cycle 3 (P9B), I recommend adversarial hunting mode for the first 15 minutes before falling back to thorough."

Cycle 3 is her final before rotation to critic-dave (3-cycle convention). Focus areas:
- **DT#5 type-change**: does the strict signature break any existing caller outside evaluator? (Grep all `kelly_size(` calls across src + tests.)
- **DT#2 sweep**: does marking `exit_reason` on existing-active-exiting positions (e.g., `exit_reason="SLO_TRIGGER"`) corrupt their in-flight exit flow? Audit exit_lifecycle's handling of overridden exit_reason.
- **DT#7 contract function**: is the pure function actually callable from expected future wiring sites? Or is it orphaned code (Fitz P3 immune-system fake)?
- **Scope bleed**: did P9B touch any monitor_refresh / evaluator-metric-routing / v2-table code paths?

Upgrade to `general-purpose` subagent_type (not `critic`) so she has Write/Edit — fix the methodology gap she flagged across cycles 1+2. Pre-commitment predictions still required. If PASS, record the 4-consecutive-first-try-PASS streak explicitly + rotation trigger for P9C (dave).

## Evidence layout

- This contract: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase9b_contract.md`
- Evidence dir (shared with P9A): `phase9_evidence/`
- critic-carol cycle 3 wide review: `phase9_evidence/critic_carol_phase9b_wide_review.md`
- critic-carol cycle 3 learnings (if final cycle): `phase9_evidence/critic_carol_phase9b_learnings.md`

## Forward-log remaining post-P9B → P9C

- DT#7 full enforcement (evaluator wiring + leverage reduction + oracle isolation)
- `Day0LowNowcastSignal.p_vector` proper implementation pre-Gate F
- `monitor_refresh.py` LOW wiring
- B093 half-2 replay → historical_forecasts_v2 (blocks on Golden Window lift)
- `--temperature-metric` CLI flag
- Second-seam data-closure tests for R-BP forecast_low column selection
- `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` re-audit
- `save_portfolio` `source` parameter for DT#6 B enforcement
- `tick_with_portfolio` persistence contract decision (persist vs document ephemeral)

---

*Authored*: team-lead (Opus, main context), 2026-04-18 post-P9A close.
*Authority basis*: user ruling 2026-04-18 (same commit) + scout verification 2026-04-18 (all 3 DT gaps confirmed with code locations) + critic-carol cycle-2 recommendation for adversarial-opening cycle 3.
