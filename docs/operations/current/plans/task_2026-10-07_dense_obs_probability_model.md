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
- G5 on fix/noaa-page-intraday-all-cities 474f6d4e0 (rebased on e3411078c), handed to Data: 37 °C canonical_resolver routes, proof 3,209/3,209; resolver route = "resolver" rival (else all 6 FA de-admit); °C batch 1 req/min, typed 403 isolation. Risks: oracle detector now active for 37 cities (watch day0_oracle_anomaly_flags); Synoptic 2 req/min; tick load; removed page rows remain absorbing.
- G5 approved by Data; held off live until post-epoch restart (after 2026-10-08 06:00Z) because forecast-live's warm materializer worker respawns and imports on-disk code (partial deploy risk). Data will watch oracle flags, Synoptic failure rate, tick p90.
- Correction: materializer worker has run the DST fix 02849df39 since 10:59Z (respawn); G3/G4 e3411078c not loaded anywhere yet.
- Design review (operator branch 4f2446661): BLOCKED G1 rule "FA row at METAR instant = MONOTONE (semantic cert)". Evidence: Lucknow 2026-09-06 HIGH (NOAA era) — METAR VILK 060730Z 37/27 in AWC+Ogimet, page settled 31; IMD FA carries METAR ints (proof 2/2). Corrected: semantic certificates only from canonical page rows (noaa_wrh_*; G5 makes them intraday); FA/AWC METAR ints = provisional with empirical page-retention survival weight + exact-interval likelihood. Regression tests: Lucknow 09-06, Tokyo 760918.
- DEFECT (mine, from G4 9c8a58613; live since 13:46Z full restart): KmaObservationConflict at merge-time re-canonicalize aborted the whole tick (~1/min from 14:31Z, RKSI 14:30Z). Fix fix/kma-merge-conflict-isolation a2b984351: per-group catch, drop only that group's KMA transport, record typed conflict. Regression test fails on live, passes with fix; 0 new module failures. Handed to Data; ships in the watched restart with G5 + Data's deploy replay gate.
- G5 LANDED+LOADED (dc483dbb6, restart 15:19Z): 37 channels / 81 intraday page rows by 15:32Z; oracle flags 0 new; Synoptic 2 batches 200; tick >11 s 2/16.
- KMA merge fix a2b984351 loaded 14:58:47Z; 0 conflict errors since (verified by log line position).
- EHAM KNMI 403: params={} strips S3 presigned query (httpx). Fix fix/knmi-presigned-download (one-line), regression test, live smoke EHAM 15:30Z 19.1. Handed to Data (epoch 3 T0 16:00Z).
- VILK ValueError = ConnectTimeout to IMD endpoint (intermittent, 3.7–14 s vs 6 s timeout); rows still landing. No change.
- KNMI fix c696081f2 LANDED+LOADED (data-ingest 15:41:12Z); first EHAM row 15:41:41Z (15:30Z 19.1). Epoch 3 freeze: no restart 2026-10-07 16:00Z → 10-08 18:00Z unless serving breaks. Dense-obs operator lands only after that.
- Obs-writer latent defect (from 8eb33debb, Codex native-custody series): _custody_revision_core_matches compares parser_version → backfill+live custody overlap vetoed (payload_hash_mismatch) instead of widened (Taipei 31→33 repro). Not firing live (no backfill over current hours). Fix dispatched on fix/obs-custody-core-parser-version; hold until after epoch 3 (10-08 18:00Z). Until then: no backfill_obs.py over current hours.
- Writer custody fix: fix/obs-custody-core-parser-version (parser_version excluded from custody core; receipts already popped). Repro (backfill_obs Taipei 31 + live tick 33) fails on live, passes; identity keys still veto; 63/63 failure set identical. One 8eb33debb test param that locked the defect (parser_version must veto) removed. Pushed; Data loads after epoch 3.
- 16:50Z: a Codex native-role session reloaded onto v7 (64e4951ec) after moving the replay gate post-reload (a1da25eb6); 0/17 posteriors. Data restored ae6f13f02 (gate-before-reload) + restart 17:01Z → epoch 3 broken. Data loading writer fix 13a0b2ee5 now, then arms epoch 4. Dense-obs operator to be scheduled against epoch 4.
- Writer fix LOADED as cad49a322 (data-ingest 17:04:42Z; 254 writer tests pass). deploy_live.py 900b1ef90: differential forecast-live gate (replay 6 queued requests on target vs running SHA; refuse if fewer READY). Epoch 4: 2026-10-07 18:00Z → 10-08 18:00Z (+2 h follow-up). Dense-obs operator: request restart after epoch 4, through this gate.

## Implementation delivered + review round (2026-10-07 ~18:00Z)
- Branch feat/day0-dense-obs-state-space @ 42773628e (15 commits, rebased on origin/live 900b1ef90), pushed to origin (branch only, not live). Agent report verified locally: failure-set diff 86 modules base 8471/682 vs branch 8504/682, NEW 0; dense tests 17+16 pass; replay 56 families (Helsinki 28 + Tokyo 14 dense, Singapore legacy), oracle max |dq| 0.00227, p99 0.684 s; legacy byte-identity on 8 V2 posteriors.
- Open coordinator concern: G1 broadened day0_is_noaa_preliminary_source to FA route sources; ~10 consumers in event_reactor_adapter.py (not editable here) incl. source_channel_pair identity checks; no end-to-end adapter test for an FA city. Read-only trace dispatched (Sol) -> scratchpad/fa_adapter_trace.md.
- Consult review fired same thread: rid REQ-20261007-125708-2dc18b (parent REQ-20261007-003521-e5e3a6, WebCodex, compare 900b1ef90...42773628e, key dense-obs-impl-review-v1). Asks (a) clustered retention, (b) page-window drop rule, (c) tau information set, (d) G1 consumers, (e) model choices bias, (f) G8/G2 edge cases, (g) revision bump blindness.
- Next: act on both; then hand to Data for landing + restart after epoch 4 (ends 2026-10-08 18:00Z) through the differential gate. Do not restart from this session.

## Consult review verdict (REQ-20261007-125708-2dc18b, 2026-10-07 ~18:33Z): NO-GO on 42773628e
Answer: /tmp/cgc/answer_REQ-20261007-125708-2dc18b.txt (repro bundle not on disk). Coordinator-verified in code:
- Provisional factor (day0_dense_state_space.py:388-396): the non-retained branch is "pending R(T) joins" = correction, not removal. Independent case [.1,.9] returns [0,1].
- Page-window resolve pads by GRID_MIN/2 and hard-deletes on a preliminary fetch span.
- gather_day runs at tau (current-print first receipt), not the decision cut. A late page row is ignored; freshness is judged at the old clock.
- Dense dispatch precedes explicit operator (day0_hourly_vectors.py ~1756), so a V2 replay can turn dense.
- Helsinki artifact retention is 1035/1043, not the plan's 1085/1127.
Consult claims, to be re-verified by the agent:
- the G1 predicate broadening makes the adapter substitute AWC 25 for JMA 24.6 → GLOBAL_DAY0_CONDITIONING_OBSERVATION_MISMATCH;
- the G2 routine-minute gate drops genuine LTFM SPECIs;
- separately averaged simultaneous factors cost 0.68 pp on Helsinki;
- tail-mass deletion;
- 5-min slot identity merges distinct reports;
- qualification is inherited, not requalified.
Fix round 2 dispatched to the operator agent with decisions D1-D7:
- D1: typed NATIVE_REPORT vs INSTRUMENT_PROXY; retract the predicate broadening.
- D2: measure page revision, visibility lag and final row outcome, then a normalized kept/corrected/removed mark with a shared outage latent. Page absence is a likelihood, not a deletion. If page rows ever revise → STOP, law fork.
- D3: decision-cut info set, sealed evidence, operator-honoured dispatch.
- D4: one prepared dense request, so fallback is byte-identical.
- D5: numerics.
- D6: requalify A0 / corrected-no-dense / corrected-dense, true log score.
- D7: positive-held census through the real consumer path.
Deploy is blocked until round 2 lands and a re-review passes.

## D2(i) law fork result + coordinator ruling (2026-10-07 ~19:00Z)
Agent measured (WORLD read-only, 09-13..10-07, 40,399 station-instants; branch at 2033c06d0 on origin/live b452210d5):
- °C page: 0 value revisions out of 33,439.
- °F page: 31 out of 6,960 instants revised once, 4–19 min after first receipt, on 9 US intraday routes, 10-01..10-05.
  - Every revision switches the conversion source: a value converted from 0.1 °C (e.g. KAUS 73.94 °F) becomes one converted from integer °C (73.4 °F).
  - 17 of the 31 changed the settlement integer (8 up, 9 down).
  - One changed a day extreme: KAUS 10-02 LOW, first 74, revised to 73, settled 73.
- Row deletion after appearance: unobservable. Each fetch covers only the last 180 min and the DB does not keep fetch membership.
- Page visibility lag after G5 (≥ 10-07 14:00Z): °C p50 13.0 / p90 33.3 / max 84.6 min (n = 261); °F p50 5.9 min (n = 46).
- Final outcome of AWC provisional rows on finalized days: 36,442 rows; 98.41% kept, 1.41% absent, 0.18% corrected.
  - Absences cluster: 09-20 03–07Z (257) and 09-22 11Z (31).
  - Two stations stand out: UUWW 105/1,089 and LTFM 47/1,027.
  - SPECIs go missing about 5× more often than routine reports.

Ruling:
- °C page rows stay the semantic certificate. The dense operator is °C only.
- Deletion-after-appearance is a named residual, the same assumption live already makes.
- °F revision is a new gap, G9, outside this branch.
- D2 implementation:
  - retention per station × report kind;
  - the removal mixture (outage keeps T evidence; a gross isolated miss voids it) fitted from the 514 misses;
  - outage latent Z over a UTC interval;
  - corrected rate measured;
  - a(lag) as a pooled posterior predictive;
  - a fetch counts as absence evidence only where its span covers t.
- G9 + G10 (°F revision root cause/fix; page-deletion measurement in the writer) dispatched to a separate agent (worktree).

## Overnight interruption (2026-10-08)
- Both agents died on API 429 (~2026-10-07 23:xx local). The converge sweep removed their worktrees at 05:10Z.
  - Dense agent: uncommitted D1 work was preserved as f44ae3314 wip(converge) on feat/day0-dense-obs-state-space.
  - G9 agent: no wip commit was found. Its root cause is established (31/31 verified live). Scratch analysis survives in scratchpad/g9_*.
- 11:30Z: both worktrees recreated at the same paths and locked (the sweep unlocks idle trees; locking is not protection). Both agents resumed via SendMessage.
- Data process restart window: after epoch 4 ends at 18:00Z today. The dense branch is NOT a candidate for it (NO-GO pending round 2 + re-review).

## D3 protected-seam ruling (coordinator, 2026-10-08 ~12:10Z)
The agent proved that the adapter (protected) always passes explicit V2/V3 and cutoff=decision_time, and that strict replay receives no persisted sealed fields. The dense law therefore cannot serve adapter decisions without an adapter edit.
- R1: the builder gets typed `evaluation` (SELECT/REPLAY) and `sealed_dense` inputs. With evaluation=None, behaviour is byte-identical to live. SELECT evaluates at the decision cut and seals the evidence. REPLAY runs the explicit operator only; DENSE replays from sealed evidence with no DB read.
- R2: the materializer writes no DENSE certificate until a caller can replay it (seam-gated, not flag-gated).
- R3: the adapter seam spec goes in this doc. It is requested from the owning lane only after D6 qualifies the law.
- R4: order is D4 → D2 implementation → D5 → D6 (offline via SELECT) → D7.
- R5: D1+G8 split onto fix/day0-fast-route-kinds from origin/live, landable alone (it fixes live bugs: Tokyo LOW wrong fact, mirror rows, supersession).

## Round 2 delivered + round 3 review fired (2026-10-08 ~17:45Z)
- feat/day0-dense-obs-state-space @ 0135c8769, pushed as a branch, not live. R5 standalone: origin/fix/day0-fast-route-kinds @ f5aebd0ed.
- D6 (true log score, receipt-frozen 09-27..10-07):
  - Tokyo HIGH is the only eligible family. B−A0 [−1.034, −0.658]; B−A [−0.086, −0.033]; 0/126 high-confidence errors.
  - Tokyo LOW is out on B−A; Helsinki HIGH/LOW are out on B−A0; Singapore has no NEA writer.
  - A is worse than A0 on Helsinki HIGH (0.863 vs 0.607). The temperature/forecast component is the weak part.
- D7: 14 held families, none dense; head ≡ live; 10 V2 rebuilds are byte-identical.
- Consult round 3: rid REQ-20261008-124415-4a2122 (parent REQ-20261007-125708-2dc18b, key dense-obs-impl-review-v3). It returns two verdicts: the seam request, and route-kinds landing. It also asks for the ensemble-carrier construction.
- Route-kinds Opus review is running; it was asked to quantify the proxy-conditioned posterior MISMATCH-until-reseed window. The G9 Opus review is also running.

## Round 3 verdict (fresh thread 6ac7ddc5, rid REQ-20261008-131420-88d180, ~19:00Z): NO-GO on both
Answer: scratchpad/round3_answer_page.txt, read from the CDP tab. The driver mis-reported unknown_send; the turn was in fact sent.
- Architecture: CORRECT-BUT-SUBOPTIMAL. Highest-leverage step: condition the current authorized ensemble carrier with the corrected observation/tape law (candidate C) instead of a single IFS path.
- Seam request: NO-GO. Open BLOCKERs:
  - numerics: a tail of 4.3e-26 comes out 0; reflection asymmetry 2e-5 vs 0.39 from CDF cancellation;
  - lifecycle: repeated absences multiplied as independent; coverage inferred from receipt; the 41 SPECI misses are unreconciled;
  - confidence: 8 variants resampled to 500 rows inflate the bound from 0.639 to 0.944;
  - R3 lifecycle: law orphaned on refit; stale seal kept after a dense→legacy switch, which blinds held families;
  - D6 not run on the actual law: offline proxy, cell 0.1, persisted A0, no multiplicity, HC counted Poisson over correlated decisions.
- Route-kinds: NO-GO as packaged. D1/G1/G8 are right. Blockers:
  - native ENTRY age gate missing: a 31-min native reading is accepted where AWC is rejected (protected adapter ~37602);
  - obsolete JMA conditioning: MISMATCH with no reseed trigger, and the reseed is blocked by the JMA clock.
- Cleared from round 2: independent removal; late page fact; the 16→15 correction; wrong-station freshness; native SPECI admission; explicit-V2 replay; 5-min merging; seed suppression; drift folding; LOW empty tape.
- Dispatched: Track A (route-kinds A1–A3) first; then Track B (B1–B6: numerics, lifecycle, confidence, R3 lifecycle, candidate C, actual-law qualification).
