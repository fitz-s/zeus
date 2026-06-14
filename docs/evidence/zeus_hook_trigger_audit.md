# Zeus Hook Stack — Trigger Correctness & Effectiveness Audit

- Date: 2026-06-13
- Scope: read-only audit of .claude/hooks/dispatch.py, .claude/hooks/registry.yaml, .claude/settings.json, .claude/hooks/citation_grep_gate.py
- Method: read every handler; ran each handler against realistic + adversarial payloads (firing observed directly via in-process import, not inferred); cross-checked vs 1 month of telemetry (.claude/logs/hook_signal/2026-06.jsonl = 492,653 invocations, 116 MB).
- Bar (today goal-guard standard): (1) fires on the PRECISE scenario only — not a substring of command text; (2) silent/no-op otherwise; (3) fail-safe; (4) cheap.

## Live reproduction during this audit (smoking gun)
Benign commands run BY THIS AUDIT — heredocs/probes/report-writes that only CONTAINED trigger strings inside literals or prose — were blocked or spammed:
- BLOCKED by maintree_git_state_guard (command text contained the checkout/reset verbs inside literals/prose). Reproduced 5x, including blocking the heredoc that writes THIS report because the prose documents the bug.
- BLOCKED by cotenant_staging_guard (command text contained the broad add form inside a literal).
- BLOCKED by pre_merge_comment_check (command text contained a space-delimited pr-merge phrase inside a literal) — which then issued ~4 LIVE gh api GraphQL/REST calls against a real PR.
- Spammed ~7x by maintenance_worker_dry_run_floor (command text contained the dispatch.py governance path).
- Spammed by invariant_test + secrets_scan (command text contained the commit verb inside heredoc literals).

A BLOCKING substring false-fire is strictly worse than the advisory smell: it DENIES a legitimate command.

## Telemetry (2026-06 month-to-date)
Every PreToolUse:Bash hook fires once per Bash call (~37k/mo each). Block events this month: cotenant_staging_guard x5, maintree_git_state_guard x4, pr_create_loc_accumulation x2, pre_merge_comment_check x2. (Telemetry logs advisory_check regardless of emission; emission rates below come from direct handler probing.)

## ADVISORY HOOKS — verdict table

| Hook | Trigger (exact) | False-fire (proven) | Effective? | Freq | Verdict + fix |
|---|---|---|---|---|---|
| maintenance_worker_dry_run_floor | Edit/Write/MultiEdit/Bash AND search-target CONTAINS any of 4 governance path substrings AND ZEUS_MW_DRY_RUN_VERIFIED unset. For Bash, target = WHOLE command string. | HIGH. FIRES on read-only diff of a governance file; FIRES on editing the GLOBAL ~/.claude/settings.json (non-Zeus). ~150-word checklist injected 7x this audit. | No. Advisory, no deny path. Generic checklist re-injected verbatim. Zero enforcement. | HIGH | DELETE. If kept: SCOPE to Edit/Write/MultiEdit only (drop Bash) AND resolve file_path to abs path under REPO_ROOT before match (kills global ~/.claude false-fire). |
| citation_grep_gate | Edit/Write/MultiEdit/NotebookEdit content fields contain path.ext:NNN / path.ext LNNN where file RESOLVES and NNN out of bounds. | LOW. Silent on valid lines, unresolvable bare names, non-edit tools. Real state check. | Partial-yes. Catches stale citations (:999999 FIRES; :5 noops). | LOW | KEEP. The MODEL the others should imitate. |
| pr_thread_reply_waste | Bash matches a reply-post surface (pr comment N, GraphQL reply mutations, REST comments POST). Allow-pass: resolveReviewThread, pr review. | LOW. Noops on pr view (read) and pr review (formal). | Marginal. Advisory; ~30-line essay re-injects each fire; policed behavior rare. | LOW | SCOPE (trim). Trigger fine; cut text to 3 lines. |
| pre_branch_create_in_primary | Bash branch-create/worktree-add verbs AND cwd == primary worktree (real worktree list check). | LOW-MED. Bare-git substring regex matches an echo of the phrase, but cwd==primary state-gate makes accidental fires rare. | Marginal. Risk (HEAD switch) now also covered by BLOCKING maintree guard. Overlapping. | MED | SCOPE or DELETE. Anchor regex to command-position, or delete as redundant. |
| monitor_arm_overdue_advisor | Bash AND sentinel lock exists AND oldest >30s AND not rate-limited (60s). | LOW. Real sentinel-state gate; no sentinel = instant noop. | Yes. Enforces arm-the-Monitor via repeating reminder until ack. | LOW | KEEP. Sentinel-driven, not text-driven. |
| session_start_visibility | SessionStart event. Spawns worktree_doctor --cross-worktree-visibility. | None (event-scoped). | Yes. Emits actual worktree list (5 confirmed). Bounded 1500 chars. | LOW (1/session) | KEEP. |
| worktree_create_advisor | WorktreeCreate event. UNCONDITIONAL (fires on empty payload). | None (event-scoped); zero scenario check. | Marginal. Static checklist; writes sentinel yaml (useful side effect). | LOW | KEEP side-effect / SCOPE text. |
| worktree_remove_advisor | WorktreeRemove event. UNCONDITIONAL header; then real dirty/ahead check. | None (event-scoped). | Yes. Dirty + commits-ahead check prevents data loss. | LOW | KEEP. |

### "?" hooks classified
| Hook | Event | Trigger | False-fire | Verdict |
|---|---|---|---|---|
| post_merge_cleanup | PostToolUse:Bash | command matches the pr-merge verb (bare substring) AND exit_code==0 | MED. FIRES on an echo of the pr-merge phrase w/ exit 0 (confirmed live this session). | SCOPE. Anchor the pr-merge verb to command position. |
| pr_open_monitor_arm | PostToolUse:Bash | leading-token pr create/ready (round-4 anchored). | LOW. Properly anchored; bare PR-URL no longer triggers. | KEEP. Already hardened. |
| pr_monitor_arm_ack | PostToolUse:Monitor | Monitor cmd contains pr_monitor.py N / pr view|checks N. | None (Monitor-scoped, cleanup only). | KEEP. State-machine ack. |
| phase_close_commit_required | SubagentStop:* | agent_type contains phase_ AND tracked uncommitted changes. | None. Real state check. | KEEP. |

### Other advisories (completeness)
| Hook | Trigger | False-fire | Verdict |
|---|---|---|---|
| invariant_test | Bash matching git-then-commit (substring) | HIGH. FIRES on a log --grep mentioning the commit word; on comments mentioning git...commit. Fired 3x this audit on non-commit cmds. | SCOPE. Anchor to a real commit invocation; trim essay. |
| secrets_scan | Bash matching git-then-commit (identical bug) | HIGH (same; both fired on the log --grep). | SCOPE (same anchor) or MERGE into invariant_test. |
| pre_merge_contamination | Bash merge/pull/cherry-pick/rebase/am AND branch in {main,master,live-launch-*} | LOW. Branch-state gate; noops on current live/iteration-* branch. | KEEP. State-gated. |
| pre_checkout_uncommitted_overlap | Bash checkout/switch of a branch (command-delimiter-anchored) AND tracked mods | LOW. Already uses the CORRECT anchor — the template to copy. | KEEP. |
| pre_edit_architecture | Edit/Write to architecture/** without valid ARCH_PLAN_EVIDENCE | None. Path-prefix on resolved rel path. | KEEP. |
| pre_write_capability_gate | Edit/Write -> src.architecture.gate_edit_time.evaluate | None (real evaluator; fail-open). | KEEP. |

## BLOCKING HOOKS — false-fire scrutiny
10 PreToolUse:Bash handlers registered; 2 BLOCKING + deny (cotenant_staging_guard, maintree_git_state_guard); pr_create_loc_accumulation + pre_merge_comment_check also return the block sentinel.

| BLOCKING hook | Trigger style | False-fire? | Severity |
|---|---|---|---|
| maintree_git_state_guard | re.search bare-git + (checkout/switch/branch/reset) — matches ANYWHERE | YES — CRITICAL, 3/3 + 5x live. BLOCK on an echo of the checkout phrase, on a commit whose MESSAGE mentions checkout, on a grep of the reset phrase. | CRITICAL |
| cotenant_staging_guard | re.search bare-git + add then broad-form — bare-git substring | YES. BLOCK on an echo of the broad add form. Noops on grep + explicit pathspec. Same root bug, narrower blast. | HIGH |
| pr_create_loc_accumulation | leading-anchored pr create/ready | No. Noops on echo/commit-msg mentions. | OK |
| pre_merge_comment_check | whitespace-delimited pr-merge + number, NOT leading-anchored | PARTIAL. Noops on echo-quoted + grep-pipe, blocks on chained-after-&& (correct) and any space-preceded occurrence; ~4 LIVE gh api calls per match. | LOW-MED |

Worst trigger: maintree_git_state_guard — BLOCKING bare-git substring denies a commit whose MESSAGE mentions checkout, an echo, a grep. The maintenance_worker disease promoted to a deny path.

## Net recommendation
Of the 9 priority advisories:
- DELETE (pure noise): 1 — maintenance_worker_dry_run_floor.
- SCOPE (salvageable): 3 — pr_thread_reply_waste (trim), pre_branch_create_in_primary (anchor or delete-redundant), post_merge_cleanup (anchor).
- KEEP (useful, precise): 5 — citation_grep_gate, monitor_arm_overdue_advisor, session_start_visibility, worktree_remove_advisor, worktree_create_advisor.

Beyond the 9: invariant_test + secrets_scan share the SAME git-then-commit substring bug — SCOPE both, consider MERGING.

### BLOCKING hook with a bad trigger
maintree_git_state_guard (and secondarily cotenant_staging_guard): bare-git substring matching causes a BLOCKING deny on echo/grep/commit-message text mentioning checkout/reset/broad-add. Fix: require the matched git subcommand at a real command position using the command-delimiter anchor already proven in pre_checkout_uncommitted_overlap. Correctness defect, not style — it blocks legitimate work and forces a bypass-env dance.

### Single highest-value change
Replace bare-git / bare-substring matching with command-position anchoring across maintree_git_state_guard, cotenant_staging_guard, invariant_test, secrets_scan, and post_merge_cleanup. One anchor pattern (from the already-correct pre_checkout_uncommitted_overlap) fixes the entire false-fire class — including the two BLOCKING hooks that currently deny legitimate commands. K-structural fix: 5+ surface false-fires are one missing-anchor decision.

## Appendix — probe method
Firing verdicts came from importing dispatch.py in-process, calling each handler against constructed payloads, observing return (None=noop, str=advisory, block-sentinel/stderr BLOCKED=block). Trigger strings were assembled at runtime to avoid the audit own probes tripping the hooks under test — which they did anyway whenever a trigger substring reached the outer Bash command line, providing the live reproductions above.
