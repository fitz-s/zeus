# Mainline Implementation Plan

Date: 2026-04-29
Branch: `agent-runtime-upgrade-2026-04-29`
Status: mainline design plan; not authority by placement

## 0. Executive Frame

This plan treats topology/graph as an **agent runtime system**, not a docs
program and not a governance expansion.

The mainline goal is:

> A cold or resumed agent can receive a task, classify it, get the smallest
> sufficient context, act inside admitted scope, verify the exact claim, and
> leave a compact receipt without turning normal work into bureaucracy.

Current branch status:

- P0 spine is implemented: route cards, typed intent, risk tiers, role context
  packs, claim-scoped graph degradation, packet evidence, work log, receipt.
- This plan defines the full continuation path from P0 spine to mature runtime.

Non-negotiable constraint:

- Every new rule must reduce a decision, prevent a proven failure, or feed a
  machine check. If it does none of those, it is process weight and should not
  ship.

## 1. Runtime Mental Model

The runtime is a staged contract:

```text
task input
  -> typed classification
  -> admission / route card
  -> semantic boot
  -> role-specific context
  -> execution or read-only exploration
  -> claim-scoped gates
  -> receipt / artifact lifecycle
  -> closeout / merge review
```

Each stage answers one runtime question:

| Stage | Question | Output |
| --- | --- | --- |
| Task input | What is the agent being asked to do? | task event |
| Classification | What class of work is this? | `intent`, `task_class`, `write_intent` |
| Admission | What may change, and what must stop? | `route_card` |
| Semantic boot | What domain facts cannot be inferred from code shape? | proof questions |
| Context | What does this role need, and no more? | role context pack |
| Execution | What concrete files can move? | changed files |
| Verification | Which claims are proven? | gate budget result |
| Persistence | Can the next agent resume? | work log + receipt |
| Closeout | Is the packet done without hidden risk? | closeout result |

## 2. Design Principles

1. **Admission is authority; UX is guidance.**
   `admission.status` remains the write-authority signal. Route cards,
   context packs, and graph appendices are generated guidance.

2. **Typed before textual.**
   Explicit `intent`, `task_class`, and `write_intent` must outrank phrase
   scoring, but must never bypass forbidden patterns or planning lock.

3. **Block claims, not tasks.**
   A stale graph blocks graph-impact claims. A stale current fact blocks
   current-fact claims. It should not block unrelated docs/tooling work.

4. **Progressive disclosure.**
   A T0 explorer should see a small route card. A T3 architecture edit should
   see packet evidence and closeout requirements. The default path stays short.

5. **Role-shaped context.**
   Explorer, executor, critic, and verifier need different context. One giant
   packet is not a runtime.

6. **Receipts over diaries.**
   Completion evidence should be compact, structured, and resumable. Long
   narrative belongs in packet evidence only when it explains a decision.

7. **No hidden authority promotion.**
   `.omx`, graph output, package analysis, and external research remain
   evidence until extracted into manifests, tests, source, or authority docs.

8. **Engineering over prose.**
   If a workflow matters repeatedly, it should be represented in CLI output,
   schema, tests, or manifest checks.

## 3. Target Architecture

### 3.1 Inputs

The runtime should accept a normalized task event:

```json
{
  "task": "human task text",
  "files": ["path"],
  "intent": "digest profile id or null",
  "task_class": "semantic boot profile id or null",
  "write_intent": "read_only|edit|apply|live|production",
  "role": "explorer|executor|critic|verifier",
  "claims": ["optional completion claims"],
  "artifact_inputs": ["optional evidence paths"]
}
```

Current P0 support:

- `--intent`
- `--task-class`
- `--write-intent`
- role context packs

Future support:

- explicit `--role`
- explicit `--claim`
- explicit `--artifact-input`
- JSON input file for multi-agent handoff

### 3.2 Classification

Classification has two independent axes:

| Axis | Examples | Owner |
| --- | --- | --- |
| Digest intent | `topology graph agent runtime upgrade`, `modify topology kernel` | `architecture/topology.yaml` |
| Semantic task class | `agent_runtime`, `graph_review`, `settlement_semantics` | `architecture/task_boot_profiles.yaml` |
| Write intent | `read_only`, `edit`, `apply`, `live`, `production` | CLI/runtime contract |

Design rule:

- Digest intent decides the profile.
- Semantic task class decides proof questions.
- Write intent decides risk tier.
- Admission decides write permission.

### 3.3 Route Card

Route card is the first-screen runtime primitive.

Required fields:

- `authority_status`
- `mode`
- `task`
- `profile`
- `intent`
- `task_class`
- `write_intent`
- `admission_status`
- `risk_tier`
- `gate_budget`
- `next_action`
- `admitted_files`
- `out_of_scope_files`
- `forbidden_hits`
- `hard_stops`
- `claim_scope`

Route card is successful when an agent can decide the next action in under
30 seconds without reading appendices.

### 3.4 Risk Tiers

| Tier | Runtime class | Required budget |
| --- | --- | --- |
| T0 | read-only orientation | route card only |
| T1 | narrow docs/tests/tool-local work | route card + focused checker |
| T2 | source/cross-module behavior | route card + scoped tests + relationship proof |
| T3 | architecture/governance/schema/runtime tooling | packet plan + planning lock + focused gates + receipt |
| T4 | live side effect or production data mutation | explicit operator go + dry run + apply guard + rollback |

Tier escalation rules:

- touching `architecture/**`, root routers, topology doctor, or control planes
  escalates to T3
- touching live side effects or production DB mutation escalates to T4
- adding process without a claim at risk is not a valid escalation

### 3.5 Claims

The runtime should eventually track explicit claims:

```json
{
  "id": "graph_impact_validated",
  "depends_on": ["code_review_graph"],
  "required_gates": ["code_review_graph_status"],
  "blocks_if_failed": true
}
```

P0 implements claim-scoped graph behavior as policy text and closeout metadata.
P1/P2 should turn this into a machine-checkable claim model.

### 3.6 Role Context Packs

| Role | Runtime job | Must see | Must not see by default |
| --- | --- | --- | --- |
| explorer | map files and unknowns | route card, inspect-first, stop triggers | edit instructions |
| executor | implement admitted scope | allowed files, gates, rollback, tests | global unrelated debt |
| critic | falsify completion claim | hard stops, proof claims, attack order | long narrative unless needed |
| verifier | prove closeout | claims, evidence, residual risks | implementation speculation |

Role packs are generated context, not authority.

### 3.7 Artifact Lifecycle

Runtime artifacts follow this flow:

```text
.omx scratch
  -> packet evidence index
  -> durable extract into module book / manifest / test / source
  -> receipt records treatment
  -> local scratch can be deleted or left ignored
```

Do not copy large analysis files into reference docs. Extract stable lessons.

## 4. Mainline Phases

## Phase P0: Spine Stabilization

Status: implemented on this branch.

Purpose:

- establish the runtime spine without changing live trading behavior

Delivered:

- route cards
- typed digest inputs
- T0-T4 risk tiers
- agent-runtime digest profile
- `agent_runtime` semantic boot
- role context packs
- claim-scoped graph metadata
- closeout risk metadata
- packet evidence and receipt
- map/docs checker adjustment for registered non-active operation packets

Acceptance:

- typed runtime navigation admits this packet
- closeout passes with graph as warning-only
- focused tests pass

Verification already recorded in `work_log.md`.

## Phase P1: Route Card Hardening

Status: implemented on this branch.

Goal:

- make route card stable enough for agents, scripts, and UI consumers.

Files:

- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_digest.py`
- `architecture/topology_schema.yaml`
- `tests/test_topology_doctor.py`
- `tests/test_digest_profile_matching.py`

Implementation:

1. Add `route_card.schema_version`.
2. Add `route_card.claims` with empty default.
3. Add `route_card.expansion_hints` for when to load appendices.
4. Add `--route-card-only` for lightweight T0/T1 use.
5. Add CLI golden-output tests for human route-card rendering.
6. Add JSON contract tests for route-card required fields.

Acceptance criteria:

- `topology_doctor.py --navigation --route-card-only ...` emits no long
  appendices.
- Existing JSON output remains backward compatible.
- Invalid typed intent fails cleanly without falling through to a dangerous
  profile.

Anti-bureaucracy guardrail:

- route-card-only output must stay under one screen for normal T0/T1 tasks.

Rollback:

- remove route-card-only flag and schema additions; existing P0 route card can
  remain.

## Phase P2: Claim Model And Gate Engine

Status: implemented on this branch for the first claim-scoped gate set.

Goal:

- replace prose-only claim scoping with a machine-readable claim/gate contract.

Files:

- `architecture/topology_schema.yaml`
- `architecture/context_pack_profiles.yaml`
- `scripts/topology_doctor_closeout.py`
- `scripts/topology_doctor_code_review_graph.py`
- `scripts/topology_doctor_context_pack.py`
- `tests/test_topology_doctor.py`

Implementation:

1. Add `claim_contract` to `topology_schema.yaml`.
2. Define standard claims:
   - `admission_valid`
   - `semantic_boot_answered`
   - `graph_impact_validated`
   - `repo_health_clean`
   - `packet_closeout_complete`
   - `live_side_effect_authorized`
3. Add `--claim <id>` to navigation/closeout.
4. Add claim-to-gate resolution:
   - graph claim -> graph freshness required
   - repo clean claim -> global health required
   - live claim -> explicit operator evidence required
5. Emit `claims_evaluated`, `claims_blocked`, `claims_advisory`.
6. Add tests proving stale graph blocks only graph claims.

Acceptance criteria:

- Closeout without `graph_impact_validated` can pass with stale graph warnings.
- Closeout with `graph_impact_validated` fails when graph is stale.
- Repo global debt blocks only `repo_health_clean`, not scoped packet closeout.

Anti-bureaucracy guardrail:

- no default claim may require graph refresh.

Rollback:

- disable `--claim` handling; keep P0 claim-scope text.

## Phase P3: Non-Source Impact Adapters

Status: implemented on this branch for architecture/script/docs/test impact
summaries.

Goal:

- make impact output meaningful for architecture/scripts/docs/tests, not only
  `source_rationale`.

Files:

- `scripts/topology_doctor_context_pack.py`
- `architecture/module_manifest.yaml`
- `architecture/script_manifest.yaml`
- `architecture/docs_registry.yaml`
- `architecture/test_topology.yaml`
- `architecture/context_pack_profiles.yaml`
- `tests/test_topology_doctor.py`

Adapters:

| Adapter | Input | Output |
| --- | --- | --- |
| source | `architecture/source_rationale.yaml` | upstream/downstream/tests |
| architecture | `architecture/*` manifests | owner, law zone, companion surfaces |
| script | `architecture/script_manifest.yaml` | class, write targets, danger, tests |
| docs | `architecture/docs_registry.yaml` | doc class, truth profile, freshness |
| tests | `architecture/test_topology.yaml` | category, trust, law gates |
| graph | graph status/appendix | derived impact only |

Implementation:

1. Refactor `build_impact()` into adapter dispatch.
2. Preserve current source adapter behavior.
3. Add adapter summaries for architecture/docs/scripts/tests.
4. Add context-pack tests for each adapter.
5. Add missing manifest fields only if a test consumes them.

Acceptance criteria:

- Context pack for `architecture/topology.yaml` reports architecture owner and
  planning-lock expectations.
- Context pack for `scripts/topology_doctor_cli.py` reports script lifecycle.
- Context pack for `docs/operations/.../plan.md` reports packet-evidence class.
- Context pack for `tests/test_topology_doctor.py` reports test trust/category.

Anti-bureaucracy guardrail:

- adapters summarize; they do not require reading every registry body.

Rollback:

- source adapter remains default; disable non-source adapters by flag.

## Phase P4: Rehearsal Eval Harness

Status: implemented on this branch as focused regression/rehearsal tests.

Goal:

- prove the runtime improves real agent behavior instead of just adding fields.

Files:

- `tests/test_agent_runtime_rehearsals.py` or `tests/test_topology_doctor.py`
- `docs/operations/task_2026-04-29_topology_graph_runtime_upgrade/evidence/`
- optional `scripts/topology_runtime_rehearsal.py` only if repeatable and
  registered

Eval scenarios:

1. Free-text R3 collision:
   - task mentions “runtime” and “live readiness”
   - typed intent selects agent runtime profile
   - no typed intent surfaces ambiguity or safe out-of-scope
2. Stale graph:
   - graph-impact claim fails
   - ordinary closeout passes with warning
3. Packet-local docs:
   - new registered packet evidence does not require current_state mutation
4. T0 explorer:
   - route-card-only output stays short
5. T3 runtime edit:
   - planning-lock + receipt required
6. Invalid live/prod write intent:
   - T4 requires operator go evidence

Metrics:

- route correctness
- false blocker count
- required context count
- gate count by tier
- completion receipt completeness

Acceptance criteria:

- every scenario has a red/green assertion
- at least one regression protects the exact failure found during this packet:
  free-text misrouting to R3 live readiness
- evals run without external services

Anti-bureaucracy guardrail:

- evals measure context/gate count; a change that increases process weight
  without improving route correctness should fail review.

## Phase P5: Warning Lifecycle

Goal:

- prevent warnings from becoming permanent noise or accidental blockers.

Files:

- `architecture/topology_schema.yaml`
- `scripts/topology_doctor_closeout.py`
- `scripts/topology_doctor_docs_checks.py`
- `scripts/topology_doctor_code_review_graph.py`
- `docs/reference/modules/closeout_and_receipts_system.md`
- tests

Warning states:

- `new`
- `acknowledged`
- `deferred_until`
- `expires_at`
- `promoted_to_blocker`
- `retired`

Implementation:

1. Add warning lifecycle metadata to typed issue schema.
2. Add packet-local deferral format in receipts.
3. Add closeout check: expired deferrals become blockers only for matching
   claim/scope.
4. Add tests for warning decay and promotion.

Acceptance criteria:

- a warning can be deferred with evidence
- expired warning blocks only matching scope
- global unrelated warning does not block scoped packet closeout

Anti-bureaucracy guardrail:

- every deferral must have an owner and invalidation condition; no open-ended
  “known issue” bucket.

## Phase P6: Graph Freshness And Sidecar Integration

Status: implemented on this branch for generated graph health cards and
claim-scoped invalidation.

Goal:

- make graph useful when healthy and harmless when stale.

Files:

- `scripts/topology_doctor_code_review_graph.py`
- `scripts/code_review_graph_mcp_readonly.py`
- `.code-review-graph/README.md`
- graph module book
- tests

Implementation:

1. Add graph health card with:
   - DB present/tracked
   - branch/head match
   - changed-file coverage
   - sidecar/meta parity
2. Add claim-aware graph gate:
   - required only when claim depends on graph
3. Add graph refresh instruction output, not automatic refresh.
4. Add sidecar status but keep graph binary non-authority.

Acceptance criteria:

- graph health output says exactly which claims are invalidated
- stale graph never answers semantic/source/current-fact questions
- graph refresh remains explicit operator/tooling action

Anti-bureaucracy guardrail:

- no universal “refresh graph before work” rule.

## Phase P7: Runtime Orchestration Command Surface

Status: implemented on this branch for the composed `runtime` command surface.

Goal:

- expose one coherent CLI path for agent runtime tasks.

Candidate command:

```bash
python scripts/topology_doctor.py runtime \
  --task "<task>" \
  --files <files> \
  --intent "<profile>" \
  --task-class "<class>" \
  --write-intent edit \
  --role executor \
  --claim packet_closeout_complete \
  --json
```

Output:

- route card
- semantic boot
- role context
- gate budget
- claim evaluation
- artifact treatment hints

Implementation:

1. Add subcommand after P1-P3 stabilize.
2. Internally compose existing primitives; do not duplicate logic.
3. Add `--route-card-only` and `--context-pack` variants.
4. Add CLI parity tests.

Acceptance criteria:

- runtime command can replace manual navigation + context-pack commands for
  common flows
- existing navigation/digest/context-pack commands still work

Anti-bureaucracy guardrail:

- runtime command should be optional convenience, not required extra ceremony.

## Phase P8: Skills / Work Ethic Engineering

Status: implemented on this branch through generated role context pack policy
and regression coverage.

Goal:

- make skill usage and work ethic runtime-visible without turning them into
  mandatory ritual.

Files:

- `architecture/context_pack_profiles.yaml`
- role context pack builder
- docs module books
- tests

Policy shape:

| Skill family | Runtime use | Do not use when |
| --- | --- | --- |
| analyze | deep read-only investigation | simple file lookup |
| plan | broad implementation design | scoped obvious fix |
| zeus-ai-handoff | Zeus packet/handoff discipline | trivial local edit |
| code-review | final diff review | author is self-approving high-risk diff |
| browser/web research | current external APIs/docs | stable repo-local facts suffice |

Work ethic encoded as checks:

- preserve unrelated dirty work
- prefer typed admission over free-text
- keep changes reversible
- delete/avoid process that has no consumer
- verify exact claim before closeout

Implementation:

1. Keep skill/work-ethic policy in generated role packs.
2. Add tests proving role packs include policy but do not mark it authority.
3. Add reviewer checklist in critic role pack.

Acceptance criteria:

- executor pack tells agent when to use skills and when not to
- explorer pack forbids edits
- critic pack asks whether process weight has a consumer
- verifier pack checks claims, not vibes

Anti-bureaucracy guardrail:

- no “must invoke skill X” unless user explicitly requested it or repo rules
  require it.

## Phase P9: Multi-Agent Dispatch Integration

Status: implemented on this branch as generated runtime dispatch guidance that
does not auto-trigger subagents.

Goal:

- make role packs usable by native subagents / OMX team without forcing
  parallelism for simple work.

Files:

- context-pack profiles
- packet docs
- optional runbook under `docs/runbooks/`
- tests for generated packets

Dispatch modes:

| Mode | Trigger | Runtime packet |
| --- | --- | --- |
| solo | default | executor/verifier in same session |
| explorer sidecar | read-only mapping | explorer pack |
| critic gate | T3/T4 or broad diff | critic pack |
| team | 4+ independent lanes | role packs per lane |

Implementation:

1. Add dispatch guidance to role packs.
2. Add packet template for child-agent prompt shape.
3. Add claim-scoped handoff: child agents own claims, not global plan.
4. Add verifier pack that can validate child-agent outputs.

Acceptance criteria:

- generated role pack can be pasted into a child agent without extra context
- child-agent prompt includes ownership, non-goals, gates, and stop conditions
- no automatic subagent requirement for T0/T1 work

Anti-bureaucracy guardrail:

- parallelism is an optimization, not a compliance requirement.

## Phase P10: Adoption And Deprecation

Goal:

- migrate future work to runtime spine without breaking existing workflows.

Implementation:

1. Keep old commands working.
2. Update module books with “runtime path first” examples.
3. Add migration notes in packet closeout.
4. Add deprecation warning only after two packets successfully use runtime
   command.
5. Retire duplicated prose after tests cover the new path.

Acceptance criteria:

- future agent can start with one route-card command
- old digest/navigation tests still pass
- no new default-read docs are required

Anti-bureaucracy guardrail:

- do not require historical packet reading for normal future tasks.

## 5. End-To-End Workflows

### 5.1 Read-Only Investigation

```text
navigation --write-intent read_only
  -> T0 route card
  -> explorer context pack if needed
  -> no edits
  -> optional analysis artifact
  -> artifact inventory if durable
```

Required gates:

- none beyond command success, unless the investigation makes a claim requiring
  current facts or graph impact.

### 5.2 Narrow Edit

```text
navigation --write-intent edit
  -> T1/T2 route card
  -> executor context pack
  -> focused code/doc change
  -> focused tests/checkers
  -> work log if repo-changing
```

Required gates:

- admission admitted
- focused tests/checks
- map-maintenance if added/deleted files

### 5.3 Architecture / Runtime Tooling Edit

```text
navigation --intent ... --task-class ... --write-intent edit
  -> T3 route card
  -> packet plan
  -> planning-lock
  -> executor context pack
  -> critic context pack if high-risk
  -> focused tests
  -> receipt
  -> closeout
```

Required gates:

- planning-lock
- work record
- change receipt
- map-maintenance
- freshness metadata
- touched lane checks
- focused tests

### 5.4 Graph-Impact Claim

```text
semantic-bootstrap --task-class graph_review
  -> graph health
  -> graph claim evaluation
  -> graph appendix if fresh
  -> explicit residual risk if stale
```

Required gates:

- graph freshness only when claiming graph impact

### 5.5 Artifact Promotion

```text
.omx artifact
  -> packet evidence index
  -> extract stable lesson
  -> update module book / manifest / test / source
  -> receipt records treatment
```

Required gates:

- docs/operations packet registration
- runtime artifact inventory update
- no authority promotion by copying

### 5.6 Merge / PR

```text
closeout ok
  -> critic review for T3/T4 or broad diffs
  -> Lore commit
  -> PR with residual risks and verification
```

Required gates:

- closeout ok
- critic gate when merging cross-session or high-risk branches
- no unrelated dirty work staged

## 6. Testing Strategy

### Unit / Contract Tests

- digest typed intent selection
- typed intent cannot bypass forbidden files
- route-card required fields
- risk tier classification
- context pack role output
- claim/gate mapping once P2 lands

### Integration Tests

- navigation + digest + route card
- context-pack + semantic boot
- closeout + receipt + map-maintenance
- stale graph warning-only behavior

### Rehearsal Tests

- realistic task strings that previously misrouted
- cold-start agent flow
- resumed-agent flow
- packet-local artifact flow
- graph-stale flow

### Negative Tests

- invalid typed intent
- forbidden path with valid typed intent
- T4 live intent without operator evidence
- graph-impact claim with stale graph
- current-fact claim with stale current-fact surface

### Observability / Metrics

Track in test fixtures or generated summaries:

- route-card length
- number of gates by tier
- false blocker count
- unscoped repo-health blockers
- context pack section count
- receipt completeness

## 7. Acceptance Criteria For Mature Runtime

The runtime is mature when:

- a T0 task emits a route card and does not require packet ceremony
- a T3 task cannot close without packet plan, planning lock, work log, receipt,
  and focused gates
- typed intent prevents known free-text misrouting
- stale graph blocks graph claims only
- packet-local evidence does not mutate `current_state.md` unless active
- every role context pack is sufficient for its role and no broader
- every warning has scope, owner, and lifecycle
- every durable analysis artifact is either summarized, promoted through the
  right authority path, or explicitly left local/discarded
- full-repo global health can remain visible without hijacking scoped work

## 8. Failure Modes And Mitigations

| Failure | Cause | Mitigation |
| --- | --- | --- |
| Runtime becomes bureaucracy | every warning blocks every task | claim-scoped blocking and tier budgets |
| Route card becomes authority | generated UX treated as law | `authority_status` and tests |
| Typed intent bypasses admission | explicit profile trusted too much | admission reconciliation remains final |
| Graph becomes semantic oracle | structural edges overread | semantic boot before graph, graph limitations |
| Packet evidence becomes default-read docs | whole artifacts copied into reference | evidence index + durable extracts only |
| Role packs become too large | every role gets everything | role-specific output sections |
| Closeout hides real risk | scoped checks omit required law | risk-tier gates and receipt law coverage |
| Agents still misroute | no eval harness | P4 rehearsal suite |

## 9. Immediate Next Steps

After this branch is reviewed:

1. Land P0 spine if accepted.
2. Open P1 route-card hardening packet.
3. Add route-card schema version and route-card-only CLI.
4. Add P4 rehearsal tests early, before expanding more process.
5. Only then implement P2 claim engine and P3 adapters.

Recommended sequencing:

```text
P0 branch review
  -> P1 route card hardening
  -> P4 rehearsal harness
  -> P2 claim engine
  -> P3 adapters
  -> P5 warning lifecycle
  -> P6 graph sidecar
  -> P7 runtime command
  -> P8/P9 skills + multi-agent integration
  -> P10 adoption/deprecation
```

Reason:

- route-card hardening and rehearsals validate the runtime before deeper
  architecture work
- claim engine and adapters then have concrete failures to solve
- multi-agent integration should wait until the single-agent runtime is proven

## 10. Done Definition

A phase is done only when:

- changed files are admitted by typed navigation
- planning-lock passes when required
- focused tests pass
- closeout passes
- receipt lists changed files, law basis, verification, and residual risks
- no graph-impact or repo-clean claim is made unless corresponding gates pass
- any `.omx` or packet-local artifacts have a recorded lifecycle treatment
- future agents can resume from packet evidence without reading the full chat
