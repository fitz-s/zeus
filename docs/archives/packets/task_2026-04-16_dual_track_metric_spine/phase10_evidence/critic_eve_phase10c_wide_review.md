# critic-eve cycle-1 Phase 10C Wide Review

**Date**: 2026-04-19
**HEAD reviewed**: staged impl on top of `4248525` (team-lead owns commit per L22)
**Branch**: `data-improve`
**Status**: eve cycle 1 complete. 2 cycles remaining before rotation.

---

## Verdict: PASS-WITH-RESERVATIONS → commit + push granted (with 2 pre-commit actions)

## Precommit-prediction hit-rate

| # | Prediction | Outcome |
|---|---|---|
| 1 | C1 type collision hit in first hour | **HIT** (flagged as SAVEPOINT + `with conn:` collision) |
| 2 | S4 test callsite expansion | **MISS** (tightly scoped) |
| 3 | S7 activated test rewrites 1-2x | **PARTIAL** (passes first try; 5 monkeypatch stubs suggest iteration pre-landing) |
| 4 | SAVEPOINT lands clean first try | **MISS** (required restructure: moved helper outside block) |
| 5 | S8 CSV flip unproblematic | **HIT** (R-CX.1 guard passes) |

2 HIT / 1 PARTIAL / 2 MISS. Respectable cycle-1.

## Probe results

- **A — SAVEPOINT covers 2/3 writes** — CRITICAL downgraded to MAJOR via Realist Check. `_dual_write_canonical_entry_if_available` runs OUTSIDE SAVEPOINT; mitigated by helper's internal RuntimeError swallow at `cycle_runtime.py:290-292`. Partial-write window bounded by `append_many_and_project` projection-layer idempotence.
- **B — 300→1000 window** — PASS. Invariant preserved by marker-position assertion in `tests/test_architecture_contracts.py:3424-3436`.
- **C — R-CU.1 AST-walk** — PASS. Structural (not string-match), catches adding 5th callsite without round_fn.
- **D — S4 escape hatch** — PASS. Documented in store.py docstring + R-CT.1/2 tests both dispatch branches.
- **E — S7 INV-20 mock** — PASS. Correct target `choose_portfolio_truth_source`.
- **F — L22 discipline** — PASS. HEAD unchanged.
- **G — ledger.py `with conn:` audit** — confirmed 3 sites (`:79, :163, :187`); P10D forward-log candidate.

## Findings

### MAJOR-1 — DT#1 atomicity language mismatch
Contract v2 §S6 implies 3-write atomicity; actual is SAVEPOINT-covers-2 + post-block-best-effort-3rd with defensive swallow. Not blocking (mitigation strong), but contract should be honest. **Fix: add one-line caveat to contract.**

### MAJOR-2 — Regression count off by 2
Team-lead: 144/1921. Eve fresh: 146/1919. Delta-direction is net-positive (-11 failed / +12 passed) either way. Topology_doctor flake likely. **Fix: commit message states "144-146 failed envelope"**.

### MINOR findings
1. R-CV.2 simulates SAVEPOINT in-test rather than exercising `execute_discovery_phase` — integration test would be stronger (P10D candidate)
2. `_dual_write_canonical_entry_if_available` silently swallows RuntimeError at DEBUG log level — WARNING or telemetry counter would be better
3. Runtime state files modified alongside P10C — `git checkout` before commit

## Regression
Reproduced 146/1919/92 (eve fresh run) vs team-lead's 144/1921/92. Both show +12 passed, -1 skipped vs post-P10B baseline. Net delta clean.

## Durable learnings L27-L30 (eve cycle-1 legacy)

- **L27** — Type collisions in impl often reveal unspecified atomicity assumptions. Precommit should classify collisions as (a) syntactic or (b) semantic-atomicity tradeoff. (b) needs explicit contract language.
- **L28** — Regression baseline should be reproduced by critic, not accepted from team-lead. Flake envelope matters; critic always states their own measurement.
- **L29** — Post-try-block helpers need explicit failure-mode docs. When a helper is moved OUTSIDE a try/except for tx reasons, its own exception contract becomes load-bearing.
- **L30** — `with conn:` audit across ledger.py is a standing antibody candidate. `src/state/ledger.py:79, 163, 187` all use the pattern; any nested SAVEPOINT caller faces the same collision. P10D should add AST rule: "no function that calls `append_many_and_project` may itself be called inside a SAVEPOINT block".

## Commit + push permission: GRANTED

Pre-commit actions required:
1. `git checkout state/auto_pause_failclosed.tombstone state/status_summary.json` (drop runtime noise)
2. Commit message envelope: "Net delta: -11 failed / +12 passed / -1 skipped (144-146 failed envelope vs topology_doctor flake)"

Optional iteration:
- Add one-line caveat to contract v2 §S6 DT#1 clarifying SAVEPOINT-2 + post-block-3rd atomicity pattern (non-blocking; can ship today + doc patch follow-up)

---

*eve cycle 1 complete. Next review: post-P10D or follow-up slice.*
