# HANDOFF — Zeus live-arm correctness gate (2026-06-01)

Authoritative session state. Read this before acting. Several earlier premises were WRONG
and corrected by the operator — they are listed explicitly so they are NOT carried forward.

## 0. STATE (authoritative)
- GOAL #36: 3 fully-verified FILLED orders e2e, then 120-min heartbeat 守护. Currently **0 fills**.
- Daemon: `com.zeus.live-trading` PID 14287, **SHADOW** (`real_order_submit=False`),
  `loaded_sha == HEAD == 0d0939a480`, cwd = this checkout (`/Users/leofitz/.openclaw/workspace-venus/zeus`).
  Booted CLEAN on all committed fixes. Reactor cycling on fresh **00Z** ECMWF OpenData.
- **0 fills is NOT edge-suppression** (Wall A fixed that — 536 clean future candidates now score
  positive) and **NOT "no edge / market efficient / working correctly"** (forbidden framing).
  It is: (a) shadow, AND (b) the candidate pool has **catastrophic correctness defects** that would
  produce WRONG trades if armed. The arm is BLOCKED on correctness, not "waiting".

## 1. WRONG PREMISES — corrected; do NOT repeat
1. **TIGGE ≡ OpenData — IDENTICAL data, per the math spec + stat spec (operator authority).**
   Task #58's "purge TIGGE/full_transport as contamination" was a **WRONG, UNAUTHORIZED** decision
   on a false "TIGGE ≠ OpenData" premise. It deleted ~2 years of VALID calibration data → June/JJA
   is calibration-starved (~8 Platt models). Never repeat "TIGGE is contamination / different product".
   Fix = re-connect TIGGE(=OpenData) calibration data + refit JJA Platt on the FULL dataset.
2. **First armed order size ≈ $44 is the MAX, in DOLLARS notional (not shares, not typical).**
   `kelly_size_usd = f* × kelly_mult(0.25) × bankroll($185)`; f*≈0.95 only for near-certain bins.
   Typical order = a few dollars. The $5 cap (which forced min(kelly,$5)) is REMOVED → Kelly governs.
3. **Phantom candidates' root = day0 PHASE-BLINDNESS** (system trades POST_TRADING markets whose
   extremum already occurred), NOT merely cost-misread / stale-book. The ae5fe38 `market_end_at`
   partial is SUPERSEDED (it fails-OPEN on NULL endDate) — do NOT ship it alone.
4. **Wall D (chain_shares NULL 100/101) is NOT a blocker.** Those are old TERMINAL positions
   (correctly skipped); the 1 active position has chain_shares=16.75; daemon uses Polymarket REST
   (not on-chain balanceOf), live; a bridged EDLI fill WOULD populate chain_shares → exitable. Resolved.

## 2. COMMITTED FIXES (HEAD 0d0939a480 — 30 commits ahead of origin/main, NOT pushed; push = operator gate)
- `11b7579a9d` Wall B pre-venue parity: tick_size sourced from bound snapshot (not cert-payload float
  default 0.01 vs DB 0.001); expected_fill re-sweeps at depth-CAPPED size (SEV-1.1 first-armed-order
  blocker); `_snap_for_depth=None` init guards a MAKER-path UnboundLocalError a prior hot fix introduced.
- `69bee9b752` Wall A (CI §4.1): bias train/serve CI-split fix. POINT posterior used bias-corrected
  members but the EDGE BOOTSTRAP resampled UNCORRECTED → spurious 15-19¢ q-CI suppressed real edge.
  Now point + bootstrap share ONE corrected surface (`_market_analysis_from_event_snapshot`). Regression-clean.
- `0d0939a480` un-hardcode `max_orders_per_day` to honor config (remove canary 1/day). + operational
  config (config/settings.json, UNCOMMITTED): `tiny_live_max_notional_usd 5→185`, `tiny_live_max_orders_per_day 1→1000`.

## 3. PRE-ARM CORRECTNESS FIX-SET (design → critic → implement COMPLETE set, NO partials)
1. **DAY0 wrong-side** — COMPLETE design `docs/operations/DAY0_OBSERVATION_WRONGSIDE_ROOT_2026-06-01.md`.
   Root: EDLI live path is **phase-blind** — never calls the in-repo `market_phase_for_decision()` /
   `MarketPhaseEvidence` (grep: 0 EDLI refs). Admits POST_TRADING markets (weather endDate = 12:00 UTC
   of target_date per F1; Paris "low 14°C June1" decided 16:06Z, 4h past close → buy_no = LOSING side of
   observed low=14°C). Fix: compute MarketPhaseEvidence once per family at admission (reactor.py:195/507),
   FAIL-CLOSED reject POST_TRADING/RESOLVED/None, admit only PRE_SETTLEMENT_DAY/SETTLEMENT_DAY.
   Blast: 13 same-day buy_no candidates, ~1/3 of live pool. Forbidden alt: wiring day0 obs into
   forecast_only (no fresh obs in DBs; resurrects gated-off day0 path).
2. **BEST-ORDER selection** — COMPLETE design `docs/operations/BEST_ORDER_SELECTION_ROOT_2026-06-01.md`.
   Root A: NO global best-order selector — `fetch_pending` (event_store.py:122) orders by ARRIVAL
   (priority/available_at/received_at); process_pending commits per-event; the only cross-candidate
   `max()` is WITHIN one family's 2 tokens (event_reactor_adapter.py:2853). An armed canary fires
   FIRST-QUALIFYING-IN-ARRIVAL-ORDER, never best-in-book. Root B: `robust_trade_score` is a binary
   admission gate mis-used as a ranker (Spearman(ts, expected_PnL)=0.66; **857 candidates outrank
   Shanghai-28 while having LOWER expected PnL**). Shanghai (q=0.995, EV +10.4¢, Kelly $44, exp PnL
   $4.61) ranks 26/56. Fix: book-wide selector ranking admitted set by `expected_PnL = kelly_size ×
   (q_posterior − cost)`, fire top-K under live cap, + §4.2 admit-on-EV. THIS is why the near-sure-win
   (operator's primary concern) never fires.
3. **SETTLEMENT correctness** — design IN FLIGHT (agent a39953) → `SETTLEMENT_CORRECTNESS_AUDIT_2026-06-01.md`.
   Axes: unit °C vs °F (SF/Seattle settle °F, 2°F bins), bin semantics exact("be 28°C")/threshold
   ("26°C or higher")/range("between 64-65°F"), rounding floor(x+0.5), settlement station, metric high/low.
   Any mismatch between OUR q's settlement assumption and the market's actual resolution = wrong bin/side.
4. **JUNE/JJA calibration + TIGGE re-connect** — design IN FLIGHT (agent aa018b31, PREMISE CORRECTED).
   Establish TIGGE=OpenData from spec; reconstruct #58 purge damage (which rows/months); blast radius of
   the false "TIGGE contamination" premise (every data_version/source_transport gate/allowlist excluding
   TIGGE-tagged valid data); fix = restore/re-tag + refit JJA Platt. Concern: live June q may be
   weakly-calibrated (identity-Platt) BECAUSE its data was wrongly purged → June edges untrustworthy = pre-arm blocker.
5. **CRITIC** (agent aab33d99) reviewing designs 1+2+§4.2 BEFORE implementation →
   `DESIGN_CRITIC_2026-06-01.md`. Key adversarial angles: phase-gate over-exclusion / NULL-fail-open /
   bypass paths; whether `kelly_size×edge` double-counts edge (over-concentration, worsened by removed
   flood/notional caps); whether §4.2 admit-on-EV re-admits false-confidence given the June calibration gap.
+ Queued: **#99** flood-guard regression (my cap change 1→1000 removed the daily flood safety — needs a
  real rate-limit decoupled from the notional cap); **#100** F-unit verify (SF/Seattle °F bins).

## 4. CI ruling `CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md`
§4.1 = Wall A (DONE, committed). §4.2 = admit on point-EV>0 ∧ FDR p<α, Kelly carries variance, keep
q_5pct as a SIZING input not a binary gate (NOT yet implemented; coupled to the selection fix; under critic).

## 5. AGENTS IN FLIGHT
- a39953afbca980637 — settlement correctness audit (opus, read-only).
- aa018b31c6d54ea8c — JJA/TIGGE calibration (opus, premise corrected via SendMessage).
- aab33d9983a1f0c4a — design critic over day0 + selection + §4.2 (opus, read-only).
(Completed: a2a000f7 day0 root; a7e610ce selection root; ae5fe38 phantom partial [superseded];
afc6581b Wall D [resolved].)

## 6. ARM GATING + PLAN
- ARM = operator's IRREVERSIBLE gate. Mechanically multi-surface: set `live_execution_mode=edli_live_canary`
  + `reactor_mode=live` + flags (real_order_submit_enabled, live_canary_enabled, market_channel_ingestor_enabled,
  edli_user_channel_reconcile_enabled, taker_fok_fak_live_enabled) + clear the INDEFINITE `entries_paused`
  override (control_plane:global, NULL expiry) + arm_live_mode.sh (plist env, cutover_guard).
  cutover_guard already LIVE_ENABLED; plist ZEUS_HARVESTER_LIVE_ENABLED=1 + WS env already set.
  Stage-readiness files (status_summary.json, source_health.json) must be <900s fresh at boot.
- Note: `live_canary_enabled=True` (needed for canary mode) RE-ENABLES the force-taker provider
  (forces FOK while fills<min). Operator wanted no special treatment — decouple OR accept for genesis.
- ARM BLOCKED until designs 1-4 land (critic-approved) → restart → re-verify pool is (in-window via
  phase-gate, correct-q via settlement, June-calibrated via TIGGE re-connect, PnL-ranked via selector) →
  surface the corrected first-order target (a PROPERLY-SELECTED real near-sure-win, e.g. Shanghai-28 June2,
  NOT a day0/phantom) → operator says "arm it".
- Integration: land the COMPLETE critic-approved set in ONE pass → single restart → re-verify → arm. No partials.

## 7. STANDING CONSTRAINTS
- SOLE committer/restarter. `git rev-parse HEAD` before any restart. Daemon cwd = this checkout (restart loads HEAD).
- Bash stdout is MANGLED by a codegraph hook → ALWAYS python→/tmp file→Read; never trust inline grep/echo.
- config/settings.json is operational/uncommitted (canary flags). Never display secrets. NO AI trailers in commits.
- Engine tests pollute `state/loaded_sha.json` → "abc123"; restore to HEAD or restart fixes it.
- Live-arm = operator's irreversible gate; NEVER flip `real_order_submit` without explicit "arm it".
- DON'T partial-fix; integrate the complete critic-approved set.
- 0 fills: never "working correctly / no edge / waiting". Report the correctness blocker + the fix in flight.
