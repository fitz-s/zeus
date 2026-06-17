# Fallback / Degraded Data — Outcome Quality Audit
**Created:** 2026-06-16  
**Scope:** All zeus DBs (zeus-world.db, zeus-forecasts.db, zeus_trades.db) read-only  
**n(settlements):** 7,514 total (VERIFIED 7,138 / QUARANTINED 376)  
**n(executed+settled trades):** 42 positions, all SETTLEMENT exit  

---

## Objective

Determine empirically whether decisions made on DEGRADED / FALLBACK / stale data settle worse than fresh-data decisions, and characterise the settlement QUARANTINE population.

---

## 1. Degradation / Freshness Tag Taxonomy

### 1a. ForecastAuthorityCertificate — `degradation_level`

| degradation_level | forecast_source_role | n certs |
|---|---|---|
| NULL (field absent — pre-schema) | NULL | 62,910 |
| OK | entry_primary | 263 |
| **DEGRADED / FALLBACK** | — | **0** |

The `degradation_level` field was added recently. Of the 63,173 ForecastAuthorityCertificates in zeus-world.db, **zero carry a non-OK degradation tag**. All 263 records that have the field say `OK`. The remaining 62,910 predate the field but are uniformly `ecmwf_open_data / LIVE_ELIGIBLE / coverage=LIVE_ELIGIBLE`.

### 1b. `edli_no_submit_receipts` — calibration source and staleness

| q_lcb_calibration_source | n | submitted |
|---|---|---|
| NULL (standard ECMWF calibration) | 62,789 | 0 |
| FORECAST_BOOTSTRAP (fallback q_lcb) | 85 | 0 |

The 85 FORECAST_BOOTSTRAP receipts used `q_src=replacement_0_1` — a bootstrap fallback probability estimate. **None were submitted.** Their `reason` values are `event_bound_final_intent_no_submit` or `SUBMIT_ABORTED_MODE_FLIPPED`, confirming the fallback path reached a NO_SUBMIT decision regardless.

### 1c. `envelope_json` staleness_violations (39 receipts with envelopes)

| staleness | n |
|---|---|
| fresh (staleness_violations=[]) | 38 |
| STALE (REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_EXPIRED) | 1 |

Only 1 of 39 envelope-tagged receipts carried a staleness violation, and it was a NO_SUBMIT.

### 1d. `opportunity_fact.availability_status` — gate decisions

| availability_status | total candidates | approved (should_trade=1) | refused |
|---|---|---|---|
| ok | 11,948 | 104 | 11,844 |
| unavailable | 23,356 | **0** | 23,356 |
| stale | 3,215 | **0** | 3,215 |
| rate_limited | 36 | **0** | 36 |

[STAT:n] Stale cohort n=3,215; unavailable n=23,356

**Every single stale or unavailable decision was hard-refused by the execution gate.** Stale-data candidates have avg_alpha=0.0 and avg_edge≈0.062 (mostly no-edge opportunities that would have been refused anyway), but the system refuses them categorically at SIGNAL_QUALITY stage before sizing is even computed.

---

## 2. Fresh vs Degraded — Settled Outcome Comparison

Because stale/unavailable decisions never pass the gate, the only meaningful fresh-vs-degraded comparison is:

### Executed and settled positions by data freshness at entry (n=42)

| Cohort | n | Wins | Losses | Win-rate | Total PnL | Avg PnL/trade |
|---|---|---|---|---|---|---|
| **avail=ok** (legacy May 2026 system, opportunity_fact linked) | 19 | 3 | 16 | 0.158 | −$7.33 | −$0.39 |
| **avail=None** (Jun 2026 edli system, no opp_fact row) | 23 | 17 | 6 | 0.739 | −$2.03 | −$0.09 |
| **avail=stale / unavailable** | 0 | — | — | — | — | — |

[STAT:n] avail=ok n=19; avail=None (new edli) n=23  
[STAT:effect_size] Win-rate difference 0.158 vs 0.739 — but see Period Effect below.

### Critical caveat — Period Effect confounds the comparison

The `avail=ok` cohort is **entirely May 2026 trades** (all `buy_yes`, early run). The `avail=None` cohort is **Jun 2026 edli-system trades**. These are different trading eras with different market conditions and model versions, not a clean A/B split on data freshness.

Checking cert-linked June trades (13 positions whose snapshot_id fell in the cert range 1,150,480–1,171,855): **all 13 show `src=ecmwf_open_data`, `deg=OK`, `cov=LIVE_ELIGIBLE`.** The two worst June outcomes (−$17.01 Karachi, −$8.25 loss) both used VERIFIED fresh ECMWF data.

[FINDING] **No degraded or fallback decisions reached settlement in either period.** The fresh-vs-degraded question cannot be answered with observed outcome data because the degraded cohort has n=0 in the executed set. The fallback/stale gate is absolute.

[STAT:n] Degraded/fallback settled positions: n=0 (out of 42 total settled)

---

## 3. Are Fallbacks Harmful? — Verdict

**Verdict: Fallbacks are TOLERABLE by architectural design, not by measurement.**

The system is built so that stale / degraded / fallback-q_lcb data never produces an executed trade:

1. `opportunity_fact.availability_status ∈ {stale, unavailable}` → refused at SIGNAL_QUALITY, should_trade=0 always (n=26,607 refused, n=0 approved).
2. `FORECAST_BOOTSTRAP` q_lcb fallback → all 85 events resolve to NO_SUBMIT (mode flip or intent gate).
3. `staleness_violations` in envelope → only 1 occurrence, still NO_SUBMIT.
4. `degradation_level` field → 263 records, all `OK`; zero `DEGRADED_*` tags have ever appeared in the live DB.

The harm model to worry about is therefore **not "do degraded decisions settle worse"** (they don't settle at all) but rather **"does refusing stale data cause missed opportunities?"** That question requires counterfactual analysis outside this DB.

---

## 4. Settlement Quarantine — Reason Breakdown (n=376 / 7,514 = 5.01%)

| quarantine_reason | source | n | date range | Avoidable? |
|---|---|---|---|---|
| harvester_live_obs_outside_bin | WU | 310 | 2025-03-09 → 2026-05-21 | Partially — WU bin boundary edge cases; harvestable with tighter rounding rules |
| harvester_live_obs_outside_bin | HKO | 8 | 2026-03-13 → 2026-05-21 | Same as above |
| harvester_live_obs_outside_bin | NOAA | 3 | 2026-04-16 → 2026-05-24 | Same |
| harvester_source_disagreement_within_tolerance | WU | 30 | 2025-01-26 → 2026-05-24 | Structural — two sources agree within tolerance but disagree on bin assignment; requires tie-break rule |
| harvester_source_disagreement_within_tolerance | HKO | 12 | 2026-05-18 → 2026-06-14 | Same — ongoing |
| harvester_source_disagreement_within_tolerance | NOAA | 3 | 2026-05-19 → 2026-06-07 | Same |
| pc_audit_dst_spring_forward_bin_mismatch | WU | 6 | 2026-03-08 | Fixed — DST spring-forward is a known one-off; patched |
| pc_audit_station_remap_needed_no_cwa_collector | CWA | 1 | 2026-03-16 | Station remap gap — needs collector |
| pc_audit_shenzhen_drift_nonreproducible | WU | 1 | 2026-03-20 | Non-reproducible — unresolvable |
| pc_audit_seoul_station_drift | WU | 1 | 2026-04-04 | Station drift period — patched |
| obs_outside_winning_bin | — | 1 | 2026-04-15 | Edge case rounding |

### Quarantine root cause analysis

**Dominant cause (n=321, 85%):** `harvester_live_obs_outside_bin` — the harvested observation (WU/HKO/NOAA) lies outside the winning bin range. This is a **data-gap problem**: the observation is real but the PM bin boundary does not contain it. This arises at bin edges when the temperature is exactly at the boundary value and rounding differs between the harvester and PM.

**Second cause (n=45, 12%):** `harvester_source_disagreement_within_tolerance` — two independent sources (e.g. WU and NOAA) disagree but the difference is within tolerance; settlement cannot be determined with certainty. This is **structurally unsettleable** without a clear tie-break authority. HKO disagreements are ongoing (12 in May–Jun 2026).

**Avoidability:** ~85% (obs_outside_bin) are potentially resolvable with a deterministic rounding authority. The 12% (source_disagreement) are genuinely ambiguous without additional resolution logic. The remaining 3% (pc_audit_*) are station-specific anomalies, largely historical and patched.

[LIMITATION] The quarantine population (n=376) contains no direct link to Zeus executed positions — we cannot determine whether Zeus held positions on these quarantined markets. The quarantine affects *observation authority* not *decision quality*.

---

## 5. Limitations

[LIMITATION] **n=42 settled trades is small.** All 42 used fresh data (avail=ok or new-edli fresh certs). There is zero empirical basis for estimating degraded-data settlement outcomes because the gate refuses them. No valid statistical comparison is possible.

[LIMITATION] **Period confound.** The `avail=ok` (May) vs `avail=None` (Jun) split reflects different market conditions, not a freshness A/B test. Win-rate difference (0.158 vs 0.739) is not attributable to data freshness.

[LIMITATION] **degradation_level schema is new** (only 263 certs, all post-Jun-7). The older 62,910 certs do not carry the field; their freshness is inferred from `reader_status=LIVE_ELIGIBLE` and `source_run_status`.

[LIMITATION] **zeus-world.db has a broken trigger** (`trg_opportunity_events_no_update` references missing `opportunity_events` table) that prevents Python `sqlite3` connections. All world-DB queries in this audit ran via the `sqlite3` CLI. Queries are read-only and unaffected by the trigger logic.

---

## Summary Answer

| Question | Answer |
|---|---|
| Do degraded/fallback decisions settle worse? | **Untestable empirically** — zero degraded decisions reached settlement. The system hard-refuses them upstream. |
| Are fallbacks harmful (must-refuse)? | **Tolerable by design** — fallback paths (FORECAST_BOOTSTRAP q_lcb, staleness violations) all terminate at NO_SUBMIT before execution. |
| Are stale-data gates working? | **Yes** — 26,607 stale/unavailable candidates, 0 approved, 0 executed. |
| What drives QUARANTINE (5% of settlements)? | **85% obs_outside_bin** (rounding/boundary gap, partially fixable); **12% source_disagreement** (structurally ambiguous); **3% station anomalies** (historical, mostly patched). |
| Are quarantine cases avoidable? | ~85% potentially fixable with deterministic bin-boundary rounding authority; ~12% require a multi-source tie-break rule (ongoing HKO disagreements are still accruing). |
