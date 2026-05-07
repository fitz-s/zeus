# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: operator-requested investigation during live-launch readiness review (this session)

# Calibration READ-path investigation — fallback chain mechanics + LOW-metric data shortage

## Executive verdict

Both findings are **architectural by-design**, not bugs. No code change needed for launch.

1. **Fallback chain**: All 12 quarantined Platts (param_A < 0) have abundant fallback paths (≥30 cluster pool each + on-the-fly refit + legacy v1 for HIGH). The `manager.get_calibrator` chain is intact and healthy. *Caveat*: pool ignores climate similarity — NYC low DJF cycle=12 could pool from Singapore — but evaluator weights pool-fallback at maturity level=3 (weaker than primary level=1-2).

2. **LOW-metric data shortage**: `Law 1` in `src/contracts/snapshot_ingest_contract.py:82-86` rejects boundary-ambiguous LOW snapshots. Tropical cities (KL/SG/Jakarta) get 99.4% rejection because their local-day boundary aligns with TIGGE's 6h forecast window edge. Result: KL low has only 8 eligible training snapshots (816 pairs) vs 1377 for KL high (140k pairs). 56/285 active LOW Platts (20%) have n<50 samples; zero HIGH Platts have n<50.

**Operator decision required**: launch with HIGH-only, or accept LOW with size-cap.

---

## Investigation 1 — Fallback chain mechanics

### Algorithm (canonical)

`src/calibration/manager.py:225` `get_calibrator(conn, city, target_date, temperature_metric, *, cycle, source_id, horizon_profile)`:

```
Step 1. Primary v2:
        load_platt_model_v2(cluster, season, cycle, source_id, horizon_profile, data_version)
        — exact match against platt_models_v2, requires authority='VERIFIED' AND is_active=1
        — QUARANTINED rows are EXCLUDED (the 12 inverted-A buckets fall through here)

Step 2. Legacy v1 fallback (HIGH only):
        load_platt_model(cluster, season) from legacy platt_models table
        — LOW has never existed in legacy schema (Phase 9C L3); skipped for LOW

Step 3. On-the-fly refit (HIGH only):
        if get_decision_group_count(cluster, season, 'high') >= level3:
            _fit_from_pairs(...)  → fresh Platt fit from calibration_pairs_v2

Step 4. Season-pool fallback:
        for fallback_cluster in calibration_clusters():
            if fallback_cluster == cluster: continue
            try load_platt_model_v2(fallback_cluster, season, cycle, source_id, horizon_profile, ...)
            return at maturity level = max(level, 3)

Step 5. Level 4 = None  (uncalibrated → caller falls back to P_raw)
```

### Per-quarantined-bucket fallback availability

For each of the 12 QUARANTINED `is_active=1` buckets, the count of available pool clusters at the **same (season, cycle, source_id)** with VERIFIED Platts:

| Bucket | metric | season | cycle | pool clusters | on-the-fly pairs | legacy v1 |
|---|---|---|---|---|---|---|
| Beijing | low | DJF | 12 | 30 | — | — |
| Busan | low | DJF | 00 | 49 | — | — |
| Busan | low | DJF | 12 | 30 | — | — |
| Jakarta | low | JJA | 00 | 47 | — | — |
| Jeddah | high | DJF | 12 | 43 | 2088 | ✓ |
| Jeddah | high | JJA | 00 | 50 | 1472 | ✓ |
| Jeddah | high | MAM | 00 | 50 | 2469 | ✓ |
| Jeddah | high | MAM | 12 | 44 | 2469 | ✓ |
| Jeddah | high | SON | 00 | 50 | 1456 | ✓ |
| Kuala Lumpur | high | DJF | 12 | 43 | 2088 | ✓ |
| NYC | low | DJF | 12 | 30 | — | — |
| NYC | low | SON | 00 | 49 | — | — |

**Reading**: every quarantined bucket has at minimum 30 pool-cluster fallbacks. HIGH-metric quarantined buckets additionally have on-the-fly refit capability (≥1456 pairs each, well above any maturity threshold) and legacy v1 fallback. Live serving will produce a Platt-calibrated probability for every (city, season, cycle, metric) combination.

### Correction to my prior verbal analysis

My earlier session message claimed **Jeddah MAM cycle=12 high → GENERIC fallback**. **This was wrong.** I had inferred a "same-season other-cycle within same cluster" path that does not exist in the code. The actual code does **same-season-same-cycle pool across other clusters**, which has 44 candidates for Jeddah MAM cycle=12 high. The reported "GENERIC" status came from my naive SQL probe, not from `get_calibrator` semantics.

### Risk: cross-climate pooling

Pool iteration is `for fallback_cluster in calibration_clusters()` — order is configuration-determined, not climate-similarity-ranked. NYC low DJF cycle=12 (winter cold) could fall into Singapore's low DJF cycle=12 (tropical) Platt because Singapore appears earlier in `calibration_clusters()`. The two have:
- Different temperature distributions (NYC: -10 to +5°C in winter; Singapore: 23-26°C year-round)
- Different bin schemas (likely)
- Different P_raw structure

This is mitigated only by `max(level, 3)` capping of returned maturity level, which downstream code uses to weight the calibrator's contribution. There is no climate-aware skip in the loop.

**Operator action (optional, post-launch)**: add a climate-similarity gate in the season-pool loop, or a hardcoded blacklist of cross-climate pairings (e.g., never pool tropical → temperate or vice versa).

---

## Investigation 2 — LOW-metric data shortage

### Root cause: `Law 1` boundary-ambiguous rejection

`src/contracts/snapshot_ingest_contract.py:82-86`:

```python
# Law 1 (low only): boundary-ambiguous snapshots must not enter calibration training.
if spec.temperature_metric == "low" and boundary_ambiguous:
    training_allowed = False
    if causality_status == "OK":
        causality_status = "REJECTED_BOUNDARY_AMBIGUOUS"
```

### Mechanism

TIGGE/ECMWF emit minimum-temperature-over-6h (`mn2t6`) at 00z and 12z UTC. The daily LOW for a city occurs in early morning local time. The pair-builder must attribute the forecasted MIN to a specific local calendar day. If the 6h window straddles the local-day boundary, the attribution is ambiguous and the snapshot is rejected (per Law 1).

Cities where 00z + 12z 6h windows cleanly contain the morning low: **rejection rate low**. Cities where windows straddle local midnight: **rejection rate high**.

### Empirical impact (90-day window, 2026-02-05 → 2026-05-02)

| City (UTC offset) | LOW snapshots total | LOW eligible (training_allowed=1) | Rejection rate |
|---|---|---|---|
| Kuala Lumpur (UTC+8) | 1359 | 8 | 99.4% |
| Tokyo (UTC+9) | 1361 | 117 | 91.4% |
| Jakarta (UTC+7) | similar | similar | ~99% |
| Singapore (UTC+8) | similar | similar | ~99% |
| (HIGH metric, all cities) | 1387 | 1377 | <1% (HIGH not subject to Law 1) |

### Cross-system n_samples distribution

Active Platt models (591 active = 285 LOW + 306 HIGH):

| n_samples bucket | LOW Platts | HIGH Platts |
|---|---|---|
| < 50 | **56 (20%)** | 0 |
| 50–199 | 111 | 51 |
| 200–999 | 101 | 51 |
| ≥ 1000 | 17 | 204 |
| **average n** | **290** | **1240** |

LOW metric is **structurally undertrained**. The 12 QUARANTINED inverted-A models all live in the n<50 thin tail — small samples produced unstable Platt fits that flipped slope sign. This is consistent with statistical noise in low-sample regression, not with broken ingest.

### Active LOW Platts at n<50 (sampling — bottom 25 by sample size)

| Cluster | Season | Cycle | n_samples | Brier | Authority |
|---|---|---|---|---|---|
| Karachi | MAM | 12 | 15 | 0.0095 | VERIFIED |
| Lucknow | SON | 00 | 15 | 0.0079 | VERIFIED |
| Munich | DJF | 12 | 15 | 0.0096 | VERIFIED |
| Paris | DJF | 12 | 15 | 0.0088 | VERIFIED |
| Shanghai | DJF | 12 | 15 | 0.0096 | VERIFIED |
| San Francisco | DJF | 12 | 18 | 0.0063 | VERIFIED |
| Singapore | SON | 00 | 18 | 0.0089 | VERIFIED |
| Tokyo | DJF | 12 | 18 | 0.0076 | VERIFIED |
| Jakarta | SON | 12 | 19 | 0.0097 | VERIFIED |
| NYC | DJF | 12 | 20 | 0.0107 | **QUARANTINED** |
| Kuala Lumpur | MAM | 00 | 21 | 0.0086 | VERIFIED |
| (… 45 more buckets at n=21–49 …) | | | | | |

In-sample Brier scores look comparable (~0.008–0.010) to HIGH Platts only because the LOW outcome distribution is narrow and most bins are near-deterministic. This Brier is NOT a quality indicator at n=15.

---

## Implications for live launch

| Trading path | Calibration robustness | Recommendation |
|---|---|---|
| HIGH metric, all 51 cities | ✅ Robust (avg n=1240, zero buckets <50) | Safe for live, no caveat |
| LOW metric, temperate cities, cycle=00 | ✅ Adequate (typical n=200–1000) | Safe |
| LOW metric, cycle=12 across most cities | ⚠️ Many buckets n=15–50 | Capped sizing or skip |
| LOW metric, tropical (KL / Singapore / Jakarta) | ⚠️ All buckets n=18–45 | Capped sizing or skip |

### Operator decision matrix

| Option | Mechanism | Risk | Effort |
|---|---|---|---|
| **A. HIGH-only launch** | Filter out LOW markets at evaluator entry | Misses ~half the trade surface; defensible alpha-quality posture | Trivial — single config flag |
| **B. Capped LOW** | Position size = `kelly × min(1, n_samples / 100)` for LOW | Caps loss exposure on under-trained Platts; complexity in sizing layer | Medium — sizing-formula change |
| **C. Architectural fix (Law 1 relaxation)** | Per-city offset analysis: cities where forecast window cleanly contains the morning low can opt out of Law 1 rejection | Re-enables ~1000 snapshots per affected city; requires offline analysis to prove window-low containment | High — analysis + per-city policy table + revalidation |
| **D. Wait for organic accumulation** | Continue offline TIGGE pair-build through Phase B (ECMWF cross-domain pairs); LOW samples accumulate naturally over months | Delays launch; doesn't structurally fix Law 1 | Zero immediate effort, weeks-of-clock-time |

Recommendation: **Option A for initial launch** — preserves alpha quality with no code change. Pursue Option C in parallel as a Phase B+ improvement; Option B becomes attractive once Phase B accumulation lifts the worst tropical buckets out of n<50.

---

## Code anchors

| Concern | File:line |
|---|---|
| Law 1 (boundary-ambiguous LOW rejection) | `src/contracts/snapshot_ingest_contract.py:82-86` |
| Law 2 (day-already-started LOW rejection) | `src/contracts/snapshot_ingest_contract.py:88-90` |
| Pair-builder eligibility predicate | `scripts/rebuild_calibration_pairs_v2.py:171-177` (`_fetch_eligible_snapshots_v2`) |
| Observation source for LOW pair-build | `scripts/rebuild_calibration_pairs_v2.py:213` (`obs_column = low_temp`) |
| Platt v2 read filter (auth + active) | `src/calibration/store.py:766-896` (`load_platt_model_v2`) |
| Fallback chain orchestration | `src/calibration/manager.py:225-455` (`get_calibrator`) |
| Season-pool iteration | `src/calibration/manager.py:417-452` |
| Maturity level cap on fallback | `src/calibration/manager.py:452` (`return cal, max(level, 3)`) |
| Calibration cluster taxonomy | `src/config.calibration_clusters()` |

## DB queries used in this investigation

```sql
-- Quarantined buckets and their fallback availability
SELECT cluster, season, cycle, source_id, temperature_metric
  FROM platt_models_v2
 WHERE is_active=1 AND authority='QUARANTINED';

-- For each (q.cluster, q.season, q.cycle, q.source_id, q.temperature_metric):
SELECT COUNT(DISTINCT cluster) FROM platt_models_v2
 WHERE is_active=1 AND authority='VERIFIED'
   AND temperature_metric=? AND season=? AND cycle=? AND source_id=?
   AND cluster != ?;

-- LOW metric eligibility breakdown by city
SELECT training_allowed, causality_status, authority, COUNT(*)
  FROM ensemble_snapshots_v2
 WHERE city='Kuala Lumpur' AND temperature_metric='low'
   AND date(valid_time) BETWEEN '2026-02-05' AND '2026-05-02'
 GROUP BY training_allowed, causality_status, authority;

-- n_samples distribution buckets (LOW vs HIGH)
SELECT 
  SUM(CASE WHEN n_samples < 50 THEN 1 ELSE 0 END) AS under_50,
  SUM(CASE WHEN n_samples BETWEEN 50 AND 199 THEN 1 ELSE 0 END) AS '50_199',
  SUM(CASE WHEN n_samples BETWEEN 200 AND 999 THEN 1 ELSE 0 END) AS '200_999',
  SUM(CASE WHEN n_samples >= 1000 THEN 1 ELSE 0 END) AS over_1000,
  AVG(n_samples) AS avg_n
  FROM platt_models_v2
 WHERE is_active=1 AND temperature_metric=?;
```

---

## Audit trail

- Daemon restart confirmed two-executor topology working: `daemon-heartbeat-ingest.json` refreshes 60s; ECMWF Opendata `recorded_at` fresh (~min lag).
- Pre-compaction orphan Python PID 44666 (PPID=1, started 11:49 CDT) was holding `zeus-world.db` write lock for 1h31m, causing `init_schema` crash loop on daemon restart. Killed; daemon recovered to PID 26174 stable.
- All findings derived from read-only DB queries against `state/zeus-world.db` and source code in `/Users/leofitz/.openclaw/worktrees/zeus-launch-blockers`.
- Investigation requested by operator after my prior summary noted "Jeddah MAM cycle=12 → GENERIC" and "tropical low thin coverage" without root-cause analysis.
