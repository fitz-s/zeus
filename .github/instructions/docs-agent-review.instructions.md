---
applyTo: "AGENTS.md,**/AGENTS.md,.agents/**,.claude/**,.github/copilot-instructions.md,.github/instructions/**,.github/pull_request_template.md,architecture/**,docs/authority/**,docs/operations/current_*.md,docs/reference/**,docs/review/**,REVIEW.md,workspace_map.md,docs/archive_registry.md"
---

# Docs / agent / instruction review

This file applies to authority surfaces — instruction files, scoped
routers, manifests, reference docs, review doctrine. Treat docs review
as **authority review**, not prose review.

## What to check

1. **Authority direction.** Does the change introduce contradiction
   with another authority surface? Code outranks docs; canonical DB
   outranks derived JSON; chain outranks portfolio cache; scoped
   `src/**/AGENTS.md` outranks root for module-specific rules.
2. **Reader contract.** Each doc has a named reader (Codex, Copilot,
   Claude session, human). Does the change preserve or break that
   contract? Bloating `.github/copilot-instructions.md` past 4000
   bytes breaks the Copilot contract — check `wc -c` (bytes).
3. **Scope creep.** Does the doc grow beyond its job? A "review" doc
   absorbing routing rules, a "skill" doc becoming a universal ritual,
   a manifest absorbing prose are all scope creep. Important.
4. **Stale citations.** If the doc points readers to other files
   (`scripts/topology_doctor.py`, `architecture/invariants.yaml`,
   scoped `AGENTS.md`), are those still current? Stale citation is
   Important; a doc that confidently misroutes is more dangerous than
   one that says "I don't know."
5. **Sunset / staleness self-awareness.** Is the doc dated and aware
   of when it can be retired?
6. **Contradiction with invariants.** A docs change that flips an
   invariant statement is not a docs change — it is a runtime change
   in disguise. Critical.
7. **Mesh maintenance.** If a new directory was added under
   `src/**` / `tests/**` / `docs/**` / `architecture/**`, does it have
   a scoped `AGENTS.md`? Per root `AGENTS.md` §4 "Mesh maintenance,"
   unregistered files are invisible to future agents.

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
Check for:
- Inline anti-pattern warnings inside the file. Inline prose warnings
  alone are insufficient to prevent ritual drift; structural enforcement
  (path-scoped `applyTo`, mandatory triggers, scoped routers) is what
  actually catches it. Do not weaken the prose, but do not rely on it.
- New mandatory rituals (default-on hooks, always-active skills,
  unconditional gates) that expand the proof tax for unrelated changes.

Deeper context: `REVIEW.md`, `docs/review/code_review.md`.
