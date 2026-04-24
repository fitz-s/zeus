# critic-alice — Phase 5B Final Verdict (round-2 re-review)

**Date**: 2026-04-17
**Subject**: Phase 5B re-review after exec-emma iterate cycle + team-lead probe questions
**Pytest**: 41/41 GREEN on phase5b suite; exec-emma reports 80/80 across Phase 4+5A+5B; full-suite flat against 5A baseline (117 failed = zero new regressions from wiring, confirmed round 1)
**Verdict**: **PASS with 5B-follow-up notes** — commit authorized.

This doc supersedes the round-1 draft.

---

## Severity table

| ID | Round 1 | Round 2 | Evidence |
|---|---|---|---|
| CRITICAL-1 | CRITICAL | RESOLVED | contract import L38 + call L182 in `ingest_grib_to_snapshots.py` |
| MAJOR-1 (classify_boundary_low behavior) | MAJOR | DEFERRED → R-AP (5B-follow-up) | spec-correct semantics, behavioral coverage lift logged |
| MAJOR-2 (refused flag) | MAJOR | RESOLVED | `stats.refused = True` at `refit_platt_v2.py:229` (+ bonus :258) |
| L3 MINOR (substring check) | MINOR | RESOLVED | `_LOW_LANE_FILES` frozenset at `truth_files.py:33` + exact-name check at :59 |
| **MINOR-NEW-1** (setdefault trusts JSON over caller) | — | NOTED, 5B-follow-up | contract cross-check authority asymmetry |
| **MINOR-NEW-2** (causality setdefault) | — | NOTED, 5B-follow-up | legacy-compat bridge; narrow to high-only |
| **MINOR-NEW-3** (`_extract_causality_status` dead) | — | NOTED, 5B-follow-up | paired-helper retention defensible |

---

## Fresh disk verification (all PASS)

```
$ pwd && cd /Users/leofitz/.openclaw/workspace-venus/zeus
/Users/leofitz/.openclaw/workspace-venus/zeus

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
41 passed in 1.68s
```

## Contract wiring end-to-end read (ingest_grib_to_snapshots.py:149-207)

- L158-162 JSON parse → graceful `parse_error`. ✓
- L164-170 legacy `assert_data_version_allowed` + `validate_members_unit` retained (defense-in-depth).
- L177-181 `contract_payload = dict(payload)` + 4× `setdefault` — documented at L173-176.
- L182-189 contract gate returns `contract_rejected: {reason}` BEFORE any DB write. ✓
- **L206-207 authority inversion complete**: `training_allowed = 1 if decision.training_allowed else 0` and `causality_status = decision.causality_status` — both sourced from `decision`, not payload self-report. ✓

## Team-lead's three probe questions — answered

**Q1: "if JSON has `temperature_metric='high'` but `metric` arg says `low`, which does the contract see?"**

The JSON wins. `setdefault` only writes when absent. Contract cross-checks JSON self-report against JSON's `data_version`; caller's `metric` arg is only a fallback when JSON is silent.

Risk: if a high-track JSON is misrouted to the `low` ingest path, contract PASSES (JSON internally consistent). DB row lands `temperature_metric=low` (L221 from caller arg) but members + provenance from a high JSON — silent corruption seam.

Mitigation today: `_TRACK_CONFIGS` routes by directory structure (accidental cross-routing requires misconfigured CLI). The asymmetry is structurally wrong though — caller-provided `metric` SHOULD be the authority. Correct pattern:

```python
contract_payload["temperature_metric"] = metric.temperature_metric   # unconditional
contract_payload["physical_quantity"]  = metric.physical_quantity    # unconditional
contract_payload.setdefault("members_unit", members_unit)             # legacy-compat OK
contract_payload.setdefault("causality", {"status": "OK"})            # legacy-compat OK (see Q2)
```

Logged as **MINOR-NEW-1** for 5B-follow-up. Today's misrouting risk is CLI-gated, not runtime-data-gated.

**Q2: "does `setdefault('causality', {'status': 'OK'})` let a genuinely missing causality slip through as OK on live low-track ingest?"**

Structurally possible; practically mitigated. The mn2t6 extractor at `scripts/extract_tigge_mn2t6_localday_min.py:264` always emits `causality = _compute_causality_low(...)` — first-class, never silent. So the setdefault only fires on pre-Phase-5 high payloads (the stated legacy-compat case).

Remaining risk: a future low extractor refactor that drops causality emission — the setdefault would silently stamp OK. Fix shape for **MINOR-NEW-2**: gate the setdefault on `metric.temperature_metric == "high"` so low-track payloads missing causality fail contract immediately.

**Q3: "`_extract_causality_status` — code-hygiene MINOR or acceptable?"**

Dead on main path post-wiring. Sibling `_extract_boundary_fields` is still live at L208 (feeds DB columns `boundary_ambiguous` + `ambiguous_member_count` that the contract doesn't re-emit). Separation-of-concerns defensible; asymmetric deletion would be surprising. **MINOR-NEW-3**: formal dead-code audit post-commit; delete if confirmed unreachable.

## Refit + truth_files audits

- `refit_platt_v2.py:229` empty-bucket branch carries `stats.refused = True` before graceful return (MAJOR-2 fix). `:258` is a second force-exit branch that also sets the flag — bonus belt-and-suspenders from exec-emma.
- `truth_files.py:33` `_LOW_LANE_FILES = frozenset(LEGACY_STATE_FILES ...)`; L59 uses `Path(path).name in _LOW_LANE_FILES` exact match. Robust against pathname variation (`platt_models_low_archive.json` no longer trips).

## Legacy-audit verdicts (confirmed)

- mx2t6/mn2t6 extractor utility-body overlap: **CURRENT_REUSABLE with drift-warning** (5B-follow-up: shared `_tigge_common.py`).
- `src/contracts/snapshot_ingest_contract.py`: **CURRENT_REUSABLE** (NEW, canonical provenance header present).

## Commit recommendation

**COMMIT 5B NOW.** 7 code files to stage:

```
scripts/extract_tigge_mn2t6_localday_min.py    (NEW, exec-dan)
scripts/ingest_grib_to_snapshots.py             (MOD, exec-dan guard removal + exec-emma contract wiring)
scripts/rebuild_calibration_pairs_v2.py         (MOD, exec-emma METRIC_SPECS refactor)
scripts/refit_platt_v2.py                       (MOD, exec-emma metric_identity threading + refused flag)
src/contracts/snapshot_ingest_contract.py       (NEW, exec-emma)
src/state/truth_files.py                        (MOD, exec-emma B078 + _LOW_LANE_FILES)
tests/test_phase5b_low_historical_lane.py       (NEW, testeng-grace, 41 tests)
```

Exclude: `state/auto_pause_failclosed.tombstone`, `state/status_summary.json`, `.claude/worktrees/data-rebuild` submodule, session-handoff markdown updates.

Suggested commit header: `feat(phase5B): low historical lane + ingest contract gate + B078 absorbed`.

Body should include: 41/41 phase5b GREEN; 80/80 Phase 4+5A+5B exec-emma cumulative; flat full-regression vs 5A baseline (zero new failures); 5B-follow-up backlog logged.

## 5B-follow-up backlog

1. **R-AP** (testeng-grace): 3 behavioral tests for `classify_boundary_low` — cross-midnight steal, safe boundary, inner-None edge.
2. **Scripts `_tigge_common.py`** (exec-dan): extract duplicated utility bodies from mx2t6/mn2t6 extractors.
3. **Dead-code audit** (exec-emma): verify `_extract_causality_status` unreachable; delete if confirmed.
4. **MINOR-NEW-1** (exec-emma): caller `metric` arg should be authoritative for `temperature_metric`/`physical_quantity` in contract_payload, not JSON self-report.
5. **MINOR-NEW-2** (exec-emma): gate causality setdefault on `metric.temperature_metric == "high"` — legacy-compat only, low must emit explicitly.

None of these block 5B commit. All are structural hardening for 5C/6 when real GRIB flows through the contract gate.

---

*Authored*: critic-alice (opus, persistent, post subagent-start re-verification)
*Disk-verified*: 2026-04-17, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, 41/41 phase5b GREEN on fresh pytest run + 3/3 grep gates fresh.
*Supersedes*: round-1 draft of this doc + `phase5b_wide_review.md`.
