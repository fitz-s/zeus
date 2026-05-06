# docs/review AGENTS

Review doctrine surface. Three files:

- `code_review.md` — canonical long-form review doctrine for humans, Codex,
  manual Claude Code review sessions, and future maintainers. Wins on
  contradiction with the compressed mirrors.
- `review_scope_map.md` — path → tier table. Wins for path classification.

The compressed reviewer-specific mirrors live outside this directory:

- root `REVIEW.md` — Claude Code Review / ultrareview entry point
- `.github/copilot-instructions.md` — GitHub Copilot Code Review
- `.github/instructions/*.instructions.md` — Copilot path-scoped surface
- `.github/pull_request_template.md` — author-facing AI Review Scope form

## Authority order within review doctrine

`code_review.md` (canonical) > `review_scope_map.md` (path table) >
compressed mirrors. Drift between any two surfaces is a Tier 3 finding
on the next review.

## When to update

- A new invariant ID class is added to `architecture/invariants.yaml`.
- A new top-level package is added under `src/`.
- The severity model needs to change (operator approval required).
- A new AI reviewer is added (e.g., new vendor) and needs its own adapter.

## When NOT to expand

- Per-task review notes belong in active packets, not here.
- Generic style/lint guidance belongs in `.importlinter` / linter configs,
  not here.
- Per-PR exceptions belong in the PR body's AI Review Scope, not here.

This is doctrine, not running notes. Keep it tight.
