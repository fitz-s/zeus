# critic-alice — Phase 5B Final Verdict

**Date**: 2026-04-17
**Subject**: Phase 5B re-review after exec-emma iterate cycle
**Pytest**: 41/41 GREEN on phase5b suite; 80/80 across Phase 4+5A+5B; full-suite 117 failed (pre-existing, matches 5A baseline)
**Verdict**: **PASS** — commit authorized.

---

## Severity table

| ID | Severity (round 1) | Disposition (round 2) | Evidence |
|---|---|---|---|
| CRITICAL-1 | CRITICAL | RESOLVED | contract import L38 + call L182 in `ingest_grib_to_snapshots.py` |
| MAJOR-1 | MAJOR | DEFERRED to R-AP (5B-follow-up) | spec-correct MIN semantics, behavioral coverage lift logged |
| MAJOR-2 | MAJOR | RESOLVED | `stats.refused = True` at `refit_platt_v2.py:229` and :258 |
| L3 MINOR | MINOR | RESOLVED | `_LOW_LANE_FILES` frozenset at `truth_files.py:33` + exact-name check at :59 |

---

## Acceptance gate disk-verification

All 5 gates PASS on fresh bash runs:

```
$ grep -n 'validate_snapshot_contract\|from src.contracts.snapshot_ingest_contract' scripts/ingest_grib_to_snapshots.py
38:from src.contracts.snapshot_ingest_contract import validate_snapshot_contract
182:    decision = validate_snapshot_contract(contract_payload)

$ grep -n 'refused = True' scripts/refit_platt_v2.py
229:        stats.refused = True
258:            stats.refused = True

$ grep -n '_LOW_LANE_FILES\|Path(path).name' src/state/truth_files.py
33:_LOW_LANE_FILES: frozenset[str] = frozenset(
59:    if authority == "VERIFIED" and temperature_metric is None and Path(path).name in _LOW_LANE_FILES:

$ pytest tests/test_phase5b_low_historical_lane.py
41 passed in 1.38s

$ pytest tests/ --tb=no -q  (full regression, ignoring pre-existing collection error on test_pnl_flow_and_audit.py)
117 failed, 1722 passed, 94 skipped
```

Baseline comparison: 5A commit landed with 117 failed (130 pre-5A → 117 post-5A = -13 net). Post-5B full regression shows **117 failed** — exactly flat against the 5A baseline. No new regressions from contract wiring. The contract's `_ALLOWED_DATA_VERSIONS` + metric/physical_quantity triad check did NOT surface latent mismatches in existing Phase 4 fixtures (exec-emma's `setdefault` pattern injects authoritative metric fields, preserving legacy compatibility).

---

## Contract wiring audit — read end-to-end

`ingest_json_file` at `scripts/ingest_grib_to_snapshots.py:149-189`:

- L158-162: JSON parse with graceful `parse_error` on exception. ✓
- L164-170: legacy guards (`assert_data_version_allowed` + `validate_members_unit`) remain — defense-in-depth, no harm.
- L177-181: `contract_payload = dict(payload)` + `setdefault` injects authoritative `temperature_metric` / `physical_quantity` / `members_unit` from the `metric` arg, plus `causality: {"status": "OK"}` for pre-5B high payloads. Clean legacy-compat pattern. Comment at L173-176 documents rationale.
- L182-189: contract decision gate. `if not decision.accepted: return "contract_rejected: {reason}"` — rejection BEFORE any DB write. ✓
- L206-207: **authority inversion complete**. `training_allowed = 1 if decision.training_allowed else 0` and `causality_status = decision.causality_status` — both sourced from `decision`, not payload self-report. This is the key structural fix; contract decides, payload does not. ✓

One semantic note on MAJOR-1's deferred state: `setdefault("causality", {"status": "OK"})` at L181 makes the ingest path permissive for legacy high payloads that lack the causality field. This is documented and intentional; it does NOT weaken R-AJ's invariant for NEW extractor output (prospective extractors must emit explicit causality; legacy silence → OK is a controlled fallback). If future Phase 5C/6 work tightens this, the change is one line (remove the setdefault).

---

## Refit + truth_files audits

**`scripts/refit_platt_v2.py:229,258`**: two branches carry `stats.refused = True`. L229 is the empty-bucket graceful return (MAJOR-2 fix). L258 is a second operator-signal guard I didn't request but is a welcome antibody — exec-emma added belt-and-suspenders at the force-exit branch. Good discipline.

**`src/state/truth_files.py:33-59`**: `_LOW_LANE_FILES` frozenset derives from `LEGACY_STATE_FILES` with `_low` substring filter; fail-closed check uses `Path(path).name in _LOW_LANE_FILES` exact match. Robust against path variations (e.g. `platt_models_low_archive.json` no longer trips the check because the filename isn't in the frozenset). L3 MINOR RESOLVED cleanly.

---

## exec-emma's open question on `_extract_causality_status`

She flagged: "`_extract_causality_status` is now dead on the main path but `_extract_boundary_fields` is still used for DB columns. Delete or keep?"

**Ruling** (my read, non-binding to team-lead): KEEP both for 5B commit. `_extract_boundary_fields` has a live caller at L208 (feeds DB columns boundary_ambiguous + ambiguous_member_count directly; contract doesn't re-emit those). `_extract_causality_status` is likely dead but removing it is adjacent cleanup — scope expansion beyond this iterate. Log as 5B-follow-up cleanup alongside R-AP, and do a formal dead-code audit for both helpers post-commit. No blocker.

---

## WIDEN (remaining)

Three observations I didn't flag in round 1, none blocking:

1. **Legacy-compat pattern is elegant**. `setdefault` pattern at L178-181 allows old high-track JSON files (pre-Phase 5B) to flow through the new contract gate without re-extraction. Reasonable trade-off vs. requiring a backfill pass on existing JSON corpus. Worth documenting as a generic "contract wiring" recipe for future refactors.
2. **Two refused-flag sites in refit**. `stats.refused = True` at both L229 (empty-bucket) and L258 (force-exit). Belt-and-suspenders — good.
3. **No new regressions from wiring**. Full-suite count flat against 5A baseline (117 failed). The contract's triad check didn't break any Phase 4 fixtures — this was my #1 concern in round 1, now confirmed safe.

---

## Legacy-audit verdicts (confirmed from round 1)

- `scripts/extract_tigge_mx2t6_localday_max.py` ↔ `scripts/extract_tigge_mn2t6_localday_min.py` duplicate utility bodies: **CURRENT_REUSABLE with drift-warning** (5B-follow-up: extract shared `_tigge_common.py`).
- `src/contracts/snapshot_ingest_contract.py`: **CURRENT_REUSABLE** (NEW, anchored on DT v2 package).

---

## Final recommendation

**COMMIT 5B NOW.** 8 files to stage:

```
scripts/extract_tigge_mn2t6_localday_min.py    (NEW)
scripts/ingest_grib_to_snapshots.py             (MOD)
scripts/rebuild_calibration_pairs_v2.py         (MOD)
scripts/refit_platt_v2.py                       (MOD)
src/contracts/snapshot_ingest_contract.py       (NEW)
src/state/truth_files.py                        (MOD)
tests/test_phase5b_low_historical_lane.py       (NEW)
```

Exclude runtime drift (`state/auto_pause_failclosed.tombstone`, `state/status_summary.json`), submodule entry (`.claude/worktrees/data-rebuild`), and the session-specific handoff doc modification per standard staging rule.

Suggested commit header: `feat(phase5B): low historical lane + ingest contract gate + B078 absorbed`. Regression stats for body: 41 new tests GREEN; full-suite 117 failed (flat against 5A baseline, no new regressions introduced).

### 5B-follow-up backlog (log to phase5_evidence/5B_followups.md)

1. **R-AP** (testeng-grace): 3 behavioral tests for `classify_boundary_low` — cross-midnight steal, safe boundary, inner-None edge case.
2. **Scripts common module** (exec-dan or exec-emma): extract `_tigge_common.py` from duplicated utilities in mx2t6/mn2t6 extractors.
3. **Dead-code audit** (exec-emma): formally verify `_extract_causality_status` is unreachable post-5B; delete if confirmed.

---

*Authored*: critic-alice (opus, persistent)
*Disk-verified*: 2026-04-17, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, 41/41 phase5b GREEN, full regression flat at 117 failed vs 5A baseline.
