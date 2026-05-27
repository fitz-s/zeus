---
applyTo: "AGENTS.md,**/AGENTS.md,.agents/**,.claude/**,.github/copilot-instructions.md,.github/instructions/**,.github/pull_request_template.md,architecture/**,docs/authority/**,docs/operations/current_*.md,docs/reference/**,docs/review/**,REVIEW.md,workspace_map.md,docs/archive_registry.md"
---

# Docs / agent / instruction review

This file applies to authority surfaces — instruction files, scoped
routers, manifests, reference docs, review doctrine. Treat docs review
as **authority review**, not prose review.

## What to check

1. **Authority direction.** Change introduces contradiction with another
   authority? Code outranks docs; canonical DB outranks derived JSON;
   chain outranks portfolio cache; scoped `src/**/AGENTS.md` outranks
   root for module-specific rules.
2. **Reader contract.** Each doc has a named reader (Codex, Copilot,
   Claude, human). `.github/copilot-instructions.md` >4000 bytes
   breaks the Copilot contract — check `wc -c`.
3. **Scope creep.** Review doc absorbing routing rules, skill doc
   becoming universal ritual, manifest absorbing prose. Important.
4. **Stale citations.** Pointers to other files still current? Stale
   citation that confidently misroutes is worse than "I don't know."
   Important.
5. **Sunset / staleness.** Doc dated + aware when it can retire?
6. **Contradiction with invariants.** A docs change that flips an
   invariant statement is a runtime change in disguise. Critical.
7. **Mesh maintenance.** New dir under `src/**` / `tests/**` /
   `docs/**` / `architecture/**` has scoped `AGENTS.md`? Unregistered
   files are invisible to future agents.

## What to ignore

Prose polish. Word-choice debates. Generic "this could be clearer"
without a specific actionable change. These are Nits at best, often
noise.

## On instruction-file size budgets

- `.github/copilot-instructions.md` ≤ 4000 bytes (verify by `wc -c`).
- `REVIEW.md` self-contained: a reviewer should be able to start
  reviewing after reading only this file.
- `.github/instructions/*.instructions.md` each ≤ 4000 bytes (match
  the primary Copilot-instructions cap).
- Severity model and Tier definitions must be identical across
  REVIEW.md, `docs/review/code_review.md`,
  `.github/copilot-instructions.md`, and the instruction files.
  Drift is itself an Important finding (Tier 3 surface).

## On agent-config changes

`.agents/**`, `.claude/skills/**`, `.claude/agents/**`,
`.claude/hooks/**`, `.claude/settings.json` affect agent behavior.
Check for: inline prose warnings as sole defense (structural
enforcement via `applyTo` / mandatory triggers / scoped routers is what
catches drift); new mandatory rituals (default-on hooks, always-active
skills, unconditional gates) that tax unrelated changes.

Deeper: `REVIEW.md`, `docs/review/code_review.md`.

## Advisory vs structural topology

Do NOT treat every docs/operations movement as runtime failure.
Block / Important only when:
- active registry/index/reader points to a missing path;
- duplicate active authority appears;
- workflow / script / test path reference breaks;
- instruction file exceeds Copilot budget or lacks `applyTo`.

Archive movement, sidecar `.gitignore` policy, and task-folder
arrangement are advisory unless they break an active reader/enforcer.
