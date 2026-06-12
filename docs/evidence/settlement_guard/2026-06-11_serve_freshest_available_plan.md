# Plan evidence: serve-freshest-available (no dark scopes) — bundle reader staleness gates

Created: 2026-06-11 ~12:45Z. Authority basis: OPERATOR LAW stated three times —
2026-06-10 "如果没有新的数据，我们就应该使用上一次获取的数据，而不是一直死" and
2026-06-11 ~12:45Z "简直是自讨苦吃 没有新的就用老的" — the freshest AVAILABLE
tradeable row serves; staleness machinery pursues fresher data and brands age
honestly; it never turns a scope dark.

## Incident
At 12:00Z the 30h staleness bound expired every scope still serving the 06-10T06Z
cycle. For bucket-whitelist-excluded cities the 00Z replacement is structurally hours
away (single-runs serves a run only ~16-22h after init), so the bound turned LIVE
scopes into REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_EXPIRED blocks with NOTHING
fresher in existence to serve. Self-inflicted darkness (operator: 自讨苦吃).

## Design change (src/data/replacement_forecast_bundle_reader.py ONLY)
The two hard-BLOCK staleness returns become non-blocking provenance brands:
1. readiness `expires_at` gate (pre-selection) — expired readiness no longer blocks;
   the expiry is recorded as `staleness_violation` in the served bundle provenance.
2. selected-row cycle-age gate (`cycle_age_exceeds_bound`) — same conversion.
Selection ALREADY serves the freshest tradeable-grade row (tradeable-latest semantics,
83e87c40fe), so by construction the served row is the best that exists; blocking on
its age can only produce darkness, never freshness.

## What is preserved (not weakened)
- The staleness BOUND itself (2×cycle + measured lag, single authority in
  replacement_forecast_cycle_policy) stays the PURSUIT trigger: downloads, polls and
  re-seeds keep firing from it; the derivation tests stay green.
- The brand is observable: provenance carries `staleness_violation` +
  `served_cycle_age_hours`; a WARN log fires per serve; alarms can key on it.
- Market-phase gates (settled/closed markets) are untouched — old data never trades a
  dead market.
- Direction law, q-mode gate, bounds gates, riskguard, mainstream gate: untouched.

## Risk note
A stale forecast's σ does not auto-widen with age; the operator explicitly accepted
serving last-available data over not trading (the >51% goal line is measured on
settlements either way, and the staleness brand makes any age-correlated loss
diagnosable post-hoc).

## Antibody
tests/data/test_serve_freshest_available.py — expired readiness + over-age row still
serves WITH the brand; fresher tradeable row present → it is served (freshness law);
provenance carries the violation string.
