# Zeus AI Workflow Bridge

Authority: `AGENTS.md`, `workspace_map.md`, `docs/authority/**`, and
`architecture/**`.
Applies to: adapting the external AI handoff starter kit to Zeus.

---

## Decision

Use the starter kit as an outer handoff workflow, not as a project template.
Zeus already has durable guidance, topology manifests, scoped `AGENTS.md`
files, packet folders, and authority docs. The safe integration is therefore:

- keep Zeus root `AGENTS.md` as the governing operating contract
- add repo-local handoff guidance under `.agents/skills/`
- use Zeus packet folders for concrete handoff documents
- keep generic starter prompts as method input, not active authority
- avoid copying starter `src/`, `tests/`, and generic root docs into Zeus

## What Is Configured

- `.agents/skills/zeus-ai-handoff/SKILL.md` defines the Zeus-specific workflow
  for requirement tribunal, architecture lock, handoff packaging, and patch
  loop handoff.
- This runbook maps the downloaded starter kit and the chat guidance onto the
  Zeus mesh.
- `docs/runbooks/AGENTS.md` registers this runbook.
- `workspace_map.md` routes future agents to repo-local skills.

## What Is Not Configured

- No generic starter `AGENTS.md` is copied over Zeus root `AGENTS.md`.
- No starter `src/` or `tests/` placeholders are copied.
- No top-level `scripts/` helper is added. Zeus scripts are authority-tracked
  in `architecture/script_manifest.yaml`; promote a packager there only if the
  workflow becomes a repeated operator command.
- No ChatGPT, GitHub, Claude Code, or gstack credentials are configured in this
  repo. Those remain user/account-level setup.

## Where Concrete Work Goes

For a real Zeus change, create a packet folder:

```text
docs/operations/task_YYYY-MM-DD_<slug>/
```

Use lower-snake-case files such as:

```text
project_brief.md
prd.md
architecture_note.md
implementation_plan.md
task_packet.md
verification_plan.md
decisions.md
not_now.md
work_log.md
```

Then update `docs/operations/AGENTS.md` and, when the packet becomes current,
`docs/operations/current_state.md`.

## Workflow

1. Reality check:
   Read root `AGENTS.md`, `workspace_map.md`, this runbook, and the relevant
   scoped `AGENTS.md` files. Run topology navigation for the intended files.

2. Requirement tribunal:
   Use `.agents/skills/zeus-ai-handoff/SKILL.md` or the starter kit's
   requirements prompt to separate facts, decisions, open questions, and risks.
   Do not write code in this phase.

3. Architecture and verification lock:
   Produce only the planning docs needed for the packet. State truth surfaces,
   invariants, not-now items, verification commands, and rollback notes.

4. Implementation:
   Make small, packet-scoped code changes. Preserve unrelated dirty work. For
   cleanup/refactor work, lock behavior with tests before cleanup edits.

5. Handoff bundle:
   If another coding surface needs a stable context bundle, package the packet
   docs plus root guidance. The zip is guidance context only; the real coding
   surface remains this repo.

## Suggested Bundle Contents

Include:

- `AGENTS.md`
- `workspace_map.md`
- `.agents/skills/zeus-ai-handoff/SKILL.md`
- `docs/operations/current_state.md`
- the active packet folder under `docs/operations/`
- any scoped `AGENTS.md` files for directories the packet will touch
- targeted authority/reference docs named by topology navigation

Do not include:

- `state/**`, `raw/**`, local DBs, logs, or generated caches
- unrelated old packets or archives
- generic starter templates that conflict with the active packet docs
- source snapshots unless the downstream surface explicitly cannot access the
  repo

## External Surface Setup

Configure these outside the repo when needed:

- ChatGPT GitHub connector: authorize GitHub access in ChatGPT settings and
  select the repository.
- Codex app/local/worktree: use this repo as the coding surface for edits,
  command execution, commits, pushes, and PRs when supported.
- Claude Code/gstack: use `gstack /office-hours` only as a discovery helper.
  If its plan file becomes stale, use the starter kit sync script from the
  downloaded package, then copy durable conclusions back into Zeus packet docs.

## Verification

For handoff-only configuration changes, verify:

```bash
python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files .agents/skills/zeus-ai-handoff/SKILL.md docs/runbooks/task_2026-04-19_ai_workflow_bridge.md docs/runbooks/AGENTS.md workspace_map.md
```

If this reports pre-existing unrelated registry issues, record them separately
and do not widen the packet.
