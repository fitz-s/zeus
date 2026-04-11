# Zeus zero-context entry protocol

Use this when an agent or reviewer does **not** have full repo context.

## Step 0 — Stop broad prompting

Do not ask the agent to “implement the architecture”, “refactor Zeus”, or “finish the system”.
Request or construct one packet.

## Step 1 — Determine the task class

Classify the task as one of:
- bugfix
- feature
- schema
- refactor

Then load the corresponding packet template from `architecture/packet_templates/`.

## Step 2 — Read authority in order

Mandatory reads:
1. `architecture/self_check/authority_index.md`
2. `architecture/kernel_manifest.yaml`
3. `architecture/invariants.yaml`
4. `architecture/zones.yaml`
5. `docs/authority/zeus_durable_architecture_spec.md` relevant section
6. `docs/authority/zeus_change_control_constitution.md`
7. then code files

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

## Step 5 — Evidence before merge

No packet is complete without:
- tests
- manifest/invariant references
- schema diff if applicable
- parity/replay output or staged waiver
- rollback note

## Rule of thumb

A zero-context agent should be able to become *safe* much faster than it becomes *smart*.
This protocol exists to make safety come first.
