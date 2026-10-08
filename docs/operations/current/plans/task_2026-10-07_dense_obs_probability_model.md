# Dense-station information → Day0 settlement-bin probability (2026-10-07)

## Problem
Settlement = max/min over METAR(+SPECI) instants S of R(T_t) (NOAA page = Math.round of the METAR feed). Dense national stations (FMI 10-min etc.) observe the same air between/at those instants. Today Zeus uses them only as a binary floor (wrong: inter-METAR peaks never settle) or as weak statistical evidence. Goal: put their information into q exactly.

## Model (state space)
H = max(B, max_{t∈S_>now} R(T_t)); B = max issued METAR ints (Zeus's existing boundary).
P(H ≤ k) = 1{B≤k} · P(T_t < k+0.5 ∀ future t∈S).
T_t = f_t (ECMWF hourly path, day0_hourly_vectors) + r_t; r ~ OU(τ_c, s_c).
Obs: METAR interval [k−0.5,k+0.5); dense y_t = T_t + b_c + e, e~N(0,σ_c).
Kalman on r → posterior at now → OU propagate to future S → orthant prob (Markov recursion / MC) → bin vector; mix over ensemble members as today.

## Integration point
src/data/day0_hourly_vectors.py day0_exact_remaining_probability_vector: today future extreme per member ~ N(mu, hypot(path, instrument)); replace that marginal by the conditioned max-over-future-METAR-instants distribution. Boundary B unchanged (settlement-channel only).

## Ship gate
Out-of-sample per city: logloss(B: METAR+dense) < logloss(A: METAR-only) and false-certainty(B) ≤ A. Experiment running: branch audit/dense-station-settlement-model → artifacts/fast_obs_audit/dense_station_model/METHOD.md.

## Consult (2026-10-07)
Fresh thread REQ-20261007-003521-e5e3a6 (ref 88e0e25e9, WebCodex). Context: scratchpad/dense_theory_context.md (market semantics, channels+latency, current math, draft model, constraints). Asks (a)–(h): exact settlement functional incl. SPECI set; latent process; observation models (interval-censored, sensor flips, misaligned grid, receipt latency); exact inference + orthant prob; proper-score guarantee + 0/1-mass condition; degradation to METAR-only; universality; Zeus integration. Deliverable: docs/authority theory + artifacts/dense_obs_theory reference impl/tests.
Local A/B backtest continues in parallel (audit/dense-station-settlement-model) as the empirical check.

## Consult result (REQ-20261007-003521-e5e3a6, Pro) — PR #536 (theory/dense-obs-settlement-20261007 @ 2ecf1de71), unmerged
Adopted theory: joint state-space of temperature + accepted source-row tape (reports, SPECI/none, revisions) + receipt times; target = final accepted tape; H = max(B, max over UNRESOLVED rows U_d incl. pending-past + future + corrections). Two-scale nonstationary residual (slow forecast-error U + fast V; one-scale OU nested). Dense obs = averaging functional + bias + error, quantized; METAR = interval-censored. Exact inference = finite-state forward recursion (hidden × running extreme × revision state); Kalman/moment-matching only approximate. Ensemble weights updated by likelihood. Proper-score: gain = I(Y;D|F) + misspec terms; dense can worsen if biased/over-confident.
Corrections to my asks: (1) near-1 probabilities can be correct Bayesian — separate semantic 0/1 certificates from statistical confidence; (2) no exact finite-age forgetting under OU; (3) displayed NOAA max is preliminary (revisions) — not absorbing unless retained; (4) Finland AIP: no SPECI (local SPECIAL only); NOAA hourly predicate includes qualifying SPECI.
Verified locally: 28 reference tests pass at 2ecf1de71. DST bug confirmed (day0_fast_obs.py:866 and :3812 use +24h UTC): fix dispatched on fix/day0-fast-obs-dst-local-day.
Missing for a causal live-benefit claim: receipt-preserving cohort linked to finalized labels (live WORLD prints since 09-29 have fetched_at_utc — start accumulating).
- DST window fix: branch fix/day0-fast-obs-dst-local-day 02849df39 (5 sites: day0_fast_obs:866,:3812; day0_oracle_anomaly:638; current_target_plan:1391; time_context lead_hours_to_settlement_close). 0 regressions over 45 modules vs origin/live. Handed to Data process for gated landing (next EU DST 2026-10-25).
- DST fix LANDED on origin/live 02849df39 (Data reviewed). Not yet loaded: Data loads it at first restart after 2026-10-08 06:00Z (acceptance epoch 2 runs 10-07 04:00Z–10-08 06:00Z). First DST transition in city set: Jerusalem 10-24 23:00Z; Europe 10-25 01:00Z; US 11-01. Do not restart from this session.

## Backtest result (landed origin/live bc024fedd; artifacts/fast_obs_audit/dense_station_model/METHOD.md)
Shared-parameter A/B, NOAA-era test days (42–44/city), day-blocked 95% CI:
B−A logloss (nats/decision): Tokyo H −0.037 [−0.052,−0.024] L −0.018; Helsinki H −0.020 [−0.025,−0.014] L −0.015; Singapore H −0.015 L −0.011; Warsaw H −0.0024 (sig, tiny); Munich ≈0 (NS); Toronto ≈0 (NS). Reach P(correct)≥0.9 earlier: Helsinki/Singapore H +20 min, Tokyo H +10.
Semantic violations 0 in every city/arm. High-confidence errors: model-expected Poisson gate passes; operator's STRICT (B errors ≤ A) fails on Helsinki L (0→2), Tokyo L (15→21), Singapore H (0→1), Toronto H (3→5) — 1–2 days each, not independent.
Per-city verdicts: Toronto FLOOR_USABLE (ECCC=METAR 7416/7416, SWOB +5.5 min); Tokyo/Helsinki/Singapore NOWCAST_ONLY (B usable); Munich/Warsaw NOT_USEFUL (dense arrives 27/13 min after AWC); Amsterdam 8 days only; Madrid no archive.
Findings: errors non-Gaussian (Student-t); Helsinki same-instant agreement 86.8% over full archive (83% May → 95% Oct, non-stationary) — the "229/240" was only the last 240; OU misspecification (real peak ~1.45 h earlier than forecast) inflates A equally; Singapore 09-20 settlement page missing METAR rows (any floor fails on such days); live day0_hourly_vectors only since 10-04 → backtest used Open-Meteo previous-runs proxy (absolute logloss pessimistic, A/B diff valid).
Decision pending operator: STRICT vs model-expected high-confidence gate.

## Implementation (operator: "执行 完成后返回consult审查检验", 2026-10-07)
Gate chosen: model-expected Poisson high-confidence bound (recommended). Live directly (no shadow/flag); legacy byte-identical when dense absent.
Scope: new operator module; per-city fitted params (FORECAST-class, refit script, walk-forward); cities Tokyo/Helsinki/Singapore (H+L); new day0 semantics revision + clock-bound identity; tests + replay via shipped carrier + perf. Branch feat/day0-dense-obs-state-space (Opus agent).
Then: same-thread consult review (parent REQ-20261007-003521-e5e3a6) of the implementation diff; then hand to Data process for gated landing.

## City algorithm matrix LANDED (origin/live 24d65cb46; docs/reference/fast_obs_city_algorithm_matrix.md, registered)
Gaps (§5) — owned here:
- G1 (verified: Tokyo 760918 q≤24 =0.64 with obs 25.2): fast_admission channels → day0_evidence_finality UNKNOWN → no observed extreme in q (6 FA cities, 1,075 posteriors/48h). The admission made Day0 WORSE for these cities. Folded into the operator implementation (feat/day0-dense-obs-state-space): FA/dense as observations; B only from settlement-instant readings; integer comparisons.
- G2: Tokyo jma_amedas 10-min non-METAR readings enter the settlement fact (10-04 LOW 18.4→18 vs settled 19). Must not set B.
- G8: fast_extreme_supersedes_settlement compares raw °C not integers.
Gaps owned elsewhere / later: G3 Amsterdam KNMI writer dead (KNMI_API_KEY not in data-ingest env; key now in keychain) + Jinan AWC no writer; G4 KMA only for held exposure; G5 non-US NOAA page written only after day end (B absent intraday for 37 cities — by IP-volume law); G6 Lucknow AWC margin 7.0 stale; G7 no live markets for Auckland/Jakarta/Lagos/Jinan/Zhengzhou.
## Gap fixes dispatched (operator "把这些问题都解决", 2026-10-07)
- G1/G2/G8 → feat/day0-dense-obs-state-space (operator implementation agent).
- G3 KNMI: config/knmi_secret.json written (gitignored, from keychain); resolver + netCDF4 dep; Jinan AWC writer; G4 KMA for live Day0 families; G6 regenerate wu_metar_divergence via canonical script; G7 market discovery vs no-market → fix/fast-obs-gaps-g3-g7.
- G5: verified one batched Synoptic request returns all 37 non-US NOAA stations (200, 135 KB, 0.49 s) → add canonical_resolver intraday routes (view=all, °C batch) → fix/noaa-page-intraday-all-cities.
All three land via Data process gated restart after review; then consult review (same thread as REQ-20261007-003521-e5e3a6).
- G3/G4 fixed on fix/fast-obs-gaps-g3-g7 e3411078c (handed to Data): KNMI key resolver (env→config/knmi_secret.json); KMA polls all eligible RKSI/RKPK families. netCDF4/cftime added to requirements — must be installed into live .venv. 0 new failures over 15 modules.
- G3b Jinan: ZSJN absent from global METAR upstream (AWC 0 in 168 h) — no writer possible; channel left (harmless). G6 Lucknow 7.0 correct (real 09-06 VILK 37/27 vs settled 31); refit NOT committed (script overwrites carried_from_method; 2026-09-20 rows inflate 7 other margins — untraced). G7: NO_MARKET_LISTED (Auckland, Jakarta, Lagos, Jinan, Zhengzhou).
- G3/G4 LANDED origin/live e3411078c; netCDF4/cftime installed in live .venv by Data. Loads at first restart after 2026-10-08 06:00Z (with the DST fix).

## Implementation design (branch feat/day0-dense-obs-state-space, from origin/live bc024fedd)

Resume rule: a fresh session reads this section plus `git log origin/live..HEAD` and continues at the first unchecked step.

### Constraints that shape the design
- Never edited: `src/engine/event_reactor_adapter.py`, `src/engine/global_batch_runtime.py`, `src/execution/exit_lifecycle.py`, `src/runtime/reactor_wake.py`.
- The adapter builds the action-time Day0 q by calling `build_day0_remaining_probability_carrier` (decision-time rebuild, then a strict replay with the returned operator). The new law therefore dispatches **inside that builder**. Callers keep their arguments. The materializer's `_day0_noaa_preliminary_carrier` reaches the same dispatch.
- Adapter FA-sourced families use `day0_exact_remaining_probability_vector` (analytic). That function gets no city or clock, so the dense law cannot reach it without an adapter edit. Those families get the corrected settlement floor (G1/G2 below) but stay on the analytic remaining path. This is a named residual.

### Operator (src/data/day0_dense_state_space.py, pure)
- The backtest model is ported. Mean path `m_t = f_t + mu(h_t) + beta (f_t - S4h f_t)` is built on a 5-min grid from local 00:00 - 180 min to the next local midnight, with boundaries built in the city tz (DST 23/25 h). Rows snap to grid slots, and same-slot METARs merge into the hull of their cells.
- **Exact OU transitions over irregular gaps** replace the 5-min SMC stepping. The residual axis is a cell grid (0.05 C, +-6.5 sd), with banded sparse transitions. An optional static offset (Tokyo two-scale, tau_slow -> inf) uses Gauss-Hermite quadrature. Each node's evidence-weighted Bayes is exact per node. An optional dense-drift axis d runs when sd2 >= 0.1 s1^2 (Singapore). Below that, d folds into the fitted white mixture: Helsinki 0.044, Tokyo 0.03, Toronto 0.06 of core variance.
- METAR integer: exact interval likelihood on `[M-1/2, M+1/2)`, averaged over the cell (uniform within the cell). Dense 0.1 C reading: P(r + d + w in [x+b-q/2, x+b+q/2)) with w a two-population Gaussian mixture, cell-averaged in closed form (psi integrals).
- Extreme functional: the filter runs one column through admitted rows up to the METAR receipt cut gm. From the first pending instant (scheduled routine instant > gm) it carries K+1 threshold columns (K bin edges + an evidence column). At pending instants it kills cell fractions with R(m+r) > k (HIGH) or < k (LOW). A Poisson SPECI hazard (rate per station, 0 for EFHK/RJTT) is applied as a killing rate on 5-min steps. Then it propagates to day end.
- Boundary and rows (corrected 2026-10-07 after coordinator review; Lucknow 09-06 HIGH: METAR VILK 060730Z 37/27 in AWC, Ogimet and the IMD route; the NOAA page dropped it; settled 31):
  - **B_A, semantic certificate** = received rows of the canonical settlement product only (`noaa_wrh_<icao>` page rows; for WU/HKO cities the existing settlement channels, which are not dense-qualified). Bins below B_A (HIGH) are structurally 0. A page row is also an exact interval likelihood at its instant.
  - **Provisional rows** = received METAR integers at their instants from AWC, Ogimet and FA routes at proven METAR instants (a METAR-content mirror is not the settlement product). Each row enters the recursion as a mark with retention probability s: factor `s * 1{T in preimage(k)} * 1{k does not cross threshold} + (1 - s)`. This is exact for independent per-row retention, with no 2^n scenarios. The posterior Bayes factor emerges from it: an isolated gross row between page rows has tiny likelihood under retention, so its drop branch dominates. A provisional row never creates a semantic 0/1.
  - s is P(page retains a METAR integer row at that instant with the same integer). It is measured per station, walk-forward, from WORLD (AWC/Ogimet instants vs `noaa_wrh_<icao>` rows, NOAA era) and stored in the params artifact (Jeffreys mean, with counts). It replaces the caller's survival weight inside the dense law, consistently for every caller. Rows are treated as independent; clustered drops (Singapore 09-20, 5 rows) make that optimistic, and this is reported.
  - Resolution: page rows are grouped by fetch (fetched_at_utc). Inside the span of a received fetch the page has spoken: a provisional instant with a page row is superseded, one without is dropped, and an unreceived scheduled instant is not on the tape. Outside every fetch span, provisional rows stay retention marks and unreceived instants are pending. This was corrected after a live replay; the first version treated the page as complete before its last row and dropped Helsinki's 10-07 morning 14 C rows.
  - Pending = scheduled routine instants with no page row and no provisional row, past or future; R(T) is unknown.
  - Dense readings are likelihood terms only and never set any boundary.
- `samples`: 500 probability-vector rows drawn by (survival scenario, parameter-bootstrap variant), seeded by the identity. They are not terminal one-hot draws.
- Identity: v7 envelope. It binds the operator, the params artifact hash, the city params hash, forecast vector ids and values, the admitted-evidence digest, the **effective clock state** (gm, the pending instants, the SPECI window), the survival weight, bins and settlement semantics. Clock-dependent math cannot alias, and identical inputs at a later clock do not mint a false revision.

### Dispatch (src/data/day0_dense_evidence.py)
The builder calls `dense_remaining_carrier(...)` after its existing input validation. The dense carrier is returned only when all of these hold:
- (a) the city and metric are fitted in the artifact, and target_date > training_cutoff;
- (b) the dense channel has receipt-gated rows on the target local day and is not stale (newest observation age <= channel `max_age_minutes`);
- (c) every input is valid: unit C, no resolver_terminal, intraday decision, `current_path_state` present (its local date is the target date), forecast path covers the window, and the bin topology is valid.

Otherwise the legacy code runs with identical arguments and gives byte-identical output.
- Evidence is read through a read-only FORECAST+WORLD connection (`?mode=ro`), or the caller's connection, with `fetched_at_utc <= decision`. The hourly vectors require `captured_at <= decision`. Computation happens outside any write lock.
- Replay of a persisted dense operator recomputes or fails closed: DAY0_DENSE_STATE_SPACE_REPLAY_UNAVAILABLE. An old V2/V3 certificate for a dense-qualified family no longer replays. That is consistent with the revision bump (SCOPE: that family; DRAIN: the seed/materialization loop rewrites it; RESET: the new certificate replays).
- An LRU cache keyed by content identity makes the adapter's rebuild + strict replay pay once.

### Parameters
- `config/day0_dense_state_space_params.json` is a tracked, versioned FORECAST-class artifact with a content hash. It records training cutoff, data_version, source hashes, the walk-forward selection and B=8 day-block bootstrap variants.
- It is loaded and validated by `src/calibration/day0_dense_state_space_params.py`. Estimation is ported to `src/calibration/day0_dense_state_space_fit.py`, and `scripts/fit_day0_dense_state_space.py` refits walk-forward.
- Cities and fits:
  - Helsinki (FMI), Tokyo (JMA) and Singapore (NEA S24) are fitted from backtest data, high + low. Singapore is inert live because Zeus has no NEA writer.
  - Toronto (ECCC SWOB) is an FA route at METAR instants with value identity, so its observation model is exact identity.
  - Moscow, Lucknow, Ankara and Istanbul have METAR-integer FA routes, so their observation is the METAR interval with an earlier receipt. Their latent parameters need forecast + METAR history; if it cannot be fetched they stay legacy (reported).

### G1/G2/G8 (coordinator scope addition, corrected)
- G2:
  - FA registry rows declare `metar_instant_minutes`, validated at load.
  - `_latest_authorized_day0_fact(require_settlement_channel=True)` admits an FA row only at a proven METAR instant. Rows at non-METAR instants stay physical facts and dense likelihood terms only, so they never set a floor.
- G1:
  - `day0_evidence_finality` returns PROVISIONAL_CURRENT_SNAPSHOT for every FA/registry route source, at any instant. MONOTONE_SETTLEMENT_BOUND stays `noaa_wrh_*` only.
  - FA sources count as preliminary carrier sources (same AWC/Ogimet report-survival likelihood as AWC), so they reach q through the carrier region instead of being dropped as UNKNOWN into `fused_normal_direct`. Dense-qualified cities then price them with the dense law.
- G8: `fast_extreme_supersedes_settlement` compares settlement integers R(native) under the city's SettlementSemantics.
- Regression tests:
  - Tokyo 760918: q(bins <= 24) drops sharply, but nothing is structurally 0 from an FA row alone. A structural 0 comes only from a page row.
  - Tokyo 10-04 LOW: a non-METAR-instant 18.4 sets no floor.
  - Lucknow 09-06 HIGH: an FA/AWC row of 37 at 07:30Z with page rows <= 31 leaves no structural 0 below 37, and q(31) stays well above 0.

### Authority
- The new Day0 semantics revision (v35, both survival and resolver) is bound in `src/events/day0_authority.py`. It is process-wide, because every q_version binder uses one constant; old certificates are parsed, never restamped.
- The new operator constant and q_shape `day0_remaining_shared_carrier_dense_v1` are allow-listed where V2/V3 are: bundle reader, cycle-policy coverage SQL, day0_authority shape sets, tier0 corpus (diagnostic only).
- `docs/authority/replacement_final_form_2026_06_09.md` records the law. Root AGENTS.md gets a one-line reconciliation of the fitted-residual exclusion for this Day0 scope.

### Steps (each a commit)
1. [x] design (this section) 4f2446661; corrected (B_A page-only, per-row retention marks)
2. [x] operator module + unit tests b8a1e31a3
3. [x] fit module + refit script f6852da68; artifact 4e1327d6e (content 20ceb2f9)
4. [x] evidence/dispatch + builder integration + revision v36/v35 + allow-lists + integration tests 310567d72
5. [x] G1/G2/G8 (in 310567d72) + regression tests
6. [x] replay proof + perf (scripts/replay_day0_dense_state_space.py) + registries 35571b4dd

Rollback: revert the branch commits. No schema or DB change. Legacy paths are unchanged when the artifact is absent.

### Progress notes (2026-10-07)
- Information set: the dense carrier gathers evidence received by tau, the current-state print's first receipt, so writer, strict replay and held rebuild agree. Staleness is judged at tau.
- Page retention measured from WORLD (AWC instant vs noaa_wrh row, page-covered days 09-13..10-05): Helsinki 0.962 (1085/1127), Tokyo 0.990, Singapore 0.992, Toronto 0.987, Lucknow 0.981 (11 integer differences), Moscow 0.898, Ankara 0.991, Istanbul 0.930. The misses cluster on 2026-09-20 (multi-station page dropout); independence is optimistic there.
- Baseline (origin/live bc024fedd), 84 modules touching changed surfaces: 8441 tests, 681 failing in 49 modules (pre-existing).
- Next: replay proof script (shipped carrier, live DBs mode=ro), perf, registries.

### Results (2026-10-07)
- Artifact (train to 2026-10-05, retention to 2026-10-05; inner walk-forward selection):
  - Helsinki one|shrunk, tau 227 min, s 1.31, beta -0.75; FMI core 0.076, outlier 0.39 at w 0.27; no SPECI.
  - Tokyo one|shrunk, tau 513 min, s 1.30, beta -1.30; JMA identity (s1 0.02). The two-scale order lost inside training (0.679 vs 0.661).
  - Singapore one|plain, tau 277 min; NEA core 0.21, drift 0.30 at tau_e 100 min; SPECI 0.00068/min.
  - Toronto one|shrunk, fitted and unqualified.
  - Qualified: Helsinki, Tokyo and Singapore, high and low. Singapore is inert live because Zeus has no NEA writer.
- Live replay (read-only, 10-06 and 10-07, out of sample): 30 dense families. The shipped core matches the backtest grid oracle with max |dq| 0.0016 (median 0.0009). Per-family compute p50 0.41 s, p99 0.63 s, including the read-only DB reads, with no lock held.
- Legacy byte-identity: 8 live V2 posteriors (Madrid, Moscow x2, London, Ankara x2, Buenos Aires, Toronto) rebuild with identical sha256 on the branch and on origin/live, and equal their persisted identity and q.
- G2 live replay of the Tokyo 10-04 LOW settlement fact at 14:55Z: origin/live 18.4 (non-METAR, R = 18, wrong); branch 18.5 (R = 19 = settled).
- G1 Tokyo 760918: q(<= 24) falls from 0.64 (served) to < 0.05 (dense law, frozen fixture), with every bin semantically allowed; a page row at 25 makes those bins semantic zeros.

### Provenance verdicts (files modified)
All CURRENT_REUSABLE, audited 2026-10-07 against bc024fedd law (AGENTS.md section 0/2, invariants INV-37, settlement semantics):
- src/data/day0_hourly_vectors.py: carrier builder (6f9bca665); dispatch inserted after validation; legacy path unchanged.
- src/events/day0_authority.py (6f9bca665): revision v36/v35; FA routes provisional; dense shape and operator in the policy sets.
- src/data/day0_fast_obs.py (3c66f43ad): G8 settlement-integer supersession; the raw comparison is kept when no city is given.
- src/data/physical_current_sources.py (2026-10-06 audit): metar_instant_minutes on fast admissions.
- src/data/replacement_forecast_current_target_plan.py (02849df39): G2 METAR-instant settlement facts; metar_content_only physical facts; dense skips the fast tail.
- src/data/replacement_forecast_seed_discovery.py (4fc499647), src/data/replacement_forecast_live_materialization_queue.py (127c15b88), src/data/replacement_forecast_materializer.py, src/data/replacement_forecast_bundle_reader.py, src/data/replacement_forecast_cycle_policy.py (6f9bca665): allow-lists and routing only.
- src/state/db_writer_lock.py: two read-only script allowlist rows.
- config/physical_current_sources.json: metar_instant_minutes on the six fast-admission rows.

### Residuals (not exact)
- Adapter FA-sourced decisions run through the carrier region via finality and carrier-source routing. No end-to-end adapter decision test exists for an FA city.
- Retention independence is optimistic on clustered page dropouts (2026-09-20 hit every station).
- The forecast proxy for fitting is the previous_day1 ecmwf_ifs path; live serves the freshest causal capture.
- The tier0 held-SELL point trace reports UNSUPPORTED_POINT_KERNEL for the dense operator. This is diagnostic only.

### Rebase and late fix (2026-10-07 evening)
- Rebased onto origin/live 900b1ef90. One mechanical conflict in src/data/physical_current_sources.py (G5 current_path, G2 settlement_instant): both kept.
- a34c413a6: a page fetch resolves only the instants it spans. Found by the live replay of Helsinki 10-07 HIGH at 20:07 local: q was 1.0 on 13 although AWC showed 14; after the fix, 1.0 on 14.

## Consult NO-GO (REQ-20261007-125708-2dc18b) and D2 measurement — STOPPED at D2(i)

Rebased onto origin/live b452210d5 (clean). Coordinator decisions D1-D7 recorded in the coordinator brief. The order is D2(i)-(iii), then D1 and onward. D2(i) > 0, so I stopped before D1 as instructed. Every query below is read-only WORLD (`?mode=ro`, `query_only`), run 2026-10-07 at about 18:40Z.

### D2(i) page-row revision (observation_prints, noaa_wrh_*, 2026-09-13..2026-10-07)
- 40,399 page instants (station, instant): 33,439 °C and 6,960 °F, from 1,946 distinct fetch receipts.
- **Value changed across fetches: 31 instants (0.077 %), all °F, all on the 9 US intraday resolver routes** (KATL, KAUS, KDAL, KHOU, KLAX, KLGA, KMIA, KSEA, KSFO), dated 2026-10-01..10-05 across 20 station-days. Each changed exactly once, 4–19 min after first receipt (p50 5.5 min).
  - Every one follows the same pattern. The first value is a tenth-°C conversion (e.g. 75.02 °F = 23.9 °C), and the second is a whole-°C conversion (75.2 °F = 24 °C). It is the page swapping the precise T-group value for the body integer.
  - **17 of the 31 change the settlement integer** (8 up, 9 down), e.g. KAUS 2026-10-02T03:30Z 73.94 → 73.4 °F (74 → 73).
  - No °C page instant ever changed value (0 / 33,439).
  - 5,894 of 6,960 °F instants are still tenth-°C valued at their latest version, so the page does not always revise.
- **Row vanishing after appearing: 0 observed.** For an earlier-seen instant to be testable it must fall strictly inside a later multi-row fetch's span: 891 °C and 235 °F such fetches; 0 testable instants, 0 absent. The recent_minutes=180 fetch window means later fetches never re-cover earlier rows. **The ledger cannot observe removal at all**, so vanishing is unmeasured, not measured zero.
- Daily derived extreme (daily_observation_revisions, noaa_wrh): 145 re-fetch records, 1 changed value. Denver 2026-09-14 low 71.24 → 67.82 °F; the low time moved 20:58 → 23:58 local after a re-fetch two days later. That is a later row arriving, not a revision of an existing row.

**Verdict: D2(i) > 0.** For °F pages a received page row is not immutable: in 17 cases its settlement integer changed minutes after first receipt. This is a law fork against `day0_evidence_finality` treating `noaa_wrh_*` as MONOTONE_SETTLEMENT_BOUND. For °C pages: 0 value revisions over N = 33,439 instants; removal is unobservable in the current ledger.

### D2(ii) page visibility lag, first page receipt minus observation instant
- All history, minutes, p10/p50/p90/p99 (per-station table in the agent report):
  - US 60-s routes: p10 ≈ 4–12, p50 ≈ 490–675 (backfilled history dominates).
  - The other 37: p10 ≈ 220–335, p50 ≈ 735–996, p99 ≈ 3,800–5,000.
  - This reflects the after-day-end daily fetch before G5.
- **Since intraday polling (≥ 2026-10-07T14:00Z):**
  - °C: n = 261, p10/p50/p90/max 5.9 / 13.0 / 33.3 / 84.6 min;
  - °F: n = 46, 4.1 / 5.9 / 13.0 / 22.9 min.
  - About 4 hours of history; not yet a distribution per station.

### D2(iii) final outcome of AWC METAR rows on finalized page-covered days
- Finalized means day end + 2 days before now; page-covered means ≥ 20 h of page span that day.
- 36,442 rows: **kept 35,861 (98.41 %), absent 514 (1.41 %), corrected to a different integer 67 (0.18 %).**
  - Corrections are all US °F routes plus VILK 10 / 994 and RPLL 1, SAEZ 1 (KORD 17).
- Absence clusters in time:
  - 2026-09-20 holds 257 of 514 absent rows. Hours 03–07Z have 30–62 absent rows each across all stations, the shared outage.
  - 2026-09-22 11Z: 31.
  - Excluding those, absences are 1–4 per UTC hour.
- Per station: UUWW 105 / 1,089 and LTFM 47 / 1,027 are high (station-specific); others 2–19.
- By report kind (all finalized days): routine 60,318 kept / 836 absent; SPECI 1,569 kept / 118 absent. A SPECI is about 5× more likely absent.
- Helsinki retention figure reconciled: the artifact cohort is 1,035 / 1,043 (AWC instants vs page, page-covered days ≤ 10-05). The plan's earlier 1,085 / 1,127 was a different (pre-fit probe) cohort that included 09-20 days with partial page coverage.

### Status
Stopped before D1 per brief: the D2(i) law fork needs a coordinator decision (how to treat °F page revisions: the precise→integer swap on US resolver routes).
- Fork impact check: of the 17 integer-changing °F revisions, 1 moved a day extreme.
  - KAUS 2026-10-02 LOW: first value 74 at 11:30Z → 73, and the day's LOW went 74 → 73; settled 73, so the revised value is the settlement one.
  - The page's first-received °F value is therefore not a safe semantic boundary for that row within about 20 min of receipt.
  - The remaining 16 were not the day extreme.

### Coordinator ruling on D2(i) (2026-10-07)
- °C page rows stay the semantic certificate (0 revisions / 33,439). Deletion-after-appearance is a named residual, the same assumption live's MONOTONE noaa_wrh makes; G10 (another lane) will measure it. No page-row retention marks in this branch.
- **G9 (out of scope here):** the °F page swaps its tenth-°C conversion for the whole-°C conversion within about 20 min, 31 / 6,960 instants, and once moved a settled LOW (KAUS 10-02). Nothing in this branch changes noaa_wrh °F finality or the legacy boundary; the dense operator is °C only.
- D2 implementation gets the refinements in the coordinator message: retention per station × report kind; a removal mixture of outage vs isolated (plausible vs gross); a shared-UTC-interval outage latent Z; a measured corrected branch; a posterior-predictive visibility a(lag) pooled over °C stations with exact 180-min fetch coverage.
- Order: D1 → D3/D4 → D2 implementation → D5 → D6 → D7. Stop if D3 needs a protected-file seam.

## D1 typed route kinds (2026-10-08; 5dc27c95b, f80a7b7dc)
- `RouteKind` on every registry route (src/data/physical_current_sources.py):
  - RESOLVER_PAGE: canonical_resolver rows (noaa_wrh, wu_station_history).
  - NATIVE_REPORT: proven fast-admission mgm_metar (LTFM, LTAC), imd_olbs_metar (VILK), metaviatelecom_metar (UUWW).
  - INSTRUMENT_PROXY: jma_amedas (RJTT), eccc_swob (CYYZ).
  - PHYSICAL: everything else (FMI, DWD, IMGW, KNMI, WU current).
- `metar_instant_minutes` and `settlement_instant` are gone. The kinds make them redundant: a native report counts at every report instant (routine or SPECI), and a proxy counts at none.
- Day0 fact law (`_latest_authorized_day0_fact`):
  - The settlement channel is the resolver page only.
  - A native report is a physical channel under AWC's unit and margin law. Its value must be the report integer.
  - A proxy is no fact at any instant: not the settlement fact, not the physical frontier.
  - PHYSICAL rows stay physical facts, as on live.
- Predicate membership: `day0_is_noaa_preliminary_source` gains native routes only, through `day0_is_native_report_source(source, station=...)` (registry kind check, station-bound). The proxy broadening is retracted.
- Native value identity with AWC, same report (read-only WORLD): MGM 285/285 and 318/318, metaviatelecom 61/61, IMD 301/305. The 4 IMD differences each equal another AWC version of that report.
- Named exceptions to legacy byte-identity (intentional corrections):
  - G1: the seed's physical fact is METAR content only (`metar_content_only=True` in seed discovery). A physical-only station (FMI) never conditions a seed. Test: `test_g1_physical_only_dense_station_never_conditions_a_seed`.
  - G8: `fast_extreme_supersedes_settlement(..., city=)` compares settlement integers. Test: `test_g8_supersession_compares_settlement_integers`.
  - D1 itself: proxy rows leave the Day0 physical fact (live used them, e.g. Tokyo 10-04 LOW 18.4). Native rows take AWC's margin law (live took them raw). Test: `test_g2_instrument_proxy_is_never_a_day0_fact`.
- Regressions (tests/test_day0_route_kinds.py): 15 tests; 10 fail on the pre-D1 head 523654b81.
  - The consult's Tokyo case (JMA 24.6, page 24.6, AWC 25) binds at the real `_global_day0_execution_payload` for ENTRY and HELD.
  - Six cities: the seed's conditioning binds at the adapter for ENTRY and HELD. A plateau clock advance binds HELD and refuses ENTRY, as for AWC.
  - Archived LTFM SPECIs 300214Z/300302Z/301033Z go parser → ledger → fact = 20 at 10:33Z, for both seed and adapter.
  - Materialization with no current-state print and no hourly vectors: native-sourced requests end exactly as AWC (`DAY0_NOAA_PRELIMINARY_CARRIER_CURRENT_TEMPERATURE_STATE_MISSING`, carrier extreme 21.0). Proxy-sourced requests stay outside the carrier region (`..._SOURCE_INVALID`, as on live).
- Failure-set diff, same whole modules (route_kinds, dense integration, dense state space, station adapters, current_target_plan, seed_discovery):
  - head: 53 failed / 280 passed.
  - base b452210d5 (current_target_plan + seed_discovery): 53 failed / 77 passed.
  - Same 53 ids, pre-existing: `MODEL_SURFACE_UNSUPPORTED` fixtures, and HKO seed fixtures lacking `city.timezone`.
- Residual:
  - An ENTRY 15-min fast-observation staleness gate keys on the literal `aviationweather_metar` in the protected adapter (`_day0_replacement_conditioning`), so a native-sourced conditioning does not get that gate.
  - A native report reaches entry only through the carrier/survival path. The protected file is unchanged.

## D3/D4 seam verification (2026-10-08) — STOPPED: protected-file seam
D3 requires a new decision to evaluate at the carrier's sealed probability cutoff. Explicit V2/V3 must run legacy, and explicit DENSE must replay from sealed evidence. What the protected adapter (`src/engine/event_reactor_adapter.py`, read-only) actually passes:
- Selection (new decision) and replay share one input set. Every adapter call reaches `build_day0_remaining_probability_carrier` with the clock and operator below, and no field says "select" or "replay":
  - Strict replay `_day0_remaining_p_raw_vector` (~46826): reached from the held direct path at ~45919 and from `_snapshot_p_raw` at ~46752, both with the caller's `decision_time`. Identity inputs carry `decision_time_utc=decision_time` (47134). Operator is the persisted `_edli_day0_probability_operator` (47204). The persisted `_edli_day0_remaining_carrier_probability_cutoff_utc` is read only to check cutoff ≤ decision (47054–47066). It is never passed to the builder.
  - Decision-time rebuild `_rebuild_decision_time_day0_carrier` (48728): `cutoff = decision_time` (48892). Operator is `None` only with resolver_terminal, else explicit V3/V2 (`final_values_native` decides). Callers are ENTRY current path (50540), held current bundle (50556), held shared current path (50573) and held A' (49051). The cutoff is written into the payload (49004).
  - The materializer writer (`_day0_noaa_preliminary_carrier`, not protected) passes no operator and `decision_time_utc=computed_at`.
- Consequences against D3:
  1. "Dense dispatch only when operator is None or DENSE; explicit V2/V3 runs legacy." Every adapter rebuild passes explicit V2/V3. Under that rule the dense law would never serve a live adapter decision; it would serve the materializer posterior only. Strict replay of a dense certificate passes DENSE, which is fine. The adapter's own fresh ENTRY/held q, however, is always V2/V3: the protected file chooses the operator, and no typed input says "dense allowed".
  2. "Evaluate at the carrier's sealed cutoff." The adapter rebuild sets the cutoff to its own decision_time. Strict replay passes decision_time and keeps the sealed cutoff private. The builder cannot tell a new decision (cutoff = now) from a replay (cutoff = sealed) without inferring purpose from the clock, which is the heuristic the brief forbids.
  3. "Seal admitted evidence into the carrier so replay needs no DB read." The builder can return sealed evidence in the carrier (`dense_evidence`). But strict replay passes no persisted carrier fields to the builder: only future/final extremes, boundary, bins, identity_inputs and operator. So replay cannot consume sealed evidence without a new builder input from the protected caller.
- What exists without a protected edit:
  - The materializer can select dense (operator None) and seal evidence into `day0_preliminary_report_survival_likelihood.dense_evidence`.
  - Strict replay of that certificate (operator = DENSE) still re-reads the DB, at the caller's decision_time.
  - The adapter rebuild for ENTRY/held always overwrites q with V2/V3.
- Minimal typed seam needed in the protected adapter. Either:
  - (a) pass a typed evaluation purpose plus the sealed dense evidence into the builder on strict replay; or
  - (b) pass `operator=None` (dense-eligible) on rebuild together with the sealed cutoff.
  Both need an edit to `event_reactor_adapter.py`.
- D4 (one prepared dense request deciding seed fast-tail suppression, lag detection and dispatch) does not need the protected file. It is held because its dispatch half depends on the D3 decision above.

## R3 seam spec for the protected adapter (requested only after D6 qualifies)
File: `src/engine/event_reactor_adapter.py`. The builder already accepts the inputs, since R1 landed on this branch (`build_day0_remaining_probability_carrier(..., evaluation: Day0CarrierEvaluation | None, sealed_dense: Mapping | None)`). With `evaluation=None` every operator is byte-identical to live.

1. Decision-time rebuild, `_rebuild_decision_time_day0_carrier` (def ~48728; cutoff at ~48892; builder call at ~48977):
   - Pass `evaluation=Day0CarrierEvaluation.SELECT`.
   - Pass `operator=None` instead of the explicit V3/V2 (~48967–48973) only when `src.data.day0_dense_evidence.dense_serves(conn, city, metric, target_date, decision=decision_time)` is true. Otherwise keep the explicit legacy operator.
   - Keep `decision_time_utc = probability_cutoff_utc = cutoff` (already the decision cut, ~48892).
   - After the call, persist `carrier["dense_evidence"]["sealed"]` into the payload as `_edli_day0_dense_sealed_evidence` whenever `carrier["operator"] == DAY0_DENSE_STATE_SPACE_OPERATOR`.
   - Callers: ENTRY current path ~50540, held current bundle ~50556, held shared current path ~50573, held A' ~49051.
2. Strict replay, `_day0_remaining_p_raw_vector` (def ~46826; identity inputs ~47134; builder call ~47204–47208):
   - Pass `evaluation=Day0CarrierEvaluation.REPLAY`.
   - Pass `sealed_dense=payload.get("_edli_day0_dense_sealed_evidence")`.
   - The persisted operator is passed already, so a dense certificate replays from sealed evidence with no DB read. Without sealed evidence it fails closed with `DAY0_DENSE_REPLAY_SEALED_EVIDENCE_MISSING`.
3. Persisted-carrier binding (~38956 `carrier_fields`): add `"day0_dense_sealed_evidence": "_edli_day0_dense_sealed_evidence"`.
   - The materializer then writes `day0_dense_sealed_evidence` into provenance, and only once (1) and (2) exist (R2). Today it passes no `evaluation`.
4. ENTRY staleness gate, `_day0_replacement_conditioning` (~37595–37640): `fast_sources` lists `aviationweather_metar` literally. Extend it with native report routes:
   `or src.events.day0_authority.day0_is_native_report_source(conditioned_source)`
   That way a native-report conditioning gets the same 15-min ENTRY age contract as AWC (D1 residual).

Test matrix for the adapter owner (all exist on this branch except the adapter-level rows, marked *):

| case | old certificate | dense now | path | expected |
|---|---|---|---|---|
| legacy V2/V3 persisted | V2/V3 | any | strict replay (REPLAY, explicit op) | byte-identical q/samples/identity (tests: `test_no_evaluation_is_legacy_and_never_reads_dense`, `test_select_with_explicit_legacy_operator_runs_that_operator`) |
| dense persisted with sealed | DENSE + sealed | absent/refitted law | strict replay | reproduces without DB (`test_select_serves_dense_at_the_cut_and_replay_reproduces_without_db`); fails closed when the law hash is gone (`test_replay_fails_closed_without_sealed_evidence_or_parameters`) |
| dense persisted, requalified | DENSE + sealed | metrics changed only | strict replay | replays (`test_requalification_alone_keeps_sealed_certificates_replayable`) |
| new ENTRY decision | none | serves | rebuild (SELECT, op None) | dense carrier sealed at the cut * |
| new ENTRY decision | none | absent/stale/wrong station/missing forecast/unqualified | rebuild | byte-identical legacy (`test_unavailable_dense_select_is_byte_identical_legacy`) |
| HELD redecision | V2/V3 | serves | rebuild | dense successor carrier; old certificate still replays * |
| rollback to live code | DENSE + sealed | — | live strict replay | live builder rejects the unknown operator → `unsupported Day0 remaining carrier operator`; held family falls to its legacy rebuild (D7) * |
| native-report ENTRY | AWC-equivalent | — | `_day0_replacement_conditioning` | 15-min age gate applies * |

## R1/R2/D2–D7 (2026-10-08)
Commits: b286c5a20 (R1 seam, operator rewrite, R2 live revert), edc78826b (sealed evidence, D4, params v2), 996f4d0e4 (pure sealed assembly), 22ce023b1 (D6 fit/qualification, D2 regressions), 44278e89a (refit artifact + D6 report), 6948b75ac/e5fdc490d (replay script), ab403ea1b (R3 seam spec).

- R1: `build_day0_remaining_probability_carrier(evaluation=None)` is legacy for every operator; dense only via `Day0CarrierEvaluation.SELECT` with operator None, or `REPLAY` with the dense operator plus `sealed_dense`. Unknown operators are rejected on the typed path.
- R2: no caller passes `evaluation`, so no dense certificate reaches the DB. The Day0 revision, coverage SQL, reader/policy allow-lists and seed/lag suppression are live content. Live behaviour is legacy plus D1/G1/G8.
- D3: SELECT admits everything received by `probability_cutoff_utc`; the latest received version of each instant wins. Sealed evidence is ~6.8 KB per certificate (live replay, Tokyo high, 7 cuts: p50 6,786 B, max 6,836 B; compute p50 0.79 s, max 1.33 s; REPLAY byte-equal 7/7). At a 30-min decision cadence for one qualified family that is ~330 KB/day.
- D4: `prepare_dense_request` is the one qualification and construction; `dense_serves` and SELECT both call it. Reasons: NOT_QUALIFIED, STATION_MISMATCH, NO_DENSE_CHANNEL, CUT_OUTSIDE_LOCAL_DAY, VECTOR_WINDOW_INCOMPLETE, DENSE_ROUTE_MISSING, DENSE_ABSENT_TODAY, DENSE_STALE. On any reason SELECT returns the legacy carrier byte for byte (tests for absent/stale/wrong-station/missing-forecast/unqualified).
- D2 lifecycle (pooled C stations, train cut 2026-09-26, 18,087 reports):
  - routine kept 0.9957, removed 0.0037, gross 0.0002;
  - SPECI kept 0.717, removed 0.277, gross 0.003;
  - outage prior 0.026 (468 reports in shared-outage hours: 09-20 02–07Z, 09-22 10–11Z);
  - corrected deltas ±1/±2;
  - a(lag) = 0.5 flat (Jeffreys): no intraday page fetch exists before the cut, since G5 polling began 10-07.
  - Deviation does not mark removal (kept 0.984 / 0.987 / 0.985 at 0 / 1 / ≥2 from the previous hour), so gross = absent and ≥2 from every kept neighbour.
  - Intraday page fetches store [receipt − 120 min, receipt] (writer window), so absence evidence covers that span only.
  - Consult independent case gives [0.1, 0.9] exactly. Lucknow 09-06 37 stays a mark: no bin 31–36 is zero, and the mass below 36 is the lifecycle no-tape share. Helsinki 10-07: a fetch covers 13:50Z but not 10:50Z. Page correction 16→15 wins.
- D5 numerics: joint closed-form integration of simultaneous factors per threshold column; complement-stable transition tails with unbounded end cells (3.2e-14 tail survives); exact report instants (59 vs 60 distinct); drift folding conserves both mixture variances; continuous SPECI exposure (1 − e^−0.1 with 1 min left); empty tape → lowest bracket (HIGH and LOW). The JMA dual role is gone (D1: JMA is no report).
- Error contract: particle oracle |ΔG| < 0.01 (HIGH and LOW, marks + pending); cell halving < 2e-3; independent pending instants vs closed form < 2e-3.

### D6 qualification (artifacts/fast_obs_audit/dense_station_model/d6/qualification_2026-10-08.json, sha256 b08a2ec4…)
Train ≤ 2026-09-26; held-out receipt-frozen decisions 09-27..10-07 every 30 min from local 06:00. A0 = live legacy posterior computed by the cut, on its own bins. Log score is true (no floor). CIs are day-block 95 %.

| city metric | days | served/decisions | log A0 / A / B | B−A [95 %] | B−A0 [95 %] | HC err/n (exp, P95) A0 · A · B | sem B | eligible |
|---|---|---|---|---|---|---|---|---|
| Tokyo high | 7 | 238/396 | 1.358 / 0.581 / 0.523 | −0.057 [−0.086, −0.033] | −0.835 [−1.034, −0.658] | 0/7 · 0/111 · 0/126 (3.59, 7) | 0 | **yes** |
| Tokyo low | 7 | 238/396 | 1.365 / 0.682 / 0.668 | −0.014 [−0.037, +0.007] | −0.697 [−1.085, −0.275] | 0/13 · 0/47 · 0/52 (1.43, 4) | 0 | no (B−A) |
| Helsinki high | 11 | 339/396 | 0.607 / 0.863 / 0.847 | −0.016 [−0.022, −0.009] | +0.240 [−0.071, +0.514] | 3/144 · 0/89 · 0/92 (2.67, 6) | 0 | no (B−A0) |
| Helsinki low | 11 | 339/396 | 2.575 / 1.691 / 1.297 | −0.394 [−1.259, −0.004] | −1.278 [−4.177, +0.502] | 2/103 · 24/91 · 0/68 (2.04, 5) | 0 | no (B−A0) |
| Singapore high/low | 0 | 0/396 | — | — | — | — | — | no (no NEA writer: DENSE_ROUTE_MISSING) |

Not-served decisions: Tokyo A0_ABSENT 69, DENSE_ABSENT_TODAY 81 (ledger JMA from 09-30), DENSE_STALE 8; Helsinki DENSE_ABSENT_TODAY 21, DENSE_STALE 36. Singapore stays legacy.

### D7 migration
- Census of positive-held families (TRADE read-only, 2026-10-08 16:00Z): Austin, Busan, Dallas, Denver, Hong Kong, London, Madrid, Miami ×2, Paris, Sao Paulo ×2, Shanghai, Taipei. None is a dense-qualified city.
- Each family's latest live posterior goes through `read_pinned_replacement_forecast_bundle` (HELD_REDECISION, raw-input HWM conn). Head and origin/live give identical status/reason for all 14: 13 `REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE`, Paris `REPLACEMENT_PINNED_POSTERIOR_NOT_COMPLETE`. The read-only replay at the posterior's own clock reflects readiness having moved since; head and live agree byte for byte.
- Held V2 carriers rebuilt through the shipped builder (evaluation=None): London 774822 and Shanghai 774676, plus 8 earlier V2 posteriors. The sha256 is identical on head and origin/live, and identity and q equal the persisted values for all 10.
- No revision bump is needed: no dense certificate is written (R2), and every persisted V2/V3 replays unchanged. The refresh path for a future dense revision is the seam's REPLAY/SELECT split.
- Rollback: a DENSE certificate reaching the live tree is rejected lawfully on all three consumers. Builder: `unsupported Day0 remaining carrier operator`. Center-policy authority: False. Bundle reader: `REPLACEMENT_DAY0_REMAINING_CENTER_POLICY_NOT_CURRENT`. Same on head without `evaluation`. None exist today.

### Residuals
- a(lag) has no intraday-fetch evidence before 10-07 (flat 0.5). It sharpens on the next refit.
- Tokyo high is the only eligible family. It needs the R3 adapter seam before it can serve live.
- Held-out windows are short: 7–11 days.
- Forecast path is the previous-day1 proxy (archive) offline vs the freshest stitched capture live.
- G9 (°F page revisions) is out of scope.
- Deletion-after-appearance of °C page rows is assumed zero (G10 measures it).
