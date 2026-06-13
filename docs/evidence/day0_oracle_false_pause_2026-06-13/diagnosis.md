# DAY0_ORACLE_ANOMALY false-pause — root cause (2026-06-13)

## Verdict: STRUCTURAL_DEFECT (high confidence). The day0 entry lane is being
## strangled by a mis-calibrated tamper detector, NOT by 174 real sensor anomalies.

## Symptom (live)
- `no_trade_regret_events` last 3h dominated by
  `TRADE_SCORE | LIVE_INFERENCE_INPUTS_MISSING_DAY0_ORACLE_ANOMALY_PAUSED_<City>_2026-06-13`
  across 15+ cities; `world.day0_oracle_anomaly_flags` holds **174 active flags**
  (effectively every day0 family paused, 24h TTL).
- First appeared **2026-06-12T20:12Z** — exactly when the obs fast-lane
  (WU-prefix + METAR-tail fusion, commit `7f4ec242fa`) went live. Rolls in
  city-by-city as local midnight crosses into the target date.

## The smoking gun (flag `detail` deltas)
61 of 174 flags carry `high_delta=0.0` (HIGH agrees byte-perfect) while
`low_delta=2.0–9.0` (Milan 7.0, Jinan 9.0, Beijing 5.0, London/Munich 3.0,
Warsaw/Helsinki 4.0). Cities flagged in local morning instead show
`high_delta=2.0, low_delta=0.0` (Paris, Madrid, Sao Paulo).
- If two feeds of the SAME station agree byte-perfect on one extreme, the
  station / units / parse are all correct. The other extreme can only diverge
  if the two sides reduce it over a **different effective window** — and the
  divergent extreme is always the one sitting near a window edge (the daily LOW
  near local-day-start in the afternoon; the in-progress HIGH near the
  truncation edge in the morning).
- 174 cities cannot be 174 tampered sensors. (The detector was built for a
  SINGLE-city sensor-tampering event: Paris CDG, April 2026.)

## Precise mechanism (threshold measured one way, applied another)
- `src/data/day0_oracle_anomaly.py:check_wu_metar_divergence` compares WU
  **running extrema** (`wu_high_so_far` / `wu_low_so_far`) against METAR running
  extrema, truncated at `wu_last_obs_time`.
- The per-city threshold (`config/wu_metar_divergence.json`, ~1.0°C / 1.5°F) was
  **measured on timestamp-MATCHED same-station readings** — 21/22 cities
  byte-identical post-rounding (file header lines 50-56).
- WU and METAR have **different sampling cadences**. Near the sparse overnight
  window where the daily LOW is set, the two feeds catch different minima — a
  2-9°C cadence gap. The truncation guard (PR#404 P0-2B) only caps the FUTURE
  edge (METAR fresher than WU); it does NOT handle the past/density edge where
  the LOW lives.
- So a noise model fitted on matched readings (~0-1 unit) is applied to
  running-extrema differences (2-9 units near overnight) → false divergence →
  24h family pause → day0 entry lane (the main tradeable lane) dead.

Conforms to the operator law (2026-06-13): *new + old code fighting; a hardcoded
threshold patched over a window/cadence mismatch nobody traced.* And to the
provenance law (CLAUDE.md §4, London-DST class): code correct, DATA semantics
broken — "daily low so far" from two different-cadence feeds is not the same
physical quantity unless the extremum-setting sample is shared.

## Honest fix (refactor, not a threshold widen — widening would blind the real
## tamper detector)
Compare WU vs METAR on the SAME basis the threshold was measured: **timestamp-
matched instants** (within the 6-min report-matching tolerance). A genuinely
tampered/injected feed diverges on matched readings; a cadence-induced extremum
gap has no matched counterpart and must yield `compared=False` for that extreme
(absence of a comparable sample is not an anomaly — the file's own NONE-verdict
doctrine, lines 27-28), never a pause.

Two parts:
1. **Code**: realign `check_wu_metar_divergence` to a matched-reading basis
   (requires the WU side to expose per-instant samples, not just reduced
   extrema). Relationship test (RED-on-revert): two feeds with identical matched
   readings but an extra unmatched colder overnight METAR sample → NOT diverged
   → no pause; a feed whose matched reading is shifted > threshold → diverged
   (tamper detection preserved).
2. **Data remediation** (after the code fix deploys): clear the 174 stale
   defect-flags from `world.day0_oracle_anomaly_flags` (operator-gated live
   write via `clear_day0_oracle_anomaly`) so paused families resume immediately
   instead of waiting out the 24h TTL. Must FOLLOW the code deploy, else they
   re-flag within the 10-min WU check interval.

## Scope of unblock
174 paused families = the bulk of the day0 entry lane. This is GATE-1 (orders
exist) blocker #1. It is independent of GATE-2 (calibration / NO-on-winning-bin).
