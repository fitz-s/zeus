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
  - Resolution: a provisional instant is superseded when the page has a received row at that instant (the page row governs). It is resolved-dropped (no factor) when the page has a received row at a later instant but none at it.
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
