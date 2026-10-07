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
- Boundary: `H_s = max(B_A, B_P if scenario survives, Y_pending)`.
  - B_A is settlement-channel facts only: the NOAA page rows, plus FA route rows **at proven METAR instants**. It is a semantic certificate; bins below it are structurally 0.
  - B_P is the received provisional METAR integers (AWC/Ogimet). It takes the **caller's survival weight** `s = sum of non-None scenario weights`; the caller's boundary value is never used. Dense readings never set any B.
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

### G1/G2/G8 (coordinator scope addition)
- G2:
  - FA registry rows declare `metar_instant_minutes`, validated at load.
  - `_latest_authorized_day0_fact(require_settlement_channel=True)` admits an FA row only at a proven METAR instant. Non-METAR rows stay physical facts and dense likelihood terms.
- G1:
  - `day0_evidence_finality` gives an FA route source at a proven METAR instant MONOTONE_SETTLEMENT_BOUND; at any other instant it gives PROVISIONAL_CURRENT_SNAPSHOT.
  - `day0_conditioning_key` takes the observation time.
  - Dense-qualified cities route into the carrier region, so the dense law prices them.
- G8: `fast_extreme_supersedes_settlement` compares settlement integers R(native) under the city's SettlementSemantics.

### Authority
- The new Day0 semantics revision (v35, both survival and resolver) is bound in `src/events/day0_authority.py`. It is process-wide, because every q_version binder uses one constant; old certificates are parsed, never restamped.
- The new operator constant and q_shape `day0_remaining_shared_carrier_dense_v1` are allow-listed where V2/V3 are: bundle reader, cycle-policy coverage SQL, day0_authority shape sets, tier0 corpus (diagnostic only).
- `docs/authority/replacement_final_form_2026_06_09.md` records the law. Root AGENTS.md gets a one-line reconciliation of the fitted-residual exclusion for this Day0 scope.

### Steps (each a commit)
1. [ ] design (this section)
2. [ ] operator module + unit tests (synthetic recovery, semantic zero, DST, reduction)
3. [ ] fit module + refit script + params artifact
4. [ ] evidence/dispatch + builder integration + revision + allow-lists + Helsinki carrier integration test
5. [ ] G1/G2/G8 + regression tests (Tokyo 760918 METAR-instant floor; Tokyo 10-04 LOW non-METAR no floor)
6. [ ] replay proof (live, read-only) + performance + registries (source_rationale, script_manifest, test_topology)

Rollback: revert the branch commits. No schema or DB change. Legacy paths are unchanged when the artifact is absent.
