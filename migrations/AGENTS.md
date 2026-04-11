File: migrations/AGENTS.md
Disposition: NEW
Authority basis: docs/authority/zeus_durable_architecture_spec.md; architecture/kernel_manifest.yaml; architecture/invariants.yaml; architecture/negative_constraints.yaml; docs/authority/zeus_autonomous_delivery_constitution.md.
Supersedes / harmonizes: ad hoc schema evolution.
Why this file exists now: schema work is high-friction and partially irreversible.
Current-phase or long-lived: Long-lived.

# migrations AGENTS

Migrations are high-stakes.

## Mandatory
- approved packet
- rollback note
- cutover note
- parity/replay status
- human gate for destructive or live cutover changes

## Do
- make migrations append-safe where possible
- keep canonical truth tables explicit
- document current-phase transitional reads/writes

## Do not
- delete or repurpose legacy tables silently
- claim cutover is complete when reads still route elsewhere
- let migration files become the only explanation of semantic change
