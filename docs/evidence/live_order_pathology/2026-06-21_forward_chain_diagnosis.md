# Live money-path forward-chain diagnosis — 2026-06-21

Created: 2026-06-21
Last audited: 2026-06-21
Authority basis: genuine-alpha mission (`/goal`) — settlement-evidenced alpha
across the full order lifecycle, real forward market-chain evidence ONLY (not
test, not replay). Verified read-only against live state DBs
(`zeus-world.db`, `zeus_trades.db`) on branch `main` @ 2eeefa14, daemon code
`zeus-live-main` @ 2eeefa14 (deployed ~2026-06-21 05:35 local).

## Real-chain ground truth (settlement-evidenced)

`position_current` realized P&L, ~3 weeks (Jun 1–21):
- **37 trades held to SETTLEMENT → net −$27.63**, 18 win / 13 loss / rest flat.
- 0 profit-taking exits, 0 reversal exits among settled — every real exit was
  hold-to-settlement. 252 voided / 15 quarantined / 19 admin_closed = $0 (never
  real trades).
- `edli_live_profit_audit`: 1512 attempts → **only 60 fills**; settlement_outcome
  + pnl_usd columns BLANK for all rows (Phase-3 writeback not landing in audit;
  real P&L lives in `position_current.realized_pnl_usd`).

Verdict: the system is **net-losing on settled trades** and trades far below the
operator's "1000+ markets" expectation (~3 fills/day).

## The losers are confident-WRONG entries on hot cities (cold-center)

The 37 settled, sorted by P&L, show the pattern (all dates pre-fix, settled
Jun 20 10:37–14:37, BEFORE exit-revival 016337dc @ Jun20 20:44 and Option C
5b5745b2 @ Jun21 04:24):

| city | bin | dir | entry p_post | last_monitor_prob | settle | pnl |
|------|-----|-----|-----|-----|-----|-----|
| Karachi | 36°C Jun8 | buy_no | 0.961 | (n/a) | 0.0 | −17.01 |
| Wuhan | 28°C Jun19 | buy_no | 0.829 | 0.824 | 0.0 | −8.04 |
| Shanghai | 31°C Jun19 | buy_no | 0.845 | **0.007** | 0.0 | −4.44 |
| Seoul | 21°C low Jun19 | buy_no | 0.807 | **0.333** | 0.0 | −6.21 |
| Singapore | 32°C Jun19 | buy_no | 0.82 | **0.0** | 0.0 | −3.69 |

`settle=0.0` on a buy_no = the bin DID occur = NO lost. System was **confidently
NO** (p_post 0.77–0.96) on HOT-Asian-city high bins that were actually reached →
**cold-center bias**: served forecast center underpredicts hot-city highs → buys
NO on bins that get hit. One Karachi −17.01 ≈ the whole net loss (also the LARGEST
size — sizing put most capital on the worst-calibrated, worst risk/reward NO:
buy NO at 0.81 risks 0.81 to make 0.19).

Winners (settle=1.0): correct NO calls, bounded upside (~$0.27–4.94). Asymmetry:
one confident-wrong NO wipes ~7 winners.

## "Observe but not act" — confirmed, and its post-fix successor

`last_monitor_prob` collapsed on the losers (Shanghai 0.845→0.007, Seoul→0.333,
Singapore→0.0): the monitor **saw** the reversal, the position **rode to full
settlement loss** anyway. This is the operator's named regression.

Forward window (post-deploy, last UTC-day) — the exit lane was REVIVED and now
fires, but fails differently:
- `MONITOR_REFRESHED` 4382 (observe works), `EXIT_INTENT` 22, `day0_window→pending_exit` 43.
- **`EXIT_ORDER_REJECTED` 21 of 22** — every salvage SELL rejected:
  - exit_reason: `DAY0_HARD_FACT_BIN_DEAD (running high extreme 27.0 beyond bin
    [24.0,24.0] — YES structurally dead; source=durable_observation_instants)`
    — excellent physics, but it only fires on **settlement-day hard-fact death**,
    the latest/lowest-salvage moment.
  - error: `executable_snapshot_gate: SELL command requires bid-side executable
    snapshot evidence` → `backoff_exhausted` → rides to worthless settlement.
- The two EXIT_INTENT positions were cheap longshot **buy_YES** (Seoul 24° entry
  p=0.138, KL 29° p=0.157) that died — a second, smaller failure mode (tail
  over-prediction + no min-price floor lets dying longshots in).

Markets are NOT universally dead: 6.8M live snapshots, two-sided books with bids
(0.95/0.98, 0.62/0.63) at 15:18 UTC. The gate blocks because the **specific
structurally-dead token** has no bid by settlement day (a YES certain to settle 0
has no buyers). The salvageable window is the **early** monitor reversal, when the
token still has a live bid — exactly the window the current lane skips.

## Systematic causal chain (one disease, many symptoms)

Miscalibrated entry q (cold center + tail over-prediction)
  → confident-wrong entries (big buy_NO on hot cities; dying longshot buy_YES)
  → monitor detects reversal LATE-acted (only DAY0_HARD_FACT), soft signal ignored
  → snapshot gate blocks the late SELL (token now bid-less)
  → full settlement loss; and the SAME bad q feeds the FDR throttle
    (FDR_REJECTED 2792/3731 = 75% of dry-run candidates) so good entries are
    also gated on garbage q.

q is upstream of EVERYTHING (entry direction, sizing, FDR throttle, monitor
reversal). Fix order: (1) entry-q calibration (cold center forward-verify +
tail/min-price/sizing), (2) early soft-reversal exit trigger so "observe" becomes
"act while a bid exists", (3) executable_snapshot_gate (false-block vs dead-token),
then (4) re-examine FDR throttle once q is trustworthy.

## Status of fixes already on main (deployed ~4.5h, NOT yet forward-verified)
- Option C cold-center (5b5745b2): warms served `_mu_diagonal` center — VERIFY forward.
- Phase 2 exit-revival (016337dc) + blockers (f81d39a5): exit lane fires but only
  on hard-fact + blocked by snapshot gate — INCOMPLETE for early reversal.
- q-provenance grader/writer (d626b0a3, 6bb3716c): attribution only, not P&L.

All losers in this doc PREDATE these fixes. Forward verification of post-deploy
behavior is the only valid proof — in progress.

## DEFINITIVE UPSTREAM ROOT (forecast materialization race + idempotency dead-end)

Traced end-to-end in code + live data. ONE upstream defect produces BOTH the
cold/wrong entries AND the held-belief freeze:

**The fusion materialization fires before its per-provider inputs are captured,
BLOCKS, and is then idempotency-blocked from retrying at that cycle.**

Chain:
1. The strategy-of-record posterior is a multi-provider Bayes precision fusion.
   `replacement_forecast_materializer.py:1141-1150`: it reads PERSISTED current
   single_runs via `read_current_instrument_values(city,metric,target_date,
   source_cycle_time)` and, if `persisted_current` is empty, logs
   "persisted current single_runs capture MISSING … -> single-anchor fallback
   (no network fetch in q path)" and returns None → `REPLACEMENT_LIVE_POSTERIOR_
   REQUIREMENTS_NOT_MET` / status BLOCKED. **No on-demand fetch** — inputs must be
   pre-persisted.
2. The reseed/poll picks the "freshest materializable cycle" from raw manifests
   (`replacement_cycle_advance_trigger.py`), builds a seed, and **records the
   idempotency marker `cycle_advance_enqueues UNIQUE(scope,target_cycle)` at
   seed-build time (`_record_enqueue`, line 662) — BEFORE materialization runs.**
   The materialization queue then runs the request; if the cycle's single_runs
   aren't serving-promoted yet, it BLOCKS — but the marker persists, and BOTH
   lanes share it, so neither re-enqueues that cycle. Dead-end until a NEWER cycle
   appears.
3. Live proof: Panama City 2026-06-22 high — single_runs for cycle 06:00 exist but
   were captured 14:10; the materialization request ran 12:11 (2h earlier) →
   `persisted_current` empty → BLOCKED (failed/ receipt: returncode 1,
   REQUIREMENTS_NOT_MET). At 15:30 the posterior is STILL frozen at 02:17/cycle
   18:00 — did NOT self-heal after single_runs landed, while the POLL scan
   materialized Tel Aviv/Tokyo at 14:31/cycle 06:00. Beijing 06-21 high has NO
   06-21 posterior at all (frozen at 06-20).
4. Scope today: **2558 failed materializations, 1905 (74%) "single_runs capture
   MISSING", 2087 (82%) REQUIREMENTS_NOT_MET.** Distinct frozen/cold families =
   the hot Asian cities that produced the losses: Karachi, Hong Kong, Guangzhou,
   Chengdu, Beijing, Busan, Kuala Lumpur, Chongqing (~96×/day each). Successful
   fusion families have 5 provider single_runs; the blocked ones have 3.

## BREADTH CORRECTION (intellectual honesty — sized from served-belief data)
The "1905/74% blocked" is materialization ATTEMPTS, NOT served entries. Of the 203
posteriors actually SERVED today, **172 (85%) are FULL_CURRENT multi-provider
fusion, 31 (15%) PARTIAL_CURRENT** (provenance `capture_status`). Blocked attempts
serve no posterior at all. So:
- Entry belief is **largely healthy** (85% full fusion + Option C); the historical
  Karachi −17 / confident-wrong-NO losses were the PRE-FIX single-anchor regime,
  not current served entries. Do NOT over-claim "entries are cold."
- Many blocked attempts are **settlement-day (lead=0)** where day0 OBSERVATION
  correctly supersedes the forecast — expected, not a bug.
- The genuine LIVE leak is narrower: **held / future-lead families that lose the
  single_runs race and hit the idempotency dead-end** (Panama City 06-22 frozen
  since 02:17; Ankara/Chengdu 06-23). For a HELD position that means frozen belief
  → no reversal exit. That is the operator's "observe but not act," live now.

Why the held-belief freeze (not entry) is the priority live leak:
- **Cold/wrong ENTRIES**: a BLOCKED fusion serves the **single-anchor (one coarse
  model) fallback = the cold center** → confident-wrong buy_NO on hot cities
  (Karachi 0.96 NO → −17.01). Option C (representativeness) only re-weights WITHIN
  a successful fusion; it cannot help a family that BLOCKS to single-anchor.
- **Frozen held BELIEF**: the held position's posterior can't advance → stale →
  `BELIEF_AUTHORITY_FAULT` fail-closed HOLD → reversal exit starved → ride to loss.
- **FDR throttle** rejects on whatever (often single-anchor/stale) q exists.

## FIX DIRECTION (to plan + cross-check, then forward-verify)
1. **Input-readiness gate on materialization**: target the freshest cycle WHOSE
   single_runs are already serving-promoted (≥ provider floor), not the freshest
   cycle with only raw manifests. Prevents the BLOCKED-then-marked dead-end.
2. **No idempotency on BLOCKED**: a BLOCKED/REQUIREMENTS_NOT_MET attempt must not
   leave a marker that prevents retry at the same cycle once inputs land (mirror
   the readiness `expires_at` "a stale row must not block its own repair" rule).
3. **Held-family materialization coverage**: held-position families must be
   re-attempted until they hold a FRESH fused posterior (don't depend on the
   fragile single-shot reseed; the poll scan excludes them).

This is the single highest-leverage, systematic (not one-order-type) fix: it warms
the served center for ~74% of families AND unfreezes held belief so reversal exits
can fire. Verify forward by: blocked-materialization count → 0, hot-city posteriors
multi-provider (≥4–5) and fresh, held posteriors advancing each cycle, and reversal
exits firing while a bid still exists.

## CHOSEN FIX (consult-validated, two layers) — 2026-06-21

ChatGPT-Pro consult (REQ-20260621-102924) independently converged and sharpened
the design. Key correction to my first instinct: the async "refresh eventually"
reseed is INSUFFICIENT as the sole correctness mechanism — the safety boundary is
the MONITOR EXIT DECISION, not the forecast scan. Confirmed by the in-code comment
`monitor_refresh.py:389` ("the Karachi position was monitored its whole life with
stale belief and nothing escalated") — the freeze is CHRONIC, the poll lane does
not self-heal held belief. Invariant must be decision-time authority validation,
not "row age looks young."

Two complementary layers (do NOT loosen the BELIEF_AUTHORITY_FAULT guard — keep the
no-cold-substitution defense):

LAYER 1 — Marker re-heal (idempotency dead-end). `_already_enqueued`
(replacement_cycle_advance_trigger.py:267) must treat a marker whose seed built but
whose materialization BLOCKED (no tradeable posterior produced for that
target_cycle) as re-enqueueable — mirror the blessed CYCLE_LEG_ARTIFACT_MISSING /
readiness `expires_at` "a marker must not block its own repair" pattern. Necessary
so ANY retry (poll OR monitor-triggered reseed) can re-attempt once single_runs
land. Surgical, low-risk.

LAYER 2 — Monitor read-through on stale held belief (consult Stage 1+2; the robust
fix). When a non-day0 held position's replacement belief is stale/missing, BEFORE
fail-closing, attempt a SYNCHRONOUS single-family read-through recompute via the
canonical fusion authority using whatever single_runs are currently persisted
(honestly wider CI if fewer providers — never the cold legacy ENS center). If it
yields a fresh posterior+CI → use it (fresh) → reversal exit can arm. If inputs are
genuinely insufficient → fail-closed HOLD + durable belief_debt event (family,
position, missing providers, first_failed_at, attempts) + reseed — NEVER a silent
permanent freeze. INV-37: monitor reads FORECASTS via ATTACH read-only on the
lifecycle connection, computes in-memory, writes only TRADES/order state; the
FORECASTS cache write stays with the forecast daemon. Exit organ unchanged: accepts
only FRESH belief; CI-separation still decides (no false exit); last_monitor_prob is
an urgency INTERRUPT to force recompute, never exit evidence by itself.

ORDER: Layer 1 first (small, isolated, TDD against the existing
test_cycle_monotone_materialization.py), then Layer 2. Forward-verify after each:
held posteriors advance, BELIEF_AUTHORITY_FAULT clears for fresh families, reversal
exits fill against a real bid (not backoff_exhausted). Defer consult Stages 3-5
(warm-lane, audit fields, SLA tuning) unless forward evidence shows Layer 1+2
insufficient — no over-engineering.

