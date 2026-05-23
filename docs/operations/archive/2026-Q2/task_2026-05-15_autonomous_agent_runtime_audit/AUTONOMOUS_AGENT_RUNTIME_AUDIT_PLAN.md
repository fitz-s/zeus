# Autonomous Agent Runtime Audit Plan

Created: 2026-05-15
Status: PLAN-ONLY
Route: `topology_doctor --navigation` admitted this packet under the agent-runtime profile with docs-only write scope.

## Goal

Build a first-principles audit of long-running autonomous agent operation across the full Zeus work ecosystem, not just one Codex live-order runtime. The audit must include Codex, Claude Code, OpenClaw/OMC, Copilot-style adapter surfaces, browser/desktop workflows, GitHub review loops, and non-code high-stakes tasks where the agent can run for hours without human steering.

The output is not a narrative retrospective. The required result is a machine-grounded failure-mode map and antibody plan for delegated intelligence that can continue safely when the human is not actively correcting it.

## Framing Lens: Designed Systems As Possible Obstacles

This audit is symmetric. It must classify our own designed systems (topology
doctor, planning-lock, map-maintenance, packet protocol, packet-directory
admission rule, PR-LOC threshold, critic-gate, SubagentStop, advisor tool,
hook dispatcher fail-open behavior, auto-memory writes, skill auto-load
triggers, OMC tier routing, source/test/script/docs registries) on two axes
at once:

1. Does the system reduce real autonomous failure?
2. Does the system itself create new failure modes by burning context/cost,
   teaching the agent to ignore advisory output, encouraging bypass, masking a
   real signal under noise, or pushing the agent toward "fake completion" to
   escape the gate?

Every governance/safety surface gets a verdict in {`PROTECTIVE`, `NEUTRAL`,
`FRICTION_NET_NEGATIVE`, `MIXED_NEEDS_REDESIGN`} backed by observed runtime
evidence (not aspiration). A system that prevents one rare disaster while
silently corrupting many routine runs is not protective on net.

The audit must also explicitly compare friction across runtimes: a hook that is
near-free in Claude Code may be impossible in Codex, and vice versa. The same
designed rule can be `PROTECTIVE` in one runtime and `FRICTION_NET_NEGATIVE` in
another - this asymmetry is itself a finding.

Scope reminder: the audit covers all real delegated work in this ecosystem -
Zeus live trading is one task class among many. Browser research, manuscript
drafting, finance reporting cron, course/admin work, GitHub review loops,
multi-agent debate, connector-driven side effects (Gmail/Calendar/Docs/GitHub),
and small one-shot edits all count. A finding that applies only to Zeus live
trading must be tagged as such.

## Primary Improvement Track: Topology System

The topology subsystem (`scripts/topology_doctor.py`,
`architecture/task_boot_profiles.yaml`, profile/route registry, planning-lock,
map-maintenance, scope/admission/expansion logic) is the single most active
source of agent-runtime friction observed to date. This audit elevates it from
"one of N audited surfaces" to a parallel improvement track with its own
deliverable.

Operator-observed failure patterns (these are now first-class evidence, not
anecdote):

- `LEXICAL_PROFILE_MISS`: profile lookup is substring/keyword sensitive. A
  task phrased as "SDK method compatibility" did not admit the existing "modify
  v2 venue adapter" profile, even though the file set
  (`src/data/polymarket_client.py`, `src/venue/polymarket_v2_adapter.py`)
  belongs squarely under that profile. Re-phrasing succeeded. The agent was
  forced into a phrasing game rather than a content description.
- `UNION_SCOPE_EXPANSION`: when a single coherent change touches files that
  individually belong to different profiles (fix + tests + recovery), admission
  fires `scope_expansion` and refuses. The agent then artificially slices the
  change to clear admission, and the cross-file invariant view is lost. This
  is the opposite of the design intent of the planning gate.
- `SLICING_PRESSURE`: repeated admission failure trains the agent to ship
  smaller-than-correct units. Slice count rises, per-slice review burden rises,
  cross-slice coherence drops, and the human pays the cost as more PRs to
  triage.
- `PHRASING_GAME_TAX`: every admission attempt costs orchestrator tokens,
  one or more bash invocations, and operator attention. Three admission tries
  before a successful route is now common. The dominant cost of small docs
  edits is admission, not the edit itself.
- `INTENT_ENUM_TOO_NARROW`: `--intent edit_existing` is rejected as
  `typed_intent_invalid`. The accepted enum requires the agent to know which
  canonical phrase the topology uses internally, not to describe the actual
  intent. (Captured live during this plan's own refinement, 2026-05-15.)
- `CLOSED_PACKET_STILL_LOAD_BEARING`: closed packets still own truth that
  current runtime depends on; the topology has no signal for "this packet is
  closed but its evidence is still authoritative."
- `ADVISORY_OUTPUT_INVISIBILITY`: the agent's tool result frequently shows
  `ok=True` while the JSON body holds `advisory_only` warnings the agent
  never surfaces. Advisory becomes silent permission.

Required topology-track outputs (in addition to the audit deliverables):

- `TOPOLOGY_FRICTION_INCIDENTS.md`: a chronological log of every observed
  admission failure, scope_expansion, slicing forced by admission, and
  phrasing-game retry. Each row carries: task description, files involved,
  failed admission attempts (with phrases), final admission attempt that
  worked, time/token cost, and whether slicing was required.
- `TOPOLOGY_IMPROVEMENT_SPEC.md`: a proposed redesign covering (at minimum)
  semantic profile matching, profile composition / union admission, intent
  enum widening, advisory-vs-blocking tool-output normalization, profile
  coverage map (which file paths have no profile and therefore always
  escalate), and an operator-readable "next-best profile and what phrase
  would admit it" surface on every miss. Spec only - implementation is a
  follow-up packet.
- `TOPOLOGY_GUARDRAILS_THAT_STAY.md`: the explicit list of topology
  behaviors that must NOT be loosened by the improvement spec - planning
  evidence, dirty-worktree refusal, runtime-truth registry separation,
  forbidden-file blocks, etc. The improvement track must reduce friction
  WITHOUT removing real-damage guards.

Track principle: the goal is not "less topology." The goal is "topology that
correctly distinguishes real-damage prevention from phrasing-game
bureaucracy, and makes the former cheap while the latter disappears."

## Non-Goals

- Do not change source, hooks, launchd jobs, live DBs, credentials, or production state in this packet.
- Do not claim Codex and Claude Code are equivalent runtimes. Model their different tool streams, hook semantics, subagent semantics, context behavior, and failure modes separately.
- Do not collapse "health OK", "tests passed", "critic approved", or "reviewers appeared" into completion proof.
- Do not use one live-order incident as the whole scope. It is one evidence sample inside a larger runtime-system audit.
- Do not turn every friction point into immediate implementation. The audit must rank which friction is protective, which is bureaucratic, and which causes real autonomous failure.
- Do not rank Codex versus Claude Code as "better" or "worse." Both are real runtimes that the user actively delegates to; the audit models their differences and chooses the right tool per task class, not a winner.
- Do not assume the user always supervises. The reference operating mode is unattended hours-to-days work; supervised flows are a special case, not the baseline.

## Runtime Surfaces To Model

### Codex Runtime

- Root/project instruction chain: root Codex rules, Zeus `AGENTS.md`, scoped `AGENTS.md`, skills, memory, and tool availability.
- Native subagents: spawn budget, role/model routing, close semantics, context isolation, partial-spawn behavior, and completed-but-not-closed slot pressure.
- Codex hooks/adapters: `.codex/hooks.json`, `.codex/hooks/**`, shared dispatcher paths, and fresh-session vs already-running-session hook loading.
- Desktop capabilities: Browser, Chrome, Computer Use, app connectors, automations, and screenshot/document/spreadsheet/presentation tools.
- Memory and compaction: memory lookup, rollout summaries, context compression, stale memory risk, and memory citation boundaries.

### Claude Code Runtime

- Project entrypoint: `.claude/CLAUDE.md` routes Claude Code back to root `AGENTS.md` and review doctrine.
- Hook stream: `.claude/settings.json` wires `PreToolUse`, `PostToolUse`, and `SubagentStop` through `.claude/hooks/dispatch.py`.
- Hook semantics: most hooks are advisory/fail-open, while PR-size and merge-comment hooks carry blocking intent when the dispatcher can evaluate them.
- Claude subagents and skills: `.claude/agents/**`, `.claude/skills/**`, role prompts, safety-gate/verifier behavior, and hidden same-tier advisor-call risk.
- Runtime state: `.claude/logs/**`, `.claude/orchestrator/**`, `.claude/worktrees/**`, `scheduled_tasks.lock`, and any stale state that can influence agent interpretation.

### OpenClaw / OMC / Team Runtime

- Task vs Team semantics: direct bounded `Task` dispatch, persistent team/mailbox state, SendMessage overhead, and worker-output visibility.
- ACP/runtime identity boundaries: harness identity (`claude`, `codex`, possible future `copilot`) must stay separate from OMC role identity (`explore`, `executor`, `critic`, `verifier`).
- Long-running coordination: session observers, run state, child lifecycle evidence, worker cleanup, mailbox drift, and cross-agent file ownership.

### Copilot / Adapter Surfaces

- Existing packet: `docs/operations/task_2026-05-09_copilot_agent_sync/PLAN.md`.
- Instruction-only limitations: Copilot cannot be assumed to have Claude Code `PreToolUse`, `PostToolUse`, `SubagentStop`, Task/Team, or model-tier selection.
- Adapter pattern: when a harness lacks native hooks, it must call explicit route/check/verify tools or delegate to OpenClaw rather than simulate parity in prose.

### Browser, Desktop, And Non-Code Work

- Browser-authenticated tasks, course/admin research, manuscript workflows, email/calendar/GitHub, and local file workflows.
- High-stakes non-code claims must still have authority/proof boundaries: official sources, screen/page evidence, exact uploaded/downloaded artifacts, and no memory-only conclusion when facts can drift.
- Visual or document workflows need render/screenshot/inspection proof, not just generated files.

## Core Questions

1. What does each runtime treat as authority, and what is only derived context?
2. Where can an agent mutate state before it has durable proof of intent, route admission, or side-effect boundary?
3. Where can a health signal be mistaken for task completion?
4. Where can stale packet evidence, hooks, worktrees, memory, or launch configuration become current truth by accident?
5. Where can a child agent, reviewer, or critic output be over-trusted after context drift?
6. What happens after 24h, 72h, 7d, and 30d without human steering?
7. Which gates prevent real damage, and which gates mainly consume context or redirect attention?
8. Which failure categories need antibodies as code/tests/checkers rather than more instructions?

## Method

### Phase 0 - Freeze Scope And Evidence Policy

Read-only only.

Inputs:
- `AGENTS.md`
- `.claude/CLAUDE.md`
- `.claude/settings.json`
- `.codex/hooks.json`
- `docs/operations/AGENTS.md`
- `docs/operations/task_2026-05-09_copilot_agent_sync/PLAN.md`
- relevant memory/Chronicle summaries only when they identify runtime facts that must be re-checked

Outputs:
- Runtime inventory table: surface, owner, authority rank, mutation ability, stale-risk, verifier.
- Evidence policy: every finding must be tagged `direct evidence`, `current sample`, `memory-derived`, or `inference`.

Stop if:
- Any step requires writing source, state DBs, live config, launchd plist, hook config, or credentials.

### Phase 1 - Task Universe Map

Build a task taxonomy across all work the user actually delegates:

- Live trading and venue side effects
- Forecast/data ingestion and DB authority
- PR review, review-thread repair, CI, merge, and release
- Topology/governance/docs packet work
- Cleanup/refactor/deslop
- Browser/desktop/admin/course/manuscript workflows
- Email/calendar/GitHub/app-connector tasks
- Long-running monitors, reminders, scheduled checks, and follow-up automations
- Multi-agent research, critic, verifier, and team orchestration

For each task class record:
- Entry trigger
- Canonical authority
- Allowed side effects
- Required proof before completion
- Common false completion
- Recovery path after partial failure
- Runtime-specific hazard in Codex
- Runtime-specific hazard in Claude Code

### Phase 2 - Authority And Control Plane Audit

Audit cross-runtime authority surfaces:

- Instruction chain: system/developer/user, root `AGENTS.md`, project/scoped `AGENTS.md`, `.claude/CLAUDE.md`, skills, prompts, and memory.
- Routing: topology digest, route cards, planning-lock, map-maintenance, script/test/docs registries.
- Runtime state: launchd, tmux/team state, `.claude/orchestrator`, Codex native subagent ledger, automation state, browser tab state.
- Source truth: Git worktrees/branches, PR head/base, GitHub checks/reviews, local dirty state.
- Data truth: canonical DBs, event journals, chain/CLOB facts, derived JSON/status files.
- Evidence truth: receipts, critic approvals, screenshots, rendered docs, logs, proof checkers.

The audit must explicitly classify each surface:

- `AUTHORITY`: can grant permission or define truth.
- `DERIVED_CONTEXT`: useful but cannot authorize action.
- `RUNTIME_SCRATCH`: local/transient, not durable truth.
- `HISTORICAL`: can explain but not control current behavior.
- `HAZARDOUS_IF_STALE`: must have freshness or commit/PID/cwd proof.

### Phase 2.5 - Concrete Runtime Probes (Not Doc-Only)

Doc reading alone cannot answer "what does the agent actually do at runtime."
This phase forces probe-based evidence before any verdict. All probes are
read-only and side-effect-free.

Required probes:

- Topology friction probe (extended): run `topology_doctor.py --navigation`
  against at least eight representative tasks - one trivial doc edit, one
  Zeus pricing edit, one PR review-comment fix, one new packet creation, one
  cross-runtime hook edit, one paraphrased-intent edit (same files, different
  natural-language description), one union-scope edit (files spanning two
  profiles for one coherent change), and one closed-packet revival edit.
  Per task capture: routing time, advisory-vs-blocking ratio, allowed_files
  width, admission-attempts-to-success count, phrasing variants tried, whether
  scope_expansion fired, whether slicing was forced, and whether the output
  materially changed agent decisions vs would-have-done. The eight rows feed
  `TOPOLOGY_FRICTION_INCIDENTS.md` directly.
- Topology coverage probe: enumerate `architecture/task_boot_profiles.yaml`
  profiles vs the actual file paths in `src/`, `scripts/`, `docs/`, and
  `architecture/`. Compute: percentage of tracked paths covered by at least
  one profile, list of paths with NO profile (always-escalate set), list of
  profiles with no recent hit (dead profile set), and admission false-block
  rate per profile (how often the profile was the right answer but admission
  required a different phrase to find it).
- Hook stream probe: enumerate `.claude/settings.json` and `.codex/hooks.json`
  hook entries; classify each as `BLOCKING_HARD`, `ADVISORY_VISIBLE`,
  `ADVISORY_FAIL_OPEN_SILENT`, or `UNKNOWN`. Sample one event per category
  (synthetic input, no real action) and capture dispatcher response and what
  the agent would actually see in its tool result.
- Subagent registry probe: dispatch one read-only subagent per available tier
  (haiku/sonnet/opus) and per role (executor/critic/explore/verifier) in
  Claude Code; record the actual tool registry each receives, whether `Task`
  is available, and what the orchestrator sees on close.
- Memory drift probe: list current auto-memory entries, sample three, and
  re-verify each against current code/state. Record stale-rate and any memory
  that contradicts current law.
- Skill auto-load probe: enumerate skills with auto-load triggers, run a
  representative prompt against each trigger keyword, and record which skills
  fire and what they inject into context.
- Packet-protocol probe: pick three closed packets, check whether the runtime
  still depends on their evidence (imports, config refs, hook env, launchd
  paths). Anything still load-bearing in a "closed" packet is a finding.
- Cost probe: for one representative orchestration session (any past trace),
  estimate tier mix and identify cheapest-tier-correct alternative. The
  orchestrator-offload-lookups pattern is the baseline; deviations are
  findings.
- Codex parity probe: for each Claude-only safety surface (PreToolUse,
  PostToolUse, SubagentStop, Task/Team, advisor tool, skill auto-load),
  document the actual Codex-side equivalent or explicit gap.

Outputs feed into Phase 3.5 verdicts and Phase 4 adversarial scenarios. No
verdict in later phases may be issued without at least one probe-derived
evidence row in addition to doc citations.

### Phase 3 - Long-Run Autonomy Scenarios

Run tabletop simulations for unattended operation:

1. 24h PR review loop: reviewers/checks appear late, comments contradict local tests, monitor automation exists.
2. 72h live-ops loop: daemon restarts, data advances, CLOB calls timeout, command remains unknown, agent context compacts twice.
3. 7d data pipeline loop: upstream 429, forecast run freshness diverges, derived status says OK, DB max timestamp is stale.
4. 30d governance drift loop: packets close/archive, runtime still depends on old packet evidence, topology global debt grows.
5. Multi-agent swarm loop: child slots fill, workers finish but are not closed, stale worker conclusions are reused after branch changes.
6. Browser/admin loop: page auth expires, tab state changes, official policy changes, agent reports from old screen/memory.
7. Manuscript/course loop: artifacts accumulate, versions drift, writer/reviewer/source-validation roles collapse.
8. Connector/app loop: Gmail/GitHub/Calendar/Docs action is prepared, but send/merge/update side effect is not separately confirmed.

Each scenario must produce:
- Expected safe behavior
- Likely Codex failure
- Likely Claude Code failure
- Detection signal
- Required stop condition
- Antibody candidate

### Phase 3.5 - Designed-System Obstacle Audit

For each designed governance/safety system, produce one row:

```
system: <topology_doctor / planning-lock / map-maintenance / packet-protocol /
         packet-dir-admission / PR-LOC-threshold / critic-gate / SubagentStop /
         advisor-tool / hook-dispatcher-fail-open / auto-memory-write /
         skill-auto-load / OMC-tier-routing / source-test-script-docs-registry /
         worktree-isolation / commit-per-phase-rule>
designed_to_prevent: <one line>
runtime_costs:
  context_tokens: <observed or estimated; cite probe>
  dollar_cost: <tier mix observed; cite probe>
  latency: <observed seconds added per invocation>
  worker_rounds: <extra subagent or critic spawns triggered>
observed_bypass_behavior: <agent learned to ignore? routed around? lazy-skip?>
observed_false_success: <APPROVE on bad change? PASS on stale snapshot?>
observed_false_block: <legitimate change blocked? agent abandoned/faked?>
runtime_asymmetry:
  claude_code: <effect>
  codex: <effect>
  copilot_or_other: <effect or "n/a">
verdict: PROTECTIVE | NEUTRAL | FRICTION_NET_NEGATIVE | MIXED_NEEDS_REDESIGN
repair_angle: tighten | loosen | replace_with_structural | delete |
              keep_as_is_documented
evidence_anchors: <probe IDs from Phase 2.5 + file:line citations>
```

Mandatory bias check: if every system in the row-set comes back `PROTECTIVE`,
the audit has failed. The historical memory record (memory notes on critic
rubber-stamp, hook fail-open misread, advisory output ignored, packet-filename
admission errors) makes a 100% PROTECTIVE verdict empirically implausible.

At least one row must propose `delete` or `replace_with_structural` or
explicitly justify why no such candidate exists in this audit window.

### Phase 4 - Adversarial Failure Angles

Use deliberately awkward angles, not just normal happy-path bugs:

- Health OK while proof checker FAIL.
- Plan APPROVE treated as implementation/live authority.
- `REVISE` treated as pass.
- `advisory_only` topology output ignored.
- `allowed_files=[]` followed by implementation anyway.
- Native child-agent budget exhausted by completed-but-not-closed children.
- Claude Code advisory hook text treated as hard block, or hard block treated as advisory.
- Same-tier advisor calls hidden inside premium subagents.
- Old packet evidence deleted by cleanup while runtime still imports or checks it.
- Runtime depends on `.omx`, `.omc`, `.claude/logs`, or browser tab state.
- Launchd still points at old cwd/PYTHONPATH after branch merge.
- Command/event journal writes before pre-SDK failure and pollutes unknown-side-effect state.
- External API returns missing/zero field and local code records it as chain truth.
- DB write is uncommitted in process but read as truth by same process.
- Official docs or laws change after memory snapshot.
- Human-language goal persists after context compaction, but negative constraints disappear.
- The agent "fixes" the nearest blocker and forgets the original completion definition.
- A fail-open hook prints a warning the agent never surfaces; the warning is later cited as "system did not block, therefore approved."
- Advisory topology output (`advisory_only`, `allowed_files=[]`) is treated as implicit permission rather than a route refusal.
- Critic returns APPROVE on a snapshot the executor has since invalidated; the orchestrator carries the approval forward.
- The PR-LOC threshold pushes the agent to bundle unrelated changes into one PR to clear the floor; reviewers then miss scope drift inside the bundle.
- A skill auto-load fires on a keyword in the user's prompt and silently re-routes the agent into a methodology the user did not ask for.
- The packet protocol forces a multi-page PLAN.md for a five-line typo; the agent learns to skip planning for "small" things and later skips it for a real change.
- Memory record contradicts current code; the agent applies the memory rule and then declares "verified per memory" without re-checking the file.
- A subagent reports `result:` with no artifact path; the orchestrator never opens the artifact and propagates the success claim.
- Worker tier mismatch: opus orchestrator delegates a semantic-reasoning task to haiku because the locate heuristic mis-fires; quality drops silently.
- Auto-memory writes the wrong generalization from one session; future sessions cite the memory as authority.
- Worktree isolation forces every edit through a worktree; the worktree drifts from main, hooks load from main, behavior diverges silently.

### Phase 5 - Proof Taxonomy

Define proof levels for every task class:

- `L0 observed`: local observation exists, not enough to act.
- `L1 routed`: topology/authority route is admitted.
- `L2 implemented`: diff exists and targeted tests pass.
- `L3 integrated`: correct branch/worktree/runtime loaded the implementation.
- `L4 runtime observed`: live process or external service produced fresh evidence.
- `L5 side-effect proven`: durable event/DB/remote authority proves the action happened or safely did not happen.
- `L6 closed`: recovery, monitoring, cleanup, and user-facing report are consistent.

No final report may claim a higher level than the weakest required proof for the task.

### Phase 6 - Runtime-Specific Findings

For Codex:
- Native subagent budget discipline.
- Tool availability and deferred plugin discovery.
- Memory and context compaction.
- Automation heartbeat/cron behavior.
- Browser/desktop tool side effects.
- Git staging/commit directives and local dirty state.

For Claude Code:
- Hook dispatcher behavior and fail-open/fail-closed distinction.
- Task vs Team lifecycle.
- `.claude/agents` tool permissions and hidden advisor/cost visibility.
- SubagentStop and phase-close behavior.
- Worktree/orchestrator/log state.
- Whether Claude runtime can enforce or only advise route/gate decisions.

For OpenClaw/OMC:
- Durable team state vs direct task output.
- Role/model routing.
- Session-observer visibility.
- Worker cleanup and branch/file ownership.

For Copilot/adapters:
- Instruction-only limits.
- Tool bridge requirements.
- Adapter event payload availability.
- Explicit delegation to OpenClaw when native semantics are missing.

### Phase 7 - Antibody Backlog

Each finding must map to one of:

- Topology profile/test
- Proof checker
- Relationship test
- Hook/adapter normalization
- Runtime monitor
- State-machine grammar
- Branch/worktree invariant
- Stale artifact detector
- Report schema
- Memory/compaction handoff rule
- Human escalation rule

The backlog must avoid vague "be careful" items. Every high-severity item needs a testable owner and verification command or a concrete reason it cannot yet be automated.

## Deliverables

1. `AUTONOMOUS_AGENT_RUNTIME_AUDIT_REPORT.md`
   - Ranked findings with evidence, confidence, runtime scope, and blast radius.
2. `AUTONOMOUS_AGENT_RUNTIME_FAILURE_MATRIX.md`
   - Task class x runtime x authority x side effect x proof x recovery matrix.
3. `AUTONOMOUS_AGENT_RUNTIME_SCENARIOS.md`
   - 24h / 72h / 7d / 30d unattended tabletop scenarios and expected safe behavior.
4. `AUTONOMOUS_AGENT_RUNTIME_ANTIBODY_BACKLOG.md`
   - Concrete proposed tests/checkers/topology/hook/runtime repairs.
5. `AUTONOMOUS_AGENT_RUNTIME_FRICTION_LEDGER.md`
   - One row per designed governance/safety system with the Phase 3.5 schema:
     designed-to-prevent, runtime cost, observed bypass, observed false
     success, observed false block, runtime asymmetry, verdict, repair angle,
     evidence anchors. Required output of Phase 3.5; not optional.
6. `AUTONOMOUS_AGENT_RUNTIME_PROBE_LOG.md`
   - Raw probe outputs from Phase 2.5 (commands, timestamps, captured
     payloads, agent-visible vs operator-visible content) so later sessions
     can re-run the same probes and detect drift.
7. `TOPOLOGY_FRICTION_INCIDENTS.md`
   - Chronological incident log: task description, files, failed admission
     attempts (verbatim phrases), final admitting phrase, attempts-to-success,
     time/token cost, scope_expansion flag, slicing-forced flag.
8. `TOPOLOGY_IMPROVEMENT_SPEC.md`
   - Proposed redesign (spec, not implementation): semantic profile matching,
     profile composition / union admission, intent enum widening,
     advisory-vs-blocking output normalization, profile coverage map,
     operator-readable next-best-profile suggestion on every miss.
9. `TOPOLOGY_GUARDRAILS_THAT_STAY.md`
   - Explicit non-negotiable list: planning evidence, dirty-worktree refusal,
     runtime-truth registry separation, forbidden-file blocks, etc. The
     improvement spec must satisfy this list as a constraint.
10. Optional critic review file
    - Required before any implementation follow-up claims this plan is sufficient.

These deliverables are plan outputs only. They do not authorize source edits or runtime mutations unless a later packet freezes an implementation slice.

## Acceptance Criteria

- The audit covers Codex and Claude Code as distinct real runtimes, with separate tool/hook/subagent/state models.
- The audit covers non-code and browser/desktop tasks, not only Zeus live trading.
- Every high-severity finding cites current file, command output, runtime artifact, or explicitly labeled memory-derived evidence.
- Every completion-proof recommendation distinguishes health, readiness, implementation, deployed runtime, side effect, and closeout.
- At least 12 adversarial scenarios are analyzed, including context compaction, stale packet cleanup, partial subagent success, delayed reviewer feedback, hidden advisor calls, and external API false-zero/missing-field behavior.
- At least 10 antibody candidates are concrete enough to become a topology rule, test, checker, hook, or report schema.
- The report states which gates are protective and which are bureaucracy/noise, with a criterion for moving a gate between categories.
- The friction ledger covers every designed governance/safety surface listed in the Framing Lens (no silent omissions). Each row carries a verdict and at least one probe-derived evidence anchor.
- At least one ledger row proposes `delete` or `replace_with_structural`, OR the report explicitly justifies why no such candidate exists in this audit window with reasoning that survives critic challenge.
- The report cites at least three concrete cases where a designed system caused agent failure (false completion, scope drift, hidden bypass, abandoned task), or explicitly records "no such case found in N samples examined" with N stated.
- Codex/Claude/(future) Copilot asymmetry is modeled per task class. No recommendation may assume cross-runtime parity that the probe log does not back.
- Non-Zeus task classes (browser/admin/manuscript/connector/finance-cron) are represented in the failure matrix and at least two scenarios. A Zeus-only audit is not acceptance.
- `TOPOLOGY_FRICTION_INCIDENTS.md` carries at least eight incident rows from the Phase 2.5 extended topology probe, each with a verbatim failing phrase, an admitting phrase, and an attempts-to-success count.
- `TOPOLOGY_IMPROVEMENT_SPEC.md` proposes concrete repairs for each named friction pattern (`LEXICAL_PROFILE_MISS`, `UNION_SCOPE_EXPANSION`, `SLICING_PRESSURE`, `PHRASING_GAME_TAX`, `INTENT_ENUM_TOO_NARROW`, `CLOSED_PACKET_STILL_LOAD_BEARING`, `ADVISORY_OUTPUT_INVISIBILITY`). A pattern with no proposed repair is acceptable only with explicit reasoning for why no repair is currently safe.
- `TOPOLOGY_GUARDRAILS_THAT_STAY.md` lists at least five guardrails the improvement spec is forbidden from loosening, each tied to a concrete real-damage scenario from Phase 3 or Phase 4.
- The improvement spec contains an explicit "next-best profile and admitting-phrase suggestion" output design - admission failures must become diagnostic, not punitive.
- Topology coverage probe results: percentage of tracked paths covered, always-escalate path list, and dead-profile list are reported. Missing = incomplete.
- A critic pass returns `APPROVE`; `REVISE` is not accepted as completion.

## Verification Plan

Plan-file verification:

```bash
python3 scripts/topology_doctor.py --navigation --task "create plan packet: autonomous agent runtime deep analysis for Codex and Claude Code real runtimes across long-running unattended tasks" --intent create_new --write-intent docs --files docs/operations/task_2026-05-15_autonomous_agent_runtime_audit/AUTONOMOUS_AGENT_RUNTIME_AUDIT_PLAN.md docs/operations/AGENTS.md --json
python3 scripts/topology_doctor.py --planning-lock --changed-files docs/operations/task_2026-05-15_autonomous_agent_runtime_audit/AUTONOMOUS_AGENT_RUNTIME_AUDIT_PLAN.md docs/operations/AGENTS.md --plan-evidence docs/operations/task_2026-05-15_autonomous_agent_runtime_audit/AUTONOMOUS_AGENT_RUNTIME_AUDIT_PLAN.md
python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files docs/operations/task_2026-05-15_autonomous_agent_runtime_audit/AUTONOMOUS_AGENT_RUNTIME_AUDIT_PLAN.md docs/operations/AGENTS.md
git diff --check
```

Audit execution verification:

```bash
python3 scripts/topology_doctor.py --navigation --task "read-only autonomous agent runtime audit execution" --intent audit --write-intent read_only --files docs/operations/task_2026-05-15_autonomous_agent_runtime_audit/AUTONOMOUS_AGENT_RUNTIME_AUDIT_PLAN.md
```

Critic verification:

- A reviewer must attack scope completeness, Codex/Claude runtime separation, unattended-run edge cases, false-completion controls, and whether each proposed antibody is testable.
- The critic must return `APPROVE` before the plan is considered ready for execution.

## Stop Conditions

- Stop before editing `.claude/settings.json`, `.codex/hooks.json`, hook dispatchers, topology source, runtime scripts, or any `src/**` file.
- Stop if a proposed runtime rule would create a second authority plane instead of a generated context/checking surface.
- Stop if the audit cannot distinguish current evidence from memory-derived evidence.
- Stop if a finding depends on private credentials, production mutation, or external side effects.
- Stop if the plan starts optimizing one runtime by weakening another runtime's safety boundary.
- Stop if the audit collapses into a Codex-vs-Claude superiority claim. Both are real runtimes; differences are modeled, not ranked.
- Stop if a "designed system is harmful" finding lacks a probe-derived bypass, false-success, false-block, or runtime-cost row. Hand-wavy "feels bureaucratic" is not a finding.
- Stop if the friction ledger returns 100% PROTECTIVE for every designed system. That outcome contradicts existing memory-record evidence and indicates the audit is rubber-stamping rather than auditing.
- Stop if the audit treats supervised flows as the baseline. Unattended hours-to-days operation is the reference operating mode.
- Stop if `TOPOLOGY_IMPROVEMENT_SPEC.md` proposes "make admission optional," "default to admit on miss," "skip planning-lock," or any change that converts a real-damage guard into a friction reduction. Reduce phrasing-game cost without removing real-damage prevention; if you cannot do both, ship neither.
- Stop if the topology improvement spec is written as implementation diff in this packet. The spec is design only; implementation belongs to a follow-up packet that re-enters topology routing on its own evidence.
- Stop if any topology friction pattern is declared "fixed" without a probe row showing the failing-then-admitting transition.

## Initial Evidence Anchors

- `AGENTS.md` requires topology routing and defines the Zeus money/truth path.
- `.claude/CLAUDE.md` explicitly routes Claude Code back to root `AGENTS.md` and warns graph tools are derived context.
- `.claude/settings.json` shows the actual Claude Code hook stream and advisory/blocking distinction.
- `docs/operations/task_2026-05-09_copilot_agent_sync/PLAN.md` records prior cross-harness sync design and Copilot limitations.
- Memory notes record prior failures around packet filename admission, plan-only critic approval being over-read, generic/advisory topology output being ignored, dirty worktree execution, hidden advisor calls, and deterministic source-change reporting.
- Live probe captured during this plan's own refinement (2026-05-15): `topology_doctor --navigation --intent edit_existing` returned `typed_intent_invalid` and `admission=ambiguous` for a docs-only edit of this plan file; admission only succeeded when the task phrase was rewritten to match the canonical enum `operation planning packet` and `--intent create_new` was used. This is a Phase 2.5 / Phase 3.5 candidate row showing the topology doctor refusing a natural English intent for a docs-only refinement and forcing the agent to guess the canonical profile name. Treat as primary evidence, not anecdote. Friction code: `LEXICAL_PROFILE_MISS` + `INTENT_ENUM_TOO_NARROW`.
- Operator-reported incident (2026-05-15): a venue-adapter task phrased as "SDK method compatibility" failed to admit the existing modify-v2-venue-adapter profile despite the file set (`src/data/polymarket_client.py`, `src/venue/polymarket_v2_adapter.py`) belonging to it. A previous attempt with the same files admitted only because tests/recovery files were mixed in, but that triggered `scope_expansion` and refused the union. The operator had to re-enter with the exact profile-canonical task name to obtain minimal admission. Friction codes: `LEXICAL_PROFILE_MISS` + `UNION_SCOPE_EXPANSION` + `PHRASING_GAME_TAX`. This incident is the canonical motivating example for the Topology Improvement Track and must appear verbatim in `TOPOLOGY_FRICTION_INCIDENTS.md` as row 1.

## Execution Handoff Shape

When this plan is executed, use bounded lanes:

- Codex runtime lane: read-only inventory of Codex instructions, hooks, native subagent semantics, automations, memories, and desktop tools.
- Claude Code lane: read-only inventory of `.claude/**`, hook logs, agents, skills, Task/Team assumptions, and settings.
- Zeus task-class lane: map live trading, data, PR, governance, cleanup, browser/admin, and document/manuscript workflows.
- Probe lane: execute Phase 2.5 runtime probes (read-only) and produce
  `AUTONOMOUS_AGENT_RUNTIME_PROBE_LOG.md`. Probe lane must be staffed before
  Phase 3.5 begins; verdicts without probe rows are rejected.
- Friction lane: own Phase 3.5 ledger; one row per designed system with the
  schema enforced. This lane explicitly looks for the system-as-obstacle
  failure mode, not just the system-as-protection mode.
- Non-code lane: enumerate browser/admin/manuscript/connector/cron task
  classes, route at least two into Phase 3 scenarios so the audit cannot
  silently degrade to Zeus-only coverage.
- Critic lane: attack false-completion, stale authority, unattended-run
  assumptions, AND any Phase 3.5 row that rubber-stamps a system as
  PROTECTIVE without surfacing observed costs or bypass paths.
- Topology-track lane: own
  `TOPOLOGY_FRICTION_INCIDENTS.md`,
  `TOPOLOGY_IMPROVEMENT_SPEC.md`, and
  `TOPOLOGY_GUARDRAILS_THAT_STAY.md`. Run the extended Phase 2.5 topology
  probe set, confirm each named friction pattern is reproduced before any
  repair is proposed, and cross-check every proposed repair against the
  guardrails-that-stay list. This lane reports to the leader, not directly
  to the user, so the audit and the topology track stay synthesized.

The leader owns synthesis. Child agents may gather file/line evidence only; they must not approve the plan they helped write.
