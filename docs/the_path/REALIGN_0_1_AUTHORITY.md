# REALIGN 2026-06-07b — Live authority moved to `replacement_0_1`; integrate 7 hotfix commits before P0-P4

> Trigger: operator "hotfix branch may have commits your worktree does not — pick them now before large refactoring." Confirmed: primary `fix/opportunity-book-selector` advanced **7 commits** past worktree base `16c35e7445` (now at `e5e5f022ee`, local-only, ahead of origin `922df99`).

## The 7 commits (intent)
- `8317cd2080` Route live forecast authority through **replacement_0_1** posterior (AIFS + Open-Meteo 0.1). Confidence=medium, scope=broad. "Live must stop using baseline OpenData probabilities after the 0.1 cutover." NO-side fail-closed native-only; no YES→NO complement.
- `aeff1cd24b` Keep replacement_0_1 authority **single-owner** — legacy replacement live-authority hook must NOT reprocess replacement_0_1 proofs (would request a direction proof that intentionally doesn't exist post no-complement). One-builder iron rule.
- `1692757d0c` Make live FSR entry replacement-gated — route live forecast-snapshot emission through the 0.1 posterior when replacement trade authority enabled; legacy source_run IDs = causal provenance only.
- `44bc10f6a4` no-cap forecast emit family-scoped (SQL fairness before Python).
- `d48e159da7` reactor write-lock contention = retryable, not terminal dead-letter.
- `c3c7ee3f4e` Block below-minimum marketable BUY submits pre-venue (no auto-inflate to venue min).
- `e5e5f022ee` Keep pre-venue submit blocks out of POST_SUBMIT_UNKNOWN state.

## THE STRUCTURAL FINDING (re-points FIX-1)
`_replacement_authority_probability_and_fdr_proof` (event_reactor_adapter.py:~5294 call site; ~5341 def) is now the live probability authority for FORECAST_SNAPSHOT_READY. It is gated by:
```python
def _replacement_authority_enabled() -> bool:
    return bool(settings["feature_flags"].get("openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled", False))
```
→ **flag-ALONE grants live 0.1 authority; NO settlement-validated evidence gate.** Same evidence-bypass class as the legacy resolver (FIX-1), but on the path that is ACTUALLY live now. Disciplined parts already present: readiness/bundle/bin-binding raise if missing (`REPLACEMENT_0_1_LIVE_AUTHORITY_{READINESS_MISSING,BUNDLE_BLOCKED,BIN_BINDING_MISSING}`); q_lcb + Wilson LCB for AIFS member probs; NO-side fail-closed; exact physical-bin-core topology match. Missing only: evidence-before-authority.

## CONSEQUENCES
1. **Phase −1 re-point.** FIX-1 (legacy `resolve_replacement_forecast_runtime_policy`) and FIX-3 (legacy `replacement_forecast_reactor_hook`) remain valid defense-in-depth, but the LIVE risk surface is the 0.1 path. The K≪N fix: ONE `replacement_live_authority_evidence_gate(conn, city, target_date, metric)` that returns whether settlement-validated promotion+capital evidence permits live authority — consulted by BOTH `_replacement_authority_enabled()` AND the legacy resolver. One gate, one truth (iron rule #4). Absent passing evidence → 0.1 path returns None (falls back to canonical/shadow), legacy resolver caps at SHADOW_VETO.
2. **FIX-2b re-locate.** event_reactor_adapter.py was rewritten (+189). The submit boundary / `_submit` guards (was 916-939) and the OperatorArm insertion point must be re-located on the merged file. Re-do FIX-2b post-rebase, not on the stale base.
3. **P0/P1/P2 re-center.** The live authority is ALREADY the 0.1 posterior — not OpenData ENS + 0.5→0.1 bridge. So: P0 bias/EMOS work targets the replacement_0_1 posterior + its bundle (`replacement_forecast_bundle_reader`), NOT the OpenData serving path. The "before/after blend tuning" = tune the replacement_0_1 posterior (AIFS/Open-Meteo 0.1 + its q_lcb), measured on VERIFIED WU settlement. The 0.5→0.1 resolution bridge is no longer the live spine (live is native 0.1); it stays only for historical prior transport. P0/P1 design briefs (in flight) were written against the OpenData model — re-point them.

## INTEGRATION ORDER (do before any P0-P4 refactor)
1. Quiesce the worktree (let the finalize workflow finish; do not rebase mid-edit).
2. Commit Phase −1 (FIX-1/2a/3/4/5a + FIX-2b) on `thepath/audit-realign`.
3. `git rebase <primary HEAD>` (currently `e5e5f022ee`; re-check at integration time — operator is actively hotfixing). Conflicts expected ONLY in `event_reactor_adapter.py` + `main.py` (FIX-2b vs replacement_0_1). All other FIX files are untouched by the 7 commits → clean.
4. Resolve: keep the 0.1 authority code; re-apply OperatorArm at the merged submit boundary; merge FIX-2b cleanly.
5. Re-point FIX-1 → the single evidence gate consulted by `_replacement_authority_enabled()` + legacy resolver. TDD: flag-true + evidence-absent → 0.1 authority NOT granted (returns None / SHADOW_VETO).
6. Re-audit the full replacement_0_1 path (event_reactor_adapter.py:5228-5510, replacement_forecast_bundle_reader.py, new test tests/engine/test_replacement_0_1_live_authority_probability.py) for residual evidence/direction-law/operator-arm/fail-closed gaps.
7. Re-point P0/P1/P2 briefs to the 0.1 posterior, then proceed.

## PROCESS NOTE (iron rule #3: reality not memory)
The operator was hotfixing the LIVE 0.1 cutover WHILE this audit ran. The worktree snapshot went stale within the same session. Lesson: re-probe primary HEAD before every integration step; the live branch moves under us.
