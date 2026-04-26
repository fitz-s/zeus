# Cloud session operating notes

This file complements the root `AGENTS.md` (read that first). It only adds
operational rules a cloud session needs that `AGENTS.md` does not cover.

## Why this file exists

A cloud session is autonomous: no human-in-the-loop iteration mid-flight.
The session decides its own workload boundary. A premature closeout
produces thin output — see PR #18 for a refresh task that lost specificity
by closing too early. Use the checklists below so the session ends only
when the work actually meets the bar.

## Before you start

```
git worktree list                              # who else is mid-task
gh pr list --state open --limit 30             # in-flight PRs
git log --since='24h' --all --oneline | head   # recent cross-branch activity
ls docs/operations/task_*/                     # active packets on disk
```

If your task overlaps with an in-flight worktree or open PR, do NOT edit
the contested files. Instead write your findings into
`docs/operations/task_<UTC-date>_<your-slug>/propose_*.md` and reference
the conflicting worktree/PR by HEAD sha + path.

## Done-ness checklist (run before opening the PR)

- [ ] Every cited `file:line` re-verified by grep within this session
      (memory rule L20: plan citations rot ~20-30% per 10 min in this repo)
- [ ] N findings collapsed into K structural decisions (K << N) — not N
      symptom patches (root `AGENTS.md` Fitz Constraint #1)
- [ ] Relationship tests (cross-module invariants) committed RED first,
      then implementation flips them green (Fitz §1: order is not reversible)
- [ ] New files carry `# Lifecycle:` / `# Purpose:` / `# Reuse:` headers
      where the lifecycle protocol applies
- [ ] `python3 scripts/topology_doctor.py --planning-lock --changed-files <files>`
      returns GREEN
- [ ] If you created a new packet folder: `plan.md` + `scope.yaml` +
      `receipt.json` + `work_log.md` all present
      (see `docs/operations/packet_scope_protocol.md`)
- [ ] No silent edit of `docs/operations/current_state.md` Active fields,
      `docs/operations/AGENTS.md`, or `architecture/AGENTS.md`. These are
      control-plane: ADDITIVE only, via new sections that do not change
      existing pointers. Use `propose_pointer_change.md` if you need to
      change Active/Receipt/Required-evidence fields
- [ ] If your task name contains "refresh" / "update" / "rewrite":
      you have NOT removed specific filenames, test names, or AST rule
      pointers from the original. Preserve specificity. If the original
      lost it, write `propose_*.md` — do not silently approve dilution

If any item fails the session is not done. Iterate before submitting.

## Reviewing another agent's PR

When asked to review a PR opened by another cloud agent or pulled from raw
GitHub:

- Verify every claim against current HEAD via grep — do not trust the PR
  body's file:line references
- Check `git worktree list` + `gh pr list --state open` for parallel local
  worktrees that may already address the same scope; flag overlap
- Reject (or request rebase on) a PR that net-deletes specificity
  (filenames, test names, AST rule pointers) from existing plans without
  a corresponding new evidence file justifying the removal

## Where AGENTS.md takes over

Everything else — money path, INV/NC discipline, mesh maintenance,
planning-lock rules, current-fact surface protocol, code review graph
two-stage protocol — lives in the root `AGENTS.md`. Do not duplicate that
content here.
