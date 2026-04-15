# Zeus Zero-Context High-Risk Overlay

Use this when a zero-context agent may touch K0/K1, schema, governance,
control, lifecycle, canonical truth, DB authority, or cross-zone behavior. It
does not replace default navigation; it adds the high-risk authority spine.

## Minimum high-risk startup spine

Read these directly.

1. `AGENTS.md`
2. `workspace_map.md`
3. `architecture/self_check/authority_index.md`

Then run:

`python scripts/topology_doctor.py --navigation --task "<task>" --files <files>`

If navigation routes this task into K0/K1/schema/governance/control/truth, keep
following this overlay before reading code.

## Step 0 — Stop broad prompting

Do not ask the agent to “implement the architecture”, “refactor Zeus”, or “finish the system”.
Request or construct one packet.

## Step 1 — Determine the packet class

Classify the task as one of:
- bugfix
- feature
- schema
- refactor

Then load the corresponding packet template from `architecture/packet_templates/`.
If the request is not packet-shaped, create a packet first; do not start from
raw implementation prose.

## Step 2 — Add high-risk authority reads

For K0/K1/schema/governance/control/truth work, add these reads before code.
Use `authority_index.md` as the order, not as a substitute for the files.

1. `architecture/self_check/authority_index.md`
2. `architecture/kernel_manifest.yaml`
3. `architecture/invariants.yaml`
4. `architecture/zones.yaml`
5. `architecture/source_rationale.yaml` for `src/**` file-level roles/hazards/write routes
6. `docs/authority/zeus_current_architecture.md`
7. `docs/authority/zeus_current_delivery.md`
8. `docs/authority/zeus_change_control_constitution.md` when deep governance applies
9. then scoped `AGENTS.md`, code files, and targeted tests

If the task touches scripts, tests, references, or config while doing high-risk
work, also read the relevant registry named by root `AGENTS.md` Mesh
Maintenance before editing.

## Step 3 — Answer the routing questions

Before editing, the agent must answer in writing:

- What truth surface is authoritative here?
- Which zone is being touched?
- Which invariant IDs are in scope?
- Which files are allowed to change?
- Which files are forbidden to change?
- Is this a math change or an architecture change?
- What evidence is required?

## Step 4 — Refuse unsafe starting states

Stop immediately if:
- the task touches K0 but has no packet
- the task has no `required_reads`
- the task wants to edit more than one high-sensitivity zone without justification
- the agent is using historical docs as if they were current authority
- the task invents fallback strategy/unit/lifecycle semantics
- the task treats a reference doc or agent artifact as authority without a
  machine/test/contract manifest path

## Step 5 — Evidence before merge

No packet is complete without:
- tests
- manifest/invariant references
- schema diff if applicable
- parity/replay output or staged waiver
- rollback note
- map-maintenance result for added/deleted/renamed files

## Rule of thumb

A zero-context agent should be able to become *safe* much faster than it becomes *smart*.
This protocol exists to make safety come first.
