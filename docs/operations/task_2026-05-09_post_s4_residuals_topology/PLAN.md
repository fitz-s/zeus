# Post-S4 Residual Mainline And Topology Friction Plan

Created: 2026-05-09
Last reused/audited: 2026-05-09
Authority basis: PR #104 closeout, S1-S4 alignment repair workflow, operator request to analyze session topology friction and start the next packet.

## Current State

- Packet base before this docs packet: `main` at `b4c42aeb` (`Merge pull request #104`).
- The old alignment repair worktrees were removed after their merged branches were deleted locally and remotely.
- The only remaining Zeus worktree is `/Users/leofitz/.openclaw/workspace-venus/zeus` on `main`.
- Local dirty/untracked files already existed outside this packet, including `scripts/cloud_tigge_autochain.sh`, several `docs/operations/task_2026-05-08_*` artifacts, `station_migration_alerts.json`, and TIGGE stderr/tmp files. This packet does not claim or mutate them.

## Dirty Worktree Triage

Read-only triage on 2026-05-09 classified the pre-existing dirty/untracked state as follows:

| Path group | Classification | Evidence | Handling |
|---|---|---|---|
| `scripts/cloud_tigge_autochain.sh` | important uncommitted operational fix | Accepts `complete`/`completed`, resolves lane status files across cycle suffix variants, logs missing/incomplete lanes. | Preserve; needs its own review/commit lane, not topology tooling. |
| `docs/operations/task_2026-05-08_*` docs | important packet/audit evidence | Includes D+10 horizon audit, alignment/deep-audit plans, ECMWF publication reports, low recalibration run log, phase-B root-cause dossier, post-merge full-chain task/run evidence. | Preserve; do not delete as cleanup. |
| `docs/operations/task_2026-05-09_daemon_restart_and_backfill/TASK.md` and `RUN.md` | important active operational task evidence | Names daemon restart/backfill work; `RUN.md` records launchctl sandbox blockage, stale success semantics, and settlement schema drift. | Preserve; likely needs packet ownership decision. |
| `station_migration_alerts.json` | benign current run artifact | Contains empty alerts with timestamp. | Low risk; can be archived/ignored after owner confirms. |
| `tmp/ecmwf_open_data_*.stderr.txt` | operational failure evidence | Contains ECMWF 404 and filename-too-long traces. | Preserve until ECMWF/backfill packet consumes or archives it. |
| `tmp/phase2_post_extract_then_stop.sh` | operational helper script | GCE watcher/scp/VM-stop workflow. | Preserve; review before promotion or deletion. |

Conclusion: none of the dirty files should be deleted as generic cleanup. They are unrelated to topology tooling but carry useful current operational evidence.

## Objective

Turn the post-S4 state into a structured next packet instead of mixing design/data-authority work into the completed S1-S4 observability/provenance repair line.

This packet is planning-only until a narrower implementation scope is routed and admitted.

## Residual Mainline Classification

| Lane | Current classification | Why it is not part of PR #104 | Next action |
|---|---|---|---|
| Calibration rebuild sentinel read-side | policy/data-design open | Existing canonical world DB has active Platt 00/12 rows without rebuild-complete sentinel rows, so a live hard gate would be unsafe as a drive-by S4 fix. | Design sentinel semantics: advisory status, promotion gate, or rebuild/refit contract. |
| Source/current facts | data-authority open | HK/HKO, WU API-vs-site, Taipei transition, and DST rebuild questions require current source evidence and possibly historical data packets. | Create current-fact evidence packet with expiry and source receipts. |
| D3 execution-cost continuity | code path currently mitigated | Kelly sizing boundary is typed through `ExecutionPrice`; focused tests passed after S4. | Keep as proof lane unless a new direct execution consumer bypass is found. |
| D4 exit evidence symmetry | statistical gate currently covered | Runtime gate tests cover asymmetric statistical exits; ORANGE/DAY0 exclusions are evidence-class/design questions. | Keep residual as design packet, not live-code patch. |
| Topology friction | system-improvement open | The S1-S4 line exposed repeated route/admission and companion-map friction. | Implement structural topology improvements under a dedicated tooling packet. |

## Topology Friction Analysis

### What Helped

- Topology admission prevented observability packets from drifting into executor, venue, schema-migration, or production DB mutation surfaces.
- Risk tiers made the blast radius visible: S1 required T4 ceremony, while S2-S4 could stay T3 reporting/read-model work.
- Digest route regressions in `tests/test_digest_profile_matching.py` converted phrase fixes into durable tests instead of one-off routing luck.
- `architecture/test_topology.yaml` and map-maintenance checks caught the missing trust/category registration for the new S4 test file before closeout.
- PR review bots found semantic gaps topology alone would not catch, especially executable snapshot fact matching and status-time scan cost.

### What Blocked Or Slowed Work

- Natural packet wording often routed to `generic` or `advisory_only` until exact strong phrases were added to topology profiles.
- High-fanout files such as `src/state/db.py` made routing brittle: the same file can be schema, producer, runtime state, or provenance depending on intent.
- New test-file companion requirements were easy to miss because implementation checklists did not explicitly say “update `architecture/test_topology.yaml` for every new `tests/test_*.py`.”
- Planning docs paths were initially unclassified, so even docs-only packet creation began with advisory topology noise.
- Topology feedback was partly ephemeral: unless it was written into `PROGRESS.md`, a packet, or memory, the same phrase/profile discovery had to be rediscovered.
- Worktree/branch cleanup after merge was manual and easy to defer, leaving old topic branches/worktrees around after they were already merged into `main`.

## Structural Improvements Proposed

1. Add a packet preflight checklist generator that emits: intended files, nearest topology profile, strong phrases to use, risk tier, companion files, and stop conditions.
2. Teach topology doctor to explain advisory-only decisions in terms of “closest admitted profile + rejected files + missing strong phrase,” not only the final profile name.
3. Generate high-fanout route hints for files such as `src/state/db.py`, `src/engine/cycle_runtime.py`, and `src/observability/status_summary.py` from current topology metadata and dry-run fixtures. Do not create a standalone hand-maintained profile catalog.
4. Add a new-test companion guard to packet templates: every new `tests/test_*.py` must update both `test_trust_policy.trusted_tests` and a `categories` bucket before PR open.
5. Make the operation-end topology capsule mandatory in packet progress: one help, one friction, one next topology delta, and whether the route matched the semantic task.
6. Add a post-merge cleanup recipe as a later docs/tooling lane: fetch main, fast-forward primary `main` worktree, detach/remove merged auxiliary worktrees, delete merged local/remote topic branches, and preserve untracked artifacts before removal.
7. Split data-authority packets from code-observability packets by default: source facts, calibration promotion, and historical rebuilds need current-fact receipts before any implementation lane opens.

## Proposed Next Packet Order

1. `topology_tooling_preflight`: implement generated, tested checklist/explain/companion-guard improvements with no runtime money-path changes. Deep plan: `phases/task_2026-05-09_topology_tooling_preflight/PLAN.md`.
2. `calibration_sentinel_policy`: decide and test how rebuild-complete sentinels relate to active Platt serving and promotion eligibility.
3. `source_current_fact_refresh`: collect HK/HKO, WU, Taipei, and DST evidence with expiry-bound receipts.
4. `d4_exit_evidence_design`: decide whether ORANGE/DAY0 residuals need a stronger evidence class or only documented non-statistical treatment.

## Stop Conditions

- Do not implement runtime, calibration, source, or settlement behavior from this packet.
- Do not mutate production DBs.
- Do not use stale packet artifacts as current fact.
- Do not delete or archive unrelated local artifacts without explicit ownership.
- If topology remains advisory-only for a proposed tooling implementation, keep that work docs-only and narrow the scope.