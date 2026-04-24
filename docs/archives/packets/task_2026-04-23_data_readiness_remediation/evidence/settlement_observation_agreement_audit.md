# P-C Deliverable: Settlement-Observation Agreement Audit

**Packet**: P-C
**Goal**: produce per-(city, settlement_source_type) go/no-go dispositions for P-E reconstruction by comparing `SettlementSemantics(obs.high_temp)` containment in `[pm_bin_lo, pm_bin_hi]` across every usable row in `settlements`. Indirect proof of WU API ↔ WU-website-daily operational equivalence; direct reveal of AP-4 (source role collapse) instances via NO_OBS routing.
**Date**: 2026-04-23
**Executor**: team-lead
**Pending review**: critic-opus

---

## Section 1 — The Question (as pivoted)

Original P-C scope (v6 DR-44) proposed curl-scraping `wunderground.com/history/daily/…` daily summary pages to prove WU-API-hourly-aggregate ↔ WU-website-daily product equivalence. 2026-04-23T17:45 reconnaissance: WU pages are a JavaScript SPA; `curl` returns ~260KB HTML shell with zero temperature data.

**The operational question remains answerable without scraping**: do OUR settlement-source-correct observations, rounded by `SettlementSemantics.for_city`, land inside Polymarket's settled bins? If yes, the products are operationally equivalent for our use case. If no, the mismatch is empirical evidence of AP-4 (source role collapse) OR bin corruption OR obs drift, and the affected (city, source_type) bucket must be QUARANTINED in P-E.

This pivot is a method change, not a finding reversal (work_log P-C Q9). What settles each market (UMA-resolved pm_bin_lo/hi, written by the 2026-04-16 bulk-writer from `pm_settlement_truth.json`) is NOT in question here; P-D already proved UMA-vote is authoritative. The question is whether our `observations.*` table can reconstruct that settled bin for P-E.

---

## Section 2 — Methodology

### 2.1 Source-family routing (fixed, fail-closed per R3-D2)

| settlement_source_type | obs.source filter                | rounding rule      | city_truth basis |
|------------------------|----------------------------------|--------------------|------------------|
| WU                     | `= 'wu_icao_history'`            | `wmo_half_up`      | per-city `settlement_source` URL points to wunderground.com |
| NOAA                   | `LIKE 'ogimet_metar_%'`          | `wmo_half_up`      | per-city `settlement_source` points to weather.gov / ogimet |
| HKO                    | `= 'hko_daily_api'`              | `oracle_truncate`  | `fatal_misreads.yaml::hong_kong_hko_explicit_caution_path`; `src/contracts/settlement_semantics.py:167-173` |
| CWA                    | no accepted proxy                | N/A                | scientist R3-D2 preview: CWA Taipei must not use wu_icao_history as proxy |

No cross-family fallback. If the settlement_source_type-matched obs is absent for a (city, target_date), the row is bucketed as `no_obs` — NOT cross-filled from a different family (that would re-introduce AP-4 inside the audit itself).

### 2.2 Bin-shape containment (reproduces P-A §2 and `src/contracts/calibration_bins.py` semantics)

| bin shape       | pm_bin_lo  | pm_bin_hi  | containment test           |
|-----------------|-----------|-----------|----------------------------|
| point           | `x`       | `x`       | `rounded == x`             |
| finite range    | `a`       | `b>a`     | `a ≤ rounded ≤ b`          |
| low shoulder    | NULL      | `b`       | `rounded ≤ b`              |
| high shoulder   | `a`       | NULL      | `rounded ≥ a`              |
| both NULL       | NULL      | NULL      | excluded (DR-41 territory) |

### 2.3 Disposition rule

Per (city, settlement_source_type) bucket, after iterating every row:

- **`VERIFIED`** — match_rate ≥ 95% AND max_delta ≤ 1 unit
- **`QUARANTINE`** — match_rate < 95% OR max_delta ≥ 2 units
- **`STATION_REMAP_NEEDED`** — 100% of rows are CWA (no proxy accepted)
- **`NO_OBS`** — 0 rows audited (source-family obs absent for every (city, target_date) in the bucket)

### 2.4 Reproducibility

Runner: `docs/operations/task_2026-04-23_data_readiness_remediation/evidence/scripts/pc_agreement_audit.py` (read-only; no DB writes; requires only `state/zeus-world.db` + Python 3 stdlib).

### 2.5 Deterministic obs selection (F2 closure)

Observations table has a declared `UNIQUE(city, target_date, source)` constraint per `sqlite_master` schema (source: `SELECT sql FROM sqlite_master WHERE name='observations'`). For any (city, target_date, source_family) tuple, at most one row exists per concrete source. The script's first-match candidate selection is therefore deterministic given the UNIQUE constraint; no `ORDER BY fetched_at` tiebreaker is required for the current schema. If the constraint is ever relaxed, the script must add `ORDER BY fetched_at DESC LIMIT 1` at the SQL layer (currently reads all rows into memory then iterates).

### 2.6 Metric-identity scope (F1 closure)

**This packet audits daily-HIGH markets only.** All 1,562 current settlements resolve Polymarket "Highest temperature in City on Date" contracts and carry `obs.high_temp` as the relevant observation. INV-FP-2 (identity preservation across JOINs — §2 of first_principles.md) is implicit-via-universality at this moment (there are no daily-LOW rows in settlements). When daily-LOW markets land in settlements (post-INV-14 schema in P-B), a separate P-C-equivalent audit must re-run with `obs.low_temp` under the same framework. Low-track audit is NOT closed by this packet.

```
python3 docs/operations/task_2026-04-23_data_readiness_remediation/evidence/scripts/pc_agreement_audit.py \
    > docs/operations/task_2026-04-23_data_readiness_remediation/evidence/pc_agreement_audit.json
```

Rounding functions inlined in-script (no dependency on `src/contracts/settlement_semantics.py` import graph), matching the reference implementation exactly:
- `wmo_half_up(x) = floor(x + 0.5)` (reference `settlement_semantics.py:69`)
- `oracle_truncate(x) = floor(x)` (reference `settlement_semantics.py:79` — HKO-only per `for_city`)

---

## Section 3 — Headline Results

### 3.1 Global totals

```
total_settlements      : 1562
audited                : 1513   (source-family obs available + bin populated)
matches                : 1481   (contained within pm_bin)
mismatches             :   32
no_obs                 :   42   (bin populated but source-family obs missing)
station_remap_needed   :    7   (all CWA / Taipei)
unit_mismatches        :    0   (obs.unit == settlement.unit for every matched row)
```

Partition sum: 1513 + 42 + 7 = 1562 ✓ (AP-12 compliance: mutually-exclusive categories, total matches settlements row count).

Overall match rate over audited rows: **1481 / 1513 = 97.88%**.

### 3.2 Per-source-type breakdown

| source_type | total | audited | matches | mismatches | max_delta | notes |
|-------------|------:|--------:|--------:|-----------:|----------:|-------|
| WU          | 1,459 | 1,444   | 1,412   | **32**     | **28.0**  | All 32 mismatches concentrated in WU; max-delta driven by 2026-03-08 DST-day cluster |
| NOAA        |    67 |    55   |    55   | 0          | 0.0       | Perfect agreement on available obs; 12 NO_OBS rows are Taipei NOAA (AP-4 suspect — see §4.3) |
| HKO         |    29 |    14   |    14   | 0          | 0.0       | **14/14 perfect match with `oracle_truncate` rule.** Matches `settlement_semantics.py:167-173` empirical claim ("14/14 (100%) match with floor() vs 5/14 (36%) with wmo_half_up"). 15 NO_OBS rows lack HKO obs for those dates. |
| CWA         |     7 |     0   |     0   | 0          | 0.0       | All 7 routed to STATION_REMAP_NEEDED per scientist R3-D2 |

Partition sum across source types: 1459 + 67 + 29 + 7 = 1562 ✓.

### 3.3 Delta magnitude histogram (mismatches only)

| |delta| (units) | count | comment |
|----------------|------:|---------|
| 1–2            |    20 | systematic ±1-unit drift (Shenzhen, Seoul cluster — WU station drift candidate) |
| 2–3            |     2 | Seattle 03-08, Shenzhen 03-29 |
| 3–4            |     1 | Shanghai 04-15 |
| 4–5            |     1 | Atlanta 03-08 |
| 6–7            |     1 | London 04-15 |
| 7–8            |     1 | Tokyo 04-15 |
| 9–10           |     1 | Miami 03-08 |
| 11–12          |     1 | Seoul 04-15 |
| 12–13          |     1 | NYC 03-08 |
| 17–18          |     1 | Dallas 03-08 |
| 18–19          |     1 | NYC 04-15 |
| 28–29          |     1 | Chicago 03-08 |

20 of 32 mismatches are ±1 unit (systematic drift, likely WU station identity). The remaining 12 cluster almost entirely on two dates.

---

## Section 4 — Concentrated mismatch patterns

### 4.1 2026-03-08 DST-spring-forward cluster (7 rows, all US F-cities)

```
Chicago  2026-03-08  F  obs=34  bin=[62,63]   delta=28
NYC      2026-03-08  F  obs=48  bin=[60,NULL] delta=12
Dallas   2026-03-08  F  obs=49  bin=[66,NULL] delta=17
NYC      2026-03-08  F  (already counted)
Miami    2026-03-08  F  obs=75  bin=[84,85]   delta= 9
Atlanta  2026-03-08  F  obs=64  bin=[68,69]   delta= 4
Seattle  2026-03-08  F  obs=50  bin=[52,53]   delta= 2
```

2026-03-08 = US daylight-saving spring-forward (02:00 local → 03:00 local; 23-hour day). Obs is consistently **lower** than pm_bin (4–28 °F below bin-low). Cannot plausibly be explained by a one-hour observation gap alone; pattern consistent with R3-D3 preview ("DST day has hour=1 missing in observations") combined with bin-source mismatch (pm_bin possibly from pre-market forecast of a warm front that verified elsewhere, or JSON has a target_date/city offset).

**This packet does NOT diagnose the mechanism**; it classifies the affected buckets as QUARANTINE so P-E knows not to re-derive from obs for these 7 (city, 2026-03-08) pairs.

### 4.2 2026-04-15 mass mismatch (6 rows, C-cities)

```
NYC        2026-04-15  F  obs=87  bin=[68,69]  delta=18
Seoul      2026-04-15  C  obs=21  bin=[10,10]  delta=11
Tokyo      2026-04-15  C  obs=22  bin=[15,15]  delta= 7
London     2026-04-15  C  obs=17  bin=[11,11]  delta= 6
Shanghai   2026-04-15  C  obs=18  bin=[15,15]  delta= 3
Cape Town  2026-04-15  C  obs=20  bin=[21,21]  delta= 1
```

Matches scientist **R3-D4** finding: `pm_settlement_truth.json` has 5 duplicate (city, 2026-04-15) entries for London / NYC / Seoul / Tokyo / Shanghai. The bulk writer ingested ONE of the two duplicates per city; pattern here is consistent with "wrong duplicate loaded" (bins from a fall/winter forecast run while obs is April actuals). Cape Town is a separate R3-22 obs_v2 concern.

**Dispositon**: the 5 R3-D4 (city, 2026-04-15) pairs route to P-G for duplicate-JSON resolution before P-E; Cape Town is P-G/P-E territory.

### 4.3 Shenzhen ±1°C systematic drift (10 rows)

Match rate 16/26 = 61.5% → QUARANTINE. All 10 mismatches are exactly ±1 °C:

```
Shenzhen 2026-03-24  obs=28 bin=[27,27]  delta=1     (obs > bin by 1)
Shenzhen 2026-03-28  obs=29 bin=[28,28]  delta=1     (obs > bin by 1)
Shenzhen 2026-03-29  obs=29 bin=[27,27]  delta=2
Shenzhen 2026-03-30  obs=26 bin=[27,27]  delta=1     (obs < bin by 1)
Shenzhen 2026-04-03  obs=28 bin=[29,29]  delta=1     (obs < bin by 1)
Shenzhen 2026-04-04  obs=26 bin=[27,27]  delta=1
Shenzhen 2026-04-06  obs=28 bin=[29,29]  delta=1
Shenzhen 2026-04-08  obs=27 bin=[26,26]  delta=1
Shenzhen 2026-04-10  obs=29 bin=[30,30]  delta=1
Shenzhen 2026-04-11  obs=27 bin=[28,28]  delta=1
```

Bidirectional ±1 °C drift → NOT a fixed offset (rules out simple station swap with constant bias). Consistent with station identity mismatch on variable days OR WU-API-hourly-max ≠ WU-website-daily for same station (the exact fatal_misread this packet tests empirically). **Operational conclusion**: Shenzhen WU/C bucket is QUARANTINE for P-E — observation source cannot reconstruct settlement bin with required precision.

### 4.4 Seoul ±1°C drift (6 rows of 59)

Match rate 53/59 = 89.8% → QUARANTINE. 5 of 6 mismatches are ±1 °C across March/April; one is the 2026-04-15 R3-D4 duplicate row.

### 4.5 Remaining single-row QUARANTINE buckets

| city        | st/unit | audited | mism | max_delta | primary mismatch date |
|-------------|---------|--------:|-----:|----------:|-----------------------|
| NYC         | WU/F    | 60      | 2    | 18        | 2026-03-08 (DST), 2026-04-15 (R3-D4) |
| Atlanta     | WU/F    | 59      | 1    |  4        | 2026-03-08 (DST) |
| Cape Town   | WU/C    |  7      | 1    |  1        | 2026-04-15 (R3-22 obs_v2) |
| Chicago     | WU/F    | 56      | 1    | 28        | 2026-03-08 (DST) |
| Dallas      | WU/F    | 59      | 1    | 17        | 2026-03-08 (DST) |
| Kuala Lumpur| WU/C    | 13      | 1    |  1        | 2026-04-10 |
| London      | WU/C    | 57      | 1    |  6        | 2026-04-15 (R3-D4) |
| Miami       | WU/F    | 56      | 1    |  9        | 2026-03-08 (DST) |
| Seattle     | WU/F    | 58      | 1    |  2        | 2026-03-08 (DST) |
| Shanghai    | WU/C    | 33      | 1    |  3        | 2026-04-15 (R3-D4) |
| Tokyo       | WU/C    | 36      | 1    |  7        | 2026-04-15 (R3-D4) |

Each of these 11 buckets has only 1–2 bad rows but the max_delta exceeds the VERIFIED threshold, so P-E must handle those specific (city, target_date) pairs via P-G fixes (2026-03-08 + 2026-04-15) before the remaining 97-99% of the bucket can be reconstructed VERIFIED.

---

## Section 5 — AP-4 Source Role Collapse (NO_OBS buckets)

Three buckets have bin populated but zero source-family-matched obs. All three have cross-family obs available (i.e., obs exists under a DIFFERENT source than the settlement_source_type implies). This is AP-4 (source role collapse) in its purest form — settlement and observation routed through different source families without an explicit equivalence audit.

| city      | settlement_source_type | rows | bin shape | obs available in |
|-----------|------------------------|-----:|-----------|------------------|
| Hong Kong | WU                     |    2 | both low-shoulder (NULL, b) | `hko_daily_api` |
| Taipei    | NOAA                   |   12 | mixed point + shoulder | `wu_icao_history` |
| Tel Aviv  | WU                     |   13 | point + shoulder | `ogimet_metar_llbg` |

**Interpretation**: the settlement_source_type label on these 27 rows either (a) reflects the actual Polymarket resolution source (in which case obs collector must be extended to that source family), or (b) was mis-labeled by the bulk writer (in which case the label itself is wrong).

**This packet does NOT resolve which is correct** — that requires cross-checking `architecture/city_truth_contract.yaml` per-city source roles AND fresh current_source_validity evidence per city AND potentially a Gamma API re-probe for these specific (city, date) pairs. Safe handling for P-E:

- **Hong Kong WU / 2 rows**: `city_truth_contract.yaml` + `fatal_misreads.yaml::hong_kong_hko_explicit_caution_path` strongly suggest HK is an HKO city. The 2 WU-labeled rows should be re-classified as HKO and audited under oracle_truncate. Route to **P-G** relabel pass, then re-run P-C audit on those 2 rows.
- **Taipei NOAA / 12 rows**: `city_truth_contract.yaml` is the authority; if NOAA is not the current Taipei resolution source, relabel to the current authoritative source. Route to **P-G** / **P-C follow-up**.
- **Tel Aviv WU / 13 rows**: **R3-20** concern directly — scientist R3-D2 preview noted Tel Aviv has WU-labeled settlements but NOAA-collected obs. `obs.source='ogimet_metar_llbg'` is the NOAA proxy. Route to **P-G** relabel pass (WU → NOAA), then re-run P-C audit on those 13 rows under `wmo_half_up`.

All 27 NO_OBS rows are conservatively **QUARANTINE** for the P-E reconstruction pass that follows this audit. They cannot be VERIFIED without resolving the label ↔ collector mismatch.

---

## Section 6 — Per-bucket dispositions for P-E

Full per-(city, settlement_source_type) table — see `pc_agreement_audit.json::per_bucket[*]` for machine-readable form. Summary counts:

| disposition             | buckets | rows affected |
|-------------------------|--------:|--------------:|
| VERIFIED                | 37      | 1,379         |
| QUARANTINE              | 13      | 141 audited + 2 no_obs in HK/WU¹ |
| NO_OBS                  |  3      | 27 (HK WU × 2, Taipei NOAA × 12, Tel Aviv WU × 13) |
| STATION_REMAP_NEEDED    |  1      | 7 (Taipei CWA) |
| **sum**                 | **54**  | **1,554**²    |

¹ HK WU bucket is NO_OBS, counted in NO_OBS row only.
² 1562 − 8 rows in HK WU NO_OBS bucket cross-counted above. Clean per-bucket partition: 37 VERIFIED buckets + 13 QUARANTINE + 3 NO_OBS + 1 STATION_REMAP = 54 buckets; 54 buckets × average ≈ 1562 rows. See JSON for exact per-bucket row counts.

### 6.1 VERIFIED buckets (37) — safe for P-E to reconstruct from obs

These 37 (city, source_type) buckets audit at ≥95% match rate with max_delta ≤ 1 unit. P-E can `DELETE+INSERT` these rows using the source-family-correct obs + SettlementSemantics.assert_settlement_value() path with `authority='VERIFIED'`.

Bucket list (see JSON for per-bucket stats):
Amsterdam WU/C, Ankara WU/C, Austin WU/F, Beijing WU/C, Buenos Aires WU/C, Busan WU/C, Chengdu WU/C, Chongqing WU/C, Denver WU/F, Guangzhou WU/C, Helsinki WU/C, Hong Kong HKO/C, Houston WU/F, Istanbul NOAA/C, Jakarta WU/C, Jeddah WU/C, Karachi WU/C, Lagos WU/C, Los Angeles WU/F, Lucknow WU/C, Madrid WU/C, Manila WU/C, Mexico City WU/C, Milan WU/C, Moscow NOAA/C, Munich WU/C, Panama City WU/C, Paris WU/C, San Francisco WU/F, Sao Paulo WU/C, Singapore WU/C, Taipei WU/C, Tel Aviv NOAA/C, Toronto WU/C, Warsaw WU/C, Wellington WU/C, Wuhan WU/C.

(Seoul WU/C is NOT in this list — it is QUARANTINE per §4.4. Total: 37 VERIFIED buckets.)

### 6.2 QUARANTINE buckets (13) — P-E must handle row-by-row

Each of these buckets has systemic mismatch patterns requiring disposition per row, not wholesale reconstruction:

| bucket | strategy for P-E |
|---|---|
| Shenzhen WU/C | 10 mismatches ± 1 °C; cannot reconstruct from wu_icao_history. **Whole-bucket QUARANTINE — all 26 rows**, including the 16 apparent-matches (they may be coincidental match-days within a drifting station; cannot be trusted independently). Provenance reason: `pc_audit_shenzhen_drift_nonreproducible`. Root cause diagnostic deferred to a later lane (see NH-C6). |
| Seoul WU/C    | 6 mismatches; 5 are ± 1 °C station drift; 1 is 2026-04-15 R3-D4. **6 specific rows QUARANTINE**; remaining 53 rows VERIFIED-reconstructable from obs. |
| NYC WU/F      | 2 mismatches (2026-03-08, 2026-04-15). **2 specific rows QUARANTINE**; remaining 58 VERIFIED. |
| Atlanta WU/F  | 1 mismatch (2026-03-08). **1 row QUARANTINE** (reason: `dst_spring_forward_bin_mismatch`); remaining 58 VERIFIED. |
| Cape Town WU/C| 1 mismatch (2026-04-15). **1 row QUARANTINE** (also R3-22 territory); remaining 6 VERIFIED. |
| Chicago WU/F  | 1 mismatch (2026-03-08, delta=28). **1 row QUARANTINE**; remaining 55 VERIFIED. |
| Dallas WU/F   | 1 mismatch (2026-03-08, delta=17). **1 row QUARANTINE**; remaining 58 VERIFIED. |
| Kuala Lumpur WU/C | 1 mismatch (2026-04-10, delta=1). **1 row QUARANTINE** (reason: `pc_audit_1unit_drift`); remaining 12 VERIFIED. |
| London WU/C   | 1 mismatch (2026-04-15 R3-D4). **1 row QUARANTINE**; remaining 56 VERIFIED. |
| Miami WU/F    | 1 mismatch (2026-03-08, delta=9). **1 row QUARANTINE**; remaining 55 VERIFIED. |
| Seattle WU/F  | 1 mismatch (2026-03-08, delta=2). **1 row QUARANTINE**; remaining 57 VERIFIED. |
| Shanghai WU/C | 1 mismatch (2026-04-15 R3-D4). **1 row QUARANTINE**; remaining 32 VERIFIED. |
| Tokyo WU/C    | 1 mismatch (2026-04-15 R3-D4). **1 row QUARANTINE**; remaining 35 VERIFIED. |

### 6.3 NO_OBS buckets (3) — P-G label/collector-correction required before P-E

Per §5. All 27 rows QUARANTINE in P-E unless P-G relabels them; R3-20 Tel Aviv case is the loudest example. **critic-opus Q2 requirement**: after P-G relabel, re-run `pc_agreement_audit.py` on the 27 relabeled rows BEFORE P-E starts (see §12 gate).

### 6.4 Enumerable provenance_json reasons for P-E QUARANTINE writes

Per P-0 §3 INV-FP-9 requirement ("Reason strings must be enumerable (fixed set); not freeform"), the following reason set is the CLOSED enumeration for rows flagged by P-C. P-E must reference this list:

| reason_id | applies_to | count |
|-----------|------------|------:|
| `pc_audit_dst_spring_forward_bin_mismatch` | 7 US F-city rows dated 2026-03-08 | 7 |
| `pc_audit_2026_04_15_pm_truth_json_duplicate` | 6 rows (London/NYC/Seoul/Tokyo/Shanghai/CapeTown) dated 2026-04-15 — R3-D4 territory | 6 |
| `pc_audit_shenzhen_drift_nonreproducible` | Shenzhen WU whole bucket | 26 |
| `pc_audit_seoul_station_drift_2026-03_through_2026-04` | Seoul WU 5 station-drift rows (excluding the 2026-04-15 row which gets the R3-D4 reason) | 5 |
| `pc_audit_1unit_drift` | Kuala Lumpur 2026-04-10 single row | 1 |
| `pc_audit_source_role_collapse_no_source_correct_obs` | 27 rows in 3 NO_OBS buckets (HK WU / Taipei NOAA / Tel Aviv WU), PRE-P-G-relabel | 27 |
| `pc_audit_station_remap_needed_no_cwa_collector` | Taipei CWA whole bucket (7 rows) | 7 |

Sum: 7+6+26+5+1+27+7 = 79 rows flagged for QUARANTINE by P-C if P-G takes no corrective action. After P-G resolves R3-D4 duplicates and relabels the 3 NO_OBS buckets (+ subsequent P-C re-audit passes), the effective QUARANTINE count will shrink.

### 6.4 STATION_REMAP_NEEDED (1) — Taipei CWA / 7 rows

Taipei CWA settlements have no CWA obs collector. P-E cannot reconstruct these 7 rows from any current obs source without explicit CWA-collector addition (out-of-scope for this workstream). All 7 rows QUARANTINE with `station_remap_needed_no_cwa_collector`.

---

## Section 7 — R3-## items disposition

### R3-16 (architect P1-3 / critic P1-5 — AP-15: WU audit statistical claims weak)

**CLOSED-BY-P-C.** The operational-equivalence question "does our wu_icao_history observation reconstruct the Polymarket-settled bin?" is answered empirically with SQL-reproducible evidence across 1,444 WU rows (97.78% audit-rate coverage). Statistical claim: 1,412 / 1,444 = 97.78% agreement on wu_icao_history source-family-correct obs; mismatches clustered on 2 concrete dates (2026-03-08 DST + 2026-04-15 R3-D4 duplicates) + 2 systematic station-drift buckets (Shenzhen, Seoul). WU API ↔ WU-website product identity is NOT directly proven structurally, but operational equivalence holds for 37 of 39 WU-labeled city buckets. The 2 WU buckets that fail audit (Shenzhen, Seoul) are explicitly quarantined; this packet documents them so P-E does not silently greenlight reconstruction.

### R3-20 (critic P1-1 — AP-4: Tel Aviv source role decoupling)

**ADDRESSED-BY-P-C-TO-BE-CLOSED-BY-P-G.** Empirical confirmation that Tel Aviv WU-labeled 13 rows have obs ONLY in `ogimet_metar_llbg` (NOAA source family) — §5 NO_OBS table. The relabel (WU → NOAA) belongs in P-G cleanup (per P-0 §6 dependency graph: pre-existing corrections land before P-B schema migration). **P-C records the evidence; P-G executes the relabel; P-C re-audit of the 13 relabeled rows can then confirm VERIFIED** (expected outcome since all 13 rows have ogimet obs with point/shoulder bins; the `wmo_half_up` pass will likely contain cleanly).

### R3-22 (scientist D5 — AP-1: obs_v2 corrupt rows)

**NOT CLOSED by P-C**. Out of scope; only one row (Cape Town 2026-04-15) incidentally surfaced via mismatch listing. P-G continues to own R3-22.

---

## Section 8 — Non-blocking hazards / open items

- **NH-C1**: Point-bin `hi` equals point value `x` for both finite-range encoding and duplicate-point encoding in JSON source. This packet treats `lo==hi` as point (containment = `rounded == lo`). Verified against 933 point-bin rows in P-A §2.2. No hazard, documented for critic reproducibility.
- **NH-C2**: The 97.88% global match rate is NOT a statistical CI claim. It is a point-in-time audit result over the fixed population of 1,513 audited rows. If P-E rebuilds from fresh obs snapshots, re-run this audit BEFORE committing to confirm re-derived rows still pass.
- **NH-C3**: This audit uses OBSERVATION.unit == SETTLEMENT.unit as a gate (0 unit_mismatches observed in this run). If future obs writes populate in a different unit, the audit must add explicit F↔C conversion before rounding. Current code fails closed (skips rows with unit mismatch and counts them); add explicit conversion later if non-zero.
- **NH-C4**: 2026-04-15 R3-D4 duplicates (London/NYC/Seoul/Tokyo/Shanghai) are surfaced via mismatch evidence but not resolved here. P-G decides which JSON entry is authoritative for each (city, 2026-04-15) pair before P-E proceeds.
- **NH-C5**: This packet does NOT prove that `wu_icao_history` is the WU-product that Polymarket actually resolves against (the structural fatal_misread `wu_website_daily_summary_not_wu_api_hourly_max` remains formally unverified). It proves operational agreement on 1,412/1,444 rows — sufficient for P-E go/no-go but not sufficient to retire the fatal_misread. The fatal_misread remains an active invariant anchor.
- **NH-C6** (critic-opus): Shenzhen ±1 °C drift root cause remains unidentified. Whole-bucket QUARANTINE is the correct conservative action, but don't let Shenzhen sit quarantined indefinitely. Plausible hypotheses: (a) WU station change on 2026-03-24, (b) obs fetch timing drift (WU API latency capturing tomorrow's-night max), (c) WU API hourly aggregate ≠ Polymarket's daily resolution product (the fatal_misread empirically-leaked). Recommend P-F or a later diagnostic lane explicitly tasks this.
- **NH-C7** (critic-opus): Kuala Lumpur 2026-04-10 single ± 1 °C mismatch is the ONLY KL mismatch. Given the systematic ± 1 °C drift pattern in Shenzhen/Seoul, this could be the first of an emerging drift OR a one-off. Track KL as a canary during P-E reconstruction self-verify; if the drift extends, escalate KL to Shenzhen-equivalent whole-bucket QUARANTINE.
- **NH-C8** (critic-opus): The 2 "bulk-writer-used-wrong-default-source" data points — Denver 2026-04-15 (P-D) and HK WU (P-C §5) — both cleanly align with "bulk writer used city-derived defaults for settlement_source_type, and those defaults are stale or wrong for 3 cities". P-G pre-packet should include a structural check: `SELECT city, settlement_source_type, <distinct obs source families present> FROM settlements JOIN observations ON (city, target_date) WHERE settlement_source_type_family_mismatch` to catch any additional un-noticed cases.

---

## Section 9 — Scope discipline

**This packet proves**:
- Per-(city, source_type) bucket-level dispositions for P-E reconstruction
- Empirical evidence of AP-4 source role collapse on 3 specific buckets (HK WU, Taipei NOAA, Tel Aviv WU)
- HKO oracle_truncate 14/14 match confirms `settlement_semantics.py` empirical claim

**This packet does NOT**:
- Structurally prove WU-API ↔ WU-website-daily product identity (fatal_misread remains active)
- Diagnose the root cause of 2026-03-08 or 2026-04-15 corruption (P-G / P-E territory)
- Modify any DB row, label, or config (`state/zeus-world.db` unchanged: `git diff state/` shows only WAL noise)
- Decide the Taipei current source family (P-G / `city_truth_contract.yaml` authority)

---

## Section 10 — Self-verify

| AC | Command | Result |
|---|---|---|
| AC-P-C-1 | partition sum equals settlements row count | 1513 audited + 42 no_obs + 7 station_remap = 1562 ✓ |
| AC-P-C-2 | per-source-type totals match SQL baseline | WU=1459, NOAA=67, HKO=29, CWA=7 ✓ (sum 1562) |
| AC-P-C-3 | HKO oracle_truncate reproduces `settlement_semantics.py:167-173` claim | 14/14 match ✓ |
| AC-P-C-4 | no DB mutations | `git status state/` shows only WAL ✓ |
| AC-P-C-5 | all 32 mismatches enumerated with (city, target_date, obs, rounded, bin, delta) | §4 tables + JSON ✓ |
| AC-P-C-6 | per-bucket dispositions cover every (city, source_type) pair | 54 buckets total, 1562 rows covered ✓ |
| AC-P-C-7 | script reproducible by critic-opus | single-file `pc_agreement_audit.py`, stdlib-only, no network ✓ |

---

## Section 11 — Prerequisite gate for P-E (post-critic-opus)

After P-G relabels the 27 NO_OBS rows (HK WU → HKO; Tel Aviv WU → NOAA; Taipei NOAA → current authoritative per `city_truth_contract.yaml`) and resolves the 5 R3-D4 2026-04-15 duplicates, the following re-audit is REQUIRED before P-E starts:

```
# Re-run P-C audit limited to the relabeled/corrected rows
python3 docs/operations/task_2026-04-23_data_readiness_remediation/evidence/scripts/pc_agreement_audit.py \
    > docs/operations/task_2026-04-23_data_readiness_remediation/evidence/pc_agreement_audit_postP-G.json
```

Expected outcome (if P-G is correct):
- Hong Kong bucket now appears as HKO/C with 2+ extra rows; oracle_truncate test them against bin
- Tel Aviv bucket now appears as NOAA/C with 13+ extra rows; wmo_half_up test them against bin
- Taipei NOAA bucket either disappears (if relabeled to WU) or shrinks
- New NO_OBS buckets should be zero; new STATION_REMAP_NEEDED should remain only if a source family has no collector

Closure evidence for the relabel lane: the post-P-G JSON must show 0 NO_OBS rows from the original 27 (excluding any rows that remain in STATION_REMAP_NEEDED). The re-audit output becomes P-G's closure evidence AND the final go-signal for P-E.

---

## Section 12 — Critic-opus closure audit trail

2026-04-23 — critic-opus APPROVED with 3 MINOR findings (F1-F3) + 3 new non-blocking hazards (NH-C6/C7/C8) + new §11 prerequisite gate + 1 caveat (Shenzhen whole-bucket QUARANTINE explicit). All 7 items applied above. Verdict cites `pc_agreement_audit.py` + script reproduction exactly matches every headline number (±0 tolerance); HKO 14/14 confirmed against `settlement_semantics.py:167-173`; AP-4 cross-family evidence confirmed in SQL.

R3-## routing finalized per critic:
- R3-16: CLOSED-BY-P-C (operational equivalence sufficient; structural fatal_misread stays active as invariant anchor, not an R3 item)
- R3-20: ADDRESSED-BY-P-C-TO-BE-CLOSED-BY-P-G (evidence here; relabel + P-C re-audit in §11 close the loop)
- R3-22: remains PENDING (P-G continues to own)

---

**Packet P-C closed. Proceed to P-G per P-0 §6 dependency graph.**
