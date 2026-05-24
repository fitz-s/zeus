# EDLI Redemption Root Cause

Created: 2026-05-24

## Verdict

PR328 is frozen as a failed spike/scaffold. It must not be merged or used for a
daemon reboot as an EDLI live implementation.

## Root Cause

The prior implementation sequence allowed runtime wiring before proving the
semantic object chain. That inverted the money-path authority order: event
tables, scheduler hooks, reports, and online config were added before the code
could prove that a single event binds to the exact causal market family,
candidate set, executable quote, FDR family, Kelly input, RiskGuard decision,
final execution intent, and executor side-effect boundary.

The specific failure mode was treating an event-triggered old cycle run as if it
were an event-sourced decision. A cycle summary is observability. It is not proof
that an order belonged to the same event, city, target date, metric, family,
condition, token, causal snapshot, or executable snapshot.

## Redemption Rule

EDLI redemption proceeds from a proof kernel first:

1. An EDLI event may only create a candidate family through explicit
   event-bound market topology matching.
2. Public market-channel events may create quote/book evidence only; they may
   not create live trade candidates.
3. Forecast live candidate binding requires `causal_snapshot_id`.
4. Day0 live candidate binding requires explicit live-authority status and
   source/station/local-date/DST/metric/rounding/source-authorization matches.
5. The R1 proof kernel must not import or call scheduler, `run_cycle`,
   executor, venue adapter, websocket, or live submit code.

## Current Scope

This packet implements only R1:

- freeze EDLI live config off
- preserve the PR328 redemption package under operations
- add a pure event model for the proof kernel
- add an event-bound candidate-family pure kernel
- add a no-runtime decision-engine skeleton
- add tests that reject unbound, market-data, and wrong-family events

Later redemption cuts may reuse scaffold pieces only after this kernel owns the
semantic binding proof.
