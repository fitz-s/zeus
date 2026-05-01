# Z0 learning — docs/architecture route is invalid in current docs mesh

Created: 2026-04-27
Phase: Z0
Category: plan-vs-topology drift

## Observation

Z0 requested `docs/architecture/polymarket_live_money_contract.md`, but topology navigation classified `docs/architecture/` as outside known workspace routes. The docs root rules also say new active docs belong in declared tracked subroots; there is no active `docs/architecture` subroot.

## Risk

Creating `docs/architecture/` would manufacture a parallel architecture-facing docs authority plane and conflict with the existing root `architecture/**` machine-law zone plus `docs/authority/**` durable law zone.

## Decision

Emit the live-money contract as packet-local evidence at `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md`, register it in the packet AGENTS, and adapt Z0 tests to that path.

## Antibody

Future R3 cards should prefer existing docs subroots (`docs/operations/<packet>/`, `docs/reference/`, `docs/authority/`) or explicitly amend `architecture/docs_registry.yaml`/`architecture/topology.yaml` via planning-lock before naming a new docs subroot.
