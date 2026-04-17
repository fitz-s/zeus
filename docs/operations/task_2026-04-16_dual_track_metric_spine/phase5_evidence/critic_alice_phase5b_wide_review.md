# critic-alice — Phase 5B Wide Review

**Date**: 2026-04-17
**Subject**: Phase 5B low historical lane (extractor + ingest unblock + contract + rebuild/refit metric-aware + B078)
**Pytest**: 41/41 GREEN on `tests/test_phase5b_low_historical_lane.py` (disk-verified)
**Posture**: L0.0 peer-not-suspect, fresh bash grep on every cited claim.

## VERDICT: **ITERATE**

5B is structurally close but has one CRITICAL L4 seam gap + two MAJOR items. The 41/41 GREEN does not fully cover the extractor→contract→DB chain; runtime ingest bypasses the new contract gate.

---

## L0-L5

### L0 / L0.0
Authority chain re-loaded post-subagent-start: methodology (including new §"Critic role — critique TASK not TEAMMATE"), AGENTS root, DT architecture §2/§5/§6/§8, team_lead_handoff, coordination handoff, package spec. Peer-not-suspect posture applied throughout — no discipline findings filed; concurrent-write hypothesis sufficient for all observed discrepancies.

### L1 — INV / FM
Contract module at `src/contracts/snapshot_ingest_contract.py:34-88` enforces `_ALLOWED_DATA_VERSIONS` consistency: data_version ↔ metric ↔ physical_quantity triad must match. Truth_files B078 additions at `src/state/truth_files.py:30-31` extend `LEGACY_STATE_FILES` with `platt_models_low.json` + `calibration_pairs_low.json`. Fail-closed logic at L55-56 downgrades authority to UNVERIFIED when `temperature_metric=None` and path stem contains `_low`. PASS.

### L2 — Forbidden Moves
- Kelvin silent-default: **BLOCKED** at contract L50-52 (`MISSING_MEMBERS_UNIT` rejection). ✓
- Fixture-bypass: R-AO spy pattern on `save_platt_model_v2`/`deactivate_model_v2` mocks side-effects, calls real `refit_v2` entry — correct shape (covered in pre-review). ✓
- Paper-mode anything: `grep -n 'paper' scripts/ src/contracts/ src/state/truth_files.py` returns zero new paper references. ✓
- MIN polarity-swap without rethink: **traced manually** — `classify_boundary_low` at `scripts/extract_tigge_mn2t6_localday_min.py:108-119` computes `boundary_ambiguous = boundary_min <= inner_min`, treats `inner_min=None + boundary_min≠None` as ambiguous. Synthetic trace (`inner=5.0, boundary=4.5 → ambiguous=True`) confirms the cross-midnight steal semantics. Not a blind flip of MAX logic. ✓

### L3 — Silent fallbacks
Contract L59 defaults causality_status to `"UNKNOWN"` when causality-dict shape is wrong; surfaced through rejection reason, not consumed silently. ✓
`build_truth_metadata` L55-56 fail-closed triggers on `"_low" in Path(path).stem` — a future path like `platt_models_low_archive.json` would trip it. MINOR: consider `stem.endswith("_low")` or an explicit suffix tuple.

### L4 — Source authority at seams (**CRITICAL-1 FOUND**)

**Evidence**:
```
$ grep -n "validate_snapshot_contract\|from src.contracts.snapshot_ingest_contract" scripts/ingest_grib_to_snapshots.py
(zero matches)

$ grep -n "from src.contracts" scripts/ingest_grib_to_snapshots.py
34:from src.contracts.ensemble_snapshot_provenance import (
```

`ingest_json_file` at `scripts/ingest_grib_to_snapshots.py:148-190` reads `training_allowed = 1 if payload.get("training_allowed") else 0` directly from the extractor's JSON. It does NOT call `validate_snapshot_contract`. The contract module exists but sits off-path.

**Impact**: The 3 quarantine laws (boundary_ambiguous, causality N/A, missing members_unit) live only as test invariants. Runtime ingest trusts the extractor's self-reported `training_allowed`. If the extractor ever emits `training_allowed=True` on a boundary_ambiguous snapshot, the DB writes a corrupted row silently. R-AM only asserts "no NotImplementedError" — it does not exercise the contract→DB wiring.

**Severity**: CRITICAL. This is security-guard-not-immune-system shape per onboarding antipattern #3. The contract lands but doesn't defend. Realist check: Zero-Data Golden Window mitigates live harm today, but Phase 5C/6 will flow real GRIB through this path without the gate.

### L5 — Phase boundary
No Phase 6/7/9 leak. Phase 5A truth-authority seam (`PortfolioState.authority`, `ModeMismatchError`, `read_mode_truth_json`) NOT re-edited — grep confirms 5B changes to `truth_files.py` are additive only (new `temperature_metric`/`data_version` kwargs). Clean.

### WIDE — off-checklist findings

**MAJOR-1**: `classify_boundary_low` logic is correct, but R-AG only asserts IMPORTABILITY, not behavior. A future refactor could invert `<=` to `>=` and R-AG stays GREEN. Recommend R-AP (3 behavioral cases: `boundary_min < inner_min`, `boundary_min > inner_min`, `inner_min is None`) as 5B-follow-up. Not a 5B blocker if accepted as deferred.

**MAJOR-2**: `scripts/refit_platt_v2.py` empty-bucket branch was changed from `RuntimeError + stats.refused=True` to `print + return stats` (refused stays False). Operator observability regression: empty-DB runs now indistinguishable from successful zero-bucket runs. Exec-emma flagged this for review correctly — the fix is 1 line: add `stats.refused = True` before the graceful return. Keeps R-AO GREEN (tests don't assert on `refused`) AND preserves the operator signal.

## Legacy-audit verdicts

- `scripts/extract_tigge_mx2t6_localday_max.py` (existing Phase 4.5) vs `scripts/extract_tigge_mn2t6_localday_min.py` (new 5B): spot-checked duplicate function bodies via grep on `_compute_manifest_hash`, `_now_utc_iso`, `_city_slug`, `_overlap_seconds`. These are small utilities (5-15 lines each) that the MAX file also defines. **MODERATE** drift risk over 2× 500-800 LOC files. Verdict: **CURRENT_REUSABLE with drift-warning** — both files are correct today; a shared `scripts/_tigge_common.py` module would be cleaner. Log as 5B-follow-up refactor candidate, not blocking 5B commit.
- `src/contracts/snapshot_ingest_contract.py` — NEW file, anchors on DT v2 package `04_CODE_SNIPPETS/ingest_snapshot_contract.py`. Header provenance present. **CURRENT_REUSABLE**.
- `src/contracts/ensemble_snapshot_provenance.py` — unchanged, still referenced by ingest L34. Out of 5B scope.

## Provenance headers

Extractor + contract module both carry canonical `# Lifecycle: / # Purpose: / # Reuse:` headers (verified by Read of both file heads). Test file carries `# Lifecycle:` header. PASS.

## Recommendation

**Cannot commit 5B as-is.** Single ITERATE round covers CRITICAL-1 + MAJOR-2 with narrow surgical changes.

Dispatch plan (direct a2a to exec-emma):
1. **CRITICAL-1**: Wire `validate_snapshot_contract` into `ingest_json_file` (preferred: replace the 3 separate `_extract_*` helpers + `validate_members_unit` with the single contract decision; use `decision.training_allowed` + `decision.causality_status` on the row; return `"contract_rejected"` when `decision.accepted is False`).
2. **MAJOR-2**: Add `stats.refused = True` before empty-bucket graceful return in `refit_platt_v2.py`.
3. **MAJOR-1**: Log R-AP as 5B-follow-up for testeng-grace (3 behavioral cases for `classify_boundary_low`). Not blocking.

Post-iterate: 41/41 pytest must stay GREEN; new contract wiring should surface as additional structural coverage. Budget re-review at 10-15min once exec-emma reports.

---

*Authored*: critic-alice (opus, persistent)
*Disk-verified*: 2026-04-17, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, pytest 41/41 GREEN, contract-wiring grep returned zero matches.
