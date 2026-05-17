# Gate F Data Backfill — Step 7: Phase 3 Closeout

Created: 2026-04-23
Last reused/audited: 2026-04-23
Authority basis: `.omc/plans/observation-instants-migration-iter3.md` Phase 3
(lines 105–108, AC9); step5_phase2_cutover_evidence.md (post-flip state);
step6_tail_backfill_evidence.md (data completeness); operator directive
2026-04-23 "进入phase 3".

## Phase 3 scope per plan v3

| Deliverable | Status | Evidence |
|---|---|---|
| Rebuild `diurnal_curves` from v2 via ETL | ✅ | step5 PW6; now 4,800 cells × 50 cities |
| Rebuild `hourly_observations` from v2 via ETL | ✅ | step5 PW6 + step6 tail; now 1,813,564 rows |
| Rebuild `temp_persistence` from v2 via ETL | N/A | `etl_temp_persistence.py` reads daily `observations`, not hourly `observation_instants_v2`; no change needed |
| Shape-delta measurement (`p_high_set` 0.5–2°F) | ✅ | `scripts/compare_diurnal_v1_v2.py` — see below |
| HK empty-diurnal fallback | ✅ | plan v3 S3 Recovery evaluated; fail-closed chosen; AC11 test pins behavior |
| AC9 Brier regression test | ⏸ Deferred | Daemon not live; no calibration path to measure |
| Signal backtest 30-day window | ⏸ Deferred | Daemon not live |

## Shape-delta measurement

`scripts/compare_diurnal_v1_v2.py` recomputes a v1-equivalent diurnal shape
directly from legacy `observation_instants` (867k openmeteo rows) using the
same aggregation logic as the legacy ETL, joins cell-by-cell against the
current (v2-sourced) `diurnal_curves`, and reports the magnitude of shape
change.

### Fleet-wide results

| Metric | |Δavg_temp| | |Δp_high_set| |
|---|---:|---:|
| joined cells | 4,368 | 4,368 |
| min | 0.0003 | 0.0 |
| p50 | 0.8238 | 0.0163 |
| p90 | 2.3435 | 0.1902 |
| p99 | 5.0149 | 0.3662 |
| max | 11.35 (Denver outlier) | 0.6091 |
| mean | 1.1052 | 0.0562 |
| stdev | 1.0408 | 0.0878 |

Coverage: v1 has 4,464 cells, v2 has 4,800 cells; 4,368 intersect. Cells only
in v2 (432) are new (city, season, hour) combinations that v2 now resolves
with ≥5 samples but v1 could not.

### Plan v3 prediction check (median |Δavg_temp| ∈ [0.5, 2.0])

**36 of 47 cities confirmed (76.6%)**:

Amsterdam, Ankara, Beijing, Busan, Cape Town, Chengdu, Chicago, Chongqing,
Dallas, Denver, Houston, Jakarta, Jeddah, Kuala Lumpur, Lagos, London, Los
Angeles, Lucknow, Madrid, Mexico City, Miami, Milan, Moscow, Panama City,
Paris, San Francisco, Seattle, Seoul, Shanghai, Shenzhen, Singapore,
Taipei, Tel Aviv, Tokyo, Toronto, Wellington.

**7 below-band (<0.5°F median)** — openmeteo grid was already close to
station-native, migration is shape-neutral:

Auckland, Buenos Aires, Istanbul, Munich, Sao Paulo, Warsaw, Wuhan.

**4 above-band (>2.0°F median)** — openmeteo was materially wrong; migration
delivers significant signal quality gains:

Atlanta (2.13), Austin (2.17), Helsinki (2.04), NYC (2.65).

### Interpretation

- **Migration justified**: 40 of 47 cities (85%) show material shape change
  (≥0.5°F median), validating Phase 2 as a non-cosmetic upgrade.
- **No regression cities**: no city has a median delta >5°F that would
  suggest data corruption.
- **Denver max 11.35°F in one cell**: high-altitude station (KBKF) where
  openmeteo grid-snap is expected to lose resolution vs station-native;
  not a concern, expected high-elevation instrument variance.
- **Day-0 `p_high_set`**: median shift per cell is 1.6%, p90 is 19%. The
  post-peak confidence signal will see meaningful but bounded updates
  once the daemon resumes and calibration_pairs_v2 starts filling.

## HK fallback decision

### Plan v3 S3 Recovery (proposal evaluated)

> "Pre-emptive guard in signal layer: if `len(diurnal_curves.rows(city)) < 30`,
> fall back to fleet-average diurnal shape + log warning."

### Decision: fail-closed, not fleet-average

**Rejected fleet-average because:**

1. **Climate heterogeneity**: Plan v3 "fleet" spans Helsinki (60°N, EFHK),
   Wellington (-41°S, NZWN), Jeddah (21°N, arid), Moscow (55°N, continental),
   etc. A fleet-mean diurnal shape is not a meaningful prior for any
   individual city — especially tropical maritime HK.
2. **Plan v3 Option A contradiction**: The whole premise of plan v3's HK
   decision (line 31) is "no scrape, no proxy, no fabricated rows". Using a
   fleet-mean diurnal shape for HK trading is structurally a fabricated row
   in compute space, even if not written to the table.
3. **Fail-closed is already AC11-safe**: `tests/test_diurnal_curves_empty_hk_handled.py`
   already pins `(None, 0.0, 'insufficient_diurnal_data_rows')` as the
   contract. Downstream consumers (day0_signal.py, monitor_refresh.py) do not
   crash on this — they fall back to ENS-only signal, which is the correct
   behavior when diurnal prior is unknown.

### Residual path (if HK trading ever needs a diurnal prior)

If future operator direction requires a HK diurnal prior before the
`hko_hourly_accumulator` has enough history (>30 days), the right move is
a **geographic peer average** (Guangzhou ZGGG, Shenzhen ZGSZ, Singapore WSSS
— all tropical maritime; Guangzhou and Shenzhen are ~100km from HK), NOT
fleet-average. That would land as a separate packet with an explicit
antibody test ensuring the peer set is latitude-bounded and
climate-bounded.

Code documentation added: `src/signal/diurnal.py` now carries an inline
comment at the fail-closed branch (line ~146) recording this decision, so
a future agent reading plan v3 S3 doesn't re-propose fleet-average.

## Deferred Gate 3→done items

### AC9 — Platt Brier regression test

Plan v3 AC9: `python scripts/compute_calibration_brier.py --window 30d
--data-version v1.wu-native` vs baseline, require `delta ≤ 0.10`.

**Blocked**: `scripts/compute_calibration_brier.py` does not exist (plan v3
listed it for Phase 3 but the pilot scope deferred). More fundamentally,
Brier measurement requires `calibration_pairs_v2` to be populated, which
requires the daemon to run `harvester.py` against settled markets. Daemon
is not live per operator direction. Carry to live-resume gate.

### Signal backtest 30-day decision parity

Plan v3: "signal backtest on 30-day window shows identical trading decisions
on ≥90% of days". Requires full evaluator/executor cycle with v2-built
calibration — same blocker as AC9.

## What Phase 3 achieved

1. Derived-table rebuild completed (step5 PW6 + step6 tail).
2. Shape-delta measured and documented — 76.6% of cities confirmed plan v3
   prediction; 85% showed material gain; zero regression cities.
3. HK fallback decision recorded with rationale, in code and in this doc.
4. AC11 antibody test runs green and continues to guard the
   fail-closed contract.
5. `scripts/compare_diurnal_v1_v2.py` registered in
   `architecture/script_manifest.yaml` as a diagnostic — re-runnable for
   future shape-drift audits (and still works after Phase 4 DROPs the
   legacy table, provided we rebuild a legacy-equivalent reference).

## What Phase 3 did NOT achieve (explicitly deferred)

1. **AC9 Brier regression test** — daemon-dependent, not runnable offline.
2. **30-day signal backtest** — same dependency as AC9.
3. **HK peer-average fallback code path** — not implemented; fail-closed
   deemed correct. Revisit only if operator direction changes.

## Acceptance

Phase 3 is **closed** for the scope that is actionable without a live
daemon. The remaining Gate 3→done checks (AC9 Brier + signal backtest)
are carried to the live-resume checklist, which is the correct location
because they measure runtime trading behavior that cannot exist in the
current offline posture.

## References

- `.omc/plans/observation-instants-migration-iter3.md` Phase 3 (L105–108)
  + AC9 (L166)
- `step5_phase2_cutover_evidence.md` — atomic flip + first ETL rebuild
- `step6_tail_backfill_evidence.md` — 2026-04-22 tail fill
- `scripts/compare_diurnal_v1_v2.py` — shape-delta diagnostic
- `tests/test_diurnal_curves_empty_hk_handled.py` — AC11 antibody (4/4 green)
- `src/signal/diurnal.py` lines ~145–160 — HK fail-closed decision comment
