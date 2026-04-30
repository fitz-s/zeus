---
name: zeus-ai-handoff
description: General-purpose handoff workflow for Zeus — covers any AI-to-AI or session-to-session handoff. Selects the right execution mode (direct / subagent / runtime multi-batch / adversarial debate) per task scale + risk; preserves Zeus authority surfaces; encodes proven discipline (disk-first, scoped critic-gate, bidirectional grep, co-tenant git hygiene, verdict erratum). Use when adapting a Zeus change of any size, converting a request into a packet-ready plan, preparing a handoff bundle, or starting a multi-batch execution across the available agent runtime. Replaces v1 single-mode workflow with a 4-mode playbook validated by Tier 1 + 3-round debate cycle 2026-04-27.
---

# Zeus AI Handoff (v2)

## Purpose

Convert a Zeus task — at any scale, from quick fix to multi-week refactor — into structured handoff truth without overriding Zeus authority files or treating any single artifact (zip, doc, debate verdict) as canonical truth.

This is the **outer workflow layer** for Zeus. The inner authority remains:
- Root `AGENTS.md` + `workspace_map.md`
- Scoped `AGENTS.md` files per package
- `architecture/**` machine manifests
- `docs/authority/**` constitutional surfaces

This SKILL selects which **execution mode** fits the task, then applies mode-specific discipline.

---

## §1 Scope Reads (mode-scoped)

Start with the smallest read set that can route the task safely:

1. `AGENTS.md` (root)
2. `python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>`

Add reads only when the route, mode, or completion claim consumes them:

- `workspace_map.md` when visibility or default route is unclear.
- `docs/runbooks/task_2026-04-19_ai_workflow_bridge.md` when preparing a
  handoff bundle, not for direct implementation.
- `docs/operations/current_state.md` when the task touches an active packet,
  pipeline/governance state, or a packet-closeout claim.
- `docs/methodology/adversarial_debate_for_project_evaluation.md` for Mode D,
  or for Mode C only when a critic-gated multi-batch boundary is active.
- `.claude/skills/zeus-phase-discipline/SKILL.md` for Mode C / R3 phase work
  when that runtime exists and the task is actually multi-batch.

If navigation is blocked by pre-existing registry issues, record that as workspace state and keep the handoff change narrow.

---

## §2 Zeus Mapping (do-NOTs from v1, preserved)

- Do NOT copy a generic starter-kit `AGENTS.md` over Zeus root `AGENTS.md`
- Do NOT copy starter `src/` or `tests/` placeholder directories
- Do NOT put generic starter docs directly under `docs/`; use an active packet folder under `docs/operations/task_YYYY-MM-DD_slug/` or a runbook/reference route
- Do NOT add top-level `scripts/` helpers without updating `architecture/script_manifest.yaml`
- Treat handoff zips/bundles as guidance, not source snapshots or canonical truth

---

## §3 Mode selection (the new decision branch)

Before choosing artifacts or dispatching, pick ONE mode based on task profile:

| Task profile | Mode | Rationale |
|---|---|---|
| Single file edit, ≤30 min, reversible | **A. Direct** | No handoff overhead needed |
| 1-3 files, clear spec, ≤2h, low stakes | **B. Subagent** | One-shot Agent dispatch with explicit task |
| Multi-batch implementation with shared dependencies, K0/K1 zone touched, or durable runtime coordination needed | **C. Runtime multi-batch executor + critic-gate** | Tier 1 pattern; per-batch critic verdict before next |
| High-stakes architecture/strategy decision; multiple valid approaches; team disagreement | **D. Adversarial debate** | 3-round methodology in `docs/methodology/adversarial_debate_for_project_evaluation.md` |

**Default if uncertain**: A for reversible direct work; B when bounded
delegation materially helps; C only for real multi-batch/K0/K1 coordination,
not merely because the diff crosses an arbitrary file count.

**Anti-pattern**: using D (full debate) for what should be A or B is the most common mistake — wastes 70+ min on a 30-min decision. Use methodology §11 ROI signals to check.

### §3.0 Runtime surface selection

The mode is the contract; the tool surface is runtime-specific:

- In Codex App / plain Codex, use Codex native subagents only for bounded
  independent work that materially improves throughput or review quality.
- In OMX CLI/runtime sessions, longlast/team surfaces are valid for durable
  multi-batch coordination when the runtime state and messaging layer exist.
- Do not hardcode provider model names such as `opus` or `sonnet` in new
  dispatches. Inherit the active repo/runtime model by default and choose the
  role/reasoning effort for the work.
- Critic gates apply to Mode C/D batch boundaries and escalated merge conflicts.
  They do not apply to Mode A/B direct work, clean cross-worktree merges, or
  narrow mechanical conflict resolution.
- Artifacts are claim-scoped. Do not create `evidence.md`, `findings.md`,
  receipts, work logs, or review records just because prior packets had them.
  Create them only when the selected mode, active packet, closeout gate, or
  future handoff consumes that file.

### §3.1 Scope-lock subclause

When operator uses approval words ("continue", "proceed", "go", "推进",
"ok", etc.) AFTER an initial task description, that approval applies
ONLY to the previously declared task class. **Approval words do NOT
expand scope to adjacent task classes.**

Specifically:
- Previously declared task: "fix X in module Y" → "continue" means
  proceed with X-in-Y, NOT "fix anything else you find in module Y"
- Previously declared task: "run cycle 1 phases A-D" → "continue" means
  next phase in the SAME cycle, NOT "start cycle 2"
- Previously declared task: "audit drift item #1" → "continue" means
  finish #1, NOT "audit all 6 drift items"
- Previously declared task: "TIGGE remainder cleanup" → "continue" does
  NOT authorize "全量 suite 扫尾" (the 2026-04-28 contamination root)

If scope expansion appears warranted mid-task, **stop and request explicit
operator re-authorization** with the new scope explicitly named. Format:
"Discovered ADJACENT_TASK while doing DECLARED_TASK; requires explicit
authorization to proceed."

This subclause exists because of the 2026-04-28 contamination event:
"continue" was interpreted as scope-expansion authorization across
multiple cycles, accumulating 9 commits of contamination before
discovery. See `docs/operations/task_2026-04-28_contamination_remediation/`.

When this rule activates against future "continue" prompts, cite this
§3.1 explicitly in the request for re-authorization.

---

## §4 Requirement Tribunal (applies to all modes ≥ B)

When the request is broad, underspecified, or likely to touch architecture / governance / source truth / lifecycle / DB authority / cross-zone:

Maintain four buckets:
- **Facts** (what is true on HEAD)
- **Decisions** (what we choose given the facts)
- **Open Questions** (unresolved; need operator or empirical resolution)
- **Risks** (what could go wrong + mitigation)

End this phase only when the next artifact can state: objective, non-goals, invariants, likely-touched surfaces, verification commands, rollback note, authority/truth boundaries.

---

## §5 Handoff Document Set (per mode)

**Mode A (Direct)**: no handoff docs; just edit + commit + verify.

**Mode B (Subagent)**: `task_packet.md` in active packet folder; include the 7-item Execution Prompt Shape (§7). Do not add a separate evidence/findings stack unless the task is an audit/review or the packet explicitly needs durable evidence.

**Mode C (Longlast multi-batch)**: candidate set, not a mandatory bundle. Start
with objective, non-goals, batch plan, verification/rollback, and ownership.
Add the following files only when the batch runtime or future handoff consumes
them:
- `project_brief.md` — context + goal
- `prd.md` — requirements
- `architecture_note.md` — design choices
- `implementation_plan.md` — phased breakdown (which batches, dependencies)
- `task_packet.md` — operator-facing summary
- `verification_plan.md` — per-phase + final acceptance criteria
- `decisions.md` — rationale ledger
- `not_now.md` — explicit out-of-scope
- `work_log.md` — chronological execution record

**Mode D (Adversarial debate)**: per `docs/methodology/adversarial_debate_for_project_evaluation.md` §2: TOPIC.md + judge_ledger.md + per-round evidence + verdict.md.

Use only the subset the task actually needs.
`findings.md` is an audit/review artifact name, not a default implementation
artifact. `evidence.md` is for durable evidence that a gate or future handoff
will actually read, not for duplicating every command result.

---

## §6 Execution mode mechanics

### §6.0 Operation-end feedback capsule

When a whole operation is complete, recycle context before the final handoff.
This is a short feedback capsule, not a new artifact stack:

1. Context recovery: state what scratch/runtime context was promoted,
   summarized, discarded, or left local.
2. Zeus improvement insight: record one to three actionable observations from
   the work, such as a code simplification, test gap, doc/routing mismatch, or
   small next repair. Mark evidence versus inference when it matters.
3. Topology experience: name what topology helped and what topology blocked,
   misrouted, or slowed.

For Mode A, include the capsule in the final response. For packet closeout,
append it to an already-selected work log or receipt. Do not create
`evidence.md`, `findings.md`, a packet folder, or follow-on implementation just
to record feedback.

### §6.A Direct
1. Edit / commit / verify
2. `git add` specific files (NEVER `-A` with co-tenant active per memory `feedback_no_git_add_all_with_cotenant`)
3. Commit message via HEREDOC; verify with `git log -1`

### §6.B Subagent
1. Use the available bounded delegation surface (Codex native subagent in
   Codex App / plain Codex; runtime Agent/teammate only when that runtime is
   active). Inherit the active model by default.
2. Provide §7 Execution Prompt Shape verbatim
3. Receive output; verify; commit per §6.A

### §6.C Runtime multi-batch executor + critic-gate (Tier 1 pattern)
1. Choose the available runtime surface:
   - Codex App / plain Codex: bounded native subagents for executor and critic lanes.
   - OMX runtime: longlast/team executor and critic lanes.
2. Spawn `executor-<topic>` with the role suited to implementation.
3. Spawn `critic-<topic>` independently; inherit the active repo/runtime model
   unless the active model contract says otherwise.
4. Per batch:
   - Executor: write changes; signal `BATCH_X_DONE` through the runtime status
     channel or final output
   - Critic: independent review (10-attack template); signal
     `BATCH_X_REVIEW APPROVE/REVISE/BLOCK`
   - Team-lead: dispatch next batch only after critic APPROVE
5. Honor methodology §5 critic-gate workflow

### §6.D Adversarial debate (if Mode D selected)
Follow `docs/methodology/adversarial_debate_for_project_evaluation.md` end-to-end (§2 setup → §3 mechanics → §8 verdict structure). Mode D is reserved for high-stakes multi-valid-approach decisions; methodology §0 has the ROI signals.

---

## §7 Execution Prompt Shape (for Mode B / batch dispatch in Mode C)

When handing a task to a coding surface:

1. Current Zeus authority reads + topology command
2. Single task objective (one sentence)
3. Files and zones likely involved
4. Invariants that must NOT move
5. Not-now list
6. Required verification + rollback note
7. Instruction to preserve unrelated dirty work (co-tenant safe staging)

**Mode C addition**: also specify (a) which batch this is in the multi-batch plan, (b) the runtime status format expected, (c) the critic that will review.

---

## §8 Discipline patterns (apply across all modes ≥ B)

These are PROVEN patterns from Tier 1 + 3-round debate cycle 2026-04-27. Bake into every handoff:

### §8.1 Disk-first
For Mode C/D or any handoff file that other agents must consume, write the
artifact to disk BEFORE runtime notification. Team/message delivery can be
asymmetric and can drop silently (memory `feedback_converged_results_to_disk`);
disk is canonical record for delegated artifacts. Recovery: if a teammate goes
idle without notification, **disk-poll** the expected output file path; if
found, treat as delivered.

### §8.2 file:line citations grep-verified within 10 min
Before any "lock" event (concession, contract, dispatch, commit), grep-verify
the file:line references that support the locked claim. Citations rot fast
(~20-30% premise mismatch in 1 week per memory
`feedback_zeus_plan_citations_rot_fast`). Use symbol-anchored citations
(function name + sentinel comment) where possible.

### §8.3 Bidirectional grep before "X% of Y lack Z" claims
Forward grep (manifest cites field?) AND reverse grep (target system back-cites identity?). Schema-citation gap (forward only) ≠ enforcement gap (both). Apply to ANY % claim. See `.claude/skills/zeus-phase-discipline/SKILL.md` "During implementation" + methodology §5.X case study.

### §8.4 Co-tenant git staging
With multiple agents/sessions active in shared repo:
- `git add` SPECIFIC files; never `-A` or `.`
- `git diff --cached --name-only` before commit; verify scope
- `git restore --staged <file>` for anything unintentionally staged
- HEREDOC commit message; verify with `git log -1` after

### §8.5 Per-batch critic-gate
Executor must NOT self-approve over multi-batch work. Independent critic dispatched in parallel. Team-lead waits for critic APPROVE before next dispatch. Memory `feedback_executor_commit_boundary_gate`.

### §8.6 Idle-only bootstrap
For long-running teammate runtimes, spawn with an idle-only boot prompt: read context → write boot evidence → SendMessage BOOT_ACK → idle. Substantive work only after team-lead dispatches. For one-shot Codex native subagents, give a bounded task directly instead of creating idle ceremony. Memory `feedback_idle_only_bootstrap`.

### §8.7 Verdict-level erratum pattern
When implementation discovers prior debate / verdict / plan was based on incomplete evidence:
- Do NOT silently fix
- Append explicit POST-IMPLEMENTATION ERRATUM to the verdict noting: original claim, what audit found, what changes (and what doesn't change)
- Update referenced artifacts with CITATION_REPAIR comments
- Add to methodology if pattern is reusable

Methodological transparency compounds across cycles.

### §8.8 Cross-session merge conflict-first protocol

When merging from another worktree/session into the active Zeus branch:

1. Identify the merge surface:
   - `git diff <current-branch>...<merging-branch>` for scope
   - `git merge-tree $(git merge-base <current-branch> <merging-branch>) <current-branch> <merging-branch>` or a no-commit merge/cherry-pick to inspect conflicts
2. If there are no conflicts, proceed with the merge and run the scoped
   verification for the changed surface.
3. If conflicts are narrow and mechanical, resolve directly or manually
   choose the correct side, then run the affected tests/checks.
4. Escalate to independent critic evidence only when the conflict surface is
   broad or semantically dangerous:
   - multi-zone or more than a small handful of conflicted files
   - K0/K1, schema, lifecycle, DB/control/live, or authority surfaces
   - ambiguous ownership or competing truth models
   - drift-keyword greps indicate settlement/source/calibration/data-version risk
5. For escalated merges, dispatch an independent critic using the active
   runtime's native review surface. Do not call a different vendor CLI just to
   manufacture independence; the independence boundary is separate
   role/context inside the current runtime. Provide the critic with:
   - Diff and conflict scope summary (files + LOC)
   - Authoring session identifier (which session/worktree produced it)
   - Boundary check: is the merging session subject to the same
     authority files (root `AGENTS.md`, methodology, planning-lock)?
   - Bidirectional grep: do drift-keyword greps trigger on the diff?
6. Escalated critic verdict gates the merge — BLOCK = abort; REVISE = address
   defects per file:line + re-dispatch; APPROVE = proceed with merge.
7. Document critic verdict path in the merge commit only when the critic path
   was actually used.

This extends §8.5 (per-batch critic-gate within a session) to the
**cross-session boundary** without turning clean merges into process debt. Per
memory `feedback_executor_commit_boundary_gate`, self-review is forbidden for
the escalated critic path; narrow mechanical conflict resolution is ordinary
merge work and does not require an independent critic.

Hook enforcement: `.claude/hooks/pre-merge-contamination-check.sh`
prints the conflict-first protocol for merge-class commands on protected
branches and allows the command by default. If `MERGE_AUDIT_EVIDENCE` is set,
the hook validates the critic verdict file and blocks only invalid or BLOCK
evidence.

---

## §9 Common failure modes (recovery procedures)

| # | Symptom | Cause | Recovery |
|---|---|---|---|
| F1 | Teammate idle, no SendMessage | SendMessage dropped | Disk-poll expected output path |
| F2 | Teammate flags MISROUTE_FLAG | Judge meta-tasks polluted team task list | Delete misleading tasks; track meta in judge_ledger only |
| F3 | Teammate idle without producing | Role unclear in dispatch | Re-dispatch with explicit "YOUR ROLE" + numbered steps + literal SendMessage template |
| F4 | Path corrections from teammate | Dispatch cited paths that moved | Acknowledge corrections; adopt teammate paths as canonical |
| F5 | Co-tenant staged work absorbed into commit | `git add -A` or `.` used | `git diff --cached --name-only` BEFORE commit; `git restore --staged` to fix |
| F6 | Documented baseline ≠ live | Baseline drifted between docs and now | Critic re-measures LIVE at boot; uses LIVE for checks; documents drift |
| F7 | Verdict claim falsified post-implementation | Debate-stage audit was incomplete | Erratum pattern §8.7 |
| F8 | Implementation drift from documented design | Execution copy of "obvious choice" diverges from documented choice | Cross-batch arithmetic equivalence audit (critic role); fix to match documented design |

---

## §10 Completion Gate

Before calling the handoff ready (any mode):

Apply this checklist only to artifacts selected by the mode. Omit non-applicable
items; do not create placeholder files to satisfy the checklist shape.

- [ ] Open questions resolved or explicitly blocking
- [ ] Not-now items explicit
- [ ] Verification commands concrete
- [ ] Rollback / blast radius stated
- [ ] Any new file registered in scoped mesh (`architecture/script_manifest.yaml` for scripts, `architecture/source_rationale.yaml` for src/, `architecture/test_topology.yaml` for tests/, scoped AGENTS.md for routers)
- [ ] Handoff bundle contains only current, non-conflicting truth surfaces
- [ ] **Mode C/D additions**: critic-gate dispatched per batch when that mode is
  active; verdict committed only for Mode D; per-phase boot evidence on disk
  only when a long-running runtime consumes it
- [ ] **Discipline checks**: §8.1 disk-first verified for delegated artifacts;
  §8.2 cites grep-fresh for locked file:line claims; §8.3 bidirectional grep
  run on any % claim; §8.4 git staging clean
- [ ] If implementation discovered prior-stage error: §8.7 erratum applied
- [ ] Operation-end feedback capsule captured in the final response or in an
  already-required packet closeout surface

---

## §11 When to NOT use this SKILL

- Trivial task (typo, single comment fix, obvious one-liner): just do it
- Task already covered by another SKILL (`.claude/skills/zeus-phase-discipline/SKILL.md` for r3 phase work; `zeus-task-boot-*` for specific task classes): use that instead
- Task is purely investigation / research with no commit at the end: use the
  lightest read-only path (`rg`, `omx explore` when OMX runtime is active, or a
  bounded explorer subagent when delegation is explicitly chosen)

---

## §12 Maintenance

This SKILL is **living**. Update after each cycle that surfaces new patterns. Cite this SKILL in `docs/runbooks/task_2026-04-19_ai_workflow_bridge.md` and methodology doc as the operational entry point. Cross-reference invariants:

- Methodology doc: `docs/methodology/adversarial_debate_for_project_evaluation.md` (Mode D specifics)
- Phase discipline SKILL: `.claude/skills/zeus-phase-discipline/SKILL.md` (Mode C per-batch)
- Task-boot SKILLs: `.claude/skills/zeus-task-boot-*/SKILL.md` (per-task-class boot profiles)

When promoting to OMC/global skill: copy to `~/.claude/skills/zeus-ai-handoff/SKILL.md` and adjust paths.

---

## Lineage

v1 (2026-04-19): single-mode workflow adapted from external starter kit.
v2 (2026-04-28): 4-mode playbook + discipline patterns + failure recovery + erratum pattern. Distilled from Tier 1 batch execution + 3-round adversarial debate cycle 2026-04-27. Validates pattern reusability beyond debate-specific use.
v2.1 (2026-04-30): runtime-neutral dispatch language plus scoped critic-gate boundaries after conflict-first merge protocol correction.
v2.2 (2026-04-30): mode-scoped reads/artifacts so handoff discipline does not become default ceremony.
v2.3 (2026-04-30): operation-end feedback capsule for context recovery, Zeus improvement insights, and topology helped/blocked notes without standalone artifact ceremony.
