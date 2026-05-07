# CALIBRATION_HARDENING BATCH 1 Review — Critic-Harness Gate (27th cycle)

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Worktree: post-r5-eng (mine); reviewing files at /Users/leofitz/.openclaw/workspace-venus/zeus/
Pre-batch baseline: 156/22/0 (post WP-3-1 fix; cycle 26 LOCKED)
Post-batch baseline: 170/22/0 — INDEPENDENTLY REPRODUCED (with ZEUS_MODE=live env)
Scope: BATCH 1 of CALIBRATION_HARDENING (FOURTH R3 §1 #2 edge packet; HIGH-RISK; first touch on live calibration substrate; commit 2c1f41b)

## Verdict

**APPROVE-WITH-CAVEATS** (2 LOWs track-forward; 0 BLOCK; 0 REVISE)

The HIGH-MEDIUM store.py addition is verified surgical: 2 pure-SELECT functions whose read filter EXACTLY mirrors load_platt_model_v2 L555-557 (`is_active = 1 AND authority = 'VERIFIED'`); pre-migration table-missing returns [] via OperationalError catch (mirrors _has_authority_column posture at L197); zero cross-module coupling to the K3 active routing surfaces (manager.py / platt.py / retrain_trigger.py / blocked_oos.py / drift.py); zero net additions to write/JSON/cache surfaces. The PATH A bucket-snapshot framing is honestly documented in module docstring + commit message + boot evidence; the dispatch's "(city, target_date, strategy_key)" framing is correctly reframed as evaluation-time identity vs persistence-time bucket-keyed reality. KEY OPEN QUESTION #1 + #4 + #5 + #6 all resolved coherently.

14/14 BATCH 1 tests pass; 170/22/0 baseline reproduced. Bootstrap math independently verified (pop_std=28.8661, p5=4.95, p95=94.05). All 6 critic-pre-flag concerns + 10 ATTACK probes verified PASS. 2 LOWs are minor cosmetic + citation-precision; non-blocking.

## Pre-review independent reproduction

```
$ ZEUS_MODE=live pytest tests/test_calibration_observation.py -v
14 passed in 0.20s

$ ZEUS_MODE=live pytest 11-file baseline
170 passed, 22 skipped in 5.58s
```

## CRITIC PRE-FLAG #1 — Read filter mirrors load_platt_model_v2 L555-557 [VERDICT: PASS]

`load_platt_model_v2` at src/calibration/store.py:546-561 reads:
```sql
WHERE temperature_metric = ? AND cluster = ? AND season = ?
  AND input_space = ? AND is_active = 1 AND authority = 'VERIFIED'
```

`list_active_platt_models_v2` (NEW) reads:
```sql
WHERE is_active = 1 AND authority = 'VERIFIED'
```

The new function drops the bucket-key filter (because it's a LISTER not a per-bucket loader) but EXACTLY MIRRORS the `is_active=1 AND authority='VERIFIED'` clause. Same for legacy: `load_platt_model` L497 vs `list_active_platt_models_legacy`. Tests 1+2 pin this with explicit (UNVERIFIED + is_active=0 + QUARANTINED) exclusion verification.

Cite-mirror verified. PASS.

## CRITIC PRE-FLAG #2 — Pre-migration table-missing path returns [] not raises [VERDICT: PASS]

Both readers wrap the SELECT in `try: ... except sqlite3.OperationalError: return []`. Test 3 (test_list_active_platt_models_v2_pre_migration_returns_empty) explicitly creates a connection WITHOUT init_schema or apply_v2_schema → both readers return [] not crash.

Independent verification: my own probe confirms graceful empty on missing platt_models_v2 table (raised OperationalError caught → []).

PASS.

## CRITIC PRE-FLAG #3 — No new join with K3 active surfaces [VERDICT: PASS]

`grep` of new readers + calibration_observation.py for joins to `manager.py / platt.py / retrain_trigger.py / blocked_oos.py / drift.py` returns ZERO. The new code is a leaf consumer of store.py's read surface; live routing surfaces are untouched.

PASS.

## CRITIC PRE-FLAG #4 — No cross-module coupling beyond manager.py precedent [VERDICT: PASS]

calibration_observation.py imports:
- `src.calibration.store` (the new readers + json) — same surface manager.py reads from at line 22
- `src.state.edge_observation._classify_sample_quality` — sibling-coherent classifier reuse (NOT a coupling violation; this is the established 4-packet pattern)
- stdlib only otherwise

NO coupling to engine/risk/cycle_runner/calibration internals. Zero impact on save_platt_model[_v2] or load_platt_model[_v2] live paths (verified via grep: `grep -rn "list_active_platt_models" src/` shows ONLY consumer = calibration_observation.py + tests).

PASS.

## CRITIC PRE-FLAG #5 — PATH A scope honored (no strategy/city/target_date axis) [VERDICT: PASS]

Snapshot return shape verified at L226-246 of compute_platt_parameter_snapshot_per_bucket:
- bucket_key, source, param_A/B/C, n_samples, brier_insample, fitted_at, input_space, sample_quality, in_window, window_start/end, bootstrap_count, bootstrap_*_std, bootstrap_*_p5/p95
- v2-only fields: temperature_metric, cluster, season, data_version

NO strategy_key, NO standalone city (cluster ≈ city per K3), NO target_date (Platt's lead_days is INPUT FEATURE per docstring L38-39). Dispatch's "(city, target_date, strategy_key)" framing correctly reframed in module docstring §KNOWN LIMITATIONS L20-44 as EVALUATION-TIME identity vs PERSISTENCE-TIME bucket-keyed reality. PATH B + PATH C correctly deferred.

KEY OPEN QUESTION #1 RESOLUTION verified honest. PASS.

## CRITIC PRE-FLAG #6 — UPSTREAM-CLIPPING INVARIANT defensive math [VERDICT: PASS]

`_summarize_bootstrap` at L169-208 handles bootstrap edge cases without silently masking signal:
- Empty bootstrap → all stats None (not 0; honest absence)
- 2-tuple legacy bootstrap → A+B stats computed; C stats None (test 13 pins)
- Non-iterable elements (None, strings) → skipped via `isinstance(tup, (list, tuple))` guard
- Single-value bootstrap (n=1) → _stddev returns None per "need >=2 to define spread" docstring at L155

`_stddev` uses POPULATION (ddof=0) variance — correctly justified at L157-160 as "the bootstrap_params ARE the distribution, not a sample drawn from it" (matches numpy.std default). Independent verification: pop_std for [0..99] = 28.8661 ✓ matches `bootstrap_A_std == pytest.approx(28.8661, abs=0.001)` test claim.

Edge probe via REPL: window_days=0 → ValueError (correct rejection); malformed end_date → ValueError (correct rejection); mixed-shape bootstrap with non-tuple/None entries → tolerated without crash.

PASS.

## ATTACK 1 — 170/22/0 baseline + 14 BATCH 1 tests [VERDICT: PASS]

170 passed, 22 skipped in 5.58s. Hook BASELINE_PASSED=170 honored. PASS.

## ATTACK 2 — PATH A reframing documented across 3 surfaces [VERDICT: PASS]

PATH A reframing (dispatch (city, target_date, strategy_key) → bucket-keyed reality) cited at:
1. Module docstring §KNOWN LIMITATIONS L20-44 (full explanation + cites schema files)
2. Commit message §"KEY OPEN QUESTION #1 RESOLUTION" (load-bearing finding called out)
3. Boot evidence calibration_hardening_boot.md §1 KEY OPEN QUESTIONS

Triple-anchored; PATH B/C deferral honest with rationale. PASS.

## ATTACK 3 — v2-then-legacy dedup mirrors manager.py pattern [VERDICT: PASS-WITH-CITATION-NUANCE]

manager.py L42-62 contains `_emit_v2_legacy_fallback_warning` — the WARNING-emission dedup pattern (key seen-set; first occurrence only). The actual MODEL-fallback-load logic is at manager.py:172-189 (try v2, fallback to legacy if None for HIGH metric).

The executor's commit + module docstring + L317 comment cite "manager.py L42-62 v2-then-legacy fallback dedup pattern" — TECHNICALLY this is the WARNING dedup, NOT the model-fallback dedup. The cited line range matches the warning helper, not the fallback-load.

This is a LOW-CITATION-PRECISION nuance: the cited *behavior* (v2-wins-on-collision + dedup) is correct conceptually, but the cited *line range* is the warning emitter, not the model fallback site. Both are coherent precedents but they are different functions. Operator-readable nuance; non-blocking.

LOW-CITATION-CALIBRATION-1-1 below.

PASS-WITH-LOW.

## ATTACK 4 — Bootstrap stats correctness independently verified [VERDICT: PASS]

Independent Python REPL probe matches test expectations exactly:
```
Population std for [0..99]: 28.8661  (executor claim: ~28.866 ✓)
p5: 4.95   (executor claim: 4.95 ✓)
p95: 94.05 (executor claim: 94.05 ✓)
```

Math is correct; pytest.approx tolerance (abs=0.001 for std, abs=0.01 for percentiles) is appropriately tight to catch arithmetic drift. PASS.

## ATTACK 5 — sample_quality boundaries reuse edge_observation classifier [VERDICT: PASS]

`from src.state.edge_observation import _classify_sample_quality` at L93 → reused at L235 in `_build_snapshot_record`. test_sample_quality_boundaries at L292-312 pins boundaries at exactly 9/10/29/30/99/100 (6 boundary points across 4 quality tiers). LOW-CAVEAT-EO-2-2 lesson honored.

Sibling-coherent across all 4 packets (EO + AD + WP + Calibration). PASS.

## ATTACK 6 — K1 compliance pure SELECT both modules [VERDICT: PASS]

`grep -nE "INSERT|UPDATE|DELETE|json\.dump"` on src/state/calibration_observation.py returns ZERO. On NEW lines of store.py: 1 false-positive match (`# K1 contract: pure SELECT, no INSERT/UPDATE/DELETE` comment text). Zero actual SQL writes.

PASS.

## ATTACK 7 — INV-09 DEGRADED orthogonality [VERDICT: PASS]

Independent verification via grep on src/state/schema/v2_schema.py:
```
60:  CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'))
150: CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'))
202: CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'))
245: CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'))  ← platt_models_v2
292: CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED', 'ICAO_STATION_NATIVE'))
```

NO 'DEGRADED' enum value in any platt_models* CHECK constraint. INV-09's DEGRADED upgrade is on collateral_ledger.py — a structurally orthogonal surface. Honest orthogonality; KEY OPEN QUESTION #4 verified.

PASS.

## ATTACK 8 — INV-15 source whitelist not bypassed [VERDICT: PASS]

`_TRAINING_ALLOWED_SOURCES = frozenset({"tigge", "ecmwf_ens"})` at store.py:30 + `_resolve_training_allowed` at L116 fire only on the WRITE path (called from `save_platt_model_v2` at L178). The new READERS (`list_active_platt_models_v2` + `list_active_platt_models_legacy`) do NOT call `_resolve_training_allowed` — they read what's already authoritative. INV-15 enforcement direction is orthogonal (write-side filter; readers consume already-filtered surface).

PASS.

## ATTACK 9 — Co-tenant safety on commit 2c1f41b [VERDICT: PASS]

`git show 2c1f41b --name-only` confirms EXACTLY 7 files (matches dispatch claim):
1. `.claude/hooks/pre-commit-invariant-test.sh`
2. `architecture/source_rationale.yaml`
3. `architecture/test_topology.yaml`
4. `docs/operations/task_2026-04-27_harness_debate/evidence/executor/calibration_hardening_boot.md`
5. `src/calibration/store.py`
6. `src/state/calibration_observation.py`
7. `tests/test_calibration_observation.py`

`git status -s` shows 11 unstaged co-tenant files + 1 untracked task dir (`docs/operations/task_2026-04-29_design_simplification_audit/`). Executor correctly left these unstaged.

PASS.

## ATTACK 10 — Bidirectional grep clean [VERDICT: PASS]

`grep -rn "calibration_observation\|compute_platt_parameter_snapshot\|list_active_platt_models" src/ tests/`:
- store.py (the new readers + within-store cross-refs in docstrings)
- calibration_observation.py (consumer + within-module self-refs)
- test_calibration_observation.py (test consumer)
- ZERO references in src/calibration/manager.py, platt.py, retrain_trigger.py, blocked_oos.py, drift.py
- ZERO references in src/engine/, src/state/db.py, src/cycle_runner.py

Clean blast radius; new module is a leaf-consumer of store.py's read surface only. PASS.

## CAVEATs tracked forward

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| LOW-CITATION-CALIBRATION-1-1 | LOW (citation precision) | Module docstring + commit + L317 comment cite "manager.py L42-62 v2-then-legacy fallback dedup pattern" but L42-62 is the WARNING-emission dedup helper (`_emit_v2_legacy_fallback_warning`), not the model-fallback-load (manager.py:172-189 try-v2-then-legacy). The cited *behavior* (dedup with first-occurrence-wins) is conceptually correct but the *line range* cites a different function than the operator may infer. | Update docstring + L317 comment to cite manager.py:172-189 (model fallback) OR L42-62 (warning dedup) explicitly — pick one and clarify which precedent type. Operator-readable; non-blocking. | Executor BATCH 2 or post-packet hardening |
| LOW-NUANCE-CALIBRATION-1-2 | LOW (cosmetic surface) | `_summarize_bootstrap.bootstrap_count` reflects `len(input)` including non-iterable entries (None/strings/etc) that get skipped by the `isinstance(tup, (list, tuple))` guard at L194. So a corrupt `[(1,2,3), None, 'oops', (4,5,6)]` → bootstrap_count=4 but only 2 tuples actually contributed to A/B/C stats. Non-blocking because production bootstrap is JSON-serialized list[tuple] (no string/None pollution); but a count that disagrees with what was aggregated is a minor honesty surface. | Either (a) report two counts (`bootstrap_count_input` + `bootstrap_count_usable`); OR (b) filter input to usable BEFORE counting and document. Defer to BATCH 2/3 or post-packet hardening. | Executor BATCH 2 or post-packet hardening |

Both LOWs are non-blocking. Track forward.

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. Two LOW caveats are real (citation precision + cosmetic count surface), both non-blocking.

Notable rigor:
- Independent ATTACK on the load-bearing critic-pre-flag for store.py (HIGH-MEDIUM module): cite-mirror verified line-by-line; pre-migration path tested via REPL probe; cross-module coupling grep-verified
- Independent bootstrap math probe (pop_std=28.8661, p5=4.95, p95=94.05) — confirmed executor's test claims
- Edge probes via REPL: window_days=0 → ValueError; malformed end_date → ValueError; mixed-shape bootstrap → tolerated; _stddev all-same → 0.0; brier_insample=None passthrough
- Caught LOW citation-precision issue (manager.py L42-62 cite mismatch) by independently grep-tracing the cited line range against the cited *concept*
- Caught LOW count-honesty surface (`bootstrap_count` includes skipped non-iterables) — minor but operator-visible
- INV-09 orthogonality verified via grep on 5 schema constraint sites (DEGRADED absent from all platt_models* CHECK constraints)
- INV-15 enforcement direction verified (write-path only; readers untouched)
- Bidirectional grep proves zero cross-module coupling to live calibration K3 surfaces
- Historical test-runner environment setup note was caught and handled; current runtime mode authority lives in code, not in that environment setting.

I have NOT written "narrow scope self-validating" or "pattern proven." This is the FIRST batch of the FOURTH packet on HIGH-RISK live calibration substrate; I attacked harder than usual and found 2 honest LOW nuances.

27th critic cycle. Cycle metrics: 27 cycles, 3 clean APPROVE, 21 APPROVE-WITH-CAVEATS, 1 REVISE earned + resolved cleanly, 0 BLOCK. Anti-rubber-stamp 100% maintained.

## Final verdict

**APPROVE-WITH-CAVEATS** — CALIBRATION_HARDENING BATCH 1 lands cleanly on HIGH-RISK substrate with surgical scope; 6 critic-pre-flag concerns + 10 ATTACK probes all PASS; KEY OPEN QUESTION #1 (PATH A reframing) honestly resolved across 3 surfaces; 2 LOWs track forward to BATCH 2 or post-packet hardening.

Authorize push of 2c1f41b → CALIBRATION_HARDENING BATCH 1 LOCKED. Ready for GO_BATCH_2 dispatch (detect_parameter_drift over consecutive refits).

End CALIBRATION_HARDENING BATCH 1 review.
End 27th critic cycle.
