# Phase 7 — Settlement Social→Type-Gate Migration

## v4 §M scope

Line 1104: "Settlement social→type-gate migration (SYNTHESIS §2.2) — stays social-gated until Phase 6+"

Note: dossier writes "Phase 6+" indicating Phase 7 (or later) is the right slot.

## What "social→type-gate" means

**Social-gated (current state)**: settlement logic branches on string fields like `umaResolutionStatus == "resolved"` or `negRiskMarketID is not None`. The "gate" is whatever data the venue happens to put in a JSON blob. Drift surface: venue can rename, deprecate, or restructure the field; Zeus reads stale strings and silently mis-classifies.

**Type-gated (target)**: settlement logic branches on typed enums (`ResolutionEra`, `SettlementOutcome`, `EraAuthorityBasis`) that the codebase OWNS. The venue's social fields become INPUT to a typing layer, not the gate itself. Drift surface: venue rename forces the typing layer to fail closed with a typed error, not a silent miss.

Phase 0 PR 1 (landed) shipped `ResolutionEra` 2-member enum + `EraAuthorityBasis` + the consolidation helper. Phase 7 completes the migration by:
1. Adding `SettlementOutcome` enum (multi-state lifecycle).
2. Adding `Position.lifecycle_state` field tracking fact_known / source_published / venue_resolved / redeemable.
3. Refactoring all settlement-reading code paths to consume typed enum instead of social strings.

## Settlement outcome lifecycle (target object)

Per dossier §3.7 + §6.7 + §13.1 #10:

```python
class SettlementOutcome(IntEnum):
    # Pre-event states
    UNRESOLVED = 0                      # event hasn't happened yet
    
    # Post-event, source-pending
    PHYSICALLY_CONFIRMED = 1             # observed temperature is final, source page hasn't published
    SOURCE_PUBLISHED_VENUE_UNRESOLVED = 2  # NOAA / WU has published, Polymarket / UMA hasn't resolved
    
    # Resolved
    VENUE_RESOLVED_WIN = 3               # market resolved, position wins
    VENUE_RESOLVED_LOSE = 4              # market resolved, position loses
    
    # Post-resolution
    REDEEMED = 5                         # winning token redeemed for collateral
    OBSERVATION_REVISED = 6              # official observation revised after PHYSICALLY_CONFIRMED; triggers re-evaluation of bound state and position sizing; routes forward to SOURCE_REVISION (if source has republished a corrected value) or DISPUTED (if correction is contested); does NOT revert to PHYSICALLY_CONFIRMED or SOURCE_PUBLISHED_VENUE_UNRESOLVED — monotonic-forward rule preserved
    
    # Edge cases
    DISPUTED = 100                       # UMA dispute filed
    UMA_UNKNOWN_50_50 = 101              # UMA returned 0.5 / unknown
    SOURCE_REVISION = 102                # official source revised after settlement
```

State transitions are monotonic forward (no `REDEEMED → UNRESOLVED`); reversion paths go to `SOURCE_REVISION` or `DISPUTED` and trigger operator review.

## Position lifecycle field

`src/state/portfolio.py::Position` gets `lifecycle_state: SettlementOutcome = SettlementOutcome.UNRESOLVED` (Phase 2 T5 added `market_slug`; Phase 7 adds lifecycle).

Update points:
- Day0 observation crossing threshold → `PHYSICALLY_CONFIRMED`
- NOAA/WU source page update → `SOURCE_PUBLISHED_VENUE_UNRESOLVED`
- Polymarket resolution event → `VENUE_RESOLVED_WIN` / `VENUE_RESOLVED_LOSE`
- Redemption tx confirmed → `REDEEMED`
- UMA dispute event → `DISPUTED`

## SettlementCaptureVerifier (dossier §3.7 + §6.7 + §13.1 #10)

Per-strategy gate: when promoting `resolution_window_maker` or `settlement_capture` to higher tier, the verifier checks that historical (`fact_known_time`, `source_published_time`, `venue_resolved_time`, `redeemed_time`) timestamps cohere across recent shadow + paper trades. Incoherence (e.g., venue_resolved before source_published) flags either a Zeus mis-classification or a venue anomaly — both block promotion.

Output: per-event audit row in `settlement_capture_verifications` table.

## Required pre-checks before Phase 7 dispatch

- `EvidenceTier` framework on main (Phase 6 dependency).
- `ResolutionEra` still authoritative on main (Phase 0 PR 1).
- `architecture/settlement_dual_source_truth_2026_05_07.yaml` is current; verify the era boundaries didn't drift since Phase 0.

## Implementation surfaces

T1 (~300 LOC): `SettlementOutcome` enum + `Position.lifecycle_state` + transition rules + relationship tests for monotonic transitions.

T2 (~400 LOC): Refactor `src/execution/harvester.py` + `src/ingest/harvester_truth_writer.py` to consume `SettlementOutcome` instead of string-comparing `umaResolutionStatus`. Backward compat: legacy rows tagged `SettlementOutcome.UNRESOLVED` until backfill ships.

T3 (~250 LOC): `SettlementCaptureVerifier` + `settlement_capture_verifications` table + report.

T4 (~200 LOC): Backfill script for existing rows; runs against `settlements_v2`, infers outcome from existing fields, writes typed enum. Idempotent + dry-run + chunked.

## Schema impact

- `Position.lifecycle_state` column (positions table, world or trades — verify).
- `settlements_v2.outcome_type: SettlementOutcome` column.
- `settlement_capture_verifications` table.
- 1 schema bump.

## Verifier probes

1. `SettlementOutcome` enum defined; IntEnum; 10 members.
2. `Position.lifecycle_state` field exists; default `UNRESOLVED`.
3. State machine: synthetic Position transitions `UNRESOLVED → PHYSICALLY_CONFIRMED → SOURCE_PUBLISHED_VENUE_UNRESOLVED → VENUE_RESOLVED_WIN → REDEEMED` all succeed; backward transition raises.
4. `harvester.py` no longer compares raw `umaResolutionStatus` strings directly; all branches go through `SettlementOutcome` classifier.
5. `SettlementCaptureVerifier` on synthetic data with incoherent timestamps (venue_resolved before source_published) raises / flags.
6. Backfill script: dry-run on 1k synthetic rows produces stable outcome assignment; idempotent.
7. CI antibody: `grep -rn "umaResolutionStatus ==" src/` returns 0 matches after Phase 7 (only typed comparisons remain).
8. Tags `phase7_track*_landed` + `phase7_landed`.

## What Phase 7 does NOT do

- Move strategies to live (Phase 6 framework gates that).
- Touch Phase 0 PR 1 era logic (already correct).
