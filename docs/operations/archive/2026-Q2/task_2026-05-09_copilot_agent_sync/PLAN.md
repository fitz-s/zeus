# Plan: Copilot / VS Code Agent Sync for Zeus Workflows
> Created: 2026-05-09 | Status: DRAFT

## Goal

Make VS Code Copilot agents inherit the operational shape of Claude Code + Zeus without pretending Copilot has Claude Code's native hook stream or subagent runtime.

## Context

- Root `~/.claude/CLAUDE.md` defines OMC orchestration: delegate for search/review/verification, route models by task depth, distinguish `Task` subagents from persistent `Team`, and preserve decisions as code/types/tests rather than prose.
- OpenClaw root `CLAUDE.md` defines the hub-and-spoke agent architecture and points Zeus work to `workspace-venus/zeus/AGENTS.md` plus `.claude/CLAUDE.md` as local authority.
- Zeus root `AGENTS.md` makes `topology_doctor --navigation` the pre-edit entrypoint, requires planning-lock for governance/control/truth paths, and treats hook/evidence artifacts as conditional rather than default ritual.
- Zeus `.claude/settings.json` and `.claude/hooks/dispatch.py` centralize Claude Code hook events. Current hook v2 is advisory/fail-open, with PR-size and merge-comment hooks still documenting blocking intent.
- `.codex/hooks/zeus-router.mjs` already proves the right adapter pattern: normalize another harness payload and forward it to the shared Claude hook dispatcher.
- Existing Copilot surface is review-oriented: `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md`. It does not yet give Copilot an execution workflow.
- OpenClaw already names `copilot` in `plugin-skills/acp-router/SKILL.md`, but `openclaw.json` currently allows only `claude` and `codex` for ACP.

## Core Decision

Do not port Claude Code hooks directly into Copilot. Build a three-layer sync:

1. **Instruction layer**: small Copilot prompt surfaces that say what Copilot must do first, when to stop, and when to call tools. Keep each file under the Zeus 4000-byte instruction budget.
2. **Tool bridge layer**: MCP/CLI tools that expose topology routing, planning-lock, map maintenance, PR monitor advice, verifier checks, and OpenClaw ACP spawn/status to Copilot.
3. **Adapter layer**: optional ACP/VS Code wrappers that normalize Copilot events into the same dispatcher contract used by Claude and Codex when event data exists.

Copilot should be a workspace UI and lightweight orchestrator. OpenClaw/Claude Code remains the durable multi-agent engine for long-running work, persistent teams, and child-agent orchestration.

## First-Principles Dispatch Design

Verdict after critic/architect review: `ADD_LIGHT_DISPATCH`.

Copilot's workflow is not Claude Code's workflow. Claude Code has native
`Task`/`Team` semantics and OMC model routing; Copilot should not simulate that
inside VS Code prompts. The objective is narrower: Copilot does bounded local
work itself, then sends a small typed handoff envelope to OpenClaw when durable
memory, parallel workers, independent review, or premium reasoning is actually
needed.

Non-goals:

- No fake Copilot-native subagents.
- No prompt-layer global model optimizer.
- No hook/finalization parity mixed into dispatch.
- No `agentId="copilot"` ACP enablement until auth/allowlist smoke tests prove
  the runtime exists.

Minimal handoff envelope:

```json
{
  "runtime": "acp",
  "agentId": "claude|codex|gemini",
  "omcRole": "explore|executor|critic|verifier|document-specialist",
  "task": "single concrete objective",
  "workspaceRoot": "/abs/workspace",
  "cwd": "/abs/cwd",
  "route": {
    "admittedFiles": ["..."],
    "outOfScopeFiles": ["..."],
    "writeIntent": "read_only|edit",
    "riskClass": "low|medium|high"
  },
  "inputs": {
    "authorityPacketPaths": ["..."],
    "artifactPaths": ["..."]
  },
  "budget": {
    "maxWorkers": 1,
    "maxRounds": 1,
    "maxOutputTokens": 4000
  }
}
```

Role first, model second:

| Work type | Default route | Escalation route |
| --- | --- | --- |
| Broad search / file finding | `explore` on cheapest long-context model such as Haiku-class | Medium model only if evidence conflicts |
| Standard implementation | `executor` on Sonnet/GPT-5-class coding model | Opus/Gemini Pro only for cross-module design or repeated failure |
| Review / adversarial critique | `critic` on medium reasoning model | Opus/Gemini Pro for high-blast-radius governance/runtime changes |
| Verification | `verifier` with executable checks first | Premium model only for semantic proof gaps |
| External docs/API ambiguity | `document-specialist` on cheap retrieval model | Medium model if docs conflict |

Escalate only when one of these is true:

- The cheap worker cannot answer from the bounded authority packet and admitted
  files.
- Evidence conflicts across code, tests, docs, or data.
- The task crosses module boundaries or touches high-risk Zeus surfaces.
- One cheap pass plus one repair pass still leaves material ambiguity.
- The requested output requires design judgment rather than search/synthesis.

Parallelize only read-only or independently reviewable work. Good fan-out:
2-3 `explore` workers for independent search questions, or `critic` plus
`verifier` after an implementation artifact exists. Do not run multiple
executors against the same file surface, and do not spawn premium workers just
because a premium model is available.

Token controls:

- Send route cards and file paths, not repo narratives.
- Use `context-bridge.mjs` for bounded authority packets.
- Do not replay chat history unless it contains requirements not captured on
  disk.
- Require worker outputs to be short structured findings, evidence, and next
  action.
- Let OpenClaw/OMC own provider-specific model choice; Copilot only states role,
  risk, budget, and escalation constraints.

## Subagent Mapping

| Claude / Zeus construct | Copilot / VS Code equivalent | Design rule |
|---|---|---|
| OMC `Task(subagent_type=..., model=...)` | OpenClaw ACP spawn via `agentId` or MCP tool | Copilot cannot spawn native child Copilots; route through OpenClaw. |
| Persistent `Team` / SendMessage | OpenClaw session graph + session-observer | Keep disk/session state canonical; do not rely on VS Code chat memory. |
| `safety-gate` subagent | `zeus_planning_lock`, `zeus_map_maintenance`, or composite `zeus_safety_gate(stage, files, planEvidence)` MCP/CLI | Run planning-lock before governed edits and map-maintenance after file shape changes; return PROCEED/REFUSE. |
| `verifier` subagent | `zeus_verify_claim(claim, evidencePath)` MCP/CLI | Re-run acceptance checks, regression baseline, artifact shape, call-site consistency, cold-start reproducibility. |
| haiku/sonnet/opus model routing | OpenClaw routing policy | Copilot does not expose equivalent tier choice; delegate deep search/review/implementation to OpenClaw agents. |
| Claude hook events | Best-effort ACP/CLI normalized events | Native VS Code Copilot does not expose PreToolUse/PostToolUse/SubagentStop in the same way. |

### Agent Invocation Semantics

Keep these namespaces separate:

| Namespace | Values | Meaning |
|---|---|---|
| ACP `agentId` | `claude`, `codex`, `copilot`, `cursor`, `gemini`, etc. | Harness/runtime identity used by `acp-router` and `acpx` |
| OMC role/subagent | `explore`, `executor`, `critic`, `verifier`, `safety-gate`, `document-specialist` | Claude/OMC task role, not an ACP runtime id |
| Zeus tool bridge role | `route`, `planning_lock`, `map_maintenance`, `verify_claim`, `spawn_agent` | MCP/CLI operation exposed to Copilot |

Rules:

- Do not call ACP with `agentId="critic"`, `agentId="verifier"`, or `agentId="executor"`.
- Current OpenClaw ACP config names `copilot` in the router skill but `openclaw.json` allows only `claude` and `codex`; any direct `agentId="copilot"` sample is Phase 3 only.
- Current delegated subagent work should use `agentId="claude"` with the desired OMC role in the task seed, or an OpenClaw server-side tool that itself invokes Claude Code `Task`/`Team`.
- Copilot can dispatch and summarize; Claude/OMC owns actual `Task`, persistent `Team`, critic/verifier independence, and model-tier routing.

### Capability Boundaries

Copilot may directly read files, produce bounded reviews, edit narrow route-admitted files, run local route/check commands through tool bridge wrappers, summarize evidence, and ask operator questions.

Copilot must delegate OMC `Task` subagents, persistent `Team` workflows, independent critic/verifier work, broad Zeus search that benefits from haiku-scale context, long-running hidden work, multi-session memory, and high-risk source/runtime implementation after planning-lock.

Copilot cannot faithfully replicate native Claude Code `PreToolUse`, `PostToolUse`, or `SubagentStop` events, Claude Code permission prompts, Claude `Task`/`Team` semantics, or haiku/sonnet/opus tier selection inside the Copilot model itself.

### Current vs Future Activation

| Capability | Current state | Future phase |
|---|---|---|
| Copilot review instructions | Present via `.github/copilot-instructions.md` and `.github/instructions/**` | Keep concise; no bloat |
| Copilot execution workflow instruction | Present via `.github/instructions/agent-workflow.instructions.md` | Phase 1 complete; keep under 4000 bytes |
| Copilot MCP/CLI route tools | Missing | Phase 2: `vscode-copilot-sync` bridge |
| Copilot ACP runtime | Router names it, but ACP allowlist does not enable it | Phase 3 after smoke test |
| Shared hook adapter | Codex adapter exists; Copilot adapter missing | Phase 4 if tool-event payloads exist |

## Hook Design

### H0 - Session Start Visibility

Purpose: prevent shared-worktree collisions before any agent edits.

Implementation:
- Claude Code: keep `.claude/hooks/registry.yaml` `session_start_visibility` and wire it when the upstream event is available.
- Copilot: provide `zeus_session_start` MCP/CLI that returns `pwd`, repo root, branch, worktree list, dirty state, and best-effort active OpenClaw session visibility.
- VS Code instruction: Copilot must call this before edits in Zeus or before spawning ACP work.
- If OpenClaw session discovery is unavailable, return `sessions: unavailable` and continue with worktree collision checks rather than blocking narrow direct work.

### H1 - Topology Route Gate

Purpose: make the Zeus task route explicit before editing.

Implementation:
- Tool: `zeus_route(task, intent, writeIntent, files)` wraps `python3 scripts/topology_doctor.py --navigation ... --json`.
- Output contract: admitted files, forbidden files, required gates, stop conditions, route card summary.
- Copilot behavior: refuse implementation if intended edit files are not admitted; ask operator or create a plan packet if route is advisory-only.

### H2 - Planning-Lock + Mesh Gate

Purpose: preserve Zeus governance/truth/control planning discipline.

Implementation:
- Pre-edit tool: `zeus_planning_lock(intendedFiles, planEvidence)` wraps `topology_doctor --planning-lock` before governed edits.
- Pre-commit tool: `zeus_map_maintenance(changedFiles)` wraps `topology_doctor --map-maintenance` after file adds/deletes/renames and before commit.
- Composite tool: `zeus_safety_gate(stage, files, planEvidence)` may call either or both, but `stage` must be `pre_edit` or `pre_commit`.
- Missing required planning evidence returns `REFUSE`, not `ADVISORY`.
- This mirrors `.claude/agents/safety-gate.md` without requiring a Copilot-native subagent.

### H3 - Pre-Commit / PR Ritual Advice

Purpose: share existing hook intelligence across Claude, Codex, and Copilot.

Implementation:
- Reuse `.claude/hooks/dispatch.py` as the single policy engine.
- Add a Copilot adapter only if ACP/VS Code supplies tool event payloads. It should look like `.codex/hooks/zeus-router.mjs`: normalize payload, forward to dispatcher, translate additionalContext into Copilot-visible output.
- Hook v2 is advisory/fail-open for most checks and fail-open on dispatcher crashes. However, `pr_create_loc_accumulation` and `pre_merge_comment_check` are blocking when `dispatch.py` can evaluate them, with `ZEUS_PR_ALLOW_TINY` / `ZEUS_PR_MERGE_FORCE` bypasses.
- A Copilot adapter must preserve dispatcher exit 2 as `BLOCKED` where the platform can honor it. If the platform cannot hard-block, surface `BLOCKED` text loudly and rely on GitHub/server-side policy or human override rather than relabeling it advisory.

### H4 - Completion Verifier

Purpose: prevent "done" claims without rerunnable evidence.

Implementation:
- Tool: `zeus_verify_claim(claim, files, tests, evidencePath)` implements the `.claude/agents/verifier.md` five-check template.
- For long tasks, spawn OpenClaw `verifier` or Claude Code verifier through ACP rather than making Copilot self-review its own work.

### H5 - Hidden Work Visibility

Purpose: make child ACP/Copilot/Claude sessions visible without transcript replay.

Implementation:
- Prefer `session-observer` for lifecycle blocks (`CHILD SESSION STARTED`, `UPDATED`, `A2A REQUEST`, `A2A REPLY`).
- Do not revive legacy live transcript mirroring as the default.

### H6 - Rule 0 Ask-Before-Final Gate

Purpose: keep the user interaction open by making `ask_user` / `#askQuestions`
a required closeout action, not a style preference.

Implementation:
- Short term: `.github/instructions/agent-workflow.instructions.md` requires
  Copilot to call the available ask-user tool before any terminal user-facing
  closeout. Ending with a prose question alone is insufficient.
- Tool bridge: add `rule0_before_final(draftSummary, nextQuestion, choices)`.
  The tool validates that `nextQuestion` is concrete, invokes the VS Code
  `askQuestions`/`ask_user` surface, and returns an `askReceipt`.
- Implemented bridge seed: OpenClaw
  `plugin-skills/vscode-copilot-sync/rule0-before-final.mjs`, with tests in
  `plugin-skills/vscode-copilot-sync/rule0-before-final.test.mjs`. The broader
  MCP/server surface is deferred until a real finalization or VS Code adapter
  seam exists.

Minimal real adapter seam:

1. The enforced boundary must be a VS Code extension command or chat participant
  wrapper that owns the final response emission. A helper or MCP tool alone is
  not enforcement.
2. Before that wrapper emits a terminal closeout, it calls VS Code UI such as
  `vscode.window.showInformationMessage(...choices)` or `showQuickPick(...)`
  with the concrete `nextQuestion`.
3. The wrapper records the selected/dismissed answer as `askReceipt`, then and
  only then emits the final response.
4. If the UI call is unavailable, cancelled, or produces no receipt, the wrapper
  emits only the blocked state (`BLOCKED_RULE0_TOOL_UNAVAILABLE` or
  `BLOCKED_RULE0_MISSING_ASK`) and does not claim completion.

Non-goal: do not rebuild MCP until this wrapper exists or the official VS Code
agent API exposes a true finalization/tool-event seam.

Copilot-only isolation:

- Rule0 closeout enforcement applies only to Copilot agent runtimes: VS Code
  Copilot agent mode, a future OpenClaw ACP `agentId="copilot"`, or a wrapper
  explicitly configured with `runtime="copilot"`.
- Enforcement must be centralized at the top-level host/finalizer boundary, not
  distributed into every agent prompt. A prompt-only rule cannot simultaneously
  guarantee that an undisciplined main agent asks and that delegated workers
  never ask.
- It must not install Claude Code hooks, Codex hooks, global OpenClaw dispatch
  hooks, or OMC `Task`/`Team` behavior changes.
- Non-Copilot runtimes return `SKIPPED_NON_COPILOT_AGENT`; they do not block,
  ask, or mutate their closeout behavior.
- Delegated Copilot workers/subagents return `SKIPPED_DELEGATED_AGENT`; they do
  not ask the user because their response audience is the parent agent, not the
  operator. Only the top-level, user-facing Copilot session may run Rule0.
- Missing top-level/user-facing metadata blocks with
  `BLOCKED_RULE0_MISSING_TOP_LEVEL_CONTEXT`; the bridge must not ask by default
  when it cannot prove the current response is the top-level operator closeout.
- The VS Code adapter must make runtime selection explicit in its invocation
  context, not infer it from repository path alone. Zeus running inside VS Code
  is not sufficient; the current responding agent must be Copilot.

Adapter plan:

1. Define `isCopilotAgent(context)` with positive evidence only: explicit
  runtime id, VS Code Copilot chat participant id, or OpenClaw ACP
  `agentId="copilot"`.
2. Implement a top-level host/finalizer boundary such as
  `copilotRule0Closeout({ runtime, invocationKind, responseAudience,
  parentAgentId, agentDepth, draftSummary, nextQuestion, choices })`. It returns
  `SKIPPED_NON_COPILOT_AGENT` unless `isCopilotAgent` is true, and
  `SKIPPED_DELEGATED_AGENT` for subagent/worker/internal parent-agent returns.
3. The wrapper must inject `responseAudience="user"` and
  `invocationKind="top-level"` only for the root user-facing closeout; worker
  result paths must be marked with parent/delegated context or bypass Rule0.
4. Inside the top-level Copilot branch, call the VS Code UI adapter
  (`showInformationMessage` or `showQuickPick`) and pass the result into
  `rule0-before-final.mjs` as the injected ask adapter.
5. Add tests for four relationships: top-level Copilot asks and returns
  `askReceipt`; delegated Copilot skips without side effects; non-Copilot
  runtime skips without side effects; missing VS Code UI adapter blocks only the
  top-level Copilot runtime.
6. Keep Claude Code, Codex, and OpenClaw global router files out of scope unless
  the operator explicitly asks for per-agent variants later.
- Harness adapter: if ACP or a VS Code extension can observe finalization,
  wrap final response emission with this tool. Missing `askReceipt` is
  `BLOCKED_RULE0_MISSING_ASK`, not advisory.
- If the platform cannot expose a finalization event, force all delegated
  Copilot closeouts through an OpenClaw `finalize_response` tool that performs
  the ask call and returns the text to send.
- Failure mode: if no ask-user tool is available, the agent must report
  `BLOCKED_RULE0_TOOL_UNAVAILABLE` and stop before claiming completion.

## Workflow Design

### End-to-End State Machine

Every Copilot/VS Code Zeus task should move through the same explicit states,
whether the work is direct editing, ACP delegation, or review-only:

```text
INTAKE
  -> SESSION_START_CHECK
  -> AUTHORITY_LOAD
  -> TOPOLOGY_ROUTE
  -> SCOPE_DECISION
  -> SAFETY_GATE_IF_NEEDED
  -> EXECUTION_OR_DELEGATION
  -> FOCUSED_VERIFICATION
  -> CRITIC_OR_VERIFIER_IF_NEEDED
  -> CLOSEOUT_CAPSULE
  -> RULE0_ASK_GATE
  -> STOP_OR_NEXT_ROUTE
```

State contracts:

| State | Required input | Required output | Stop condition |
|---|---|---|---|
| INTAKE | User task, workspace root | Task summary, likely files, intent guess | User asks only a question or forbids edits |
| SESSION_START_CHECK | Workspace root | Branch, worktree list, dirty paths, active sessions | Shared root collision or unrelated dirty files overlap intended writes |
| AUTHORITY_LOAD | Route class | Minimal authority packet | Missing root `AGENTS.md` / Zeus authority files |
| TOPOLOGY_ROUTE | Task, files, intent, write intent | Route card JSON | `advisory_only`, forbidden files, or scope expansion required |
| SCOPE_DECISION | Route card | Direct edit / plan packet / ACP delegation / review-only | Any T4 live/prod/data side effect without operator approval |
| SAFETY_GATE_IF_NEEDED | Intended files before edit; changed files before closeout | PROCEED / REFUSE | Planning-lock or map-maintenance failure |
| EXECUTION_OR_DELEGATION | Admitted files and gate result | Patch, ACP task, or review finding | Agent lacks tool/runtime capability |
| FOCUSED_VERIFICATION | Changed files and route gates | Test/check evidence | Required check unavailable or failing |
| CRITIC_OR_VERIFIER_IF_NEEDED | Claim plus evidence | APPROVE / REVISE / UNVERIFIED / FAILED | High-risk work without independent check |
| CLOSEOUT_CAPSULE | Result and topology experience | Final capsule and durable backlog note if routed | New insight needs implementation without new route |
| RULE0_ASK_GATE | Final capsule and concrete next question | `askReceipt` from ask-user tool | Missing tool call, vague question, or terminal answer with no interaction path |

### Agent Selection Matrix

Copilot must not try to simulate every Claude subagent locally. It should choose
one of four execution modes:

| Task shape | Primary mode | Agent/tool | Why |
|---|---|---|---|
| Find files, map symbols, locate all call sites | Delegate | OpenClaw `explore` / Claude haiku | Zeus grep/search is broad and cheap outside Copilot context |
| Single admitted docs/test edit | Direct | Copilot agent mode + `zeus_route` | Low coordination overhead |
| Source/runtime edit touching K0/K1, `src/state/**`, lifecycle, DB, control, or >4 files | Delegate with gate | Claude Code `executor` + `safety-gate` + `critic` + `verifier` | Copilot lacks persistent subagent/team discipline |
| PR/code review | Direct review or delegated review | Copilot review instructions, then Claude synthesis | Existing Copilot surface is review-oriented |
| Multi-batch or cross-session packet | Delegate | OMC `team` / persistent teammates | Requires disk-first evidence and persistent roles |
| External docs / SDK uncertainty | Delegate | `document-specialist` | Avoid stale guessed API behavior |
| Completion claim for non-trivial work | Independent check | `zeus_verify_claim` or Claude verifier | Self-verification is weak evidence |

Subagent choice rule:

```text
If the task requires memory across more than one response, independent criticism,
or concurrent workers, Copilot should become the dispatcher and OpenClaw/Claude
should run the agents. If the task is a narrow admitted edit with local checks,
Copilot may execute directly.
```

### Workflow A - Direct VS Code Copilot Editing Zeus

Full sequence:

1. Load minimal authority:
   - `AGENTS.md`
   - `.claude/CLAUDE.md`
   - matching scoped `AGENTS.md` for any target module
   - matching `.github/instructions/*.instructions.md` only when reviewing or editing that surface
2. Run `zeus_session_start`.
3. Run `zeus_route` with task, intent, write intent, and candidate files.
4. If route admits all target files and no planning-lock applies, edit only admitted files.
5. If route admits only a plan packet, write/update only that plan packet and stop before implementation.
6. If planning-lock applies, cite or create `docs/operations/task_<date>_<topic>/PLAN.md`, then run `zeus_planning_lock` before editing.
7. If files are added, deleted, or renamed, run `zeus_map_maintenance` before commit/closeout.
8. Run route-required focused checks.
9. Run `zeus_verify_claim` for non-trivial claims or any source/runtime edit.
10. End with the feedback capsule; append durable insights to `architecture/improvement_backlog.yaml` only under a separate admitted route.

Sample Copilot direct-edit preamble:

```text
I am in Zeus. Before editing I will run zeus_session_start, then zeus_route
with the intended files. I will only edit files admitted by the route card. If
the route returns advisory_only or planning_lock, I will stop and create/cite a
plan packet before implementation.
```

### Workflow B - Copilot as OpenClaw ACP Harness

Full sequence:

1. Run `zeus_session_start` from the parent orchestrator.
2. Run `zeus_route`; if not admitted, do not spawn an editing ACP session.
3. Spawn ACP through OpenClaw with explicit workspace and task seed. Current implementation uses `agentId="claude"` or `agentId="codex"`; `agentId="copilot"` is Phase 3 only after allowlist and scratch smoke test.
4. Start `session-observer watch-spawns` for lifecycle visibility.
5. Require the ACP session to write evidence to disk before reporting status.
6. Run `zeus_planning_lock` before risky writes and `zeus_map_maintenance` before commit/closeout when files were added, deleted, or renamed.
7. Run focused checks from the route card.
8. Run verifier or critic as a separate agent for non-trivial completion claims.
9. Stop observer and close the ACP session.

Sample ACP spawn payload:

```json
{
  "runtime": "acp",
  "agentId": "claude",
  "thread": true,
  "mode": "session",
  "cwd": "/Users/leofitz/.openclaw/workspace-venus/zeus",
  "omcRole": "executor",
  "task": "You are a Claude/OMC executor in Zeus, spawned by Copilot through OpenClaw. Read AGENTS.md and the attached route card first. Do not edit outside admitted_files. Write evidence to docs/operations/task_<date>_<topic>/work_log.md if the packet requires it. Stop on forbidden_files, planning_lock failure, or live/prod/data side effects."
}
```

Phase 3 may replace `agentId="claude"` with `agentId="copilot"` only after `openclaw.json` enables Copilot and a read-only scratch ACP session succeeds.

### Workflow C - Claude Orchestrates, Copilot Reviews

1. Claude/OMC remains primary orchestrator for multi-agent tasks.
2. Copilot receives bounded review prompts using `.github/copilot-instructions.md` and path-scoped `.github/instructions/**`.
3. Reviews follow Tier 0 -> Tier 1 -> Tier 2 -> Tier 3 ordering and must report coverage limits.
4. Claude synthesizes Copilot/Codex/critic findings and decides implementation follow-up under Zeus route gates.

Sample bounded review prompt:

```text
Review this Zeus diff by runtime risk. Read .github/copilot-instructions.md
and matching .github/instructions/*.instructions.md. Exhaust Tier 0 before
Tier 1. For every finding cite path, invariant if applicable, why it matters,
and a concrete fix. If coverage is partial, say partial and name unreviewed
Tier 0/1 surfaces. Do not review archives/cache/runtime scratch unless the diff
changes runtime behavior.
```

### Workflow D - Copilot Dispatches Claude/OMC Subagents

This is the most important parity path. Copilot should route work to Claude
subagents when the task needs decomposition, independent review, or persistent
state.

1. Copilot runs `zeus_route` and decides the lane.
2. Copilot calls `openclaw_spawn_agent` with an ACP runtime id plus one of these OMC roles in the task seed:
   - `explore`: read-only broad search; returns file map and candidate files.
   - `executor`: implementation inside route-admitted scope.
   - `safety-gate`: planning-lock and map-maintenance evidence.
   - `critic`: adversarial design/code review against the diff or plan.
   - `verifier`: proof-of-done after executor claim.
3. Each spawned agent receives:
   - route card
   - allowed files
   - forbidden files
   - stop conditions
   - evidence path
   - exact return schema
4. Copilot waits for result, summarizes to the user, and does not claim done until verifier/critic evidence matches the route gates.

Sample executor dispatch seed:

```text
ACP agentId: claude
ROLE: executor
Workspace: /Users/leofitz/.openclaw/workspace-venus/zeus
Authority: root AGENTS.md, .claude/CLAUDE.md, route card below.

TASK: <one sentence>
ROUTE_CARD: <paste JSON or path>
ALLOWED_FILES: <list>
FORBIDDEN_FILES: <list>
STOP_CONDITIONS: <list>
PLAN_EVIDENCE: <path or N/A>

Required behavior:
1. Re-read scoped AGENTS.md for each allowed source/test path.
2. Do not edit forbidden files.
3. If a new file is added, update the owning registry.
4. Stage only named files; never git add -A in the main worktree.
5. Return BATCH_DONE with changed files, tests, and evidence paths.
```

Sample critic dispatch seed:

```text
ACP agentId: claude
ROLE: critic
Review target: <diff path or plan path>
Compare against: root ~/.claude/CLAUDE.md OMC workflow, Zeus AGENTS.md,
.claude/settings.json hooks, .claude/agents/safety-gate.md,
.claude/agents/verifier.md, .codex/hooks/zeus-router.mjs.

Focus:
1. Can Copilot reproduce the safety properties without native Claude hooks?
2. Are subagent choices correct, or is Copilot asked to self-review too much?
3. Are there missing stop conditions for topology, planning-lock, worktree collision, or live/prod/data side effects?
4. Which parts are impossible without OpenClaw/MCP/ACP support?

Return: APPROVE / REVISE / BLOCK with concrete plan edits.
```

## Tool Bridge Sample Design

### MCP Tool Set

Minimal first version:

```json
[
  {
    "name": "zeus_session_start",
    "description": "Return repo root, branch, worktree list, dirty state, best-effort OpenClaw session visibility, and collision warnings.",
    "inputSchema": {"type": "object", "properties": {"workspaceRoot": {"type": "string"}}, "required": ["workspaceRoot"]}
  },
  {
    "name": "zeus_route",
    "description": "Run topology_doctor --navigation and return the route card plus admission status.",
    "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}, "intent": {"type": "string"}, "writeIntent": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}}, "required": ["task", "files"]}
  },
  {
    "name": "zeus_planning_lock",
    "description": "Run planning-lock before editing governed paths and return PROCEED or REFUSE evidence.",
    "inputSchema": {"type": "object", "properties": {"intendedFiles": {"type": "array", "items": {"type": "string"}}, "planEvidence": {"type": "string"}}, "required": ["intendedFiles"]}
  },
  {
    "name": "zeus_map_maintenance",
    "description": "Run map-maintenance after adds/deletes/renames and before commit/closeout.",
    "inputSchema": {"type": "object", "properties": {"changedFiles": {"type": "array", "items": {"type": "string"}}, "mode": {"type": "string", "enum": ["advisory", "precommit", "closeout"]}}, "required": ["changedFiles"]}
  },
  {
    "name": "zeus_safety_gate",
    "description": "Composite safety gate for pre_edit or pre_commit stages.",
    "inputSchema": {"type": "object", "properties": {"stage": {"type": "string", "enum": ["pre_edit", "pre_commit"]}, "files": {"type": "array", "items": {"type": "string"}}, "planEvidence": {"type": "string"}}, "required": ["stage", "files"]}
  },
  {
    "name": "zeus_verify_claim",
    "description": "Verify a completion claim using the Zeus verifier five-check template.",
    "inputSchema": {"type": "object", "properties": {"claim": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "tests": {"type": "array", "items": {"type": "string"}}, "evidencePath": {"type": "string"}}, "required": ["claim", "files"]}
  },
  {
    "name": "openclaw_spawn_agent",
    "description": "Spawn an OpenClaw/ACP harness runtime with an OMC role encoded in the task seed.",
    "inputSchema": {"type": "object", "properties": {"agentId": {"type": "string"}, "omcRole": {"type": "string"}, "cwd": {"type": "string"}, "task": {"type": "string"}, "routeCard": {"type": "object"}}, "required": ["agentId", "cwd", "task"]}
  },
  {
    "name": "rule0_before_final",
    "description": "Invoke ask_user/#askQuestions before any terminal response and return an askReceipt.",
    "inputSchema": {"type": "object", "properties": {"draftSummary": {"type": "string"}, "nextQuestion": {"type": "string"}, "choices": {"type": "array", "items": {"type": "string"}}}, "required": ["nextQuestion"]}
  }
]
```

Tool output contract:

```json
{
  "ok": true,
  "status": "admitted|advisory_only|scope_expansion_required|refuse|verified|failed",
  "summary": "human-readable one-screen result",
  "evidence": {
    "commands": [],
    "paths": [],
    "routeCard": {},
    "warnings": []
  },
  "nextAction": "proceed|stop_and_plan|delegate|ask_operator|verify"
}
```

### Hook Adapter Payload

If Copilot ACP exposes tool events, the adapter should normalize them into the
same shape `.claude/hooks/dispatch.py` already understands:

```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "cwd": "/Users/leofitz/.openclaw/workspace-venus/zeus",
  "session_id": "copilot-acp-<id>",
  "agent_id": "copilot",
  "tool_input": {
    "command": "git commit -m 'docs: add agent workflow plan'"
  }
}
```

Adapter behavior:

1. Ignore events outside the Zeus root.
2. For edit events, extract patch paths like `.codex/hooks/zeus-router.mjs`.
3. Call `python3 .claude/hooks/dispatch.py <hook_id>`.
4. Return `additionalContext` to Copilot.
5. Fail open on dispatcher crashes, but preserve dispatcher exit 2 as `BLOCKED` for hook ids that are blocking in Zeus law. If VS Code cannot enforce the block, show the block as an operator-facing stop condition.

### Minimal Copilot Instruction Sample

Target file: `.github/instructions/agent-workflow.instructions.md`.

Size budget: under 4000 bytes.

```markdown
---
applyTo: "**"
---

# Zeus Copilot Agent Workflow

Before editing Zeus, run the Zeus route tools. Do not infer authority from chat
history.

1. Read `AGENTS.md` and `.claude/CLAUDE.md`.
2. Run `zeus_session_start` for branch, worktree, dirty state, and collision warnings.
3. Run `zeus_route(task, intent, writeIntent, files)`.
4. Edit only files admitted by the route card.
5. If route is advisory-only, scope-expansion, forbidden, or planning-lock applies, stop and create/cite a `docs/operations/task_<date>_<topic>/PLAN.md` packet.
6. For `architecture/**`, `docs/authority/**`, `src/state/**`, `src/control/**`, `src/supervisor_api/**`, cross-zone changes, or >4 files, run `zeus_planning_lock` before editing.
7. After file adds/deletes/renames, run `zeus_map_maintenance` before commit/closeout.
8. For broad search, implementation, critic, verifier, or multi-step work, delegate through OpenClaw rather than pretending Copilot has native subagents.
9. Run focused route checks and `zeus_verify_claim` before saying done.
10. Before any top-level, user-facing terminal closeout, call `ask_user` /
  `#askQuestions` through `rule0_before_final`; do not substitute a prose-only
  final question. Delegated workers/subagents return to the parent agent without
  calling ask-user.

Stop before live/prod/data side effects unless the operator explicitly approves.
```

### Test Fixture Samples

MCP bridge tests:

```text
test_route_admits_plan_packet
  input: task="operation planning packet", files=[docs/operations/task_x/PLAN.md]
  expect: status=admitted, admitted_files includes PLAN.md

test_route_refuses_forbidden_source_from_plan_route
  input: task="operation planning packet", files=[src/state/db.py]
  expect: status!=admitted, nextAction=stop_and_plan

test_planning_lock_blocks_missing_plan_evidence
  input: intendedFiles=[src/state/db.py], planEvidence="missing.md"
  expect: status=refuse, evidence includes planning-lock output

test_map_maintenance_flags_missing_registry_update
  input: changedFiles=[docs/operations/task_x/PLAN.md]
  expect: status=advisory_or_refuse, evidence names docs/operations/AGENTS.md when registry is missing

test_hook_adapter_matches_claude_dispatch
  input: normalized git commit payload
  expect: additionalContext contains invariant/secrets advisory text

test_rule0_before_final_blocks_missing_ask_receipt
  input: draft final response with no askReceipt
  expect: status=blocked, reason=BLOCKED_RULE0_MISSING_ASK
```

End-to-end dry run:

```text
1. Run zeus_session_start in Zeus.
2. Run zeus_route for a docs-only plan packet.
3. Create a scratch plan file in a temporary test repo or dry-run fixture.
4. Run zeus_planning_lock only if the route requires it; otherwise skip with recorded route evidence.
5. Run zeus_map_maintenance for the scratch plan file; expect registry requirement evidence.
6. Run zeus_verify_claim against the created artifact; expect VERIFIED for artifact shape and UNVERIFIED only for tests if no tests were required.
7. Spawn an OpenClaw read-only explore agent through `agentId="claude"`, `omcRole="explore"`, and verify the session appears in session-observer lifecycle output.
```

## Proposed Artifacts

### Zeus repo

- `.github/instructions/agent-workflow.instructions.md`: short execution workflow for Copilot agent mode; `applyTo` covers Zeus repo surfaces and remains under 4000 bytes.
- `.github/instructions/runtime-review.instructions.md`: keep runtime review focused; do not absorb routing ritual.
- `.github/copilot-instructions.md`: do not append to this file; it is already near the 4000-byte budget. Replace/shrink only if needed.
- `.codex/hooks/zeus-router.mjs`: use as reference adapter for any future Copilot ACP event router.
- `docs/operations/task_2026-05-09_copilot_agent_sync/PLAN.md`: this plan packet.

### OpenClaw root

- `.github/copilot-instructions.md`: updated with global role-first/model-second subagent dispatch rules for any Copilot agent.
- `plugin-skills/vscode-copilot-sync/SKILL.md`: user-facing workflow for syncing Copilot with OpenClaw/Zeus.
- `plugin-skills/vscode-copilot-sync/rule0-before-final.mjs`: implemented Rule0 ask-before-final bridge primitive.
- `plugin-skills/vscode-copilot-sync/mcp-server.mjs`: deferred; when built, use the official SDK or a verified transport and add subprocess interoperability tests.
- `plugin-skills/vscode-copilot-sync/context-bridge.mjs`: implemented read-only bridge that reads workspace authority docs and emits bounded prompt packets.
- `plugin-skills/vscode-copilot-sync/zeus-route.mjs`: implemented read-only wrapper around `topology_doctor.py --navigation --json`.
- `plugin-skills/vscode-copilot-sync/zeus-gates.mjs`: implemented read-only planning-lock/map-maintenance wrapper.
- `plugin-skills/vscode-copilot-sync/dispatch-envelope.mjs`: implemented role-first/model-tier-second handoff envelope builder.
- `openclaw.json`: add `copilot` to ACP `allowedAgents` only after a smoke test proves auth and command availability.
- VS Code user prompts folder: optional reusable prompt files, not authority.

## Implementation Phases

- [ ] Phase 0 - Drift audit
  - Files: root `CLAUDE.md`, `~/.claude/CLAUDE.md`, Zeus `AGENTS.md`, `.claude/**`, `.github/**`, `openclaw.json`, `plugin-skills/acp-router/SKILL.md`.
  - What: record exact authority map and contradictions before changing behavior.

- [x] Phase 1 - Minimal Copilot instruction sync
  - Files: Zeus `.github/instructions/agent-workflow.instructions.md`; avoid `.github/copilot-instructions.md` unless replacing/shrinking existing text.
  - What: add a tiny Copilot execution contract: session-start, topology route, planning-lock, focused checks, verifier, ask/stop conditions.
  - Implementation note: added topology classification for `.github/instructions/**` first, because Phase 1 initially routed as `navigation_requested_file_unclassified`.
  - Verification: `wc -c .github/copilot-instructions.md .github/instructions/*.instructions.md` under 4000 each.

- [ ] Phase 2 - MCP/CLI bridge
  - Files: OpenClaw `plugin-skills/vscode-copilot-sync/**`.
  - What: implement read-only wrappers first: route, safety-gate dry run, session status, authority context packet.
  - Rule0 seed implemented: `rule0-before-final.mjs` validates question shape, skips non-Copilot runtimes with `SKIPPED_NON_COPILOT_AGENT`, calls injected ask adapter, returns `askReceipt`, and blocks missing ask/tool states for Copilot runtimes.
  - Authority context bridge implemented: `context-bridge.mjs` reads only explicit workspace-relative authority files, caps bytes per file, and refuses path traversal.
  - Route bridge implemented: `zeus-route.mjs` wraps `topology_doctor.py --navigation --json` with explicit workspace root and relative file validation.
  - Safety-gate wrappers implemented: `zeus-gates.mjs` wraps planning-lock and map-maintenance in JSON mode with relative changed-file validation.
  - Dispatch envelope implemented: `dispatch-envelope.mjs` maps work type to OMC role and model tier, ignores premium-model requests without evidence triggers, caps read-only fanout at 3 and edit fanout at 1.
  - Global Copilot dispatch rule implemented: root `.github/copilot-instructions.md` now tells any Copilot agent when to use cheap explore workers, standard executors, critic/verifier roles, and premium escalation.
  - MCP exposure deferred after critic review: do not present a pseudo-MCP JSON-RPC wrapper as enforcement without a real client/transport proof.
  - Verification: JSON schema tests and Zeus topology route smoke tests.

- [ ] Phase 3 - ACP Copilot enablement
  - Files: `openclaw.json`, ACP docs/skill.
  - What: add `copilot` to allowed ACP agents only after confirming auth, command availability, and scratch smoke-test behavior.
  - Verification: one read-only ACP session in Zeus runs `zeus_session_start` and exits without edits.

- [ ] Phase 4 - Shared event adapter
  - Files: optional Copilot ACP router modeled after `.codex/hooks/zeus-router.mjs`.
  - What: normalize any available Copilot/ACP tool events into `.claude/hooks/dispatch.py` payloads.
  - Verification: fixture tests for Bash/Edit payloads; prove advisory text matches Claude/Codex dispatch output.

- [ ] Phase 5 - Long-task orchestration path
  - Files: `plugin-skills/vscode-copilot-sync/SKILL.md`, session-observer docs if needed.
  - What: define when Copilot delegates to OpenClaw `explore`, `executor`, `critic`, `verifier`, or ACP harness sessions.
  - Verification: one dry-run multi-agent plan where Copilot asks OpenClaw for read-only exploration and receives a bounded result.

## Risks / Non-Goals

- Do not put secrets or Copilot tokens into prompts, instruction files, logs, or evidence. Use Keychain-backed providers only.
- Do not make Copilot a fake Claude Code clone. Native Claude Code hooks, `Task`, and `Team` are not available inside VS Code Copilot.
- Do not downgrade existing blocking PR lifecycle gates. Preserve `dispatch.py` exit 2 as `BLOCKED` where the platform can honor it, while keeping non-blocking advisory hooks fail-open on dispatcher crashes.
- Do not bloat Copilot instruction files. Tool calls and MCP should carry detailed context on demand.
- Do not trust VS Code chat history as durable memory. Use repo docs, OpenClaw sessions, and evidence files.

## Open Questions

- Should Copilot be allowed to edit Zeus directly, or should it default to review/orchestration and delegate implementation to Claude/Codex through OpenClaw?
- Should `copilot` be enabled in ACP `allowedAgents` globally, or only for the Venus/Zeus workspace after a scratch smoke test?
- Should the first implementation target a minimal `.github/instructions/agent-workflow.instructions.md`, or the MCP bridge first so Copilot has real tools before stronger instructions?
