# U2 learning — raw provenance needed its own topology profile

Created: 2026-04-27
Phase: U2
Category: topology-routing drift

## Observation

A first U2 navigation request for `venue_order_facts`, `venue_trade_facts`, `position_lots`, and provenance envelope events was incorrectly routed to the R3 heartbeat-supervisor profile because no U2-specific digest profile existed. That false routing marked `src/state/db.py` and `src/state/venue_command_repo.py` as outside the allowed set even though U2 explicitly owns the raw provenance schema backbone.

## Risk

Without a U2 profile, agents either stop unnecessarily or bypass topology guidance by hand. Both outcomes are bad for a high-risk state/schema slice: the first wastes context and the second weakens the planning-lock discipline around durable truth tables.

## Decision

Add a narrow `r3 raw provenance schema implementation` profile to `architecture/topology.yaml`, plus a digest regression test, before editing U2 source files. The profile owns only the U2 schema/repo/executor/test surfaces and explicitly forbids M1 grammar, M3 websocket, M5 sweeping, R1 redemption side effects, and live venue submission.

## Antibody

When adding future R3 slices, first check whether the task keywords route to the intended profile. If a packet slice owns a new schema/control plane but topology does not recognize it, add the profile and a regression test before implementation.
