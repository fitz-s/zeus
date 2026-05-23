# PR #311 Rebase Recipe — schema-ladder collision with PR-D (2026-05-23)

`origin/main` advanced to **SCHEMA_VERSION=32** (PR-D, added `NoTradeReason.PROBABILITY_SANITY_GATE`,
day0 HIGH sanity gate) while `wave/combination-strategies-20260522` sits at **31** (C-EPIC C1/C2,
added 5 members). Collision is purely additive. Target after rebase: **SCHEMA_VERSION=33**, both
enum sets coexist, every CHECK ladder includes 32 AND 33.

## Worktree (do NOT touch live primary tree)
```
git fetch origin
git worktree add -B wave/combination-strategies-20260522 \
  .claude/worktrees/pr311-rebase-20260523 origin/wave/combination-strategies-20260522
cd .claude/worktrees/pr311-rebase-20260523
git rebase origin/main
```

## Conflict resolution (take BOTH sides — additive union)
1. `src/state/db.py` line 1 — `SCHEMA_VERSION = 33  # 2026-05-23: PR-D PROBABILITY_SANITY_GATE (32) + C-EPIC C1/C2 combos (31) reconciled`
2. `src/contracts/no_trade_reason.py` — keep main's `PROBABILITY_SANITY_GATE` AND the 5 C-EPIC members
   (`JOINT_EVT_ALERT_UNWIRED`, `JOINT_EVT_ALERT_LR_MISSING`, `JOINT_EVT_TAIL_NO_EDGE`,
   `OPENING_STALE_FOK_UNWIRED`, `OPENING_STALE_FOK_NO_EDGE`). All distinct values; no collision.
3. ALL `schema_version IN (...)` CHECK ladders — append `32, 33` so each ends `...30, 31, 32, 33)`:
   - `src/state/db.py` (3 sites)
   - `src/state/schema/no_trade_events_schema.py` (2 sites)
   - `src/state/schema/phase6_evidence_schema.py` (1 site: `IN (25,...,33)`)
   Plus any `CASE WHEN schema_version IN (...)` remap keep-list — extend to 33.
4. Migration guards (`_rebuild_stale_no_trade_events_table`, `_migrate_evidence_tier_assignments_schema`)
   are already enum-iteration / `str(SCHEMA_VERSION) in table_sql` aware (wave fix). Confirm they
   reference `SCHEMA_VERSION`, NOT a hardcoded version substring. No new edit expected.
5. `tests/state/_schema_pinned_hash.txt` — regenerate AFTER all schema edits land:
   `python -m pytest tests/state -k pinned_hash` will print expected; or run the repo's hash-gen path.
6. `tests/test_p1_findings_evidence_risk.py` — brittle, hardcodes SCHEMA_VERSION → set 33.
7. `architecture/test_topology.yaml`, `src/analysis/promotion_proof_router.py`,
   `src/strategy/candidates/c1_joint_tail_bayes.py`, `c2_opening_stale_fok.py`,
   `tests/test_cepic_combination_candidates.py`, `tests/test_promotion_proof_router_wave.py`
   — take BOTH (branch additions + any main-side edits); these are namespace-additive, no semantic merge.

## Verify before push
- `python -m pytest tests/state tests/test_cepic_combination_candidates.py tests/test_promotion_proof_router_wave.py tests/test_p1_findings_evidence_risk.py` → all green
- v28→…→33 migration antibody passes (start-from-prior-version table shape)
- Money-safety unchanged: C1/C2 still shadow + kelly 0, not in `live_allowed_keys`
- `git diff origin/main...HEAD --stat` — only the 12 expected files, no stray reverts

## Push
`git push --force-with-lease origin wave/combination-strategies-20260522`
Then confirm `gh pr view 311 --json mergeStateStatus` → CLEAN.
