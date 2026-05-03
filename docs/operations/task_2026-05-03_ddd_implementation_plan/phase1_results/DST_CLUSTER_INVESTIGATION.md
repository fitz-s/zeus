# DST-Cluster v2 HALT Investigation

Created: 2026-05-03
Authority: V1_VS_V2_REPLAY_SYNTHESIS.md §5 surprise finding #3
Trigger: Replay shows 7 US/EU cities receive 2–3 v2 HALTs each clustered around 2026-03-08, plus 4 NE-US cities lose LOW-only on 2026-04-03.

## Headline

The HALTs are **two distinct incidents**, both root-caused upstream of DDD:

1. **2026-03-08 (DST spring-forward)**: `wu_icao_history` truncates at hour=1 local for **13 North-American cities**; hours 2–23 missing. Ogimet/Meteostat failover sources backfilled fully → other consumers see complete data, but DDD reads `wu_icao_history` exclusively, so it sees `cov=0` and HALTs both HIGH and LOW.

2. **2026-04-03 (NE-US morning gap, separate incident)**: `wu_icao_history` for Atlanta/Chicago/Miami/NYC misses hours 2–7 (or 2–8). **No failover backfill** — the gap remains in the DB. LOW-window (~03–08 local) caught; HIGH-window (~14–17) untouched, so only LOW HALTs fire.

DDD v2's behavior is correct in both cases — it is correctly identifying real `wu_icao_history` coverage gaps. The fix is **upstream**, in the source-tier ingest / failover layer, not in DDD.

---

## §1 Evidence — 2026-03-08

```
NYC observation_instants_v2 row counts around 2026-03-08:
  2026-03-06:  n_rows=24  distinct_hours=24      (full day)
  2026-03-07:  n_rows=24  distinct_hours=24      (full day)
  2026-03-08:  n_rows=2   distinct_hours=2       ← anomaly, only hours 0,1
  2026-03-09:  n_rows=24  distinct_hours=24      (back to normal)
  2026-03-10:  n_rows=24  distinct_hours=24
  (filtered to source='wu_icao_history')

NYC 2026-03-08 v2 row breakdown by source:
  source=ogimet_metar_klga    n=23  hours=0,1,3,4,...,23   (DST-correct: 23 hrs, skip hour 2)
  source=wu_icao_history      n=2   hours=0,1               ← truncated at DST transition
```

13 affected cities (all WU ICAO + N-American TZ): Atlanta, Austin, Chicago, Dallas, Denver, Houston, Lagos (partial — different shape: 7 random hours), Los Angeles, Miami, NYC, San Francisco, Seattle, Toronto.

Lagos is a coincidence on the same day — different shape (7 hours scattered: 2,3,9,12,14,15,19), unrelated to DST since Africa/Lagos doesn't observe DST. Already known infra-flaky station.

The 12 N-American cities all show the **identical signature**: hours 0 and 1 only. This is a clean pre-DST cutoff.

## §2 Why ogimet/meteostat backfilled but DDD still HALTs

`fetch_cov_full` in the H1-fix replay (and the production DDD evaluator) reads:

```sql
WHERE source = 'wu_icao_history' AND data_version = 'v1.wu-native'
```

This is **deliberate**: DDD evaluates the *settlement source's* availability, not "does any source happen to have data". The settlement contract is keyed on which source is the canonical settlement feed. For these 13 cities the canonical primary is `wu_icao_history`; ogimet/meteostat are evidence-of-truth fallbacks for other purposes.

So DDD correctly says: *"the canonical primary settlement source is unavailable for this day"* → HALT.

## §3 Evidence — 2026-04-03 (separate incident)

```
2026-04-03 wu_icao_history coverage for the 4 affected cities:
  Atlanta:  n=18  hours=0,1,8,9,10,...,23      (hours 2-7 missing)
  Chicago:  n=19  hours=0,1,7,8,...,23         (hours 2-6 missing)
  Miami:    n=17  hours=0,1,8,9,...,15,17,...  (hours 2-7 + hour 16 missing)
  NYC:      n=18  hours=0,1,8,9,10,...,23      (hours 2-7 missing)

NYC 2026-04-03 sources comparison:
  source=wu_icao_history      n=18  hours=0,1,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23
  (no other source present — gap NOT backfilled)
```

This is **not** a DST day (US DST already started 2026-03-08; next transition isn't until November). The pattern is a 6-hour morning outage starting around 02:00 local for the eastern-time-zone WU ICAO endpoints.

LOW-window for these cities centers around 04–07 local → directly clipped. HIGH-window 13–17 local → untouched. That's why only LOW HALTs.

**Critical**: ogimet/meteostat did NOT backfill on 2026-04-03. The gap persists in our DB. This is more concerning than 2026-03-08.

## §4 Root-cause categorization

Two distinct upstream issues; neither is a DDD bug:

| Issue | Impact | Severity | Owner |
|---|---|---|---|
| WU ICAO history endpoint has a DST-day failure mode (truncates at the local-time spring-forward boundary) | 13 cities, 1 day per spring | MEDIUM (failover saves it) | Source ingest team |
| Same endpoint occasionally misses 6 morning hours with no failover trigger | 4 cities, sporadic dates | HIGH (silent gap) | Source ingest team |

DDD correctly halts both — that is the protective behavior we built.

## §5 Recommendations

### R1 — DST-aware WU ICAO retry (P2)

The WU ICAO ingest pipeline should detect DST-day truncation and either:
- Retry the day after DST settles (~6 hours after spring-forward at the ICAO clock skew)
- OR re-fetch the day with explicit UTC ranges that bypass the local-time DST boundary

Owner: source ingest team. Out of scope for DDD work.

### R2 — Failover-trigger discipline (P1)

Codify a rule: **any (`wu_icao_history`, city, date) row count below some threshold (e.g. `< 12` hours of 24) should automatically trigger a backup-source backfill within 24 hours of detection**.

Today, ogimet sometimes backfills (2026-03-08 NYC ✓) and sometimes doesn't (2026-04-03 NYC ✗). The behavior is non-deterministic. Until this is fixed, days like 2026-04-03 leave silent gaps in the DB and DDD correctly flags them as catastrophic.

Owner: source-tier subsystem. Out of scope for DDD work.

### R3 — Document the policy (P3)

Update `docs/reference/zeus_oracle_density_discount_reference.md` §3 to explicitly state: DDD evaluates `wu_icao_history` (the canonical settlement source) availability, NOT composite-source availability. Operators reading replay reports must understand that "v2 HALT on 2026-03-08 NYC" means "the canonical source is missing", which is the correct call even when other sources have backfilled.

I will add this paragraph in the next ref-doc update unless operator prefers a separate authority note.

## §6 No code change required

This investigation is a **diagnosis memo**. No DDD module change is warranted — DDD is doing its job correctly. The two upstream fixes (R1 / R2) belong to the source-ingest workstream and are not blocking for DDD live activation. They will, however, increase the false-positive halt rate at live until R2 lands; operator should weigh whether to:

- (a) accept a higher HALT rate at launch (safer; aligned with v2 design intent of ruin-protection-first)
- (b) defer launch until R2 lands (no rush; quality first)

Operator decides. Suggested default: (a) — the additional HALT count is small (≤ 30 decisions across the test window across all affected cities) and they correspond to real source gaps we should not be trading through.

## §7 Files of record

- Replay halt inventory: `phase1_results/v1_vs_v2_replay.json` → `halt_dates`
- DST-day SQL probes: see §1, §3 of this memo
- Source-tier wiring (where R2 belongs): `src/state/db.py` ingest paths,
  not Phase 1 / DDD
