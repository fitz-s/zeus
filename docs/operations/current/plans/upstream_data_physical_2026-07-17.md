# Upstream data physical — issuance shock / mixed-cycle fusion / staleness isolation (2026-07-17)

Goal: full upstream forecast-layer体检 + redesign: per-source publication bias vs fusion, issuance-shock handling (probability jumps on new cycles), per-city best-source fusion form, latency quantification, staleness degrade ladder (per-city isolation instead of stale-q trading), non-blocking pipeline under source outage. First principle / zero-sum / act-faster-than-market. NO market-price backtests (walk-forward only; only settlement-source accuracy backtest allowed).

## Ground evidence (live DB, measured 2026-07-17)
- Publication latency (available-cycle, single_runs 7d): ukmo 7.4h avg, ecmwf_ifs 6.5h, icon_global 3.7h, nbm 3.1h, jma max 14h. previous_runs ~8h uniform. ENS ~8.3h.
- Mixed-cycle fusion: instruments served at cycle ages up to 12h apart in one fusion (anchor 18z + ukmo 12z); ENS shape 12z vs anchor 18z → delta_ens partially = cycle skew.
- Issuance shock (14d, n=8108 successive live posterior center diffs): avg |dμ| 0.265C, 15% >0.5C, 4.7% >1C, 0.5% >2C, max 4.04C.
- Posterior compute delay after source_available: avg 25min, max 11.7h (tail!). CONUS intra-target refresh gaps up to 30-37h.
- Current staleness now: Hong Kong low 10.7h old, Shanghai low 7.8h; rest ~1.5h.

## Workstreams
- W1: latency/issuance measurement + per-source cycle clock truth (explorer: exp-ingest)
- W2: issuance-shock propagation + re-decision handling (explorer: exp-redecision)
- W3: fusion weights / per-city best source / market-definition alignment (explorer: exp-fusion)
- W4: staleness degrade ladder + per-city isolation (design; consult REQ-20260717-032202-ec62d2 pending)
- W5: non-blocking queue architecture (head-of-line risk)
- Execution: worktree implement → git-master merges to live branch promptly (parallel co-workers consume commits).

## Status
- [x] Boot; all explorer reports in; exp-starve CONUS root cause DEFINITIVE (degF members_unit bug, fixed 305897168 on 07-14; the 30h darkness = missing alerting + blind receipts + fingerprint churn)
- [x] Consult v2 answered (/tmp/cgc/answer_REQ-20260717-040148-1c678b.txt) — verdict folded below
- [x] impl-ensage DONE + cherry-picked to live branch 3c532675d (ENS shape 30h age bound; 3 tests green on live)
- [ ] impl-lowens (quarantine≠missing + floor alignment) — running
- [ ] impl-receipts (typed block sub-reasons + cycle-scoped fingerprint) — running
- [ ] impl-watchdog (posterior-starvation alert before TTL) — running
- [ ] git-master loop: cherry-pick each as it lands; live branch advances continuously

## Consult v2 verdict (adopted/rejected — reasoning recorded)
ADOPTED (non-authority-changing, implementing now or next):
- (e) INVARIANT: "adding/discovering/failing optional evidence may never reduce availability of the last validated active posterior" — the class behind both incidents. Minimal Zeus form: last-good posterior already serves until 30h TTL; the gaps were alerting/receipts/retry-waste (3 impl slices running). Full candidate/active-pointer CAS architecture deferred — existing monotone-cycle + TTL already gives 80% of it.
- (a) Post-release fast lane mandatory; pre-release sigma widening REJECTED (issuance risk ≠ settlement uncertainty — polluting meteorological sigma smears bins, false edge); jump-distribution-as-q-calibration REJECTED (information arrival ≠ forecast error). Pre-positioning = machinery only (queue priority, poll hazard windows), never directional inventory.
- (f) CP-on-51-members treats dependent members as independent → overdispersion; beta-binomial or effective-n before CP is the statistically honest form. ALSO: CP+Cantelli max() composition needs simultaneous-coverage accounting. Both = follow-up math slice, settlement-graded walk-forward only.
- (b) Mixed-cycle: correct unit is ERROR VARIANCE not age haircuts: v_m(cycle-lag) from strictly-prior settlements added to diag(M); between-spread only over freshest coherent cohort (±3h); delta_ens only within one coherent ENS cycle recentered on fused center. Minimal first step: exclude stale-vintage instruments from between-spread (they may still enter center via inflated variance).
- (c) Frozen weights: nightly refit / weekly-or-7-settled-dates activation, 120d window 60d half-life, immutable versioned artifact keyed by exact model id (NEVER positional), candidates = {equal, diagonal precision, LW+bb^T simplex} activated only on out-of-sample settlement CRPS win. New model = weight zero until 25 prior settlements (kills the 0.766-cold-start class).
REQUIRES OPERATOR DECISION (authority-law changes):
- (d) Degrade ladder GREEN(<=18h)/AMBER(<=24h, settlement-fitted sigma inflation, 1-provider+ENS OK)/RED(>24h or newer-cycle-detected-not-active: no new entries, cancel makers, monitor only)/EXPIRED(30h) — contradicts current binary fail-closed + two-provider law in replacement_final_form authority doc. Trades more availability for fitted-inflation complexity.
- ENS member partial handling: interpolate single missing step + coverage-effective n_cov=(Σa)²/Σa² instead of null-member — touches leakage law (R-AH/R-AJ).
REJECTED (with reasoning):
- Same-cycle-only fusion: recreates the outage under asynchronous publication; dominated by variance-penalty form.
- Shadow-mode migration: operator law forbids; consult agrees cutover-in-one-transaction works.

## Key findings so far (file:line in explorer reports, verified spot-checks)
1. AUTHORITY DRIFT: T2/Ledoit-Wolf fusion center computed then DISCARDED (materializer:1765-1847). Live center = RAW diagonal precision mean OR frozen per-city CSV weights (51/54 cities, snapshot 2026-06-25, never re-fit). fused.sd survives only as non-source-clock fallback. Authority doc describes a dead path.
2. LIVE BUG (found via DB): low-track ENS members nulled per-scope -> MISSING_EXPECTED_MEMBERS blocks low posterior refresh for ~51 cities' far dates; run-level says COMPLETE so no retry lane fires. HK low 10.7h stale while high 1.5h.
3. Mixed-vintage fusion: per-model serving picks newest available cycle per model (up to 12h skew); between-spread computed over mixed cycles; no per-instance staleness down-weight.
4. Issuance shock real: cycle-boundary q TV distance p90=0.206, max 0.49; center jumps 4.7%>1C. Zeus posterior lags source_available by avg 25min (tail 11.7h); market can reprice first (adverse selection window).
5. Publication-lag blind spot: source_available_at - source_cycle_time persisted but never monitored; 30h TTL rests on it.
6. Isolation skeleton exists: FAMILY_ENTRY_BLOCKED (entry-only, family-scoped) never touches monitor lanes; staleness currently only expires posteriors (30h) — no graded per-city isolation tied to freshness.
7. Resting orders: BELIEF_REPRICE_DELTA=0.03 hysteresis + Q_VERSION_STALE zero-magnitude cancel (5min lane) + 20min rest deadline. Held: CI-separation + 2-cycle confirmation. New entries: no issuance-aware guard.
8. INCIDENT 2026-07-13/14 (measured): ALL CONUS cities' 07-14 posteriors starved 30-37h (Atlanta 37.3h, Seattle 32.1h, Chicago/Austin/LA 30.5h, NYC hi+lo 30.4h), all recovered same instant 07-14T14:42Z (Day0 lane). 277 failed materialize subprocesses for Chicago alone (~5min cadence, fingerprint suppression NOT effective), all BLOCKED REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET. Onset exactly at fusion_upgrade instrument_set_expansion 07-13T08:15:39 when gem_hrdps_continental (CMC) first became capturable for the target (Chicago frozen scheme weight 0.766!). Raw values/anchors/ENS all present throughout. Root-cause chain: exp-starve tracing (suspect: frozen scheme completeness/serving ceiling -> center None -> live_eligible False).
9. Second live bug ROOT-CAUSED (exp-lowens, full file:line report): LOW-track per-member boundary-ambiguous quarantine nulls member values (extract_open_ens_localday.py:574-580: value=None when boundary_min < inner_min — cross-midnight window colder); downstream treats ANY null member as hard block (ingest_grib_to_snapshots.py:386-396 missing_member_value_for_contract_extrema -> contributes_to_target_extrema=0). Nulled count == ambiguous_member_count exactly (Amsterdam low 07-19: amb=10 -> 41/51). HIGH immune structurally: extract:513-522 has NO quarantine (daytime max stays inside local day). Introduced by d13d93c37 (2026-06-10, #403): relaxed snapshot-level quarantine to 26/51 majority but left per-member nulling + downstream any-null block — three-layer semantic inconsistency. BINDING BLOCK = contributes=0 (EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA, reader :826-834,:1430 — floor-40 CANNOT save it; floor only cures MISSING_EXPECTED_MEMBERS). Run-level COMPLETE vs scope PARTIAL is by design (ecmwf_open_data.py:887-905) but means NO retry lane fires. Test blind spot: tests/test_opendata_observed_members_aggregation.py covers amb=51 and genuine-partial only, not minority-quarantine. FIX = option 1: ingest distinguishes 'quarantined by boundary rule' from 'genuinely missing' — minority-ambiguous scope keeps contributes=1 using remaining members' min; + regression test. Producer floor alignment alone (option 2) is INSUFFICIENT. Must pass leakage law (R-AH/R-AJ) review.
9b. Impacted live markets NOW: HK low 07-17 stale 11.5h; ZERO posteriors: Shanghai low 07-17+07-18, HK low 07-18, London low 07-18, Miami low 07-17, Paris low 07-18 — each w/ 11 live tokens.
9c. CONFIRMED third defect (DB proof, posterior 46741 London low 07-18): live FUSED_NORMAL_FULL posterior on carrier 07-16T18Z serves bpf.current_evidence_shape from ENS snapshot 1206110 cycle 2026-07-12T12Z — 4.5 DAYS old — as "current evidence" (within=1.30C, delta_ens=0.86C computed against stale member mean). Materializer ENS query (materializer:1339-1362) enforces causality (ENS cycle <= carrier) but NO age floor; quarantine bug starves newer contributing rows, so the query walks back indefinitely. Silent stale-as-current on the sigma path — violates root time law in spirit while passing every gate. Fix: ENS shape age bound (e.g. same 30h law or <= 2 cycles behind carrier) -> else shape=None -> non-live-grade (existing downstream handles it).
10. Retry-loop waste: 277 subprocess spawns x 240s cap on single-worker lane; SKIPPED_UNCHANGED_BLOCKED_INPUT fingerprint (queue:845-971) did not suppress (fingerprint churned or gate bypassed) — verify + fix.

## Constraints (operator law)
- Fail-closed freshness; no stale-as-fresh. No shadow modes. No gate accretion / over-engineering. Minimal machinery.
- Only settlement-source accuracy backtest allowed; strictly walk-forward otherwise.
- Live branch advances continuously; merge worktree results promptly to main tree.
