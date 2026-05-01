# Protocol evolution proposal — avoid unregistered `docs/architecture` path

Status: PROPOSED
Created: 2026-04-27
Origin phase: Z0

## Original protocol/card behavior

The original `slice_cards/Z0.yaml` named `docs/architecture/polymarket_live_money_contract.md` as a new file before Z0 adapted it to the packet-local path.

## Reality

`python3 scripts/topology_doctor.py --navigation ...` reports that `docs/architecture/polymarket_live_money_contract.md` is outside known workspace routes and cannot be classified. `docs/AGENTS.md` does not declare `docs/architecture` as an active docs subroot.

## Proposed amendment

For R3 packet-local contracts, use a registered operations packet path unless the work explicitly intends to create a durable authority doc and updates `architecture/docs_registry.yaml`, `architecture/topology.yaml`, `docs/AGENTS.md`, and relevant authority docs under planning-lock.

## Evidence

Z0 implementation used `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` and registered it in that packet router.

## Local application

Z0 has already adapted its card and tests to use `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md`. Operator review can later decide whether to incorporate this as a permanent protocol rule.
