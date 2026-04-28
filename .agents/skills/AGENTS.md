# .agents/skills AGENTS

Repo-local workflow skills and handoff guidance. These are execution aids, not
Zeus architecture authority.

## File Registry

| Path | Purpose |
|------|---------|
| `zeus-ai-handoff/SKILL.md` | **v2 (2026-04-28)** — General-purpose 4-mode handoff playbook (Direct / Subagent / Longlast multi-batch with critic-gate / Adversarial debate). Includes proven discipline patterns (disk-first, bidirectional grep, co-tenant git hygiene, verdict erratum). Replaces v1 single-mode workflow. |

## Rules

- Skills must defer to root `AGENTS.md`, `workspace_map.md`, scoped
  `AGENTS.md` files, and machine manifests.
- Do not copy generic starter-kit authority into Zeus root files.
- Register any new repo-local skill here and route durable context through
  `docs/runbooks/` or `docs/operations/`.
