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

## Authority within review doctrine

Two distinct authorities, scoped to avoid circular precedence:

- `code_review.md` is authoritative for **doctrine** — severity ladder,
  large-PR rule, evidence rule, reporting template, reviewer behavior,
  uncertainty handling.
- `review_scope_map.md` is authoritative for **path → tier mapping** only.

Compressed mirrors (root `REVIEW.md`, `.github/copilot-instructions.md`,
`.github/instructions/*.instructions.md`) reconcile to whichever of the
two canonical files governs the disagreement. Mirror-vs-canonical drift
is itself an **Important** finding on the next review (the Tier of the
file in question is Tier 3, but the severity is the dimension that
matters).

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
