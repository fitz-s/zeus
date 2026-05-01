# Topology Graph Runtime Upgrade Plan

Date: 2026-04-29
Branch: `agent-runtime-upgrade-2026-04-29`
Status: implementation packet plan

Detailed mainline continuation plan:
`docs/operations/task_2026-04-29_topology_graph_runtime_upgrade/implementation_plan.md`

## Objective

Turn the topology / graph guidance system into an agent-runtime surface: a
small, typed, claim-scoped workflow that helps agents decide what to read, what
they may change, which gates prove the claim, and how to close out without
creating bureaucracy.

## Scope

This packet may change:

- topology doctor routing and CLI rendering
- docs checker treatment of registered non-active operation packets
- map-maintenance treatment of packet-local evidence and direct-child doc globs
- digest profile admission ergonomics
- context-pack role profiles
- closeout and graph-degradation behavior
- topology schema documentation
- dense module books for topology, graph, docs, and receipts
- packet evidence and runtime-artifact inventory
- focused regression tests for the above

## Non-Goals

- no live trading behavior changes
- no production DB mutation
- no source-routing, settlement, calibration, risk, or execution semantics
- no graph refresh or graph database rewrite
- no new dependency
- no replacement of root `AGENTS.md` authority
- no promotion of `.omx` analysis artifacts into authority

## Runtime Model

The runtime should expose five compact surfaces:

1. `route_card`: profile, admission status, risk tier, next action, permitted
   change set, hard stop conditions.
2. Typed task input: explicit `intent`, `task_class`, and `write_intent` before
   free-text phrase matching.
3. Risk-tier gate budget: T0 read-only through T4 live side-effect work.
4. Claim-scoped blocking: stale graph and unrelated repo health block only the
   claims they invalidate.
5. Role-specific context packs: explorer, executor, critic, and verifier see
   the context they can act on.

## Engineering Batches

### Batch 1: Packet And Documentation Routing

- create this packet
- register packet evidence in `docs/operations/AGENTS.md`
- record `.omx` analysis treatment in `docs/operations/runtime_artifact_inventory.md`
- extract durable ideas into existing module books only

### Batch 2: Route Card And Typed Digest Inputs

- add route-card generation to navigation and digest payloads
- render route card before legacy `allowed_files`
- add typed `--intent`, `--task-class`, and `--write-intent` CLI inputs
- keep admission.status as the only write-authority signal

### Batch 3: Context Pack Runtime Roles

- add explorer, executor, critic, and verifier profiles
- expose role-specific context packs through existing `context-pack`
- include workflow/work-ethic/skill-use policy as generated runtime guidance,
  not authority

### Batch 4: Claim-Scoped Graph And Closeout Behavior

- classify graph health as claim-scoped
- make stale/missing graph block graph-impact claims, not ordinary closeout
- add risk-tier gate budget metadata to closeout output

### Batch 5: Tests And Closeout

- focused digest/navigation/context-pack/closeout tests
- digest profile mirror regeneration if `architecture/topology.yaml` changes
- topology/schema/context-pack checks
- packet work log and receipt

## Verification Plan

Required:

- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-29_topology_graph_runtime_upgrade/plan.md --json`
- `python3 scripts/topology_doctor.py --context-packs --json`
- `python3 scripts/topology_doctor.py --task-boot-profiles --json`
- `python3 scripts/topology_doctor.py --schema`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files <files> --json`
- `pytest -q tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py`
- `pytest -q tests/test_topology_doctor.py -k 'navigation or digest or context_pack or closeout or code_review_graph'`

Known caveat:

- Code Review Graph is stale/unhealthy on this branch. This packet must not
  claim graph-impact validation unless graph freshness is separately restored.

## Rollback

Revert this packet's changed files. No runtime state, DB, live-money, or source
semantics should require migration.
