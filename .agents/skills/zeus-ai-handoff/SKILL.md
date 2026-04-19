---
name: zeus-ai-handoff
description: Use when adapting an AI handoff workflow to Zeus, converting a broad Zeus change into a packet-ready plan, or preparing a handoff bundle for downstream coding agents.
---

# Zeus AI Handoff

## Purpose

Convert a broad or cross-surface Zeus request into stable handoff truth without
overriding Zeus authority files or treating a zip bundle as code truth.

This skill adapts the external AI handoff starter kit to Zeus. It is an outer
workflow layer only. Zeus authority remains rooted in root `AGENTS.md`,
`workspace_map.md`, scoped `AGENTS.md` files, `architecture/**`, and
`docs/authority/**`.

## Required Reads

1. `AGENTS.md`
2. `workspace_map.md`
3. `docs/runbooks/task_2026-04-19_ai_workflow_bridge.md`
4. `docs/operations/current_state.md` when a concrete packet is involved
5. `python scripts/topology_doctor.py --navigation --task "<task>" --files <files>`

If navigation is blocked by pre-existing registry issues, record that as
workspace state and keep the handoff change narrow.

## Zeus Mapping

- Do not copy a generic starter-kit `AGENTS.md` over Zeus root `AGENTS.md`.
- Do not copy starter `src/` or `tests/` placeholder directories into Zeus.
- Do not put generic starter docs directly under `docs/`; use an active
  packet folder under `docs/operations/task_YYYY-MM-DD_slug/` for concrete
  work, or a runbook/reference route when the artifact is procedural context.
- Do not add top-level `scripts/` helpers without updating
  `architecture/script_manifest.yaml` and satisfying script freshness rules.
- Treat handoff zips as guidance bundles, not source snapshots or canonical
  truth.

## Requirement Tribunal

Use this phase when the user's request is broad, underspecified, or likely to
touch architecture, governance, source truth, lifecycle, DB authority, or more
than one zone.

Maintain four buckets:

- Facts
- Decisions
- Open Questions
- Risks

End this phase only when the next artifact can state the objective, non-goals,
invariants, likely touched surfaces, verification commands, rollback note, and
authority/truth boundaries.

## Handoff Document Set

For a concrete Zeus task, create or update files inside the active packet
folder, not in generic root template locations:

- `project_brief.md`
- `prd.md`
- `architecture_note.md`
- `implementation_plan.md`
- `task_packet.md`
- `verification_plan.md`
- `decisions.md`
- `not_now.md`
- `work_log.md`

Use only the subset that the task actually needs. These documents are planning
and execution context unless a deeper authority file explicitly promotes them.

## Execution Prompt Shape

When handing a task to a coding surface, include:

1. Current Zeus authority reads and topology command.
2. The single task objective.
3. Files and zones likely involved.
4. Invariants that must not move.
5. Not-now list.
6. Required verification and rollback note.
7. Instruction to preserve unrelated dirty work.

## Completion Gate

Before calling the handoff ready:

- Open questions are either resolved or explicitly blocking.
- Not-now items are explicit.
- Verification commands are concrete.
- Rollback or blast radius is stated.
- Any new file is registered in the scoped mesh when required.
- The handoff bundle contains only current, non-conflicting truth surfaces.
