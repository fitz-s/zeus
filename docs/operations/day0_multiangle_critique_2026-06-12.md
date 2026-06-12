Assessment: the landed work is a major observation-plumbing improvement, but it is not yet the ideal day0 lane. The biggest remaining gap is no longer “can Zeus see the same-day temperature fast enough?” for eligible WU/ICAO cities. The gaps are: source eligibility/city scoping, persistent restart-safe fact state, day0-specific probability authority/calibration, day0 microstructure gates, and a controlled promotion protocol. A global edli_live_scope=forecast_plus_day0 flip is not yet justified; a narrowly capped canary can be made honest after the BLOCKER items below.

Angle 1 — OBSERVATION TRUTH

Ideal. Day0 observation truth should maintain a settlement-grade, city-local running high/low from local 00:00 through decision time, with latency low enough to protect entries and exits, source fidelity by settlement family, explicit rounding/preimage semantics, DST-safe local-day membership, restart recovery, and a fail-closed state when the full observation window cannot be proven.

Landed. For eligible wu_icao cities, the landed Option B path falls back from stale/absent/WINDOW_INCOMPLETE WU timeseries to the in-process METAR fast-lane memo; the cache must be ≤15 minutes old, source is tagged metar_fast_lane, and non-wu_icao cities are not fabricated into coverage. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/379351ed2c36c17e70271bda54801049d39455bd/03_optionbc_impl_report.md
 and https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/53f9c8a58a46e291f1726d3dd44186b464c76dc0/observation_client.py
. 
Gist
+1
 The fast lane enforces same-station identity, skips F-settled reports without the T-group, uses ZoneInfo local-day membership, and computes running extrema from cached METAR reports. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/3c31afa195493185a230f67c18e6c40260b2f1d8/day0_fast_obs.py
. 
Gist
+1

Gap A — live scope is still broader than the source truth. Seoul is empirically non-faithful in the WU-vs-METAR table: same-station timestamp-matched rounded deltas are ≥1C about 4.5% of the time, and the table marks Seoul/RKSI faithful=False. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/4e8b1055ef04e3f5862c4f288140fa895caed0d6/05_wu_metar_divergence.md
. 
Gist
 Option B+C also says HK/London/Miami/NYC/Paris/Seoul/Shanghai/Tokyo do not benefit from the fast path under the landed source gates and fall through to WU. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/379351ed2c36c17e70271bda54801049d39455bd/03_optionbc_impl_report.md
. 
Gist
 A global flip would therefore promote cities whose observation path is still WU-latency-bound or source-ineligible.

Completion item. Add a live day0 source allowlist gate before submission, not just inside fast_obs_source_for_city: src/engine/evaluator.py or src/engine/event_reactor_adapter.py should reject live day0 entries unless city.name is in day0_live_city_allowlist, fast_obs_source_for_city(city) is non-null, source in {"metar_fast_lane","wu_api"} passes the settlement-family policy, and the latest observation is under the source-specific age budget. Seoul must be excluded until there is a settlement-faithful fast source or a WU-only, explicitly capped policy.

Severity. BLOCKER for a global live flip; SHOULD for later expanding beyond the initial allowlist.

Gap B — WINDOW_INCOMPLETE is necessary but not sufficient. The current coverage status mainly proves the first local-day sample arrived within a 2-hour grace window and sample count is at least four; it does not prove continuous coverage, maximum tolerated gaps, or that all local-day intervals up to the decision time are represented. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/53f9c8a58a46e291f1726d3dd44186b464c76dc0/observation_client.py
. 
Gist
 The original plan explicitly identifies the WU 23-hour window and restart-after-02:00 failure as a structural day0 issue. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/cd7d748bb0a3101f9bbee69daf871e3711531a51/01_obs_fastlane_plan.md
. 
Gist

Completion item. Replace _compute_day0_coverage_status(first_local, n_samples) with a richer Day0CoverageProof in src/data/observation_client.py: first sample, last sample, max gap, expected cadence by station, DST day length, restart/backfill source, and coverage_through_reference_utc. The evaluator quality gate should accept only FULL_THROUGH_DECISION or a consciously named canary state.

Severity. SHOULD for initial eligible-city canary; BLOCKER for unsupported/non-fast cities and for DST-transition days.

Gap C — the fast lane’s entry/kill truth is process-local. Day0FastObsEmitter stores cached reports and the kill/live memos in memory. A restart clears the latest rounded kill memo; the next successful aviationweather fetch can rebuild from 36 hours of reports, but if the restart coincides with an aviationweather outage, hard-fact protection degrades to slower WU. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/3c31afa195493185a230f67c18e6c40260b2f1d8/day0_fast_obs.py
 and https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/4c8c4ab44dd59fbe48115af90012446ed99647c3/day0_hard_fact_exit.py
. 
Gist
+1
 The brief also says day0_metric_fact and day0_nowcast_runs exist but have never been wired. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/57f33c49a84160f8d32dd5501b0b7bf69dccbaeb/00_BRIEF.md
. 
Gist

Completion item. Wire day0_metric_fact or an equivalent sanctioned world-class writer from Day0FastObsEmitter.emit_prefetched: persist (city, target_date, metric, rounded_extreme, raw_extreme, source, station, observation_time, observation_available_at, coverage_proof, authority_status). On boot, day0_hard_fact_exit.settlement_grade_effective_extreme should read the latest persisted authorized fact before relying on in-process memo.

Severity. BLOCKER once live day0 positions can exist across daemon restarts.

Gap D — timestamp-matched WU/METAR fidelity does not prove final daily-extreme fidelity. The divergence table proves WU and METAR agree at matched timestamps for most stations; it does not prove that the fast lane’s max/min of current METAR temperatures equals the eventual WU daily high/low settlement. The parser uses the temp field and running_extremes_for_local_day computes max/min over those current-temperature reports; it does not parse METAR max/min remark groups or compare against final WU daily settlement highs/lows. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/3c31afa195493185a230f67c18e6c40260b2f1d8/day0_fast_obs.py
 and https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/4e8b1055ef04e3f5862c4f288140fa895caed0d6/05_wu_metar_divergence.md
. 
Gist
+1

Completion item. Add a 30–90 day final_daily_extreme_vs_fastlane_extreme audit by city/metric: final WU settlement high/low vs reconstructed fast-lane running high/low using only data available by each decision time. Until that audit is clean, add a one-bin-edge quarantine for entries whose EV depends on an unobserved one-unit spike not having occurred.

Severity. BLOCKER for material size; SHOULD for a tiny canary with boundary quarantine.

Angle 2 — NOWCAST PROBABILITY

Ideal. Day0 q should be one authority: P(final rounded local-day extreme in bin | observed extreme so far, current temp, remaining-day distribution, time-of-day, source quality, market topology). It should make absorbing states explicit, share the same q source between entry and finite-bin exit, carry calibration provenance, and degrade confidence when uncalibrated.

Landed. The held-position monitor path builds a day0 observation, fetches an ensemble, constructs temporal context, computes remaining-day member extrema, routes through Day0Router, then applies a monitor calibrator if one exists. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/cbb4b6ab2ed7b03daa7339d011ea533ba2cded43/monitor_day0_path_excerpt.py
. 
Gist
+1
 The settings say day0_remaining_day_q_enabled=true, but only for shadow evidence; the brief says day0_horizon_platt_fits is empty and the diurnal/persistence ETLs exist but are unscheduled. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/0b16f10478a9392d4a6922f137bf8ad4e9219b10/settings_day0_keys.json
 and https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/57f33c49a84160f8d32dd5501b0b7bf69dccbaeb/00_BRIEF.md
. 
Gist
+1

Gap A — no honest calibration story for live q. The comparator can now start accumulating because receipts dual-persist, but historically it had zero settled paired cells. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/2c2cc73000c0ebe0be07d30b2e6d94635e473954/02_receipt_dualpersist_report.md
. 
Gist
 Empty day0_horizon_platt_fits means live day0 q is either raw or borrowing a non-day0 calibrator; that is not the same thing as a calibrated same-day posterior.

Completion item. In src/engine/evaluator.py and the day0 proof path, require one of: a day0-specific calibrator keyed by city/metric/horizon/source, or an explicit UNCALIBRATED_DAY0_NOWCAST mode with a severe LCB haircut, small caps, and shadow-comparator monitoring. Also write every live/shadow nowcast to day0_nowcast_runs with q_raw, q_cal, q_lcb, source, horizon, observed_extreme, and market price.

Severity. BLOCKER for normal-size live entries; SHOULD for a tiny, explicitly uncalibrated canary.

Gap B — entry q and exit belief are not clearly one authority. The brief says the replacement posterior is now the exit belief authority, while day0_remaining_day_q feeds shadow receipts only. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/57f33c49a84160f8d32dd5501b0b7bf69dccbaeb/00_BRIEF.md
. 
Gist
 On day0, a posterior updated only a few times a day can be stale within an hour.

Completion item. Create a single Day0NowcastAuthority adapter used by both entry proof and finite-bin exit evaluation. monitor_refresh._refresh_day0_observation should be the source of held-position day0 q, not an optional side refresh. Exit receipts should record probability_source=day0_nowcast, observation source, observation age, horizon, and calibration level. If the nowcast is unavailable, finite-bin estimator exits should fail-soft to hold while hard-fact exits remain active.

Severity. BLOCKER for opening live day0 positions.

Gap C — diurnal tables are zombie organs unless explicitly declared non-authoritative. The code path can use ensemble hourly remaining extrema and a solar/temporal context, so the dead diurnal tables are not necessarily mandatory for a first canary. But leaving diurnal_curves, diurnal_peak_prob, and temp_persistence ETLs unscheduled while day0 q is live creates ambiguity about whether the model is using all intended intraday structure. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/57f33c49a84160f8d32dd5501b0b7bf69dccbaeb/00_BRIEF.md
 and https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/cbb4b6ab2ed7b03daa7339d011ea533ba2cded43/monitor_day0_path_excerpt.py
. 
Gist
+1

Completion item. Either wire and monitor those ETLs, or add code comments/config assertions saying the live day0 q does not depend on those tables. Do not leave them as implied-but-dead calibration organs.

Severity. SHOULD.

Angle 3 — ENTRY ECONOMICS ON DAY0

Ideal. Day0 entries need stricter economics than day-2 entries: maker-first routing, no taker escalation except explicit emergency exit, max spread, min displayed depth, per-city/per-day notional caps, no entries during observation transition windows, no entries close to settlement, and no finite-bin entries when a one-degree move can flip the bin from alive to dead before the book reprices.

Landed. The settings expose edli_live_scope, day0_extreme_trigger_enabled, day0_authority_catchup_scanner_enabled, day0_hard_fact_live_enabled, and day0_remaining_day_q_enabled, but they do not show a day0 city allowlist, notional cap, time window, spread gate, maker-only gate, or transition-window ban. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/0b16f10478a9392d4a6922f137bf8ad4e9219b10/settings_day0_keys.json
. 
Gist
 The hard-fact module cancels resting entry orders only when a bin is hard-fact dead or the family is anomaly-paused; it explicitly says general screen-reprice/stale-quote cancellation remains future work. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/4c8c4ab44dd59fbe48115af90012446ed99647c3/day0_hard_fact_exit.py
. 
Gist

Gap A — no canary economics envelope. A global flag flip makes the day0 lane live without a visible day0-specific money envelope.

Completion item. Add day0_live_city_allowlist, day0_live_max_order_notional, day0_live_max_city_notional_per_day, day0_live_max_global_notional_per_day, day0_live_one_position_per_city, and day0_live_high_only_initial settings. Enforce them in the final pre-submit proof path, not only in the scanner.

Severity. BLOCKER.

Gap B — transition-window controls are not evidenced. The operator’s 06-10 revert context explicitly included a panic-sell-on-transition incident. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/57f33c49a84160f8d32dd5501b0b7bf69dccbaeb/00_BRIEF.md
. 
Gist
 The landed code has hard-fact exits but no visible no-entry window after DAY0_EXTREME_UPDATED, no “book caught up” requirement, and no edge-bin quarantine.

Completion item. Add _day0_entry_economics_rejection_reason(...) in src/engine/evaluator.py: reject entries within N minutes after a rounded extreme update, within one rounding quantum of a bin edge unless the EV survives an edge-stress scenario, after historical_peak_hour + X for high markets unless the posterior is already absorbing, and within the last 2–3 local hours of the market day. The same gate should require fresh book timestamp after the latest observation timestamp.

Severity. BLOCKER.

Gap C — maker/taker and escalation deadlines are not day0-specific. Thin same-day books make a day-2 escalation policy dangerous if reused.

Completion item. In the order-routing layer, force day0 entries to maker-only for the first stage. Disable automatic crossing/escalation for day0 entries. Allow taker only for hard-fact exit or explicitly approved finite-bin EV cash-out, and only with a max-slippage guard.

Severity. BLOCKER for entries; SHOULD for later size expansion.

Angle 4 — EXIT / PROTECTION

Ideal. Exit protection should have two separate organs: immediate absorbing hard-fact exit/cancel for structurally dead sides, and finite-bin estimator exit driven by the same day0 nowcast q used for entry, with post-cost EV and confidence separation. It should never panic-sell merely because the observed extreme entered a finite bin; it should never hold a structurally dead YES or dead shoulder NO.

Landed. The hard-fact lane correctly treats dead bins and absorbing shoulders as immediate structural exits/wins, while finite bins merely containing the running extreme return None as “estimator territory.” Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/4c8c4ab44dd59fbe48115af90012446ed99647c3/day0_hard_fact_exit.py
. 
Gist
 The monitor has a held-position day0 q refresh path, but the attached evidence does not prove that this nowcast is the sole input to finite-bin exit decisions. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/cbb4b6ab2ed7b03daa7339d011ea533ba2cded43/monitor_day0_path_excerpt.py
. 
Gist

Gap A — finite-bin adverse move policy is not complete. The landed hard-fact design intentionally leaves finite bins to the estimator lane. That is correct, but the estimator lane must be day0-specific and observation-constrained.

Completion item. Add Day0FiniteBinExitPolicy near src/engine/monitor_refresh.py / Position.evaluate_exit: compute q_nowcast from the day0 authority, compare held-side expected value to executable bid after fees/slippage, require CI separation or calibrated LCB, and apply a transition-window maturity gate. For buy_no, compare against 1 - q_bin; for buy_yes, compare against q_bin. Record the exit decision’s q source and observation age.

Severity. BLOCKER before opening live day0 positions that can need finite-bin exits.

Gap B — stale posterior exit belief is still a day0 hazard. The brief’s “replacement posterior exit belief” is a good general lifecycle fix, but day0 needs an intraday replacement when observations move every few minutes. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/57f33c49a84160f8d32dd5501b0b7bf69dccbaeb/00_BRIEF.md
. 
Gist

Completion item. Gate day0 finite exits on position.probability_fresh == True from _refresh_day0_observation, and reject/hold with a loud stale-belief alarm if the only available belief is the coarse replacement posterior. Hard-fact exits remain independent and immediate.

Severity. BLOCKER.

Gap C — resting-order protection is still narrow. Dead-bin cancel exists; stale quote, stale screen, and transition-window cancel are explicitly future work.

Completion item. Extend cancel_day0_dead_bin_resting_entries or add a sibling cancel_day0_risky_resting_entries: cancel live day0 BUY orders when the latest observation is newer than the quote snapshot, when q moved outside proof, when an extreme update occurred within the transition window, or when source health is degraded.

Severity. SHOULD for micro canary; BLOCKER for meaningful size.

Angle 5 — FAILURE CONTAINMENT

Ideal. Failure policy should be explicit: source divergence pauses entries and irreversible exits; aviationweather outage disables METAR-based entries but allows WU if fresh; WU outage allows eligible METAR canary entries only if cache and coverage proof are fresh; restart reconstructs running facts from persisted state; stale belief never forces a sell; unknown source state fails closed for entries and fail-soft-hold for positions.

Landed. The fast lane has a faithfulness gate, anomaly check, stale-cache states, entry cache-age limit, and kill-vs-live memo separation. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/3c31afa195493185a230f67c18e6c40260b2f1d8/day0_fast_obs.py
. 
Gist
+1
 The hard-fact lane fail-softs to None on source failure and suspends on active oracle-anomaly pause. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/4c8c4ab44dd59fbe48115af90012446ed99647c3/day0_hard_fact_exit.py
. 
Gist

Gap A — no explicit source-health gate is visible at pre-submit. The pieces exist, but the promotion-critical behavior should not be implicit in scattered helpers.

Completion item. Add day0_source_health(city,date,metric) and require it in the final live proof: states should include OK_FAST_AND_WU, OK_FAST_ONLY, OK_WU_ONLY, DEGRADED_FAST_STALE, DIVERGENCE_PAUSED, WINDOW_INCOMPLETE, and UNSUPPORTED_SOURCE. Entries accept only explicitly allowed states. Positions use the same state to decide hold vs hard-fact exit vs finite EV exit.

Severity. BLOCKER for global flip; SHOULD for tiny canary if allowlist gates enforce equivalent behavior.

Gap B — comparator persistence is fail-soft, which is correct operationally but weak for evidence. The dual-persist report says shadow ledger persist failure logs a warning but still writes regret. That avoids blocking the reactor, but the promotion evidence stream can silently undercount if those warnings are not health-critical. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/2c2cc73000c0ebe0be07d30b2e6d94635e473954/02_receipt_dualpersist_report.md
. 
Gist

Completion item. Add a scheduler health metric: day0_shadow_receipt_dual_persist_success_rate. Promotion evidence should be invalid if the success rate is below 99% or if no-submit receipt counts diverge materially from day0 regret counts.

Severity. SHOULD.

Gap C — unsupported source families still lack day0 truth paths. HKO and OGIMET are explicitly outside the METAR fast lane, and the plan says monitor paths for HKO/OGIMET require separate investigation. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/cd7d748bb0a3101f9bbee69daf871e3711531a51/01_obs_fastlane_plan.md
. 
Gist

Completion item. Until HKO/OGIMET monitor observation paths are source-native and tested, exclude those cities from day0 live entry allowlists.

Severity. BLOCKER for those cities.

Angle 6 — PROMOTION PROTOCOL

Ideal. Promotion should separate the operator’s umbrella flag from risk admission. edli_live_scope may be operator-controlled, but live day0 submission still needs honest gates, canary scope, caps, and auto-revert triggers. The comparator should be used for evidence, not as a retroactive justification.

Landed. Settings are currently edli_live_scope="day0_shadow", with notes saying the 06-09 global opening was interim-reverted on 06-10 pending review and that promotion remains a separate operator decision on comparator evidence. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/0b16f10478a9392d4a6922f137bf8ad4e9219b10/settings_day0_keys.json
. 
Gist
 The comparator substrate only now starts accumulating because edli_no_submit_receipts was not being populated for day0 shadow. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/2c2cc73000c0ebe0be07d30b2e6d94635e473954/02_receipt_dualpersist_report.md
. 
Gist

Gap A — global flag is too coarse. There is no visible staged live-scope key that says “only these cities, only high markets, only this notional, only this time window.”

Completion item. Add canary gates before the umbrella flip: day0_live_stage, day0_live_city_allowlist, day0_live_metric_allowlist, day0_live_max_order_notional, day0_live_max_city_notional, day0_live_max_global_notional, and day0_live_time_window_policy. These should be enforced in the final submit adapter even if edli_live_scope=forecast_plus_day0.

Severity. BLOCKER.

Gap B — no settled evidence yet under the repaired comparator. The settings note itself references a comparator bar of >51% after-cost and about 150–270 samples, but those samples can only begin accumulating after the dual-persist fix. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/0b16f10478a9392d4a6922f137bf8ad4e9219b10/settings_day0_keys.json
 and https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/2c2cc73000c0ebe0be07d30b2e6d94635e473954/02_receipt_dualpersist_report.md
. 
Gist
+1

Completion item. For full live expansion, require: ≥150 settled paired cells for the initial city/metric cohort, positive after-cost edge with a lower confidence bound above 51%, no source-integrity incidents, and no invariant violations. For a canary, allow lower N only with tiny caps and explicit “uncalibrated canary” provenance.

Severity. BLOCKER for full live; SHOULD for micro-live.

Angle 7 — BLIND SPOTS

Blind spot A — low-temperature markets are not just high markets with signs flipped. Daily lows often occur near local midnight or in the next pre-dawn window; by the time same-day trading is active, many low bins may already be structurally constrained. The code is metric-aware, but the rollout evidence and fast-lane narrative are mainly framed around running highs. Completion: initial live allowlist should be temperature_metric="high" only; add a separate low-market audit covering local midnight, early-day coverage, and low shoulder semantics. Severity: SHOULD for canary; BLOCKER for low-market live.

Blind spot B — forecast run age can dominate after observation freshness is fixed. The monitor path fetches an ensemble and truncates remaining-member extrema to the temporal context, but the attached evidence does not show a hard gate on the ensemble run’s initialization age relative to the current observation. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/cbb4b6ab2ed7b03daa7339d011ea533ba2cded43/monitor_day0_path_excerpt.py
. 
Gist
 Completion: require ens_run_age_hours <= threshold for day0 q, and log q rejection when the remaining-day distribution is older than the observation update it is asked to condition on. Severity: SHOULD.

Blind spot C — quote timestamp must be newer than the observation state it prices. Day0 books can be stale immediately after a rounded extreme update. Completion: in the entry economics gate, require orderbook captured_at > latest_observation_available_at plus a post-extreme-update quiet period. Severity: BLOCKER.

Blind spot D — final settlement audit is more important than timestamp-matched feed audit. The WU-vs-METAR table is necessary but not sufficient; the missing audit is “would Zeus’s fast-lane running high/low at decision time have matched the eventual WU settlement high/low?” Completion: add that audit before material size. Severity: BLOCKER for scale.

(1) BLOCKER checklist gating the scope flip

 Add hard pre-submit canary controls: day0_live_city_allowlist, metric allowlist, per-order cap, per-city/day cap, global/day cap, one-position-per-city cap, and maker-only entry mode.

 Exclude all unsupported or non-fast/non-faithful cities from live day0 entry: Seoul, HKO/OGIMET families, and any city for which fast_obs_source_for_city(city) returns None until a native source path is proven.

 Persist restart-safe day0 observation facts: wire day0_metric_fact or equivalent and make day0_hard_fact_exit recover latest authorized extremes after restart.

 Add day0 entry economics gates: transition-window ban after rounded extreme moves, quote-after-observation freshness, spread/depth checks, post-peak/near-settlement restrictions, and edge-bin quarantine.

 Make day0 nowcast the single probability authority for live day0 entry and finite-bin exit; forbid stale replacement-posterior-only finite exits.

 Add finite-bin exit policy using observation-constrained day0 q, executable bid, fees/slippage, and CI/LCB separation.

 Add an explicit day0 source-health state machine and fail-closed entry behavior for degraded/unknown source states.

 Establish an honest calibration/evidence mode: either day0-specific calibration, or an explicit uncalibrated micro-canary with severe caps and q haircut.

 Audit final WU settlement daily high/low versus fast-lane reconstructed running high/low; until clean, apply one-unit boundary quarantine or do not scale.

(2) Staged rollout I would run

Stage 0 — repaired shadow, no live submit. Keep edli_live_scope=day0_shadow while adding the canary gates. Run at least 24–48 hours with structured metrics: WU age, METAR age, fallback reason, coverage proof, source-health state, q_raw/q_lcb, book timestamp, spread/depth, and would-submit reason. Require dual-persist success rate ≥99%.

Stage 1 — micro-live high-only canary. Flip the umbrella only after canary gates exist. Allow only high-temperature markets in a small, faithful WU/ICAO set with strong table behavior and major liquidity: start with Chicago/KORD, Dallas/KDAL, Denver/KBKF, Atlanta/KATL. Exclude Seoul and all non-fast/no-benefit source families. Caps: max $5 notional per order, max $20 per city/day, max $75–100 global/day, one open day0 position per city. Entries are maker-only; no taker escalation. Time window: local 10:00 through historical_peak_hour + 1h, but never within 15 minutes after a rounded DAY0_EXTREME_UPDATED, never if quote timestamp precedes latest observation availability, and never in the last 3 local hours of the market day.

Stage 2 — broader faithful-city canary. After 3–5 clean local trading days, expand to 8–10 eligible WU/ICAO high markets that pass fast_obs_source_for_city and final-daily-extreme audit. Candidate additions: Los Angeles/KLAX, Austin/KAUS, San Francisco/KSFO, Seattle/KSEA, possibly Houston/KHOU only with the one-unit boundary quarantine because the divergence table shows a rare rounded max delta. Caps: max $10/order, $50/city/day, $250/global/day. Still maker-only entries.

Stage 3 — C-unit faithful expansion. Add C-unit cities only after confirming they pass the fast source gate in live config, not merely the divergence table. Candidate cohort: Amsterdam/EHAM, Milan/LIMC, Munich/EDDM, Madrid/LEMD, Toronto/CYYZ. Keep high-only unless the low-market audit is complete. Caps: max $25/order, $100/city/day, $500/global/day.

Stage 4 — normal live. Require ≥150–270 settled paired cells for the relevant city/metric/source cohort, after-cost lower confidence bound above 51%, zero source-integrity incidents, no restart-recovery failures, no transition-window losses, and no day0 quote freshness violations. Add low markets and unsupported source families only through separate audits.

(3) The one thing most likely to cause the next real-money day0 loss that nobody has named yet

The most likely next loss is fast-lane daily-extreme undercapture: the landed METAR lane proves fast timestamped current-temperature observations, but the attached divergence table is timestamp-matched WU-vs-METAR, not final daily-settlement high/low vs fast-lane reconstructed high/low. The code parses temp and takes max/min across report temperatures; it does not prove that no sub-hourly station maximum/minimum, WU daily summary value, or METAR remark max/min group can settle one unit beyond Zeus’s running extreme. Evidence: https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/3c31afa195493185a230f67c18e6c40260b2f1d8/day0_fast_obs.py
 and https://gist.githubusercontent.com/fitz-s/cf04f4a6038fa1f7440e8dd0991ea5c7/raw/4e8b1055ef04e3f5862c4f288140fa895caed0d6/05_wu_metar_divergence.md
. 
Gist
+1

That failure mode is worse than ordinary WU latency: Zeus can believe a bin is alive, quote it as alive, or fail to hard-exit, while final settlement has already crossed by one unit. The concrete antidote is a settlement-level audit: compare final WU daily settlement extrema to the fast-lane extrema that would have been known at each decision time, by city and metric, then add parsing/support for official max/min groups or impose a one-unit boundary quarantine until the audit proves the fast lane is settlement-complete.