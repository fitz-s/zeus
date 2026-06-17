# Handoff — 2026-05-31 (EDLI cert fix → positive-edge receipts proven; full-49 shadow pending)

## LIVE STATE
- Branch `main @ 9ce856393e`. Daemon `com.zeus.live-trading` pid 22665, SHADOW (`edli_shadow_no_submit`, `real_order_submit_enabled=false`). Wallet ~$185 untouched.
- `forecast-live` pid 32071 up. `edli_v1.market_channel_ingestor_enabled=true` (quote producer on).
- **edli_no_submit_receipts = 0** (no PERSISTED receipt yet). decision_events = 0.

## BREAKTHROUGH THIS SESSION — cert chain PROVEN to produce positive-edge receipts
Commit `9ce856393e` fixed the two cert bugs that blocked every candidate. Read-only dry-run (`scripts/edli_dryrun_high_calibrator_receipt.py`) via the REAL adapter on live DBs → **proof_accepted=True for all 5 HIGH-calibrator cities**, genuine >1¢ edge:
- Wuhan 32°C buy_no q=0.9994 cost=0.70 **score=+0.0129**
- Tel Aviv 28°C buy_no q=0.9995 cost=0.525 **score=+0.0227**
- Wellington 18°C buy_no q=0.9984 cost=0.545 **score=+0.0208** (Taipei/Toronto similar)
The repeated "no edge / efficient market" reads were WRONG — the cert bugs were the wall. Model near-certain NO, market underpricing NO = real edge.

### The two cert bugs (commit 9ce856393e, 74 tests pass, 5 new RED→GREEN)
1. **snapshot_id==causal assertion** (compiler.py:357-373 `_validate_no_submit_parent_consistency` + verifier.py:730) contradicted the committed reader-elect fix (reader returns the EXECUTABLE-authority snapshot ≠ causal when the causal source_run is still ingesting). Fix: bind TWO chains — causal-provenance (`source_truth.{causal_snapshot_id,snapshot_id}==event.causal`) + executable-authority (`source_truth.derived_from_snapshot_id==forecast.snapshot_id==belief.forecast_snapshot_id`).
2. **horizon_profile 'full' != None** — `ensemble_snapshots` has NO horizon_profile column; the calibrator DERIVES it from cycle (00/12→'full' via `derive_phase2_keys_from_ens_result`). Adapter read the nonexistent column→None. Fix: adapter `_forecast_authority_payload_and_clock` (event_reactor_adapter.py:2083-2107) derives horizon_profile from source_issue_time/source_cycle_time; both compiler guard sites made strict (removed None-skip mask) to match the verifier.

## REMAINING TO FIRST PERSISTED RECEIPT (#49)
- The reactor marks cert-rejected FSR as `processed` (terminal, no retry). All 49 prior COMPLETE FSR are consumed (36 cert-rejected→processed, 13 quote-uncapturable→dead_letter). They will NOT reprocess on restart.
- Daemon pid 22665 NOW runs the cert fixes (restarted 9ce856393e). A PERSISTED receipt needs ONE fresh COMPLETE FSR flowing through it → next ECMWF forecast ingest cycle (forecast-live emits), or Low's re-ingest, or a sanctioned cascade-replay (re-emit via the real daemon path — NOT manual INSERT, NOT bulk DB mutation; the classifier blocks those).
- Receipt watcher running (bg task) — fires on first `edli_no_submit_receipts > 0` with q/cost/score.

## FULL-49 SHADOW (operator: receipts on 5 cities ≠ full universe)
- Runtime cities = 54. Tradeable universe = **49** (5 unlisted on Polymarket: Auckland, Jakarta, Jinan, Lagos, Zhengzhou).
- Coverage now: forecast COMPLETE **54/54**; market **49/54**; calibrator = identity-default for all HIGH (`platt_oos_resolver`: p_cal=p_raw fail-closed default, no per-city Platt required).
- The cert fix is GENERAL (not city-specific) → all 49 HIGH should flow. **MUST audit all 49 on the next FSR cycle** — per-city outcome (receipt OR principled reject), confirm NO silent drop/crash. Not yet run (needs fresh FSR). This is the real "#24 + full shadow" proof, not the 5-city sample.

## COMMITS THIS SESSION (all on main, durable)
- `9ce856393e` fix(edli-cert): accept reader-elected snapshot + derive horizon_profile
- `a131f07930` fix(edli-reactor): prioritize COMPLETE FSR + dead-letter PARTIAL at intake (fetch_pending tier-0 FSR / tier-2 market-channel; event_store.py)
- `9ef90548b1` fix(forecast-ingest): source_run.observed_members aggregate over contributing-extrema snapshots (#46 — fixed the COMPLETE gate; was members=0 since May 5)
- `2a7dcd1860` fix(edli-live): TOPOLOGY_CLOCK_MISSING (market_scanner created_at writer) + pytest suite unblock (Platt sqlite→canonical helper)
- `424e14ec92` fix(edli-live): reader-elected snapshot + quote producer enable + bankroll self-heal

## SIDE-TRACKS (in flight at handoff)
- **#27 Platt:** sampled run (N_groups=2000 cap) gave HK/London/Miami=PROMOTE, NYC/Paris/Seoul/Shanghai/Tokyo=IDENTITY. NOT trustworthy (sampled). **Full-corpus re-run in flight** (agent ad3fb36e → /tmp/platt_verdict_full.tsv). Promotion = operator call (needs a PROMOTE row; identity is the live default).
- **#10 LOW:** a prior agent did a SMALL gate-clear only — wrote 12,496 ecmwf_opendata LOW calibration_pairs for 5 cities (NYC/Paris/Seoul/Shanghai/Tokyo) into canonical `calibration_pairs`, HIGH fingerprint deabf8f64bde27b7 unchanged, dropped empty calibration_pairs_v2 (no version regression). This is NOT the real #10 (D1-LOW 3h-window re-extract). **Real re-extract verify+execute in flight** (agent a57fe4e — verifying whether existing LOW snapshots are old-6h or corrected-3h window).
- **#24 bias** (shadow p_raw vs online open-meteo, bin bias ≤1, unshadow gate): in flight (agent af9a233a, verify-first).

## STANDING RULES (operator, this session — load-bearing)
- **No trade = not working.** No "should work next cycle"/timing-race/no-edge/structurally-fixed counts. Only a real artifact: shadow receipt w/ q/cost/score, then live fill w/ P&L. See memory `feedback_no_trade_equals_not_working`.
- **Long missions need a Monitor + fixed-interval liveness heartbeat.** No agent + no py process = DEAD. Checkpoint long jobs (per-unit append). Report NUMBERS not "running". See `feedback_long_mission_liveness_heartbeat`.
- **NO version on main.** Never create calibration_pairs_v2 / any versioned table / schema_version bump. Canonical `calibration_pairs` only.
- **Subagents MUST get explicit `model` tier alias** (opus/sonnet/haiku) — session is Opus-1M; subagents can't inherit `[1m]` (caused mass credit-death 401/429 before the model-routing fix). 
- NO advisor / AskQuestion (advisor 400s + kills agents).
- Live daemon loads the WORKING TREE (uncommitted changes go live on restart). 4 files uncommitted at session start (oos_gate, event_store, main, db) — already loaded in the running daemon; commit/review them before relying on a clean tree.

## GOTCHAS
- Bash stdout MANGLES tokens (data_version/snapshot ids) → verify via Read on /tmp dumps.
- `created_at` stored 'T'-separated; `datetime('now')` uses space → `created_at >= datetime('now',...)` matches ALL of today (false). Use a 'T'-literal threshold.
- Daemon boot ~4-5 min (exchange_reconcile) before "Scheduler ready" — don't rapid-restart.
- Bulk DB mutation on live zeus-world.db blocked by auto-classifier (correctly) — needs operator-run or a structural code path, not a tunnel.

## NEXT ACTIONS (priority order)
1. **#49 first persisted receipt:** fresh COMPLETE FSR → cert-fixed daemon. Watch receipt watcher. On receipt: verify q/cost/score legitimate.
2. **Full-49 audit:** on that FSR cycle, dump per-city outcome (receipt vs reject-reason) for all 49 — prove no silent drop.
3. **#24 bias verdict** (af9a233a) → unshadow gate.
4. **Platt-full** (ad3fb36e) + **Low real re-extract** (a57fe4e) verdicts.
5. Then: #24 pass → operator unshadow (#12) → first real order (#25) → 3 verified fills (#36).

## MEMORY (durable, this session)
- `project_live_zero_candidate_producer_off_2026_05_30.md` (dual root + gate progression + retractions)
- `feedback_no_trade_equals_not_working.md`
- `feedback_long_mission_liveness_heartbeat.md`
