# LEARNING_LOOP BATCH 1 Review — Critic-Harness Gate (30th cycle)

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Worktree: post-r5-eng (mine); reviewing files at /Users/leofitz/.openclaw/workspace-venus/zeus/
Pre-batch baseline: 189/22/0 (post CALIBRATION_HARDENING; cycle 29 LOCKED)
Post-batch baseline: 203/22/0 — INDEPENDENTLY REPRODUCED (with ZEUS_MODE=live env)
Scope: BATCH 1 of LEARNING_LOOP (FIFTH and FINAL R3 §1 #2 edge packet; HIGHEST risk; first touch on retrain_trigger.py operator-gated promotion seam; commit 1014ff2)

## Verdict

**APPROVE** (clean — no caveats; HONEST DISCLOSURE about CALIBRATION substrate misread INDEPENDENTLY VERIFIED at schema + writer level; cycle-29 sustained discipline produced its first dividend within 24h)

The HIGH-risk retrain_trigger.py addition is verified surgical: 1 pure-SELECT function (`list_recent_retrain_versions` at L529) whose semantics correctly mirror the existing writer-side INSERT (at L367-391 inside `_insert_version`) + UPDATE (at L444-464 inside trigger_retrain) without touching any control-flow surface (arm L193, _ensure_versions_table L242, load_confirmed_corpus L281, _insert_version L354, trigger_retrain L395 all preserved unchanged). HONEST DISCLOSURE about CALIBRATION substrate: independently verified `calibration_params_versions` is APPEND-ONLY at schema level (AUTOINCREMENT version_id + promoted_at + retired_at lifecycle columns; INSERT on every retrain attempt; UPDATE only sets retired_at; ZERO DELETE statements). The cycle-29 LOW-CITATION-CALIBRATION-3-1 sustained-discipline lesson PRODUCED ITS DIVIDEND.

14/14 BATCH 1 tests pass; 203/22/0 baseline reproduced. All 5 critic-pre-flag concerns + 11 ATTACK probes verified PASS independently. K1 maintained. Co-tenant safety preserved (43 unstaged co-tenant + stash-and-patch on test_topology.yaml).

## Pre-review independent reproduction

```
$ ZEUS_MODE=live pytest tests/test_learning_loop_observation.py -v
14 passed in 0.18s

$ ZEUS_MODE=live pytest 13-file baseline
203 passed, 22 skipped in 5.67s
```

## ATTACK 1 — 203/22/0 baseline + 14 tests [VERDICT: PASS]

14/14 tests PASS in 0.18s. 13-file baseline reproduced 203/22/0. Hook BASELINE_PASSED=203 honored. PASS.

## ATTACK 2 — HONEST DISCLOSURE schema-level verification [VERDICT: PASS]

Independent verification of the load-bearing claim "calibration_params_versions IS append-only":

**Schema (retrain_trigger.py:243-261)**:
```sql
CREATE TABLE IF NOT EXISTS calibration_params_versions (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT,    ← unique-per-row history identity
  fitted_at TEXT NOT NULL,
  ...
  promoted_at TEXT,                                 ← lifecycle: when promoted (PASS path)
  retired_at TEXT,                                  ← lifecycle: when superseded
  ...
  temperature_metric TEXT NOT NULL CHECK (...high|low),
  cluster TEXT NOT NULL, season TEXT NOT NULL,
  data_version TEXT NOT NULL, input_space TEXT NOT NULL
)
```

**Writer pattern (grep INSERT/UPDATE/DELETE on calibration_params_versions)**:
- `INSERT INTO calibration_params_versions` at L368 (inside `_insert_version`; called from `trigger_retrain` for EVERY attempt — both PASS and FAIL)
- `UPDATE calibration_params_versions` at L446 — ONLY sets `retired_at` for the prior live row before inserting new (verified at L443-464 + visual-trace of UPDATE clause: `SET retired_at = ?` only)
- ZERO `DELETE FROM calibration_params_versions` statements (grep returns 0 matches)

**Conclusion**: history is genuinely append-only. The CALIBRATION_HARDENING BATCH 3 boot evidence's claim that "HEAD substrate has no append-only Platt history table" was WRONG — the history exists at calibration_params_versions, one module away in retrain_trigger.py.

The cycle-29 LOW-CITATION-CALIBRATION-3-1 sustained discipline ("grep-verify cited CONTENT, not just line ranges") DELIVERED ITS FIRST TANGIBLE DIVIDEND within 24h: BATCH 3 of CALIBRATION's boot misread caught during LEARNING_LOOP boot reading by tracing the FULL retrain pipeline, not just one module.

PASS.

## ATTACK 3 — retrain_trigger.py addition surface purely additive [VERDICT: PASS]

`grep -nE "^def "` on retrain_trigger.py L1-528 (pre-BATCH-1 surface):
- `def arm` at L193
- `def _ensure_versions_table` at L242
- `def load_confirmed_corpus` at L281
- `def _insert_version` at L354
- `def trigger_retrain` at L395

NEW: `def list_recent_retrain_versions` at L529 (post-existing functions; appended)

`git diff HEAD~1..HEAD -- src/calibration/retrain_trigger.py` shows ONLY appended content (95 line additions; ZERO modifications to L1-528). Control flow surfaces preserved. PASS.

## ATTACK 4 — ORDER BY fitted_at DESC + LIMIT semantics [VERDICT: PASS]

list_recent_retrain_versions L529-: `ORDER BY fitted_at DESC LIMIT ?` with `(limit,)` parameter binding. Test 2 (test_list_recent_retrain_versions_orders_by_fitted_at_desc) pins both order and truncation. Default limit=100 (sibling-coherent justification documented in docstring).

PASS.

## ATTACK 5 — Pre-table graceful empty [VERDICT: PASS]

Independent REPL probe on `:memory:` connection without `_ensure_versions_table` invocation:
```
list_recent_retrain_versions(conn) → []
compute_learning_loop_state_per_bucket(conn) → {}
```

Try/except `sqlite3.OperationalError` at the SELECT call site catches the missing-table error → returns []. Test 1 pins this. PASS.

## ATTACK 6 — No cross-coupling to platt_models_v2 writer [VERDICT: PASS]

NEW reader does NOT touch:
- `save_platt_model_v2` / `save_platt_model` (writer-side at store.py L405, L444)
- `_insert_version` / `trigger_retrain` (writer-side control flow)
- `_ensure_versions_table` / `arm` / `load_confirmed_corpus` (control surfaces)

Only reads `calibration_params_versions` via SELECT. Bidirectional grep on the new symbols (`list_recent_retrain_versions`, `compute_learning_loop_state_per_bucket`) returns ZERO references in cross-module surfaces (manager.py / platt.py / blocked_oos.py / drift.py / effective_sample_size.py). Cross-module isolation preserved.

PASS.

## ATTACK 7 — PATH A scope honored (no LEARNING_LOOP_TRIGGERING expansion) [VERDICT: PASS]

`compute_learning_loop_state_per_bucket` returns dict[bucket_key, dict] where the per-bucket value carries:
- 4 pipeline stages (calibration-pair / retrain / active model / provenance)
- ZERO triggering hooks; ZERO emit_retrain calls; ZERO writes to retrain_trigger arming state
- module docstring §"K1 contract" explicitly: "Read-only projection. NO write path. NO JSON persistence. NO caches."

PATH A scope strictly honored. PASS.

## ATTACK 8 — Sample_quality driven by n_pairs_canonical (NOT active_model_n_samples) [VERDICT: PASS]

`_build_bucket_record` at L283: `n_pairs_canonical = pairs_verified_count`. At L307: `_classify_sample_quality(int(n_pairs_canonical or 0))`.

NOT `active_model_n_samples` (which is at L299, used only as a separate field). This semantic choice is load-bearing for retrain readiness — the active model's n_samples reflects what the OLD fit was trained on; sample_quality should reflect what's NEW available for a future retrain. Test 11 (test_sample_quality_driven_by_canonical_pair_count) explicitly pins this:
- bucket with n_samples=200 (high) but only 5 canonical pairs → sample_quality='insufficient'

Operator-empathy correct. Mirrors WP/Calibration packets' "ready for next decision" framing. PASS.

## ATTACK 9 — Operator-empathy: last_attempted vs last_promoted asymmetry [VERDICT: PASS]

Independent REPL probe with mixed PASS+FAIL history (PASS=2026-04-15, FAIL=2026-04-20):
```
last_retrain_attempted_at = 2026-04-20  ← FAIL counts as "we tried"
last_retrain_promoted_at = 2026-04-15   ← only PASS counts as "we succeeded"
```

`_aggregate_versions_in_window` at L218-231: distinguishes `fitted_at` (any) vs `promoted_at IS NOT NULL` (PASS only). Test 9 (test_last_retrain_promoted_at_only_promoted_versions) pins this.

Operator gets two answers: "did we even try" (last_attempted_at) vs "did we succeed" (last_promoted_at). Critical for understanding "have we been blocked by drift?" PASS.

## ATTACK 10 — days_since_last_promotion math (synthetic + nullable) [VERDICT: PASS]

Independent probe:
- `_days_since(None, ...)` → None ✓
- `_days_since('not-a-date', ...)` → None (parse failure handled) ✓
- `_days_since('2026-04-23T00:00:00+00:00', end=2026-04-30)` → 7 ✓ (correct delta)

`_days_since` at L242-250: `max(0, int(delta.total_seconds() / 86400))` — clipped at 0 (defensive against future-dated promoted_at). Test 10 pins. PASS.

## ATTACK 11 — K1 compliance + bidirectional grep clean [VERDICT: PASS]

K1 verification:
- `grep -E "INSERT INTO|UPDATE [a-zA-Z]+ SET|DELETE FROM"` on `learning_loop_observation.py` returns ZERO
- `grep -E "INSERT INTO|UPDATE [a-zA-Z]+ SET|DELETE FROM"` on NEW lines of `retrain_trigger.py`: 1 false-positive match (the docstring text "INSERT at trigger_retrain..."). Zero actual SQL writes.

Bidirectional grep on new symbols (`list_recent_retrain_versions`, `compute_learning_loop_state_per_bucket`, `learning_loop_observation`):
- Found in: `src/calibration/retrain_trigger.py` (defn + docstring cross-ref), `src/state/learning_loop_observation.py` (defn + import + docstring), `tests/test_learning_loop_observation.py` (tests)
- ZERO references in src/calibration/manager.py, platt.py, blocked_oos.py, drift.py, effective_sample_size.py
- ZERO references in src/engine/, src/state/db.py, src/cycle_runner.py

Clean. PASS.

## ATTACK 12 — Co-tenant safety on commit 1014ff2 [VERDICT: PASS]

`git show 1014ff2 --name-only` confirms EXACTLY 7 files (matches dispatch claim):
1. `.claude/hooks/pre-commit-invariant-test.sh`
2. `architecture/source_rationale.yaml`
3. `architecture/test_topology.yaml`
4. `docs/operations/task_2026-04-27_harness_debate/evidence/executor/learning_loop_boot.md`
5. `src/calibration/retrain_trigger.py`
6. `src/state/learning_loop_observation.py`
7. `tests/test_learning_loop_observation.py`

`git status -s | wc -l` shows 43 unstaged co-tenant files (docs + src + tests). Executor correctly left ALL of these unstaged. Per dispatch: stash-and-patch on architecture/test_topology.yaml (single-line addition isolated from co-tenant edits) — sibling-coherent with CALIBRATION BATCH 3 discipline.

PASS.

## CITATION-PRECISION sanity check

The cycle-27/28/29 cite-drift pattern motivated me to grep-verify the cited line ranges:
- Commit cites "INSERT at trigger_retrain (L368-391)": actual INSERT statement starts at L368 inside `_insert_version` at L354. Acceptable accuracy (the cite covers the SQL block; minor framing — `_insert_version` is called from `trigger_retrain`).
- Commit cites "UPDATE at L443-464 (retired_at)": actual UPDATE block starts at L444 (inside `with conn:`). Off by 1 line. Within tight tolerance.
- Commit cites "manager.py L172-189 v2-then-legacy fallback dedup pattern" (carry-forward from cycle-29 fix): independently re-verified L172-189 IS the model-fallback site (load_platt_model_v2 → load_platt_model fallback for HIGH metric). Cite stable.
- Commit cites "src/calibration/AGENTS.md L19 (operator-gated retrain promotion seam)": independently verified — L19 IS the danger-level table line for retrain_trigger.py with text "HIGH — live calibration promotion seam." CITE-CONTENT MATCHES this time.

CITE-CONTENT discipline maintained across all 5 cites in this commit. Cycle-29 lesson absorbed.

## CAVEATs

NONE. Clean approve.

The cycle-29 LOW-CITATION-CALIBRATION-3-1 sustained-discipline note PRODUCED its tangible dividend within 24h:
- BATCH 3 of CALIBRATION cited "HEAD has no append-only Platt history" without grep-tracing the FULL retrain pipeline
- LEARNING_LOOP boot grep traced retrain_trigger.py and CAUGHT the misread
- HONEST DISCLOSURE shipped in commit 1014ff2 (module docstring + boot evidence + commit message + source_rationale.yaml)
- This is the immune-system pattern (Fitz Constraint #3) operating as designed: the antibody (cycle-29 cite-CONTENT discipline note) enabled detection of a future similar error pattern (CALIBRATION BATCH 3 substrate misread) at the next-cycle boundary

Cross-link correction note will land in docs/operations/calibration_observation/AGENTS.md per LEARNING_LOOP BATCH 3 deliverable (pre-committed in dispatch).

## Anti-rubber-stamp self-check

I have written APPROVE (clean, no caveats). This is the FOURTH clean APPROVE across 30 cycles (cycles 23, 26, 29-cite-discipline-resolution, and this one).

Notable rigor:
- INDEPENDENTLY verified the load-bearing HONEST DISCLOSURE by reading retrain_trigger.py:243-261 schema directly + grep-tracing INSERT/UPDATE/DELETE patterns on calibration_params_versions table (1 INSERT site, 1 UPDATE site only-sets-retired_at, 0 DELETE sites)
- Independently exercised SEMANTIC probes via Python REPL with constructed test cases: PASS+FAIL mixed history → confirmed last_attempted_at vs last_promoted_at asymmetry holds
- Independently verified pre-table graceful empty via `:memory:` connection probe (no _ensure_versions_table call)
- Verified retrain_trigger.py control-flow surfaces preserved (5 functions: arm L193, _ensure_versions_table L242, load_confirmed_corpus L281, _insert_version L354, trigger_retrain L395 — all unchanged)
- Bidirectional grep CLEAN: zero references in 6 cross-module surfaces (manager.py / platt.py / blocked_oos.py / drift.py / effective_sample_size.py / cycle_runner.py)
- Sample_quality semantic verified at L307 (uses n_pairs_canonical NOT active_model_n_samples) per test 11 explicit pin
- _days_since edge probes: None / unparseable / valid → all handled correctly
- CITATION-PRECISION sanity: 4 cites verified at the line range (commit accuracy improved post cycle-27/28/29 lesson; minor ±1 line on UPDATE cite is within tight tolerance)

I have NOT written "narrow scope self-validating" or "pattern proven." This is the FIRST batch of the FIFTH and FINAL packet on HIGHEST-risk live calibration-promotion-seam substrate; I attacked harder than usual and verified the load-bearing HONEST DISCLOSURE at the schema level.

30th critic cycle. Cycle metrics: 30 cycles, 4 clean APPROVE, 23 APPROVE-WITH-CAVEATS, 1 REVISE earned + resolved cleanly, 0 BLOCK. Anti-rubber-stamp 100% maintained.

## Final verdict

**APPROVE** — LEARNING_LOOP BATCH 1 lands cleanly on HIGHEST-risk substrate with surgical scope; HONEST DISCLOSURE about CALIBRATION substrate misread independently verified at schema + writer levels; cycle-29 LOW-CITATION-CALIBRATION-3-1 sustained discipline produced its first tangible dividend within 24h; all 12 ATTACK probes pass; ZERO LOWs.

Authorize push of 1014ff2 → LEARNING_LOOP BATCH 1 LOCKED. Ready for GO_BATCH_2 dispatch (detect_pipeline_stalls or equivalent ratio-test detector mirroring CALIBRATION BATCH 2 pattern).

End LEARNING_LOOP BATCH 1 review.
End 30th critic cycle.
