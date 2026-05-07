# Hook redesign v2 — minimal correction

**Created:** 2026-05-07
**Authority basis:** session 5278ceeb post-mortem (`docs/archives/sessions/session_post_mortem_2026-05-07.md`); critic NO-GO findings (`evidence/hook_redesign_v2_critic.md`); operator framing reset 2026-05-07.
**Supersedes:** the K=3 PLAN previously at this path (built on the wrong premise that we needed our own authorization model).

## Framing reset

The session-2026-05-07 hook crash + critic findings exposed that the entire `STRUCTURED_OVERRIDE` env-var + `evidence/operator_signed/<file>.signed` infrastructure is duplicating Claude Code's built-in permission system. In default mode, an Edit on `.claude/hooks/**` already triggers a user permission prompt — that *is* operator authorization. In bypass mode, the user has explicitly opted out of prompts; layering env-var bypass tokens on top fights the user's own mode choice. On a single-user mac there is no real "operator-only path" anyway (critic verified `chflags uchg` + `~/.gitconfig` are both writable by the agent's uid).

Therefore: drop the bespoke authorization model. Hooks become advisory-only. BLOCKING tier retires. Authorization = Claude Code's permission prompt (default) or user's bypass choice (bypass).

## Requirements (K=1)

**K1 — schema/handler boot integrity.** dispatch.py at boot validates every hook listed in `registry.yaml` has the matching `_run_advisory_check_<id>` symbol. Missing symbol → stderr warning + advisory fall-open for that hook id. Never fail-closed at first invocation. (This is the *only* structural decision being kept from the prior K=3 round; it directly closes the session-2026-05-07 full-Bash crash.)

Everything else collapses to small corrections, not new infrastructure.

## Correction list (one PR, removal-heavy)

1. **`registry.yaml`** — set every hook's `severity: ADVISORY`. Remove `bypass_policy` blocks. Delete `pre_edit_hooks_protected` entry entirely. Update `pr_create_loc_accumulation` intent text back to advisory-tone.
2. **`overrides.yaml`** — delete file. No BLOCKING hooks remain → no overrides needed.
3. **`.claude/settings.json`** — remove the `pre_edit_hooks_protected` entry from the `Edit|Write|MultiEdit|NotebookEdit` matcher block. Keep all other hooks (they are ADVISORY).
4. **`dispatch.py`** —
   - Remove `_run_blocking_check` and all `_run_blocking_check_<id>` functions.
   - Remove `validate_override`, `_detect_override`, `_log_override_use`, `_NEVER_EXPIRY_WHITELIST`, `_SEEN_PAIRS`, `_get_evidence_file_commit_time`.
   - Remove `_OPERATOR_SIGNED_CLASSES`, `_PROTECTED_HOOK_PATHS`.
   - Simplify `main()` to: load registry → find spec → call `_run_advisory_check` → emit additionalContext or nothing → exit 0.
   - Add boot self-test: at module load, iterate registry hooks; for each, assert `_run_advisory_check_<id>` exists; on miss, log to stderr and skip (do NOT raise).
   - Tighten `_run_advisory_check_pr_create_loc_accumulation` regex from substring `gh\s+pr\s+(create|ready)` to command-head anchor `^\s*(?:env\s+\S+\s+)*gh\s+pr\s+(create|ready)\b` (no more self-DoS on echo/heredoc).
5. **`evidence/operator_signed/`** — delete `HOOK_SCHEMA_CHANGE_2026-05-07.signed`. Remove the directory if empty.
6. **`evidence/pr_overrides/`** — directory unused; do not create.
7. **`evidence/baseline_ratchets/`, `cotenant_shims/`, `secrets_overrides/`, `destructive_checkouts/`, `operator_overrides/`, `hook_schema_changes/`, `main_regressions/`** — leave existing files (historical evidence) but no longer load-bearing for any hook decision.

## What stays

- All ADVISORY hooks remain functional: `pr_create_loc_accumulation` (with command-head regex), `pr_open_monitor_arm`, `phase_close_commit_required`, `post_merge_cleanup`, `session_start_visibility`, `worktree_create_advisor`, `worktree_remove_advisor`.
- Telemetry (`hook_signal/<YYYY-MM>.jsonl`) unchanged.
- Boot self-test (K1) is the only addition.

## What this does NOT solve, on purpose

- Bypass-mode agent indiscipline. If the user runs `--dangerously-skip-permissions` and the agent ignores an advisory, that is an agent-discipline / advisory-wording issue, not a hook-architecture issue. Treat as skill/feedback-memory work, not infra.
- Real authorization (GPG / Touch ID / external trust anchor). If a future task genuinely needs hard authorization, scope it as its own packet with explicit operator decision on the trust anchor — do not graft it onto the hook layer.

## Acceptance

- `python3 -c "import sys; sys.path.insert(0,'.claude/hooks'); import dispatch"` runs without error.
- Boot self-test prints "[hook integrity] OK" or named warnings on stderr; never raises.
- Every Bash command runs through 6 advisory hooks (PreToolUse:Bash) without any deny path.
- An Edit to `.claude/hooks/dispatch.py` in default Claude Code mode produces a permission prompt to the user (existing Claude Code behavior); in bypass mode it goes through. No env-var dance either way.
- `git grep -n 'STRUCTURED_OVERRIDE'` returns zero hits in `.claude/`.

## Tasks closed by this PR

- #55 (3 hook architectural defects) — all 3 dissolve: registry/handler drift now caught at boot; env-var coupling removed; Bash bypass irrelevant once nothing is gated.
- #56 (`fields_required` / `operator_signature` enforcement) — moot; no overrides remain.
