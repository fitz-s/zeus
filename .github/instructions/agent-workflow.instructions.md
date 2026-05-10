---
applyTo: "**"
---

# Zeus Copilot Agent Workflow

Authority: `AGENTS.md` and `.claude/CLAUDE.md`.
Last audited: 2026-05-09.

Use this file for Copilot agent-mode execution. Review-only work still follows
`.github/copilot-instructions.md` and the path-scoped review instructions.

## Before Editing

1. Read `AGENTS.md` and `.claude/CLAUDE.md`; read scoped `AGENTS.md` files for
   target paths.
2. Check session/worktree state with the available bridge tool or CLI:
   `pwd`, `git rev-parse --show-toplevel`, `git branch --show-current`,
   `git worktree list`, and `git status --short`.
3. Run topology routing before edits:
   `python3 scripts/topology_doctor.py --navigation --task "<task>" --intent <intent> --write-intent edit --files <files>`.
4. Edit only files listed as admitted. Stop on forbidden files,
   `advisory_only`, `scope_expansion_required`, shared-worktree collision, or
   live/prod/data side effects without explicit operator approval.

## Gates

- For `architecture/**`, `docs/authority/**`, `src/state/**`, `src/control/**`,
  `src/supervisor_api/**`, cross-zone changes, governance/control/DB/lifecycle
  changes, or more than four files, run planning-lock before editing:
  `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <plan>`.
- After file adds/deletes/renames, run map maintenance before closeout:
  `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <files>`.
- Run the focused checks named by the route card.
- Verify non-trivial completion claims with the available verifier bridge or an
  independent Claude/OpenClaw verifier; do not self-certify risky work.

## Delegation

Copilot may directly edit narrow route-admitted docs/tests/tooling files and run
local checks. Delegate broad search, source/runtime implementation, independent
critic/verifier work, persistent Team work, and long-running hidden work through
OpenClaw/Claude. ACP `agentId` values are runtimes such as `claude`, `codex`, or
future `copilot`; `explore`, `executor`, `critic`, and `verifier` are OMC roles,
not ACP agent ids.

## Closeout

Report changed files, route/check evidence, unresolved risks, and the next
operator decision.

## Rule 0 Ask Gate

Before any response that would otherwise conclude the task or conversation,
call the available ask-user tool (`ask_user`, `#askQuestions`, or VS Code
`askQuestions`) with one concrete next question. A prose-only final question is
not enough.

Bridge seed: OpenClaw root `plugin-skills/vscode-copilot-sync/rule0-before-final.mjs`.

The question must ask for a real next decision, for example whether to continue
to the next phase, run a verifier, commit the scoped files, or stop. If no
ask-user tool is available, report `BLOCKED_RULE0_TOOL_UNAVAILABLE` and stop
before claiming completion.
