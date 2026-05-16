# Zeus Hooks Ecosystem Audit (2026-05-16)

## 1. Registry-to-Implementation Table
| Hook ID | Registry'd | Handler | Settings | Tier | Codex | Last Audited | Authority Basis |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| invariant_test | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | hook_redesign_v2 |
| secrets_scan | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | hook_redesign_v2 |
| cotenant_staging_guard | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | hook_redesign_v2 |
| pre_checkout_uncommitted_overlap | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | hook_redesign_v2 |
| pr_create_loc_accumulation | Yes | Yes | Yes | BLOCKING* | Yes | 2026-05-16 | pr_discipline_2026_05_09 |
| pre_merge_comment_check | Yes | Yes | Yes | BLOCKING* | No | 2026-05-16 | pr_discipline_2026_05_09 |
| pr_thread_reply_waste | Yes | Yes | No | ADVISORY | No | 2026-05-16 | pr_discipline_2026_05_09 |
| pr_open_monitor_arm | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | pr_discipline_2026_05_09 |
| phase_close_commit_required | Yes | Yes | Yes | ADVISORY | No | 2026-05-16 | hook_redesign_v2 |
| pre_merge_contamination | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | hook_redesign_v2 |
| post_merge_cleanup | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | hook_redesign_v2 |
| pre_edit_architecture | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | hook_redesign_v2 |
| pre_write_capability_gate | Yes | Yes | Yes | ADVISORY | Yes | 2026-05-16 | hook_redesign_v2 |
| session_start_visibility | Yes | Yes | No | ADVISORY | No | 2026-05-16 | worktree_doctor |
| worktree_create_advisor | Yes | Yes | No | ADVISORY | No | 2026-05-16 | worktree_doctor |
| worktree_remove_advisor | Yes | Yes | No | ADVISORY | No | 2026-05-16 | worktree_doctor |

\* *Tier Note: pr_create_loc_accumulation and pre_merge_comment_check are marked BLOCKING in registry.yaml despite v2 "all ADVISORY" protocol, reflecting their status as hard backstops for PR discipline.*

## 2. Gap List
- **HANDLER_BUT_NOT_REGISTERED**: None. All 16 functions in `_ADVISORY_HANDLERS` are in `registry.yaml`.
- **DECLARED_BUT_NO_HANDLER**: None. `dispatch.py` boot self-test reports `OK: all 16 registry hooks have handlers`.
- **UNWIRED_IN_SETTINGS**:
  - `pr_thread_reply_waste`: Registered and implemented, but missing from `.claude/settings.json`.
  - `session_start_visibility`: Registered and implemented, but `SessionStart` event is not wired in `.claude/settings.json`.
  - `worktree_create_advisor`: Registered and implemented, but `WorktreeCreate` event is not wired.
  - `worktree_remove_advisor`: Registered and implemented, but `WorktreeRemove` event is not wired.
- **CODEX_DRIFT**:
  - `pre_merge_comment_check`: Missing from `.codex/hooks.json`. (Intentional? Codex often skips merge gates if the operator handles the final merge).
  - `pr_thread_reply_waste`: Missing from `.codex/hooks.json`.
  - `phase_close_commit_required`: Missing from `.codex/hooks.json`.
  - `session_start_visibility`: Missing from `.codex/hooks.json`.
  - `worktree_create_advisor`: Missing from `.codex/hooks.json`.
  - `worktree_remove_advisor`: Missing from `.codex/hooks.json`.
- **STALE_HEADER**: None. Registry and Dispatch have `Last audited: 2026-05-16`. Legacy hooks in `legacy/` are stale (>30d) but are correctly partitioned.

## 3. Top 5 Highest-Impact Gaps
1. **Unwired `pr_thread_reply_waste`**: This key Principle 2 backstop is implemented but never fires because it's missing from `settings.json`.
2. **Unwired Worktree/Session Advisors**: `session_start_visibility` and `worktree_create_advisor` provide critical cross-worktree context but are currently silent.
3. **Registry/Protocol Tier Drift**: `pr_create_loc_accumulation` and `pre_merge_comment_check` use `severity: BLOCKING` while `dispatch.py` header claims "all hooks ADVISORY-only".
4. **Codex mirror gap (`pr_thread_reply_waste`)**: Codex agents are prone to thread-reply waste; mirroring this is high-value for PR discipline.
5. **LOC Threshold Label Drift**: `settings.json` description for `pr_create_loc_accumulation` says "LOC < 80", but `registry.yaml` and `dispatch.py` implementation use "LOC < 300".

## 4. Suggested Additions
- **`maintenance_worker_dry_run_floor`**: Advisory hook for `maintenance_worker.cli` or tool calls that modify `.claude/settings.json` or registry files.
- **`pre_submit_token_burn_check`**: Advisory on extremely large UserPromptSubmit payloads (>50k tokens) to suggest `/compact` or file exclusion.

