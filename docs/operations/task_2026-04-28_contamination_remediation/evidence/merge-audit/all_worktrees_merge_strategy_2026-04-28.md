# All-worktrees merge strategy — 2026-04-28

Status: **planning / no merge executed**
Target branch: `plan-pre5 @ 8a433f6`
Safety backup: `backup/plan-pre5-before-worktree-merge-20260428 @ 8a433f6`
Clean integration worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-merge-integration-20260428`
Clean integration branch: `integration/all-worktrees-2026-04-28 @ 8a433f6`

Hard operator invariant: **Hong Kong has no WU ICAO.** No accepted merge result may add or preserve HK/Hong Kong `WU`, `VHHH`, or `wu_icao` settlement-source assumptions. HKO must remain a caution/HKO-specific source path unless separately proven by fresh operator-authorized evidence.

## 1. Inventory conclusion

Already contained in `plan-pre5` history, so no branch merge is needed:

- `claude/zeus-full-data-midstream-fix-plan-2026-04-26` — 0 unique commits; only dirty runtime state in its worktree.
- `claude/live-readiness-completion-2026-04-26` — 0 unique commits; only dirty `.code-review-graph/graph.db`.
- `claude/pr18-execution-state-truth-fix-plan-2026-04-26` — 0 unique commits; only dirty runtime state.
- `worktree-post-r5-eng` — 0 unique commits; only dirty `.code-review-graph/graph.db`.

Do not merge runtime-only dirt into branch by default:

- `.code-review-graph/graph.db` is derived context, not authority.
- `state/status_summary.json` and `state/auto_pause_failclosed.tombstone` are runtime projections/control artifacts; they are not source commits for an all-worktree code merge.

Unique branches still requiring integration work:

- `claude/mystifying-varahamihira-3d3733` — 16 unique commits; critic verdict **BLOCK**.
- `claude/quizzical-bhabha-8bdc0d` — 5 unique commits; critic verdict **BLOCK**.

## 2. Why direct merge is wrong

A direct “merge everything” would violate the current gates because:

1. Both unique branches failed independent cross-session critic review.
2. `mystifying` has high-risk conflicts in authority/topology/live/DB files.
3. `quizzical` is conflict-clean mechanically, but semantically unsafe: it contains HK WU/VHHH assumptions and current-fact updates based on unauthorized DB mutation packets.
4. The current target branch has validated contamination-remediation guardrails that must not be overwritten.
5. The main worktree and other worktrees contain co-tenant dirty/untracked state that must not be absorbed with `git add -A` or broad cleanup.

Therefore the only safe way to “merge all worktrees” is **curated integration**, not blind branch merge.

## 3. Integration lanes

### Lane A — Baseline and no-unique branches

Action:
- Record that four worktree branches are already contained in `plan-pre5`.
- Exclude dirty runtime/derived artifacts unless a separate runtime-state packet authorizes them.

Verification:
- `git rev-list --count plan-pre5..<branch>` is `0`.
- No code/doc changes need to be imported from those branches.

### Lane B — `quizzical` sanitized import

Candidate branch: `claude/quizzical-bhabha-8bdc0d`

Useful material to consider importing:
- LOW market history caution / antibody if independently verified and kept as packet-evidence-backed fact.
- Observation provenance packet evidence, but only as historical packet evidence, not active current-fact authority.
- Tests after removing hard-coded absolute workspace DB paths and marking them runtime-local/read-only where appropriate.

Must revise before any merge/cherry-pick:

1. Rewrite HK/Hong Kong material:
   - `docs/operations/task_2026-04-28_obs_provenance_preflight/rfc_hko_fresh_audit_promotion.md`
   - `docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md`
   - Remove or correct `WU values for VHHH`, `WU/VHHH airport data`, and `wu_icao` as an HK route.
   - State: Hong Kong remains HKO/fresh-audit caution only.

2. Do not import `docs/operations/current_data_state.md` as-is:
   - It claims current canonical data posture from production DB mutation work not authorized by the active remediation packet.
   - Either omit this file from the merge, or convert claims into packet-local evidence that does not update current-fact status.

3. Quarantine or guard apply-capable scripts:
   - `backfill_low_settlements.py`
   - `migrate_settlements_physical_quantity.py`
   - `recompute_source_role_canonical.py`
   - `remove_synthetic_provenance.py`
   - `fill_obs_v2_payload_identity_existing.py`
   - `fill_obs_v2_payload_identity_ogimet.py`
   - `fill_observations_provenance_existing.py`
   Required guard: explicit operator approval flag/env, read-only default, no implicit production DB path, no unreviewed external API side effects.

4. Treat dirty uncommitted `gate5_ogimet_quarantine_apply_2026-04-28.json` as not branch content until separately committed/audited.

Recommended mechanics after edits are prepared:
- Use a clean integration branch, not the dirty main worktree.
- Prefer `git cherry-pick -n` of the five commits into a scratch branch, then immediately edit blockers before committing a new curated integration commit.
- Alternative: generate a patch from `quizzical`, apply selectively, omit/restore current-fact files, then commit the curated result.
- Re-run cross-session critic; only if verdict is APPROVE/REVISE may it be merged onward.

Minimum verification:
- HK grep: no HK `WU/VHHH`/`wu_icao` route remains in accepted files.
- Topology planning lock on changed current-fact/operations/test files.
- Tests for HK no-WU guards and any newly retained tests.
- Full suite only after curated diff passes targeted tests.

### Lane C — `mystifying` split import, no branch-level merge

Candidate branch: `claude/mystifying-varahamihira-3d3733`

Direct branch merge is not safe because `git merge-tree` reports conflicts in:

- `.claude/hooks/pre-commit-invariant-test.sh`
- `.claude/settings.json`
- `architecture/invariants.yaml`
- `architecture/source_rationale.yaml`
- `architecture/test_topology.yaml`
- `architecture/topology.yaml`
- `docs/methodology/adversarial_debate_for_project_evaluation.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `scripts/backfill_openmeteo_previous_runs.py`
- `scripts/rebuild_settlements.py`
- `src/data/forecasts_append.py`
- `src/execution/executor.py`
- `src/state/db.py`
- `tests/test_digest_profile_matching.py`
- add/add harness-debate docs

Split it into sub-lanes instead:

#### C1. Documentation/evidence-only commits

Likely lower-risk imports after packet-router review:
- `7b2d73e` backtest first-principles planning packet.
- `6a93d18` F11 forecast issue-time packet plan.
- `5bd9be8` F11 apply runbook + WU observation triage packet plan.
- `fef0c8a` forecasts consumer audit evidence.
- `3ece859`, `cdb19bb`, `1e0c197` corrections/status edits inside the backtest review packet.

Guard:
- These should remain packet evidence, not current law/current-fact updates.
- Register packet directories only if operations router requires it.

#### C2. Backtest purpose/skill code slices

Candidate commits:
- `99e0b96` backtest purpose contracts + decision-time provenance.
- `5ab1468` skill orchestrator + economics tombstone.

Guard:
- Run topology for `src/backtest/**` and corresponding tests.
- Preserve current target branch manifests; manually add only required registry entries.
- Run targeted tests: `tests/test_backtest_purpose_contract.py`, `tests/test_backtest_skill_economics.py`.

#### C3. Forecast issue-time / training eligibility slices

Candidate commits:
- `14d87ae` dissemination schedule registry.
- `5b1b05d` forecast availability/schema/writer/backfill/eligibility.
- `7b46003` review-fix slice.
- `57fdc81` replay skill-eligibility filter.

Guard:
- This is data/replay/calibration-adjacent. Run semantic boot for data/replay/calibration.
- Do not accept branch changes to `src/state/db.py`, `src/data/forecasts_append.py`, or migration scripts without schema/current-data proof.
- Keep current target protections and registry structure; do not overwrite topology generated by contamination remediation.
- Targeted tests: forecast writer provenance, training eligibility, replay skill eligibility, dissemination schedules.

#### C4. Do not import `8dbe7c2` as a merge commit

`8dbe7c2` is a historical merge of `origin/plan-pre5` into the candidate branch and is the source of a broad replay of authority/topology/live-money files. It should not be cherry-picked or replayed as a unit. Reconstruct only the candidate-side unique deltas from the specific commits above.

#### C5. Settlement rebuild guard

Any retained change involving `scripts/rebuild_settlements.py` must prove:
- Hong Kong/HKO rejects `wu_icao_history` and `wu_icao`.
- `settlement_source_type == hko` maps only to HKO source family.
- Current remediation tests for HKO/no-WU still pass.

## 4. Proposed execution sequence

1. Keep `plan-pre5` untouched until curated integration branch passes review.
2. Work only in clean integration worktree/branch.
3. Lane A: record no-op branches; exclude dirty runtime/derived artifacts.
4. Lane B: build a sanitized `quizzical` patch first because it is conflict-clean and smaller.
5. Run critic on sanitized Lane B diff. If approved, commit Lane B integration.
6. Lane C1/C2/C3: import `mystifying` in slices, each with its own topology/semantic boot, targeted tests, and critic gate.
7. After all accepted slices are integrated, run:
   - HK no-WU grep/proof tests.
   - Topology doctor planning/map maintenance checks.
   - Targeted tests per lane.
   - Full pytest only after targeted green.
   - `scripts/live_readiness_check.py --json` as read-only readiness evidence, not deploy authorization.
8. Dispatch final critic + verifier on integration branch.
9. Only after final APPROVE/PASS, update `plan-pre5` by fast-forward/merge from the integration branch using specific evidence and Lore merge commit.
10. Stage only explicit files. Never `git add -A`.

## 5. Non-goals / exclusions

- No live deployment.
- No production DB mutation.
- No credentialed CLOB/API action.
- No implicit acceptance of runtime dirty state.
- No HK WU/VHHH/`wu_icao` source route.
- No blind overwrite of current contamination-remediation packet evidence.

## 6. Current recommendation

Do **not** directly merge either unique branch. The correct path is to create curated integration commits that preserve useful work while deleting or rewriting unsafe claims. The first practical implementation target should be a sanitized `quizzical` import with HK WU assumptions removed and current-fact updates omitted or downgraded to packet-local evidence.
