# Slice K1.G6 — LIVE_SAFE_STRATEGIES typed frozenset + boot assert

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: parent packet `docs/operations/task_2026-04-26_live_readiness_completion/plan.md §5 Wave 1` + source workbook `docs/to-do-list/archive/2026-04-26_closed/zeus_live_readiness_upgrade_checklist_2026-04-23.md` row G6 (P0 — wave 3 in original sequencing; promoted to first-Wave-1 here for low-blast-radius reasons).
Status: planning evidence; implementation has NOT begun.
Branch: `claude/live-readiness-completion-2026-04-26`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-readiness-2026-04-26`

## 0. Scope statement

Make non-allowlisted live execution **unconstructable at boot** by introducing a typed `LIVE_SAFE_STRATEGIES` atom and refusing to start the live daemon if any enabled strategy is outside the set.

Today's situation (verified 2026-04-26):
- 4 known strategies live in `src/engine/cycle_runner.py:KNOWN_STRATEGIES` and `src/state/strategy_tracker.py:STRATEGIES`: `settlement_capture`, `shoulder_sell`, `center_buy`, `opening_inertia`.
- The only operator-approved live-safe strategy is `opening_inertia` (per the workbook's pro/con-Opus convergence).
- The current safety mechanism is `set_strategy_gate` (`src/control/control_plane.py:332`) — operator-driven, advisory-only per `src/supervisor_api/contracts.py:141`. A typo or missed disable lets the other 3 strategies execute under live mode.

Fitz §1: replace "I see 3 latent bugs (3 strategies could leak)" with "1 structural decision (typed allowlist + boot assert)".

## 1. K-decision lineage

This slice closes 1 of 3 decisions under K1 (parent plan):
- K1.G6 — LIVE_SAFE_STRATEGIES (this slice)
- K1.G7 — LIVE_SAFE_CITIES (Wave 2; same pattern; gated on pr18 worktree settling executor.py)
- K1.B4 — physical-bounds CHECK (independent slice; similar typed-bound idea but at write-path)

## 2. Files touched

| File | Change | Hunk size |
|---|---|---|
| `src/control/control_plane.py` | Add `LIVE_SAFE_STRATEGIES: frozenset[str] = frozenset({"opening_inertia"})` near top + `assert_live_safe_strategies_under_live_mode(enabled: Iterable[str]) -> None` helper that raises `SystemExit` (matching existing `sys.exit("FATAL: ...")` boot pattern at `src/main.py:473`) when ZEUS_MODE=live and any name not in set | ~20 lines added |
| `src/main.py` | Inside the live-mode boot guard at L472-477, AFTER ZEUS_MODE=live validation passes, call `assert_live_safe_strategies_under_live_mode(_resolve_enabled_strategies())` where `_resolve_enabled_strategies()` reads control_plane state for which strategies have `gate.enabled=True`. SystemExit trickles up before scheduler launch | ~12 lines added |
| `tests/test_live_safe_strategies.py` (NEW) | 6 antibody tests — see §4 | ~120 lines |
| `architecture/test_topology.yaml` | Register new test under appropriate category | 1 line added |

**No edits** to `src/engine/cycle_runner.py:KNOWN_STRATEGIES`, `src/state/strategy_tracker.py:STRATEGIES`, `src/state/portfolio.py`, or `src/engine/evaluator.py` strategy-assignment logic. Those names are the universe of POSSIBLE strategies; LIVE_SAFE_STRATEGIES is the subset PERMITTED under live mode. The assertion is at boot, not at every assignment.

## 3. Worktree-collision check (re-verified 2026-04-26)

- `src/control/control_plane.py`: NOT touched by `zeus-pr18-fix-plan-20260426` or `zeus-fix-plan-20260426` (re-verified `git -C <worktree> diff --name-only main..HEAD | grep control_plane`). SAFE.
- `src/main.py`: NOT touched by either active worktree. SAFE.
- `tests/test_live_safe_strategies.py`: NEW file. SAFE.
- `architecture/test_topology.yaml`: touched by `zeus-fix-plan-20260426` (P3 plan + tooling registration) — companion file; mesh maintenance only; soft-warn acceptable.

## 4. Antibody test design (Fitz §1: relationship-test-first)

`tests/test_live_safe_strategies.py` shipped RED first, then implementation flips it green.

| Test | Asserts | Why |
|---|---|---|
| `test_live_safe_strategies_is_frozenset_of_str` | `isinstance(LIVE_SAFE_STRATEGIES, frozenset)` and all members are `str` | Type discipline — prevents accidental list/set substitution |
| `test_live_safe_strategies_pins_current_allowlist` | `LIVE_SAFE_STRATEGIES == {"opening_inertia"}` | Pin current operator-approved set; future additions must come through explicit packet (no silent drift) |
| `test_live_safe_strategies_subset_of_known_strategies` | `LIVE_SAFE_STRATEGIES <= KNOWN_STRATEGIES` (from `src/engine/cycle_runner.py`) | Cross-module relationship invariant — no name in allowlist that the engine doesn't know about |
| `test_assert_live_safe_strategies_under_live_mode_silent_when_safe` | Calling helper with `{"opening_inertia"}` under `ZEUS_MODE=live` returns None silently | Happy path |
| `test_assert_live_safe_strategies_under_live_mode_raises_on_unsafe` | Calling helper with `{"center_buy"}` under `ZEUS_MODE=live` raises `SystemExit` with FATAL message naming the offending strategy | Negative path — the entire reason this slice exists |
| `test_assert_live_safe_strategies_silent_under_paper_mode` | Calling helper with `{"center_buy"}` under `ZEUS_MODE=paper` is silent (no raise) | Live-only enforcement; paper sessions remain experimental |

Bonus (relationship test):
| `test_main_boot_under_live_mode_refuses_unsafe_enabled_strategy` | Patch `_resolve_enabled_strategies()` to return `{"center_buy"}`, set `ZEUS_MODE=live`, import `src.main` boot path, assert `SystemExit` raised before scheduler initialization | End-to-end boot guard |

## 5. RED→GREEN sequence

1. Write `tests/test_live_safe_strategies.py` with all 7 tests; pytest red on imports / asserts (frozenset/helper don't exist yet).
2. Commit RED: `Slice G6 RED — LIVE_SAFE_STRATEGIES antibody (no impl yet)`.
3. Add frozenset + helper to `src/control/control_plane.py`; tests 1-6 turn green.
4. Add boot-time call in `src/main.py`; test 7 turns green.
5. Register test in `architecture/test_topology.yaml`.
6. Commit GREEN: `Slice G6 GREEN — wire LIVE_SAFE_STRATEGIES into live boot`.
7. Run regression panel: `pytest -q tests/test_architecture_contracts.py tests/test_live_safety_invariants.py tests/test_cross_module_invariants.py` — must show no NEW failures vs current branch baseline.
8. Write `receipt.json` and close slice.

## 6. Acceptance criteria

- All 7 tests in `tests/test_live_safe_strategies.py` green.
- Lifecycle headers (`# Lifecycle:` + `# Purpose:` + `# Reuse:` + `# Authority basis:`) present on the new test.
- `architecture/test_topology.yaml` lists the test.
- Regression panel shows no NEW failures (pre-existing failures may persist; document those in the receipt).
- `receipt.json` records: branch, GREEN commit hash, test counts, regression delta (NEW failures = 0).
- `work_log.md` records each step's commit + decision.

## 7. Out-of-scope for this slice

- G7 (LIVE_SAFE_CITIES) — same pattern, separate file, gated on pr18 settling executor.py. Will reuse this slice as a template.
- Removing or modifying `set_strategy_gate` advisory mechanism. The new boot assert sits ABOVE the gate; both can coexist.
- Any modification of `KNOWN_STRATEGIES` or `STRATEGIES` lists. Those define the universe of buildable strategies; the allowlist is a different axis.
- Any change to `src/engine/evaluator.py` strategy-assignment logic. Boot-time refusal is the antibody; per-cycle filtering is a different concern (R-track if needed).

## 8. Provenance

Recon performed live 2026-04-26 in this worktree:
- `src/main.py:472-477` — `ZEUS_MODE=live` boot guard pattern (current FATAL exit pattern).
- `src/control/control_plane.py:1-40` — module structure / COMMANDS set / set_strategy_gate handler.
- `src/engine/cycle_runner.py` — `KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}`.
- `src/state/strategy_tracker.py` — `STRATEGIES = ["settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"]` (list form).
- `src/control/control_plane.py:332,358,471` — `set_strategy_gate` is currently the only enable/disable mechanism, advisory per `src/supervisor_api/contracts.py:141`.
