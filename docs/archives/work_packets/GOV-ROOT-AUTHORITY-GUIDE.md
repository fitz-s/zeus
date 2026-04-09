# GOV-ROOT-AUTHORITY-GUIDE

```yaml
work_packet_id: GOV-ROOT-AUTHORITY-GUIDE
packet_type: governance_packet
objective: Install a root-level authority guide that states Zeus's foundation, summarizes the live invariant and negative-constraint sets, and names the core system boundary rules without creating a competing routing surface.
why_this_now: The user explicitly directed that the highest authority guide should live in the repo root, carry the essential method-and-law content, and stop reading like a pile of local routing details. Fresh authority review confirms the repo has 10 live invariants and 10 live negative constraints in machine-checkable form, but no single root-level guide that presents them together with the core boundary rules.
why_not_other_approach:
  - Expand AGENTS.md into the root authority guide | would mix methodology/authority with operational execution rules and recreate drift risk
  - Move authority_index.md to root and make it the full guide | would turn a routing file into a content-heavy parallel authority surface
  - Restate packet/team/model/evidence policy in a new guide | duplicates AGENTS.md and violates the requirement that the highest authority file stay about foundations, method, and law
truth_layer: the exact machine-checkable rule sources remain `architecture/invariants.yaml` and `architecture/negative_constraints.yaml`; the new root guide is the compressed human/agent entry point that must point back to those sources when exactness matters.
control_layer: keep this packet bounded to the root authority guide and the minimal routing/index updates needed to surface it. Do not widen into archive cleanup, completed-ledger retirement, or runtime/spec rewrites.
evidence_layer: counted inventory of live invariant IDs and negative-constraint IDs, a source-backed boundary-rule mapping, work-packet grammar output, and kernel-manifest check output.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - AGENTS.md
  - architecture/self_check/authority_index.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/zeus_FINAL_spec.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/negative_constraints.yaml
files_may_change:
  - work_packets/GOV-ROOT-AUTHORITY-GUIDE.md
  - ZEUS_AUTHORITY.md
  - AGENTS.md
  - architecture/self_check/authority_index.md
  - docs/README.md
  - WORKSPACE_MAP.md
  - README.md
files_may_not_change:
  - docs/governance/**
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/negative_constraints.yaml
  - architecture/zones.yaml
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/zeus_FINAL_spec.md
  - src/**
  - tests/**
  - scripts/**
  - migrations/**
  - .github/workflows/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required: []
parity_required: false
replay_required: false
rollback: Revert the root authority guide and the routing/index updates together; repo returns to the prior authority-entry shape without changing the underlying machine-checkable law.
acceptance:
  - `ZEUS_AUTHORITY.md` exists in the repo root as the root authority guide
  - the guide states Zeus's foundation and authority method, lists exactly 10 live invariants and 10 live negative constraints, and names 5 core boundary rules
  - the guide explicitly points back to the exact machine-checkable and architecture authority sources instead of replacing them
  - AGENTS.md, authority_index.md, and orientation docs surface the new root guide without turning it into a second routing constitution
evidence_required:
  - counted inventory of live invariant IDs
  - counted inventory of live negative-constraint IDs
  - source-backed mapping for the 5 boundary rules
  - work-packet grammar output
  - kernel-manifest check output
```

## Notes

- This packet is a foundation/methodology packet, not a control-surface or archive packet.
- The guide must stay concise and principled. It must not restate AGENTS execution policy, team rules, or packet mechanics except by pointer.

## Evidence log

- Fresh source inventory confirmed `architecture/invariants.yaml` contains exactly `INV-01` through `INV-10`, with no duplicate IDs.
- Fresh source inventory confirmed `architecture/negative_constraints.yaml` contains exactly `NC-01` through `NC-10`, with no duplicate IDs.
- The chosen 5 boundary rules are compressed from existing source authority rather than invented as new law:
  - authority boundary → `INV-03`, `INV-10`, `NC-02`
  - lifecycle boundary → `INV-01`, `INV-02`, `INV-07`, `NC-04`, `NC-07`
  - governance boundary → `INV-04`, `NC-03`
  - temporal-truth boundary → `INV-06`, `INV-09`, `NC-05`
  - durability boundary → `INV-09`, `INV-10`, `NC-06`, `NC-10`
