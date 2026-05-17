# WAVE 3 CRITIC — Post-Mortem Review

**Critic**: fresh-context sonnet (WAVE_3_CRITIC.md)
**Date**: 2026-05-16
**Scope**: 4 commits: `53f1185087` (TIER 0C), `2d5aa611a7` (Batch A), `d0fc900d1c` (Batch B), `32e5e2a307` (Batch C+D absorbed)

---

## VERDICT: REVISE

**Reason**: 10 of 16 Batch D docs are unaccounted for — no fix commit, no false-positive declaration, no explicit DONE verdict. This includes TIER 0 truth-owning surfaces (`src/state`, `src/riskguard`, `src/venue`). The §8.5 Rule 5 stop-condition requires an explicit verified-clean or fixed verdict per doc, not silence. Additionally, 6 of 8 fixes in the absorbed commit `32e5e2a307` have no PROVENANCE TRIPLE — a direct §8.5 Rule 4 violation.

---

## Probe Results Table

| Probe | Result | Severity |
|-------|--------|----------|
| 1. PROVENANCE completeness on `32e5e2a307` | FAIL — 6 of 8 fixes unenumerated | MAJOR |
| 2. Drift reality (5 spot-checks) | PASS | — |
| 3. Fix correctness (5 spot-checks) | PASS | — |
| 4. GAP markers in constitution.md | PASS — exactly 4, each with substantive content | — |
| 5. False-positive verification (3 spot-checks) | PASS | — |
| 6. Canonical zeus contamination | PASS — canonical branch is `main`, no 2026-05-17 commits | — |
| 7. Topology_doctor error delta | PASS (marginal) — 838 → 835 | — |
| 8. §8.5 compliance spot-checks | PARTIAL FAIL — Batch B missing `AGENTS-NAV:` header | MINOR |
| 9. False-positive rate assessment | Plausible overcaution — supported by FP spot-checks | — |
| 10. PR LOC impact (WAVE 3 only) | PASS — 12 files, +89/-21 lines, proportionate | — |

---

## Major Findings

### F1 — MAJOR: 10 Batch D docs unaccounted for (scope closure failure)

`SCOUT_TIER_1_INVENTORY.md` defines Batch D as 16 docs. Commit `32e5e2a307` absorbed 6 of them (verified via `git diff 32e5e2a307~1 32e5e2a307 --name-only`). The remaining 10 appear in no commit, no false-positive list, and no explicit DONE declaration:

```
src/observability/AGENTS.md
src/state/AGENTS.md           ← TIER 0 (K0/K1 Truth Ownership)
src/riskguard/AGENTS.md       ← TIER 0
src/venue/AGENTS.md           ← TIER 0
src/data/AGENTS.md
src/engine/AGENTS.md
src/control/AGENTS.md
docs/operations/edge_observation/AGENTS.md
docs/operations/attribution_drift/AGENTS.md
docs/reference/modules/AGENTS.md
```

`src/state`, `src/riskguard`, and `src/venue` are TIER 0 surfaces by REVIEW.md classification — exactly the surfaces where stale AGENTS.md entries silently corrupt fresh-agent navigation. My quick spot-check on `src/state/AGENTS.md` found key files all present, but the §8.5 Rule 5 stop-condition requires citation grep-verification within 10 min — not file existence checks. That verification was never performed.

**Fix**: process each of the 10 docs: run the standard SCOUT + FCI4 checks; commit fixes with `AGENTS-NAV:` header + provenance triple if drifts found; otherwise log an explicit false-positive declaration in this critic report or a follow-up commit message.

---

### F2 — MAJOR: 6 of 8 fixes in `32e5e2a307` have no PROVENANCE TRIPLE

The commit message PROVENANCE TABLE for `32e5e2a307` enumerates exactly 2 fixes:
- `scripts/AGENTS.md` — python3 → module invocation
- `src/AGENTS.md` — 16 → 20 packages

But `git diff 32e5e2a307~1 32e5e2a307 --name-only` shows 8 files changed. The 6 unenumerated fixes (all from absorbed Batch D):

```
docs/operations/calibration_observation/AGENTS.md  — :242-264 → ::_ensure_versions_table
docs/operations/learning_loop_observation/AGENTS.md — :242-264 → ::_ensure_versions_table
docs/operations/ws_poll_reaction/AGENTS.md          — L114-126 → ::Strategy families table
src/execution/AGENTS.md                             — UPSERT description → INSERT OR REPLACE
src/signal/AGENTS.md                                — 3 stale file entries deleted
src/strategy/AGENTS.md                              — 4 substitutions (OracleStatus enum, path, thresholds)
```

§8.5 Rule 4 requires REPLACES / WHY / VERIFIED-AT for every changed authoritative statement, stored in the commit message footer. These 6 fixes have none. The fix content itself is correct (verified via grep: `_ensure_versions_table` exists at `src/calibration/retrain_trigger.py`; `OracleStatus.BLACKLIST` at `src/strategy/oracle_status.py:30`; `INSERT OR REPLACE` at `src/execution/harvester.py:1300`). The audit trail is missing, not the correctness.

**Fix** (choose one): (a) amend `32e5e2a307` to append provenance triples for the 6 unenumerated fixes; (b) create a follow-up commit `fix(wave-3-provenance-addendum)` with zero file changes and only the missing provenance entries in the commit body.

---

## Minor Findings

### F3 — MINOR: Batch B (`d0fc900d1c`) missing `AGENTS-NAV:` commit header

PLAN.md §5 WAVE 3 states the `AGENTS-NAV: <doc>::<section> [REASON: ...]` prefix is "mandatory" for every WAVE 3 drift fix. `d0fc900d1c` has 1 real drift fix (`.github/workflows/AGENTS.md`) but no `AGENTS-NAV:` header line. The PROVENANCE TABLE is present and correct; only the header prefix is absent. Fix is correct (all 5 workflow files now registered, matches `ls .github/workflows/*.yml`).

---

## What Passed

**Drift reality (Probe 2)**: All OLD-text citations verified pre-commit:
- `scripts/AGENTS.md` pre-`32e5e2a307`: `python3 scripts/topology_doctor.py --scripts --json` — confirmed present.
- `src/AGENTS.md` pre-`32e5e2a307`: `16 packages organized` — confirmed present.
- `.github/workflows/AGENTS.md` pre-`d0fc900d1c`: only `architecture_advisory_gates.yml` listed — confirmed (single-row table observed).

**Fix correctness (Probe 3)**: All NEW citations verify post-commit:
- `PYTHONPATH=. python -m scripts.topology_doctor --scripts --json` — present in `scripts/AGENTS.md`.
- `20 packages organized` — present in `src/AGENTS.md`.
- `OracleStatus.BLACKLIST` — enum value at `src/strategy/oracle_status.py:30`.
- `INSERT OR REPLACE` — at `src/execution/harvester.py:1300`.
- Deleted signal files (`day0_residual.py`, `day0_residual_features.py`, `forecast_error_distribution.py`) — absent from disk (were never in git history; AGENTS.md entries were orphaned).

**GAP markers (Probe 4)**: `grep -c 'GAP_NEEDS_OPERATOR_2026-05-17' docs/authority/zeus_change_control_constitution.md` → 4. Each has an inline HTML comment with substantive explanation (dead-symbol identity, verification command, action options). The 4 markers correspond to: `fold_event`, `apply_transition`, `StrategyKey`, and the `position_events` INSERT prohibition. All correctly placed.

**False-positive verification (Probe 5)**: Three FP spot-checks:
- `config/reality_contracts/AGENTS.md` (Batch B): 4 YAML files present, `tests/test_reality_contracts.py` exists, INV-11 correctly cited. Clean.
- `src/contracts/AGENTS.md` (Batch C): 5 key files spot-checked — all present. Clean.
- `docs/review/AGENTS.md` (Batch C): `code_review.md` and `review_scope_map.md` both present. Clean.
Supports the ~75% false-positive rate as genuine scout overcaution, not worker laziness.

**Canonical contamination (Probe 6)**: `git -C /Users/leofitz/.openclaw/workspace-venus/zeus log --since='2026-05-17 00:00' --oneline` returns only hotfix/PR-merge commits on `main`. No WAVE 3 commits on canonical. PASS.

**Topology_doctor delta (Probe 7)**: Baseline = 838 errors; post-WAVE-3 = 835 errors (3 net reduction). Low absolute drop is consistent with PLAN scope exclusions (TIER 2/3 deferred, `source_rationale_missing` × 50 declared out-of-scope). No regression. PASS.

**TIER 0C constitution.md (Probe 8 / §8.5)**: All 4 surgical fixes are 1-line each. All 4 GAP markers are inline comments (0 net lines to document body). AGENTS-NAV header present. PROVENANCE TABLE has all 8 entries with REPLACES / WHY / VERIFIED-AT per §8.5 Rule 4.

**PR LOC impact (Probe 10)**: WAVE 3 only diff: 12 files changed, +89/-21. Proportionate for 8 doc-alignment fixes + 4 GAP markers + 1 deletion block.

---

## Required Before WAVE 4 Gate

| # | Action | Assignee |
|---|--------|----------|
| R1 | Process 10 unaccounted Batch D docs (FCI4 grep-verify each; fix or declare FP with evidence) | Executor (fresh Batch D worker) |
| R2 | Append PROVENANCE TRIPLE for 6 unenumerated `32e5e2a307` fixes — amend commit or add follow-up provenance-addendum commit | Executor |
| R3 | Note Batch B `AGENTS-NAV:` header gap — no re-fix required unless operator mandates header-only amend | Orchestrator to decide |

WAVE 4 (final reality audit + PR open) is gated on R1 and R2. R3 is informational.

---

## Self-Audit

- F1 (Batch D scope gap): HIGH confidence. SCOUT_TIER_1_INVENTORY defines 16 Batch D docs; only 6 appear in `git diff 32e5e2a307~1 32e5e2a307 --name-only`; remaining 10 absent from all FP lists and all commits. Evidence is deterministic.
- F2 (PROVENANCE gap): HIGH confidence. Commit message body has 2 entries in PROVENANCE TABLE; diff has 8 files. No author can refute.
- F3 (AGENTS-NAV prefix): HIGH confidence. PLAN.md §5 says "mandatory"; commit body contains no `AGENTS-NAV:` line.

Realist check on F1: my spot-check of `src/state` and `src/riskguard` found key files all present — no obvious dead-paths. In the best case these 10 docs are true FPs (scout overcaution). But the §8.5 Rule 5 verification was not run, and the audit-of-audit antibody (`feedback_audit_of_audit_antibody_recursive`: 50% baseline self-error rate on STALE/CLEAN verdicts) means "looks fine on first glance" is not an adequate substitute. Severity stands at MAJOR.

Realist check on F2: the fixes are CORRECT; only the audit trail is missing. Probability of operational damage from this gap is low. However, the whole point of provenance triples is future-agent traceability, and the gap is provably present. Severity stays MAJOR but this is among the easier fixes in this phase.

---

*Mode: THOROUGH. No escalation to ADVERSARIAL — 2 MAJOR findings, no CRITICAL, no systemic pattern suggesting hidden cascading failures.*
