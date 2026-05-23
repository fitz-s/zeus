# TIGGE ingest operator decision — 2026-05-01

**Decision:** APPROVED for live `entry_primary` use
**Operator:** Fitz
**Date:** 2026-05-01
**Authorization timestamp:** 2026-05-01T22:50 UTC (17:50 CDT)

## Scope

Activates `tigge` forecast source for `entry_primary` role under the
`forecast_source_registry.SOURCES["tigge"]` spec
(`requires_operator_decision=True`, `requires_api_key=True`,
`env_flag_name="ZEUS_TIGGE_INGEST_ENABLED"`).

Concurrent with this artifact, `ZEUS_TIGGE_INGEST_ENABLED=1` is added to the
`com.zeus.live-trading` LaunchAgent plist.

## Pre-activation evidence

- `ensemble_snapshots_v2` shows fresh TIGGE rows ingested today (`recorded_at`
  2026-05-01T18:50:55Z), `data_version=tigge_mx2t6_local_calendar_day_max_v1`,
  `model_version=ecmwf_ens`, `authority=VERIFIED`, 51 members,
  `causality_status=OK`.
- `degradation_level=OK` for the `tigge` source spec (only source authorized
  for `entry_primary`).
- All upstream gates open: cutover_guard=LIVE_ENABLED, heartbeat=HEALTHY,
  ws_user_channel=SUBSCRIBED with `m5_reconcile_required=False`,
  governor `reduce_only=False`, `entries_paused=False`.

## Risk envelope at activation

This authorization artifact is not current sizing authority. The temporary
cap-based activation envelope has been superseded: live bankroll truth now comes
from the wallet bankroll provider, and entry discipline is enforced by
RiskGuard, posture, executable-price, and max-exposure gates. Do not promote
this dated risk envelope into current live sizing, replay, or learning truth.

## Why now

Live trading was blocked all day on 2026-05-01 by a chain of structural
issues: (1) bankroll P0-A trailing-loss fiction, (2) WS ingestor missing
deps and proxy/auth misconfiguration, (3) `m5_reconcile_required` permanent
latch, (4) auto-pause tombstone after chronic exception loop. All four
fixed in same-day commits on `b4-resume-2026-05-01`. With those gates
clear, the registry's `entry_primary` requirement was the final block.
Without TIGGE active, the only available sources for `entry_primary` are
`monitor_fallback`/`diagnostic`-only OpenMeteo sources whose
`degradation_level=DEGRADED_FORECAST_FALLBACK` makes them unauthorized.
Activating TIGGE is the design-correct path to live entries (not a
workaround).

## Reversal

Set `ZEUS_TIGGE_INGEST_ENABLED=0` (or remove the env var) and reload the
plist. Source instantly returns to gated-closed; entries stop. Or move
this artifact out of the `evidence/` directory; the `requires_operator_decision`
glob check fails next daemon boot.
