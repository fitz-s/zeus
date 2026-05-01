# Topology System

> Status: reference, not authority. See `AGENTS.md`, `workspace_map.md`, `architecture/**`, and `docs/authority/**` for authority.

## Purpose

The topology system is Zeus's machine-readable governance and routing kernel. It tells agents what law to read, which files are in scope, what companions are required, which manifest owns each fact type, and which failures block a given mode.

## Authority anchors

- Root `AGENTS.md` requires topology navigation before code changes.
- `workspace_map.md` defines default route and visibility classes.
- `architecture/topology.yaml`, `zones.yaml`, `invariants.yaml`, and `negative_constraints.yaml` define the durable kernel.
- `architecture/docs_registry.yaml`, `module_manifest.yaml`, `source_rationale.yaml`, `test_topology.yaml`, and `script_manifest.yaml` define registries.
- `scripts/topology_doctor.py` and `tests/test_topology_doctor.py` enforce and regress the kernel.

## How it works

Topology has five interacting layers:

1. **Authority surfaces**: system/developer/user instructions, root/scoped AGENTS, authority docs, manifests, tests, and executable source.
2. **Routing manifests**: compact machine facts about files, zones, docs, modules, tests, scripts, graph, and context budget.
3. **Validator lanes**: topology doctor checks that expose drift and missing companions.
4. **Mode policies**: navigation, closeout, strict/global health, context pack, and packet prefill use different blocking rules.
5. **Cognition surfaces**: module books and derived appendices explain hidden obligations without becoming law.

## Hidden obligations

- Topology indexes authority; it never outranks source, tests, canonical DB/event truth, or current law.
- Archives are accessed through `docs/archive_registry.md` and are not default-read.
- Planning lock applies to architecture/governance/control/lifecycle/cross-zone and broad file changes.
- Machine manifests should stay compact; module books carry explanation.
- Global health drift must remain visible even when a scoped route is clear.
- Agent-runtime output should reduce decisions, not add ceremony: route cards
  summarize admission, risk tier, next action, and gate budget before appendices.
- Operation-end feedback should create a positive loop without becoming a new
  gate: agents capture context recovery, Zeus improvement insights, and
  topology helped/blocked notes in the final response or an already-required
  packet closeout surface. The topology note should name route/admission/risk,
  semantic match, one help, one friction, and one next topology delta or
  `none_observed`.

## Failure modes

- Lane conflation makes unrelated repo-health drift block a focused task.
- Flat issue shapes make repair ownership impossible to automate.
- Dense knowledge remains hidden in tests, package plans, source comments, or graph blobs.
- A tidy manifest layer becomes cognitively hollow and causes zero-context agents to guess.

## Repair routes

- Start with `python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>`.
- Prefer typed `--intent`, `--task-class`, and `--write-intent` when free-text
  routing could collide with live-money or R3 profiles.
- Use `--route-card-only` for first-screen orientation and `--claim` only when
  the completion statement depends on a specific gate such as graph impact,
  repo health, packet closeout, semantic boot, or live authorization.
- Treat high-fanout file-only matches as soft advisory routing, not as a hard
  ambiguous topology failure. They must not admit edits or choose a profile,
  but they should let the agent continue orientation and then pass typed intent
  only when the edit actually needs that file.
- Use typed issue `owner_manifest` and `repair_kind` metadata to select the owning registry.
- Stop and plan when planning-lock or unknown ownership appears.
- Close with targeted tests. Add changed-file `closeout` and work/receipt
  evidence only when a packet, closeout claim, or high-risk gate consumes them.
- If topology helped, preserve the pattern in tests, route-card text, or module
  docs only when the current route owns that surface. If topology blocked or
  misrouted, record the friction as feedback first; promote it to a topology
  fix only when it is repeatable or already in scope.
- Use the `direct operation feedback capsule` profile for "context recycling",
  "回收 context", and topology-experience closeout tasks. No-file invocations
  are final-response only; packet `work_log.md`/`receipt.json` writes are
  admitted only when that packet already consumes them.

## Agent runtime posture

Topology is most useful when it behaves like a runtime contract:

- T0 read-only work gets a route card and stops before edits.
- T1/T2 work gets focused gates, not full-governance ritual.
- T3 work gets planning-lock when governed files are touched and focused gates
  for the changed surface. Packet evidence, receipts, and critics are
  conditional on packet closeout, explicit claims, or semantic ambiguity.
- T4 live/prod work remains blocked without explicit operator authorization.
- Impact/context output should use the owner manifest for the file class:
  `source_rationale` for `src/**`, script manifest for `scripts/**`, docs
  registry or operations router for `docs/**`, and test topology for `tests/**`.
- Every completed operation should leave one compact feedback capsule. The
  capsule is not authority and not a standalone artifact requirement; it is the
  runtime's mechanism for harvesting useful lessons without forcing unrelated
  repairs into the current diff.
- Scoped closeout should say whether the changed surface is clean separately
  from repo-wide drift. Global drift remains visible, but unrelated weekly
  diagnostics should not be presented as a changed-surface failure.

Route cards and context packs are generated guidance. They never replace
`AGENTS.md`, manifests, tests, or executable source.

## Cross-links

- `docs/reference/modules/topology_doctor_system.md`
- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/closeout_and_receipts_system.md`
- `docs/reference/modules/docs_system.md`
- `docs/reference/modules/code_review_graph.md`
