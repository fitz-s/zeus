# Day0 Mechanism — Whole-Mechanism First-Principles Audit

Date: 2026-07-18
Scope: Zeus Day0 same-day trading mechanism, end to end — the money path
`observed running extreme → remaining-window distribution → settlement-support
transform → served q / q_lcb → admission → execution → exit → calibration/learning`.
Method: eight first-principles investigators, each adversarially verified; this is the
synthesis. Read-only. Settlement law under audit:

```
HIGH:  Y = max(observed_high_so_far, X_remaining)
LOW:   Y = min(observed_low_so_far, X_remaining)
X_remaining = extreme over the NOT-YET-OBSERVED remainder of the local day
```

---

## 1. Whole-mechanism verdict

**The Day0 mechanism is structurally sound in its settlement semantics and defends
its hardest invariants well — but it carries one tier0 mis-specification in the
probability lane that actually prices and sizes live trades, plus a cluster of HIGH
defects that each bite under normal or plausible live conditions.**

What is genuinely right (and this is most of the surface): the 3-branch
impossible/straddle/ordinary conditioner transform is exact and symmetric HIGH/LOW at
all four call sites; the running extreme is a monotone absorbing floor/ceiling with a
correct max-for-HIGH / min-for-LOW composition law documented against two live
incidents; the hard-fact exit/hold matrix is settlement-correct and symmetric;
obs-source == settlement-source is a real, config-anchored, fail-closed comparison on
the live actionable path; the decision-kernel LIVE certificate requires genuine
`DAY0_AUTHORITY + ABSORBING_BOUNDARY` parents with no-look-ahead clock ordering;
per-decision freshness fails closed for entries; and the WU-ICAO ingest lane preserves
true sub-hourly extrema with an independent AWC cross-check. The HIGH/LOW causality
asymmetry — the single most dangerous class of Day0 misread — is respected everywhere
checked, with no metric swap found.

The load-bearing gap is a **missing distinction the money-gating code cannot name:
"the remaining-hours distribution" versus "the whole-day distribution."** The
settlement math requires `X` to be the extreme over the *unobserved remainder* of the
day, whose dispersion must shrink as the day elapses and the peak/trough is realized.
One lane (the `Day0Router` Monte-Carlo signal, and the `day0_metric_fact` estimate)
computes this correctly. **The lane that produces the served/persisted posterior and
the `q_lcb` that gates edge and sizes the trade (materializer → qkernel → `joint_q`)
integrates the support transform over a whole-day `Normal(mu, sigma)` that never
narrows.** Post-peak this systematically over-weights settlement-impossible-adjacent
bins and under-weights the observed/straddle bin, and the `q_lcb` bootstrap cannot
catch it because it shares the same over-wide sigma. This is confirmed live in the DB,
not hypothetical.

Beneath that: a resting BUY order can fill on a structurally dead bin because the
cancel sweep never consults the durable settlement-grade extreme source; the
submit-recapture gate re-prices the book but reuses a stale selection-time `q`, so a
newly-dead bin can be entered; NOAA/HKO cities have no independent second observation
feed, so a single-pipeline outage silently understates their running extreme; and the
Day0-horizon Platt model is threaded into the signal but never applied, so the instant
a real fit ships every ≤6h trade silently prices on raw uncalibrated probabilities.

Net: correct from first principles in its *settlement algebra and its hard-fact
organs*, but mis-specified in the *probability it actually serves*, and missing three
independent second-lines-of-defense on the execution/ingest edges.

---

## 2. Findings ranked by runtime money risk

### TIER0 — mispriced live q now

#### T0-1 · Day0 served q integrates the whole-day distribution, never the remaining-hours distribution; sigma never narrows
> **⚠️ REFUTED / DOWNGRADED by settlement-graded verification — see §6.** HIGH shows no
> over-dispersion (n=786; post-peak served 0.045 vs realized 0.070); LOW only a
> small-sample mid-day signal (n=36). Not a tier0. Do not implement the sigma-narrowing
> rewrite. Retained below verbatim as the original code-reasoning hypothesis.

`src/forecast/sigma_authority.py:405` · `src/probability/joint_q.py:346` · `src/data/replacement_forecast_materializer.py:3136,3403`

- **Settlement math requires:** `X` is the extreme over the *unobserved remainder* of
  the day. Its dispersion must shrink toward the observed extreme as hours elapse and
  the peak/trough is realized, so that post-peak `P(fresh extreme beyond obs) → 0` and
  the observed/straddle bin holds ~all remaining mass. Both center and sigma of `X`
  must depend on hours observed.
- **What the code does:** `day0_remaining_process_sigma()` returns
  `max(1.0, sqrt(sd² + resid²))` — the *whole-day* predictive sigma, with **zero
  elapsed-time dependence** (the name lies). `joint_q` folds `probability_high/low_day0_bin`
  over `Normal(mu_native, sigma_native)` using that same whole-day sigma (its own
  comment: "the two lanes integrate the identical underlying Gaussian" — i.e. the Day0
  family uses the *same* dispersion as the bare NORMAL family). The materializer's
  served point-q and its `q_lcb`/`q_ucb` bootstrap draw only *centers* and integrate
  every draw at the whole-day sigma. `mu_native = max(center, obs)` clamps the center
  *up* to the observed bound but never collapses it toward a post-peak center. Live DB
  provenance confirms the served posterior is this whole-day path with
  `day0_conditioning.active=true`. The correctly-narrowed remaining-extreme estimate
  **exists** in `day0_metric_fact` (`q50/q90_remaining_extreme`, `daylight_progress`)
  but is never referenced by any serving code — computed and thrown away.
- **Failure scenario:** HIGH, late afternoon post-peak, obs = 30 °C, whole-day
  mu ≈ 30, sigma ≈ 2.5. Straddle bin 30 → `cdf(30.5) = Φ(0.2) ≈ 0.58`; bins 31, 32
  receive ~0.15, ~0.12. Physically the observed bin is ~0.9+ and the above-obs bins
  ~0. Zeus prices ~0.42 of mass onto near-impossible bins and buys YES / sells NO
  cheap, losing when settlement = 30. The `q_lcb` gate shares the same sigma so the
  edge check never flags it. Symmetric loss on LOW.
- **Fix direction:** Feed the support transform a *remaining-hours* distribution.
  Two correct estimators already exist — the `Day0Router` remaining-window ensemble
  and `day0_metric_fact`'s `q_remaining_extreme` columns. Route one of them into
  `joint_q`/the materializer so both `mu` and `sigma` narrow with elapsed fraction /
  peak-confidence, and make the `q_lcb` bootstrap draw from the narrowed sigma. Rename
  `day0_remaining_process_sigma` once it actually depends on remaining hours. Do **not**
  patch by clamping sigma downward heuristically — derive it from the remaining window.

### HIGH — correctness gaps that bite under a plausible live condition

#### H-1 · Resting-entry cancel sweep never consults the durable settlement-grade extreme
`src/execution/day0_hard_fact_exit.py:1068`

- **Requires:** A resting BUY order on a bin the observed extreme has already made
  settlement-impossible must be cancelled with the *same* dead-bin authority the
  held-position exit uses; both lanes must agree on what is dead, so the cancel sweep
  must consult every settlement-grade source the exit lane does (WU API, METAR, **and**
  durable VERIFIED `observation_instants`).
- **Does:** `cancel_day0_dead_bin_resting_entries` calls
  `settlement_grade_effective_extreme(...)` **without** `world_conn`, so
  `_durable_observation_instants_extremes` returns `(None, None)` and the durable
  source is silently excluded. Only the volatile in-process WU-API (600 s memo) and
  METAR fast-tail memo drive the cancel. The held-position exit path
  (`cycle_runtime:5565`) *does* pass `world_conn=conn`, so the two lanes disagree.
- **Failure scenario:** After a restart (both memos cold) or for any non-METAR city, a
  bin is provably dead per durable VERIFIED WU-hourly rows but WU-API/METAR are cold. A
  resting BUY order on that dead bin is **not** cancelled and fills → immediate
  structural loss (paid for a share that cannot pay out). Cold memos on restart and
  non-METAR cities are normal operating states, not exotic.
- **Fix direction:** Thread `world_conn=conn` into the `settlement_grade_effective_extreme`
  call at line 1068 (mirroring the exit lane at ~700), or call
  `_durable_observation_instants_summary` directly, so the cancel sweep sees the same
  durable truth the exit lane already sees.

#### H-2 · Submit-recapture reuses selection-time `q_lcb` and hardcodes `forecast_still_current=True`
`src/engine/event_reactor_adapter.py:13057` (and `:25479`)

- **Requires:** Selection-time truth ≠ submit-time truth. Between selection and submit
  the observed extreme can cross a bin's survival edge (bin → settlement-impossible,
  `q → 0`). The recapture gate exists to fail-closed on a stale forecast and a reversed
  edge; to catch a newly-dead Day0 bin it must re-condition `q` against a *fresh*
  observed extreme at submit.
- **Does:** On the synchronous money path `forecast_still_current=True` is passed as a
  hardcoded literal, and `RecaptureInputs.recaptured_q_lcb = float(candidate.q_lcb)` is
  the **selection-time** `q_lcb`; only the cost curve is refreshed. `edge_lcb =
  stale_q_lcb − fresh_cost`, and the fail-closed forecast-currency gate
  (`redecision.py:519`) is neutralized by the hardcoded `True`. `q` is never re-derived
  from a submit-time observation on this path. This fires on **every** Day0 submit
  recapture, not an edge case.
- **Failure scenario:** A HIGH bin is selected with `q_lcb > 0`; before submit the
  running high crosses the bin's far edge (true `q = 0`). Recapture re-walks the book,
  finds price in band, computes `edge_lcb = stale positive q_lcb − cost > 0`, and
  submits a buy_yes into a dead bin. Salvaged only next monitor cycle by the hard-fact
  exit — bounded loss, but a guaranteed dead-bin entry every time the crossing lands in
  the select→submit window.
- **Fix direction:** In the recapture path, re-fetch the submit-time observed extreme
  and either (a) re-run the Day0 hard-fact verdict and abort on `EXIT_DEAD_BIN`, or
  (b) re-condition `q_lcb` against the fresh extreme before computing `edge_lcb`. Stop
  hardcoding `forecast_still_current`; compute it from a real submit-time re-check.

#### H-3 · No independent second observation feed for NOAA/HKO cities' sole ingest lane
`src/data/day0_fast_obs.py:186` · `src/data/day0_observation_reader.py:396`

- **Requires:** Any city whose Day0 running extreme is settlement-critical needs some
  defense against a mid-day gap in its single pipeline — a gap *silently understates*
  the running extreme (MAX-over-available-rows degrades to a wrong-but-plausible answer,
  not a loud error).
- **Does:** `fast_obs_source_for_city` returns `None` for `hko`/`noaa` by design, so
  the AWC same-station gap-bridging union that protects WU-ICAO cities has **no
  analogue** for Moscow/UUWW, Tel Aviv/LLBG, Istanbul/LTFM, or Hong Kong. Each depends
  on a single Ogimet/HKO pipeline guarded only by a row-count coverage check and a
  latest-row staleness check — neither of which detects a mid-day hole.
- **Failure scenario:** Ogimet ingest for Tel Aviv stalls 12:00–16:00 local during the
  afternoon peak, then resumes. Rows before+after total ≥ 6 (coverage reads OK), latest
  row is fresh (staleness passes), but the true peak inside the gap is never recorded.
  `high_so_far` is understated vs the real settlement value with no signal, and a bin
  near the true peak prices as alive when it is already dead. External free-API outages
  are the exact failure class the WU fast-tail was built to survive.
- **Fix direction:** Give NOAA/HKO cities a second independent settlement-faithful
  feed (an AWC/METAR cross-check for the ICAO stations; wire the SPEC'd HKO redundancy),
  or at minimum add a gap detector (see M-2) that forces DATA_DEGRADED when the
  qualifying-row timeline has a hole spanning the metric's likely peak/trough window.

#### H-4 · `HorizonPlattFit.predict_proba` is never applied — Day0-horizon calibration is theater
`src/signal/day0_high_nowcast_signal.py:105`

- **Requires:** If a Platt model is loaded and threaded into the nowcast signal (per the
  class's own docstring), its transform must actually be applied — otherwise the whole
  fit/persist apparatus reports real-looking coefficients while changing nothing.
- **Does:** `Day0HighNowcastSignal.__init__` stores `model` into `self._model`, but
  `settlement_samples()`/`p_bin()`/`p_vector()` never reference it. `Day0LowNowcastSignal`
  takes no model at all. The live evaluator already loads a fit and threads it in on
  every ≤6h trade; it is silently discarded. Confirmed live: 3361/3364 `day0_nowcast_runs`
  rows have `p_nowcast_json` byte-identical to `p_now_raw_json`.
- **Failure scenario:** Harmless *today* only because the sole persisted fit is a
  documented zero-skill identity. The moment a real temporal-holdout fit is persisted
  (the explicit roadmap next step), every ≤6h trade prices on raw uncalibrated
  probabilities while the persisted receipt (`fit_run_id`, `alpha ≠ 1`, `n_obs`) claims
  calibration was applied — a live pricing error with no code-visible signal.
- **Fix direction:** Apply `self._model.predict_proba`/`predict_logit` inside the
  nowcast probability computation (both HIGH and a new LOW path), and add a guard/test
  that a non-identity fit measurably moves `p_nowcast` off `p_now_raw`. Until then, the
  fit-persistence script must not ship a non-identity fit.

### MEDIUM — drift / degradation risk

#### M-1 · Held-position monitor runs on a stale observed extreme with no staleness-uncertainty margin
`src/engine/monitor_refresh.py:2844`
The `stale_extreme_uncertainty_margin` machinery exists but is wired only into the
entry lane and `day0_fast_obs`, never into the monitor/exit belief — the one lane
deliberately permitted to run on stale bounds has the *least* protection. A quiet
settlement station leaves an unmodeled gap window `[observed_at, now]`; the belief
conditions on the raw stale point value as exact, degrading boundary calibration on
hold/exit decisions. **Fix:** widen a staleness-sized boundary band on any monitor
belief that conditions on a stale bound.

#### M-2 · `coverage_status` is a row-count threshold, not a mid-day contiguity check
`src/data/day0_observation_reader.py:354`
Coverage is `n_rows ≥ 6 → OK` with no check on the time distribution of rows, so
front-loaded rows + one resumption row after a multi-hour hole reports identically to
complete hourly coverage. This is the general mechanism that makes H-3's gap invisible
at the reader layer for *any* city. `monitor_refresh.py:2357` filters only `NONE`,
accepting OK and LOW_COVERAGE identically. **Fix:** add a max-inter-sample-gap check;
degrade to DATA_DEGRADED when the gap spans the metric's peak/trough window.

#### M-3 · Two of nine Day0 live-admission circuit breakers are hardcoded to never fire
`src/engine/event_reactor_adapter.py:15601`
`in_post_extreme_quiet_window` and `in_final_localday_noentry_window` are hardcoded
`False` at the sole live call site; no code computes real values, so
`DAY0_POST_EXTREME_QUIET_WINDOW` and `DAY0_FINAL_LOCALDAY_NOENTRY` are structurally
unreachable. Residual risk: a settlement-source *revision* of an already-observed
extreme (unmodeled by the solve) and late-local-day entry with no exit time are
uncovered — and the sibling fragility gate #7 is disabled on the live current-state
path, so all three flip-risk overlays are inactive. **Fix:** compute both windows from
real temporal/event context, or delete the dead fields and document the coverage gap.

#### M-4 · City-local-midnight-of-target-date reimplemented five times with no canonical source
`src/strategy/market_phase.py:119` (+ `forecast_target_contract.py:96`,
`time_context.py:42`, `event_reactor_adapter.py:29292`, `dispatch.py:214`)
All five agree today. A future DST-fold/tzdata edge-case fix applied to one copy would
silently desync admission timing across subsystems, reopening the one-off-day skew this
mechanism already suffered. **Fix:** extract one declarative function (the repo already
does this for `settlement_preimage_offsets`) and have all five call it.

#### M-5 · LOW Day0 nowcast has no quantization/instrument noise floor that HIGH requires
`src/signal/day0_low_distribution.py:85`
`build_day0_low_distribution` = `min(obs, member_mins)` then rounding, with no jitter,
sigma, or `QUANTIZATION_NOISE_FLOOR` — HIGH's ensemble MC path floors against ~0.3
native-unit sensor/quantization noise, LOW does not. Entry impact is largely defused
(uncalibrated point-mass is hard-blocked pre-edge; Platt's 0.01/0.99 clamp caps a
degenerate mode ~0.92), but the exit/monitor lane deliberately skips Platt and consumes
the raw deterministic vector, so a tight-ensemble LOW near a boundary yields an
overconfident exit belief. **Fix:** add the same quantization noise floor to the LOW
settlement sample path.

#### M-6 · LOW Day0 has no trough-confidence analogue of `post_peak_confidence`
`src/signal/diurnal.py:368`
`diurnal.py` is HIGH-only (no `p_low_set`, no `post_trough_confidence`). LOW is still
time-aware via the shrinking remaining window, but HIGH layers a peak-timing structural
sigma + instrument noise that LOW lacks — a second-order overconfidence risk at the
dawn trough if the ensemble is miscalibrated there. **Fix:** build a symmetric
trough-timing empirical table, or accept and document the asymmetry.

#### M-7 · `OK_FAST_ONLY` health state is admissible by policy but unreachable from the classifier
`src/engine/event_reactor_adapter.py:15442`
`_day0_live_source_health_state` yields only `OK_FAST_AND_WU` or `BLOCKED`; the rich
5-state classifier in `day0_source_health.py` that defines `OK_FAST_ONLY` has zero
production callers. Fails safe (over-restrictive), but can silently zero out a whole
non-WU city's live Day0 opportunity set with no alert (circumstantial evidence points
at Hong Kong via `wu_station=None` → MISMATCH cascade). **Fix:** wire the real
classifier, or narrow the policy's `allowed_health_states` to what the gate can produce
and document the exclusion.

#### M-8 · Durable hard-fact path compares raw `running_max/min` to integer bins without `round_single`
`src/execution/day0_hard_fact_exit.py:526`
The durable `observation_instants` lane passes `CAST(running_max AS REAL)` straight into
the strict grid comparison with no `SettlementSemantics.round_single`, asymmetric with
the WU/METAR sibling paths that round. Currently masked (all 1M+ wu% rows are
integer-valued), but a sub-degree value from a new WU variant / unit conversion / a raw
source matching `LIKE 'wu%'` would declare a still-winnable bin DEAD (force-exit a YES,
hold a losing NO). **Fix:** round the durable extreme via `SettlementSemantics` before
the comparison; document/enforce the integer-grid invariant.

#### M-9 · `day0_nowcast_runs` mixes two computational lineages under identical columns
`src/state/db.py:4344`
The entry lane writes a real `Day0HighNowcastSignal.p_vector()`; the exit lane writes
the already-blended served posterior under the same `p_nowcast_json` column with the
same `source='live_nowcast'` tag and no provenance field. 3364 rows accumulated. A
future walk-forward fit built on this table would train on a covariate meaning two
different things. **Fix:** add a lane/signal-class provenance column before any fit
harness consumes the table.

#### M-10 · wu_pws/ogimet ingest health probes check an unrelated frozen historical query
`src/data/source_health_probe.py:128`
`_probe_wu_pws`/`_probe_ogimet` fetch a hardcoded EGLL station for a fixed 2025-01-01
date and stamp `last_success_at=now()` on any HTTP-200 — they never validate this
city's live data or Zeus's own ingest-write path. If Zeus stops writing fresh
`observation_instants` rows while the upstream API stays reachable, the DAY0_CAPTURE
circuit breaker never trips. Money is saved only because the *separate* per-candidate
freshness gate fails closed on the real observation; the coarse breaker is inert for
this failure mode. **Fix:** probe the actual per-city settlement station/date, or key
freshness off the freshest `observation_instants` write timestamp per city.

#### M-11 · DAY0_CAPTURE ingest short-circuit is global, not scoped to the affected city
`src/engine/cycle_runner.py:618`
One stale source flips `day0_capture_disabled` for the whole process; in a fail-closed
mode the entire cross-city discovery/entry cycle returns early. Bounded because the
separate `run_exit_monitor_cycle` (2-min cadence, ungated) still services held-position
exits, so held positions are not blindsided — the cost is lost entry opportunity and
duplicate monitor coverage for unrelated cities on a transient single-source hiccup.
**Fix:** scope the gate to the affected city/source pairing.

#### M-12 · Dedicated Day0 input-ordering module is dead code with divergent boundary semantics
`src/strategy/live_inference/day0_input_correctness.py:63`
Zero production callers; the live gate (`day0_admission.py` gate 5) reimplements the
ordering property but with *different* equality semantics (dead module rejects
`quote ≤ obs`; live gate rejects only `quote < obs`, so equality passes live). An
auditor reading the dedicated file + its passing test concludes a property is enforced
that the live path does not enforce. **Fix:** delete the dead module or make the live
gate call it; reconcile the equality boundary and document the chosen semantics.

#### M-13 · Decision-kernel verifier never re-proves the conditioning math from primitives
`src/decision_kernel/verifier.py:458`
The LIVE-gate verifier checks certificate self-consistency (recorded `q_lcb` matches a
persisted transform dict, clocks ordered, enums equal MATCH) but never recomputes `q`
from `(obs_extreme, bin preimage, mu, sigma)` — so a metric/side swap or wrong-obs bug
at the single upstream `condition_day0` call site would produce an internally
consistent-but-wrong `q` that passes every gate. The verifier already re-derives the
forecast-path transform name independently (`_expected_members_extrema_transform`), so
the precedent exists. **Fix:** add a defense-in-depth re-computation (or at minimum a
coarse "q must be 0 for a bin structurally beyond the observed extreme" sanity check) at
the LIVE gate.

### LOW / observation

- **L-1** `src/engine/evaluator.py:209` — NOAA source authorization validates by
  `source_role` bucket, not the specific city's station; a mislabeled cross-station tag
  (`ltfm/uuww/llbg`) passes the class gate. Not reachable via the normal city-scoped
  read today; a defense-in-depth gap for a future caller.
- **L-2** `src/signal/forecast_uncertainty.py:380` — `day0_blended_highs` silently
  discards its `observation_weight`/`backbone_high` args (correct per FIX-3), but
  `forecast_context()` still surfaces them as if load-bearing; a maintainer "honoring"
  them could reintroduce the sub-observed-value bug.
- **L-3** `src/probability/joint_q.py:401` — the q-shape gamma temper cannot represent
  an elapsed-time-varying correction and would mask T0-1's bias in aggregate
  reliability. Currently fully inert (`gamma = 1.0`, no fitter, no artifact) — a
  forward-looking hazard to flag before a temper fitter ships.
- **L-4** `src/calibration/day0_horizon_calibration.py:151` — `fit_day0_horizon_platt`
  and `read_latest_platt_fit` carry no internal walk-forward/temporal-boundary check;
  the INV-16 guarantee lives entirely in a not-yet-written offline harness. Inert today.
- **L-5** `src/engine/monitor_refresh.py:602` — the consecutive-stale-belief escalation
  counter is an in-process dict reset by any restart, delaying the loud
  `BELIEF_AUTHORITY_FAULT` alert by up to 3 cycles; the per-cycle marker is durably
  written so history is reconstructable. Observability delay, not a money defect.
- **L-6** `src/decision_kernel/compiler.py:284` — NO_SUBMIT certificates for
  DAY0_EXTREME_UPDATED candidates are certified against the forecast graph, losing the
  Day0Authority/AbsorbingBoundary provenance; audit-trail fidelity only (no money path,
  deliberate post-incident tradeoff).

---

## 3. Cross-cutting / architectural gaps (the missing distinctions)

### 3A. The deep-conditioning adjudication: X is served as the whole-day distribution

**Adjudication: `X` *should* be the remaining-hours distribution, and the lane that
prices/sizes live money serves the whole-day distribution instead. This is a genuine
mis-specification, confirmed live.**

There are (at least) **two parallel Day0 probability lanes with inconsistent
remaining-window semantics**:

1. **`Day0Router` / `Day0Signal` Monte-Carlo lane** (`evaluator.py:4105`,
   `monitor_refresh.py:3069`) — draws remaining-window ensemble members
   (`remaining_member_extrema_for_day0` slices only the not-yet-elapsed local hours),
   adds peak/trough-confidence-scaled jitter, and hard-clamps `max(obs, ·)` / `min(obs, ·)`.
   This lane **correctly narrows** as the day elapses (shrinking window + shrinking
   sigma). It produces `p_raw`.

2. **Materializer → qkernel → `joint_q` lane** — applies the analytic 3-branch
   conditioner over a whole-day `Normal(mu_native, sigma_native)`, where sigma is the
   whole-day realized RMSE and mu is the whole-day anchor clamped only *upward* to obs.
   `joint_q`'s own comment confirms the Day0 family uses the *identical* Gaussian as the
   bare NORMAL family — **no narrowing**. This lane produces the served/persisted
   posterior and the `q_lcb`/`q_ucb` that gate edge and size the trade.

The static + live-DB evidence is that the **money-gating economics `q_lcb` comes from
lane 2** (the qkernel "global current-state solve," verified in the DB as the
whole-day `predictive_sigma_c` path with `day0_conditioning.active=true`). The
correctly-narrowing lane 1 exists but is **not** the one wired into execution
economics, and a third correctly-narrowing artifact (`day0_metric_fact`'s
`q_remaining_extreme` columns) is computed and discarded entirely.

Consequences:
- **The served q is over-dispersed post-peak** — impossible-adjacent bins get mass they
  cannot physically receive; the observed/straddle bin is under-weighted. (T0-1.)
- **The `q_lcb` gate is blind to it** because the bootstrap shares the same sigma.
- **The two lanes can disagree for the same bin** — `p_raw` (narrowed) vs the persisted
  posterior / execution `q_lcb` (whole-day). A belief that says "trade" and a price that
  sizes it are computed from different dispersions; this internal incoherence is itself
  an architectural hazard beyond the mispricing.
- **The code cannot name the distinction.** `day0_remaining_process_sigma` is a misnomer
  with no `hours_remaining` argument; nothing in lane 2 has a place to put the elapsed
  fraction. This is Wittgenstein's limit: the mis-specification persists because the
  serving lane has no vocabulary for "remaining."

The fix is architectural, not a patch: **unify on a single remaining-hours distribution
and feed it to whichever lane prices money.** The estimator already exists twice
(`Day0Router` members, `day0_metric_fact`); the work is to route it into
`joint_q`/materializer and make the `q_lcb` bootstrap draw the narrowed sigma. A live
probe (below) should first confirm the exact lane split so the fix targets the real
money-gating computation.

### 3B. Two lines of defense have been quietly removed on the execution edge

The mechanism was designed with layered defenses (durable-source dead-bin detection,
submit-time re-conditioning, admission circuit breakers). Three of these are wired only
on *one* of the two lanes that need them: the durable extreme reaches the held-position
exit but not the resting-order cancel (H-1); the freshness/re-condition reaches entry
selection but not submit-recapture (H-2); the quiet-window/final-day breakers are
declared but hardcoded off (M-3). The pattern — *a guard exists, is tested in isolation,
and is wired into only one of its two required call sites* — recurs (H-1, H-2, M-1, M-3,
M-13, M-12). The systemic gap is the absence of a "both lanes must consult the same
evidence" invariant.

### 3C. Freshness/coverage is counted, not shaped

Coverage is a row *count* everywhere (`day0_observation_reader` and the ingest probe),
never a *timeline*. A running-extreme aggregate is uniquely vulnerable to a hole around
the peak/trough, which the count cannot see (M-2, H-3, M-10). The missing distinction is
"complete coverage" vs "sparse coverage with a hole in the window that matters."

### 3D. Calibration apparatus reports skill it does not apply

The Day0-horizon Platt fit persists coefficients, `fit_run_id`, and `n_obs` that no code
consumes (H-4), trains from a table with mixed provenance (M-9), carries no internal
walk-forward guard (L-4), and its aggregate-reliability temper cannot represent the
elapsed-time bias T0-1 would produce (L-3). Today it is inert; the hazard is a receipt
that will claim calibration the instant a real fit ships while nothing applies it.

---

## 4. Verified CORRECT (coverage the operator can rely on)

- **Settlement transform:** the impossible/straddle/ordinary 3-branch conditioner is
  exact and symmetric HIGH/LOW at all four sites (`probability_high/low_day0_bin`,
  `joint_q`, materializer scalar + vectorized bootstrap) with correct strict/non-strict
  boundaries. HK `oracle_truncate [t,t+1)` preimage is correctly threaded; the
  finite-evidence tail floor masks impossible bins to exactly 0.0 with the same
  predicate and cannot be re-inflated.
- **Running-extreme ingest (WU-ICAO):** true intra-hour max/min preserved (SPECI peaks),
  monotone-widening-only writes with downward disagreement routed to
  `observation_revisions`, MAX/MIN aggregated across the whole day (not the latest row),
  single-source priority with no source mixing, and an independent AWC fast-tail union
  bridging WU outages. Fail-closed unit law (F-cities skip whole-C reports lacking a
  T-group). Live+canonical composition applies a correct absorbing floor documented
  against the Toronto and Chicago incidents.
- **HIGH/LOW causality asymmetry:** respected everywhere checked — `Day0Router` LOW-only
  causality gate, `Day0Signal` TypeError on LOW construction, post-peak-maturity for
  HIGH vs terminal-hours for LOW exit authority, correct max-for-HIGH/min-for-LOW
  direction throughout. **No metric swap found.**
- **Admission timing:** target-local-day start computed DST-correctly (ZoneInfo), the
  conditioner is a strict boolean (missing observation fails the whole write closed, no
  partial state), forecast-lane buy-YES entries forced out once the local day starts,
  and the historical UTC-vs-local skew fix defaults ON with no override in config or the
  live launchd plist.
- **Hard-fact exits:** the HIGH/LOW × YES/NO verdict matrix is symmetric and
  settlement-correct; EXIT_DEAD_BIN sells reduce-only on a fresh bid bypassing the
  auction/evidence/maturity gates; HOLD_STRUCTURAL_WIN is a terminal hold; the exit
  belief is day0-conditioned or exact 0/1, never bare-Normal; the extreme is
  monotone-irreversible with spike-plausibility and anomaly-pause guards.
- **Per-decision freshness:** the station-clock exception is correctly limited to
  hourly station sources and independently re-checks the availability clock; entries
  fail closed on any staleness; monitor re-fetches live every cycle and falls back only
  to the canonical DB surface (never a frozen cache) via the absorbing law.
- **Walk-forward learning:** the entry-lane INV-16 `causality_status` reject is correct
  and tested (distinct from OBSERVATION_UNAVAILABLE); historical/nowcast Platt is a
  strict post-decision side-write that never overwrites the served q; the general
  `calibration_pairs` learning gate is strictly `== 'OK'`.
- **Decision-kernel LIVE gate:** obs-source == settlement-source is a real,
  config-anchored, fail-closed comparison; Day0Authority minting enforces no-look-ahead
  clock ordering; the money-moving certificate genuinely requires
  `DAY0_AUTHORITY + ABSORBING_BOUNDARY` parents.

---

## 5. Coverage honesty

- **Not reached / needs a live probe (blocks the T0-1 fix scoping):** the exact division
  of labor between the `Day0Router` remaining-window lane and the qkernel/materializer
  whole-day lane. Two investigators disagreed on which q "is served" — one traced the
  entry `p_raw` to `Day0Router` (narrowed), the other traced the persisted posterior and
  `q_lcb` economics to the materializer (whole-day, live-DB-confirmed). My adjudication
  is that the **money-gating `q_lcb` uses the whole-day lane** (the DB provenance is
  decisive), and the narrowing lane feeds a parallel belief — but a live probe that
  logs, for one live Day0 family, the (mu, sigma, per-bin q) actually used by the
  execution `q_lcb` vs the `Day0Router` `p_raw` would definitively confirm the split and
  quantify the disagreement before code changes.
- **PLAUSIBLE, not CONFIRMED (need a live boundary probe):** M-5 (LOW no-dispersion
  floor) and M-6 (no trough-confidence) — the entry-path money impact was refuted by the
  Platt clamp / hard-block, leaving a bounded exit-path belief asymmetry whose realized
  magnitude depends on live ensemble spread near a boundary at the dawn trough. M-7
  (`OK_FAST_ONLY` unreachable) has strong circumstantial evidence of silently excluding
  Hong Kong via a `wu_station=None` MISMATCH cascade, but the end-to-end HKO live event
  builder was not fully traced — a probe of a live HKO DAY0_EXTREME_UPDATED payload's
  `station_match_status` would close it.
- **Explicitly out of scope of this bundle:** the qkernel "global current-state solve"
  internals (the `q_lcb` execution economics were treated as a boundary in several
  sub-mechanisms); the upstream replacement-forecast hook's submit-time observed-extreme
  freshness (flagged by the H-2 investigator as an entry-lane surface not traced here).
- **Refuted:** 8 candidate findings did not survive verification (3 in the conditioning
  core, 1 each in nowcast, calibration, freshness, and input-correctness).

---

---

## 6. Empirical verification addendum (team-lead, settlement-graded) — 2026-07-18

The audit above is code-reasoning + adversarial verification. Its headline **T0-1
(tier0: whole-day sigma → symmetric HIGH+LOW over-dispersion)** was tested against
settled outcomes before any fix. **T0-1 is REFUTED for HIGH and only weakly/partially
present for LOW — it is NOT the symmetric tier0 the audit claimed.**

Method: for every settled Day0 posterior with `day0_conditioning.active` and a recorded
`observed_extreme_c` + `observation_time`, compute the served probability mass on bins
strictly beyond the observed extreme (HIGH: above; LOW: below), integer-bin pivot, and
compare to the realized exceedance frequency, stratified by lon-based local **solar**
hour (diurnal peak ~14–15, trough ~5–6). Scripts in scratchpad `t01_overdispersion.py`
/ `t01_low.py`.

HIGH (n=786 deduped markets), served P(settle>obs) vs realized:
| solar bucket | n | served P(>obs) | realized | ratio |
|---|---|---|---|---|
| pre (<12) | 54 | 0.547 | 0.852 | 0.64 |
| peak (12–16) | 158 | 0.255 | 0.234 | 1.09 |
| post (>16) | 574 | 0.045 | 0.070 | 0.65 |

Post-peak the served mass on above-obs bins is **0.045**, not the 0.15–0.42 the audit's
failure scenario hypothesized, and it is *below* the realized frequency (0.070). There
is **no post-peak over-dispersion**; the center clamp `mu=max(mu_forecast,obs)` plus the
`max(obs,X)` transform correctly concentrate mass into the observed bin because
post-peak the whole-day forecast center is typically at/below the observed peak. The
audit's scenario assumed `mu≈obs` (clamped up) with sigma=2.5 held wide; in the live
data `mu_forecast ≤ obs` post-peak, so the upper tail above obs is thin. If anything the
model slightly **under-weights** exceedance (ratios 0.64–0.65) — the *opposite* of T0-1,
and consistent with the finite-evidence floor fix landed the same day (commit
`c363aacfb`), which raises humility on possible zero-hit bins. Implementing T0-1's
sigma-narrowing would push above-obs mass even lower, **worsening** the observed
under-coverage.

LOW (n=110 deduped markets), served P(settle<obs) vs realized, by solar hour:
| solar bucket | n | served P(<obs) | realized | ratio |
|---|---|---|---|---|
| overnight (<5) | 3 | 0.599 | 0.667 | 0.90 |
| day (8–18) | 36 | 0.640 | 0.222 | 2.88 |
| eve (>18) | 71 | 0.011 | 0.000 | ~0 (correct) |

The bulk evening bucket (n=71) is **correct** (served 0.011 collapses like HIGH
post-peak). A real ~2.9× over-weight appears in the mid-day bucket (n=36) but is
**sample-limited**, and aligns with the audit's own LOW-specific gaps M-5 (no LOW
dispersion/quantization floor) and M-6 (no LOW trough-confidence) rather than a
symmetric whole-day-sigma tier0.

**Corrected severity — T0-1: downgraded from tier0 to LOW/PLAUSIBLE (LOW-track only).**
The mechanism's served Day0 q is *not* broadly over-dispersed; HIGH is well-calibrated to
slightly-conservative, LOW is well-behaved except a small-sample mid-day over-weight
worth more data. No architectural sigma-narrowing rewrite is warranted; if the LOW
mid-day signal strengthens with more settled data, the fix is the M-5/M-6 LOW
dispersion-floor / trough-confidence work, not a two-lane unification.

**Still standing (code-confirmed this session):** H-1 (cancel sweep at
`day0_hard_fact_exit.py:1068` omits `world_conn` → durable `observation_instants`
excluded; body verified at :578/:608 — real, resting BUY on a durable-dead bin escapes
cancel on cold-start/non-METAR) and H-2 (recapture `event_reactor_adapter.py:13077`
hardcodes `forecast_still_current=True` and reuses selection-time `q_lcb` at :25499 —
code fact confirmed; money impact still pending a trace of whether the upstream
replacement-forecast hook re-conditions the Day0 obs at submit). These are the real
priority items, not T0-1.

---

*File:line index of surviving findings*

| # | Severity | Finding | file:line |
|---|----------|---------|-----------|
| T0-1 | tier0 | Whole-day X, sigma never narrows | `src/forecast/sigma_authority.py:405`; `src/probability/joint_q.py:346`; `src/data/replacement_forecast_materializer.py:3136,3403` |
| H-1 | high | Cancel sweep omits durable source | `src/execution/day0_hard_fact_exit.py:1068` |
| H-2 | high | Recapture reuses selection-time q | `src/engine/event_reactor_adapter.py:13057,25479` |
| H-3 | high | No 2nd feed for NOAA/HKO | `src/data/day0_fast_obs.py:186`; `src/data/day0_observation_reader.py:396` |
| H-4 | high | HorizonPlattFit never applied | `src/signal/day0_high_nowcast_signal.py:105` |
| M-1 | medium | Stale monitor bound, no margin | `src/engine/monitor_refresh.py:2844` |
| M-2 | medium | Coverage count, not contiguity | `src/data/day0_observation_reader.py:354` |
| M-3 | medium | Two admission gates dead | `src/engine/event_reactor_adapter.py:15601` |
| M-4 | medium | Local-day boundary ×5 | `src/strategy/market_phase.py:119` |
| M-5 | medium | LOW no dispersion floor | `src/signal/day0_low_distribution.py:85` |
| M-6 | medium | No trough-confidence signal | `src/signal/diurnal.py:368` |
| M-7 | medium | OK_FAST_ONLY unreachable | `src/engine/event_reactor_adapter.py:15442` |
| M-8 | medium | Durable extreme not rounded | `src/execution/day0_hard_fact_exit.py:526` |
| M-9 | medium | nowcast_runs mixed provenance | `src/state/db.py:4344` |
| M-10 | medium | Ingest probe decorrelated | `src/data/source_health_probe.py:128` |
| M-11 | medium | DAY0_CAPTURE gate global | `src/engine/cycle_runner.py:618` |
| M-12 | medium | Input-correctness dead code | `src/strategy/live_inference/day0_input_correctness.py:63` |
| M-13 | medium | Verifier never re-proves math | `src/decision_kernel/verifier.py:458` |
| L-1 | low | NOAA auth not station-scoped | `src/engine/evaluator.py:209` |
| L-2 | low | obs_weight/backbone dead in value path | `src/signal/forecast_uncertainty.py:380` |
| L-3 | low | q-shape temper can't fix elapsed bias | `src/probability/joint_q.py:401` |
| L-4 | low | Fit has no built-in walk-forward check | `src/calibration/day0_horizon_calibration.py:151` |
| L-5 | low | Stale-belief counter not persisted | `src/engine/monitor_refresh.py:602` |
| L-6 | low | NO_SUBMIT skips Day0 authority parents | `src/decision_kernel/compiler.py:284` |
