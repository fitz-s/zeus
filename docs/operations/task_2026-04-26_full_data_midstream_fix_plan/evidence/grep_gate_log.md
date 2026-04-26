# Grep-Gate Log — Workbook citations vs current source

Created: 2026-04-26
Last audited: 2026-04-26
Authority basis: `feedback_grep_gate_before_contract_lock.md` (L20) — citations grep-verified within 10 min before contract lock
Source HEAD at audit: `6499f9c` (origin/midstream_remediation) for source files; `6e2b4b8` (origin/copilot/full-review-of-upstream-data-storage) for workbook content
Audit window: 2026-04-26 within 10 minutes prior to plan.md write

---

## Method

For each workbook citation, run a fresh grep against the current `src/` tree on the plan branch's base. Record:

- **Cited at**: workbook claim (file + line if given, else file + symbol).
- **Verified at**: actual current location (`file:line` or `file:line — symbol`).
- **Status**: `CURRENT` (verbatim or trivially equivalent) | `CURRENT, ENRICHED` (workbook understated severity) | `STALE` (citation rotted) | `DISCREPANCY` (workbook contradicts code).

---

## Source-citation verifications

### Finding 5 — `manager.py` calls metric-blind functions

**Cited at**: `src/calibration/manager.py` calls `get_decision_group_count(conn, cluster, season)` and `get_pairs_for_bucket(..., bin_source_filter="canonical_v1")`.

**Verified at**:
- `src/calibration/manager.py:193`: `n = get_decision_group_count(conn, cluster, season)` — exact match, no metric arg.
- `src/calibration/manager.py:275`: `pairs = get_pairs_for_bucket(conn, cluster, season, bin_source_filter="canonical_v1")` — exact match, no metric arg.

**Status**: CURRENT.

### Finding 5 (continued) — `store.py` lacks metric filter

**Cited at**: `src/calibration/store.py` has no `temperature_metric` filter on `get_pairs_for_bucket` and `get_decision_group_count`.

**Verified at**:
- `src/calibration/store.py:207`: `def get_pairs_for_bucket(...)` — signature includes `bin_source_filter: str | None = None`, NO `temperature_metric` parameter.
- `src/calibration/store.py:292`: `def get_decision_group_count(...)` — confirmed no metric arg in signature.

**Status**: CURRENT.

### Finding 6 — Maturity gate vs refit input subset mismatch

**Cited at**: `get_decision_group_count()` counts all VERIFIED groups in cluster/season; `_fit_from_pairs()` reads only `canonical_v1` rows.

**Verified at**:
- Maturity-gate call at `manager.py:193` uses no `bin_source_filter` (counts all bin_sources) and no `temperature_metric` (counts both metrics).
- Refit-input call at `manager.py:275` uses `bin_source_filter="canonical_v1"` (one bin_source only) and no `temperature_metric` (both metrics).

**Status**: CURRENT — subset mismatch is real on `bin_source` axis AND on `temperature_metric` axis.

### Finding 7 — Snapshot stamp `or "high"` fallback

**Cited at**: `src/engine/evaluator.py::_store_ens_snapshot()` uses fallback of `"high"` when `ens.temperature_metric` is missing.

**Verified at**:
- `src/engine/evaluator.py:1818` (post-rebase, was 1819 pre-rebase): `snap_metric = getattr(getattr(ens, "temperature_metric", None), "temperature_metric", "high") or "high"` — DOUBLE fallback (`getattr` default + `or` clause).
- `src/engine/evaluator.py:1801` (post-rebase, was 1802 pre-rebase): `def _store_ens_snapshot(conn, city, target_date, ens, ens_result) -> str:` — function definition matches.

**Additional fallback sites discovered (not in workbook citation but same family)**:
- `src/engine/evaluator.py:91`: `temperature_metric: str = "high"` — CandidateContext dataclass default.
- `src/engine/evaluator.py:497`: `temperature_metric=candidate.temperature_metric or "high"` — `or` fallback at construction.
- `src/engine/evaluator.py:537`: same pattern.
- `src/engine/evaluator.py:674`: same pattern.

**Status**: CURRENT, ENRICHED — workbook cited 1 fallback site (`_store_ens_snapshot`); fresh grep finds 5 distinct fallback sites in evaluator.py alone (L91, L497, L537, L674, L1819). Slice A3 scope is broader than workbook implied.

### Finding 8 — Rescue authority returns "high"+UNVERIFIED

**Cited at**: `src/state/chain_reconciliation.py::resolve_rescue_authority()` returns `("high", "UNVERIFIED", ...)` when `temperature_metric` missing.

**Verified at**:
- `src/state/chain_reconciliation.py:29`: `def resolve_rescue_authority(position) -> tuple[str, str, str]:` — function exists.
- `src/state/chain_reconciliation.py:49` (post-rebase, was 48 pre-rebase): `return ("high", "UNVERIFIED", f"position_missing_metric:{_raw_metric!r}")` — exact match.
- `src/state/chain_reconciliation.py:33-38` docstring: "downstream analytics can filter on authority='VERIFIED' for strict forensic work" — confirms intent is in docstring, not structure.

**Status**: CURRENT.

### Finding 9 — Terminal-state set duplicated (ELEVATED to semantic-bug)

**Cited at**: `src/engine/cycle_runner.py` mirrors terminal states from `_TERMINAL_POSITION_STATES` in `src/state/portfolio.py` in a local frozenset.

**Verified at (post-rebase exact contents)**:
- `src/state/portfolio.py:962`: `_TERMINAL_POSITION_STATES = frozenset({"settled", "voided", "admin_closed", "quarantined"})` — canonical definition. Matches `LEGAL_LIFECYCLE_FOLDS` ground truth (4 phases that fold only to themselves).
- `src/engine/cycle_runner.py:54`: `_TERMINAL_POSITION_STATES_FOR_SWEEP = frozenset({"settled", "voided", "admin_closed", "quarantined"})` with comment at L51-52 "Mirrors `_TERMINAL_POSITION_STATES` in src/state/portfolio.py" — **AGREES with canonical** (correct mirror).

**Additional discovery (NOT in workbook), with SEMANTIC DISAGREEMENT**:
- `src/engine/cycle_runner.py:341`: `terminal_states = {"settled", "voided", "admin_closed", "economically_closed"}` — inline set literal in `_evaluate_run_status` smoke-test portfolio cap branch, NO comment, NO cross-reference. **DISAGREES with canonical**: includes `economically_closed` (per `LEGAL_LIFECYCLE_FOLDS["ECONOMICALLY_CLOSED"] = {ECONOMICALLY_CLOSED, SETTLED, VOIDED}` — NOT terminal) and excludes `quarantined` (per `LEGAL_LIFECYCLE_FOLDS["QUARANTINED"] = {QUARANTINED}` — IS terminal).

**Authority proof**: `src/state/lifecycle_manager.py:79-83`:
```python
LifecyclePhase.SETTLED: frozenset({LifecyclePhase.SETTLED}),
LifecyclePhase.VOIDED: frozenset({LifecyclePhase.VOIDED}),
LifecyclePhase.QUARANTINED: frozenset({LifecyclePhase.QUARANTINED}),
LifecyclePhase.ADMIN_CLOSED: frozenset({LifecyclePhase.ADMIN_CLOSED}),
```
A phase is terminal iff its fold is the singleton of itself. Only those 4 phases satisfy this → canonical set = `{settled, voided, admin_closed, quarantined}`.

**Status**: CURRENT, ELEVATED — finding 9 is not "1 extra duplicate" (workbook said 2 sources of truth) and not even just "3 sources of truth" — it is a **semantic bug**: the third source has the wrong set members. Slice B1 is no longer pure refactor; it includes a behavior fix at L341.

---

## Doc-citation verifications (governance)

### Workbook claim: P4 mutation BLOCKED

**Cited at**: `docs/operations/current_state.md`, `docs/operations/task_2026-04-25_p4_readiness_checker/plan.md`.

**Verified at**:
- `docs/operations/current_state.md` (head section): "Active package source: `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md` ... Active execution packet: none frozen; next packet pending phase-entry" and later "post-P3/P4 preflight evidence packet closed after confirming `market_events_v2`, `settlements_v2`, `ensemble_snapshots_v2`, and `calibration_pairs_v2` are still empty" — confirms P4 mutation BLOCKED state.
- `docs/operations/task_2026-04-25_p4_readiness_checker/plan.md`: file does NOT exist at this path on `origin/midstream_remediation@6499f9c`. Workbook citation is approximate; the active P4 packet is referenced in `current_state.md` narrative without a dedicated `task_2026-04-25_p4_readiness_checker/` folder visible in `ls docs/operations/`.

**Status**: CURRENT (state) but WORKBOOK PATH IMPRECISE (`task_2026-04-25_p4_readiness_checker/plan.md` not found). Plan.md downgrades the citation to `current_state.md` only.

### Workbook claim: HKO/HK gap

**Cited at**: `docs/operations/known_gaps.md` records HKO floor-containment evidence and early HK WU/VHHH source mismatch.

**Verified at**: `known_gaps.md` exists; specific HKO content not re-grep'd in this audit window. To verify in slice C-HK packet.

**Status**: PRESUMED CURRENT (file exists; specific content deferred to slice-time verification).

### Workbook claim: DST historical rebuild OPEN

**Cited at**: `docs/operations/known_gaps.md` marks historical diurnal aggregate rebuild as open.

**Verified at**: `known_gaps.md` head reads `[OPEN — NOT LIVE-CERTIFIED] Historical diurnal aggregates still need DST-safe rebuild cleanup`.

**Status**: CURRENT.

---

## Schema reality discovery (post-rebase 2026-04-26)

Inspecting `src/state/db.py:309` `CREATE TABLE calibration_pairs` plus all
`ALTER TABLE calibration_pairs` statements (none add `temperature_metric`):
**legacy `calibration_pairs` table has no `temperature_metric` column.**
Only `calibration_pairs_v2` carries that column.

Implication for Workbook Finding 5: the cited risk
"if legacy `calibration_pairs` contains both high and low canonical rows,
HIGH on-the-fly refit can mix tracks" is theoretical, not actual — there
is no schema mechanism by which LOW rows can be distinguished in legacy,
and the code comment at `manager.py:165-170 Phase 9C L3 CRITICAL` records
"LOW has never existed in legacy" as the operating convention.

Slice A1 design therefore shifts from "filter on `temperature_metric`
column" to "encode the LOW-never-in-legacy convention as structure":

- Add `metric: Literal["high","low"] | None = None` kwarg to read functions.
- `metric="high"` and `metric=None`: current behavior (legacy is HIGH-only).
- `metric="low"`: raise `NotImplementedError` pointing to v2 API
  (`load_platt_model_v2` / `calibration_pairs_v2` reads).

This makes the implicit invariant structural — wrong code (asking for LOW
from legacy) becomes unwritable rather than silently degrading.

## Discrepancies and follow-ups

### Discrepancy D1: AGENTS.md vs portfolio.py terminal-state vocab

**AGENTS.md §1**: "Terminal states: `voided`, `quarantined`, `admin_closed`." (3 items, includes `quarantined`)

**portfolio.py:962** canonical set: `{settled, voided, admin_closed, economically_closed}` (4 items, no `quarantined`)

**Code is authority over docs**, but this gap should be reconciled. Recorded as scaffold unknown #6 and flagged for slice B1.

### Discrepancy D2: P4 packet path

Workbook cites `docs/operations/task_2026-04-25_p4_readiness_checker/plan.md` which does not exist at that path on `origin/midstream_remediation@6499f9c`. Active P4 evidence is narrated in `current_state.md` but not at the cited folder. May exist on a different branch (e.g., the workbook's `copilot/full-review-of-upstream-data-storage` branch).

**Action**: plan.md cites `current_state.md` only and notes the workbook's path is approximate.

---

## Summary

- **5 source citations grep-verified, 0 stale.** Premise rot rate: 0% (vs historical Zeus baseline ~20–30% per memory `feedback_zeus_plan_citations_rot_fast.md`).
- **2 workbook citations enriched by grep**: F7 (5 sites vs 1 cited), F9 (3 sources of truth vs 2 cited). Slice scopes adjusted accordingly in `plan.md`.
- **2 doc/code discrepancies recorded** (D1, D2). Routed to slices.
- Audit window: complete within 10-minute window before `plan.md` write.

End of grep-gate log.
