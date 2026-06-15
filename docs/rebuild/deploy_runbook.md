# Q-Kernel Spine — Live Cutover Runbook

Created: 2026-06-15. Authority: docs/rebuild/arm_replay_report.md (settlement validation) +
docs/rebuild/impl_w5b_integration.md (integration) + critic PASS (loop-back).

## State going in
- Rebuild branch `claude/qkernel-rebuild` @ `c25319449e` — spine built, settlement-validated
  (center proven, q coherent, σ honest std(z)=0.93), wired live behind `qkernel_spine_enabled`
  (config/settings.json `feature_flags`, **default false**). Flag OFF = legacy byte-for-byte.
- Live daemon runs the MAIN TREE `/Users/leofitz/zeus` on `live/iteration-2026-06-13` (`d7788f0f3a`).
- **Merge is CLEAN**: rebuild ↔ live-branch file overlap = ZERO (operator divergence is all
  workspace-routing docs/hooks; rebuild is all q-engine src). No conflicts.
- **Blocker for the merge**: main tree has UNCOMMITTED operator work — `M AGENTS.md`,
  `M config/settings.json` (the live bias-off; already committed on the rebuild branch as
  `dbd1fd9287`), `?? docs/evidence/9router_*`. The working tree must be clean to merge.

## Cutover steps (run in the MAIN TREE /Users/leofitz/zeus)
1. **Handle the uncommitted operator work** (operator decision — commit or stash):
   `git -C /Users/leofitz/zeus add -A && git -C /Users/leofitz/zeus commit -m "wip: operator main-tree state pre-qkernel-cutover"`
   (or `git -C /Users/leofitz/zeus stash`). NOTE: config/settings.json bias-off is already on
   the rebuild branch, so it survives the merge regardless.
2. **Merge the rebuild (clean, no conflicts):**
   `git -C /Users/leofitz/zeus merge --no-ff --no-edit claude/qkernel-rebuild`
3. **Flip the cutover flag ON:** set `feature_flags.qkernel_spine_enabled = true` in
   `/Users/leofitz/zeus/config/settings.json`, commit.
4. **Restart the daemon:** `launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading`
5. **Watch (the goal):** a real candidate → `venue_command` → fill → settlement-graded
   POSITIVE after-cost EV (not a fixed %). Watch decision receipts carry the spine fields
   (predictive_distribution_id, q_source=qkernel_spine, edge_lcb, delta_u, payoff_vector_hash).

## Post-flip ARM watch (positive after-cost EV + calibration; NO fixed-% bar)
- Daemon health: receipt flow alive, no fail-closed family storm, decision loop not dead.
- Point-q calibration, q_lcb coverage, after-cost EV by class/route, PIT/width, market-disagreement.
- Inverse-failure tripwire: the modal/favorite cohort shows POSITIVE after-cost EV on its OWN
  settled rows (not base-rate favorite-buying).

## Rollback (instant)
- Flip `qkernel_spine_enabled = false` in config/settings.json + restart → legacy path
  (byte-for-byte; all legacy authorities still INERT-present, not yet deleted).
- The single flag is the kill switch. Stage-11 legacy DELETION happens only AFTER ARM-stable.

## Known non-blocking gaps (critic-judged conservative; close post-deploy)
- day0 served as NO_DAY0 at the seam — envelope-lock alone fixes the Tokyo class; day0 only
  licenses leaving the envelope toward an observed extreme (absence is conservative).
- σ no-floor cells use the thin-substrate RSS (conservative-widening vs ARM); floor cells σ-identical.
- after-cost EV-by-class — needs live fills or the condition_id→bin live join (post-deploy ARM).
