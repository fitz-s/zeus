# PLAN — Continuous Re-Decision Architecture (maximum-alpha) — 2026-05-31

# Created: 2026-05-31
# Authority basis: operator directive "根据已有的天气做决策绝对不能是一次性的" (decisions on
#   already-held weather must not be one-shot) + "deep-reason the maximum-alpha method" +
#   GOAL #36 (live earns alpha, 3 verified fills). Tier-0 alpha core. SHADOW stays on until #24.
# Status: PLAN — pending critic review BEFORE any live-core edit. Relationship-tests-first.

## 1. The defect (evidence-grounded)

The #363 EDLI redesign (commit `00b73fbbce`, 2026-05-24) made the decision model **one-shot per
forecast snapshot**:
- Only `FORECAST_SNAPSHOT_READY` (FSR) drives a decision. Each FSR processed once →
  `mark_processed` terminal (`reactor.py` process_pending).
- Every price-move event (`BEST_BID_ASK_CHANGED`, `BOOK_SNAPSHOT`, `NEW_MARKET_DISCOVERED`) is
  hard-rejected `MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE` at `reactor.py:222-224`.
- `DAY0_EXTREME_UPDATED` (obs trigger) IS wired (`reactor.py:250`).
- grep confirms: **no re-decision path exists** anywhere in `src/events/` or the adapter.

Live evidence (2026-05-31 ~04:14Z): `opportunity_events`=417,701 of which **407,044 (97%) are
BEST_BID_ASK_CHANGED**, all rejected `NO_DIRECT_STALE_TRADE` (1,379 regret rows + climbing ~1000/min).
`edli_no_submit_receipts`=0, `decision_events`=0. Freshest FSR 00:43Z; none since. The system spends
compute + DB writes to **throw away its primary alpha signal**.

## 2. Root design failure (the inversion)

We make the **belief**; the market makes the **price**; edge lives at the joint
`edge = P_posterior[bin] − price − cost`. Belief updates ~2×/day (forecast cycles 00Z/12Z). Price
updates ~18×/sec. **The decision cadence must be slaved to the fastest-moving edge input (price),
with belief cached as the slow input.** The redesign inverted this: decisions fire on the slow input
(forecast) and discard the fast input (price). Between forecast cycles — ≈95% of wall-clock — the
system is blind to every price dislocation, which is exactly where the between-cycle alpha is.

Alpha decomposes by which input just moved:
| Source | Trigger | Status | Magnitude |
|---|---|---|---|
| Forecast-edge (belief sharpens) | FSR | wired | medium, persistent |
| **Price-dislocation (transient mispricing)** | market-channel | **rejected** | **high, time-critical, ~95% of clock** |
| Convergence (obs determine outcome) | day0 | wired | high, near-riskless, small windows |

## 3. The deeper reframe — two lanes, not one flood

The 407k price events were mis-modeled as **decision events** (opportunity_events) when they are
**price-capture** signals. The correct architecture separates two lanes:

- **Price-capture lane (high-freq, cheap):** market-channel events keep `executable_market_snapshots`
  fresh per market. They MUST NOT each trigger a full cert decision (18/sec is intractable) and MUST
  NOT write a regret row each (the flood). They update price freshness, nothing more.
- **Decision lane (periodic, over live pairs):** the reactor cycle (default 60s, tightenable)
  re-evaluates **every live `(family, market)` pair** — a family with a COMPLETE cached belief and an
  open market — against `cached belief × latest captured price`. This is the legacy-cron continuous
  scan, restored, but with the EDLI belief + FDR/Kelly/cert pipeline. One-shot becomes
  unconstructable: every cycle re-decides every live pair.

This collapses the per-tick problem: we do NOT process 18/sec; we re-scan all live pairs each cycle
against the freshest captured price.

## 4. The K=5 structural decisions

1. **Unify the decision trigger.** One `decide(family, market, belief, price, obs, clock)` evaluated
   per live pair per cycle. No per-event-type decision branches. FSR/day0 update the belief cache;
   market-channel updates price freshness; the cycle decides.
2. **Cache the belief.** Persist/maintain the latest COMPLETE `P_posterior` vector per
   `(family, snapshot_id)` so a cycle re-eval is O(1) per pair (no 51-member MC per pair per cycle).
   Updated on FSR/day0; read by the decision scan. (Investigate reusing an existing belief-bearing
   artifact — `probability_trace_fact` / decision_chain — before adding a table; a new minimal cache
   table is acceptable but MUST be canonical-named, no `_v2`, registered in ownership yaml +
   `_FORECAST_TABLES`, fingerprint re-pinned.)
3. **Two-tier: cheap screen → expensive cert.** Per pair per cycle: cheap screen
   `P_posterior[bin] − best_price − cost > min_edge`? No → drop silently (NO regret write — kills the
   flood). Yes → full FDR/Kelly/cert/receipt persist. Market-channel events drop cheaply at intake
   (price-capture only), never reaching cert and never writing regret.
4. **Act-once-per-edge idempotency.** Key by `(family, market, side)` with last-acted edge/price;
   re-fire a receipt only when edge improves materially past a threshold or flips. Prevents
   re-trading the same wiggle each cycle. (Reuse `no_submit_receipts` receipt_id idempotency for
   identical projections; add an edge-delta gate for the "improved" case.)
5. **Heartbeat floor.** The periodic scan IS the floor — edge decays with time-to-settlement even
   with no event. Event triggers (FSR/day0/price-capture) are the low-latency layer feeding the
   caches the scan reads. If an event is missed, the next cycle still re-decides.

SD-1+2+3 unblock the **first candidate NOW** from existing committed belief × current captured price
(no waiting for the 08:05Z FSR) and capture the dominant between-cycle alpha. SD-4+5 harden sustained
operation.

## 5. Implementation phases (TDD relationship-first; SHADOW throughout)

**P0 — Relationship tests (RED first).** Pin the invariants before any live-core edit:
- R1: cached belief + open market + price moved so `edge > min_edge` → decision scan fires a receipt
  with q/cost/score (persisted), even with NO new FSR.
- R2: same pair, price sub-edge → silent drop, NO regret row, NO receipt.
- R3: no COMPLETE belief for family → reject (not silent — provenance-blocked).
- R4: already-acted edge unchanged next cycle → NO duplicate receipt (idempotent).
- R5: market-channel event at intake → price-capture only; never a cert decision; never a regret row.
- R6: two cycles, price improves past delta on cycle 2 → second receipt fires (continuous, not
  one-shot). This is THE one-shot-killer relationship test.
Each must fail RED against current code first (proving it tests the defect).

**P1 — Belief cache (SD-2).** Author/locate the per-`(family,snapshot)` `P_posterior` cache;
populate on FSR/day0 processing. Verify O(1) read. No versioned table.

**P2 — Decision scan (SD-1+3+5).** Reactor cycle iterates live `(family, market)` pairs → cheap
screen → full cert on pass. Drives receipts from cache×price. The FSR/day0 handlers shrink to
"update belief cache"; the periodic scan owns the decision.

**P3 — Price-capture lane (SD-3 intake).** Market-channel events: consume as price-freshness updates
to `executable_market_snapshots`; drop at intake without regret. Stops the flood; keeps prices fresh.

**P4 — Idempotency (SD-4).** Edge-delta gate on `(family,market,side)` last-acted edge.

**P5 — Critic + #24 bias + commit.** Opus critic on the full implementation (Tier-0 alpha). Then #24
bias on the FIRST REAL receipts' p_raw vs online (≤1) — the real comparison, not hypothetical cities.
Commit as coherent units. Unshadow remains operator-gated (#12).

## 6. Acceptance criteria (artifacts, not "should work")

- A1: with the daemon in SHADOW on existing committed beliefs, the decision scan persists ≥1
  `edli_no_submit_receipts` row with legitimate positive edge (q/cost/trade_score) — WITHOUT waiting
  for a fresh FSR. (Proves continuous re-decision from cached belief × current price.)
- A2: across ≥2 consecutive cycles with a price improvement, a SECOND receipt fires for the same pair
  (continuous, one-shot killed). Live evidence, not test-only.
- A3: `NO_DIRECT_STALE_TRADE` regret rows stop growing (price events drop cheaply at intake).
- A4: full-49 audit: each live `(family, market)` either persists a receipt or carries a principled,
  provenance-grounded reject — no silent drop, no crash.
- A5: #24 bias on A1/A2 receipts ≤1 (the unshadow gate).

## 7. Risks + rollback

- Tier-0 alpha core: a wrong q/edge = silent mis-trade. Mitigation: relationship-tests-first + opus
  critic + SHADOW until #24. No real_order_submit.
- Belief-cache schema add: fingerprint drift. Mitigation: canonical name, ownership yaml +
  `_FORECAST_TABLES` registration, `--write-pin` after DDL, boot-guard verify.
- Decision-scan compute: re-eval all live pairs per cycle. Bounded by cheap screen (O(1)/pair) +
  pair count (49 cities × open bins ≈ low thousands). If a cycle exceeds budget, cap with a logged
  drop (no silent truncation).
- Rollback: the scan + price-capture changes are additive to the reactor; revert = restore the
  FSR-only `_process_one` + re-disable market_channel decision lane. Backup the pre-change
  `reactor.py` + config.

## 8. Out of scope (tracked separately)

- Platt promotion to production (#27): verdict done (HK/London/Miami/Paris PROMOTE; NYC/Seoul/Shanghai/
  Tokyo IDENTITY on full corpus). Promotion = a deliberate live calibration write, operator-gated;
  NOT part of this decision-architecture change. Identity remains the live-safe default for all 49.
- #10 D1-LOW real re-extract: incomplete (only the ecmwf_opendata_mn2t3 gate-clear exists). LOW is
  `apply_to=high` training-corpus correction, not a live trade path; separate.
- #45 bankroll cycle-warm reliability (self-heal violated no-live-fetch contract) — fold into P2 when
  the scan warms bankroll once per cycle.

---

# REVISION v2 — 2026-05-31 (post-critic + expanded GOAL: exit, evidence-gating, chain-alignment)

## v2.A — Critic verdict resolution (opus, PROCEED_WITH_FIXES)
The periodic-scan MODEL was sound but the MECHANISM was wrong on 4 code-verified points + 1 SEV-1
capital hole. ADOPT the critic's Skeptic alternative — it dissolves all of them:

**MECHANISM CHANGE (load-bearing): the re-decision does NOT build a parallel scan lane that reads
`executable_market_snapshots` directly. Instead, each cycle ENQUEUES a synthetic pending re-decision
opportunity_event per live `(family, market)` pair, routed through the EXISTING pending-event path.**
This makes `_refresh_pending_family_snapshots` (main.py:2039/3180) fire just-in-time for each pair
(it is scoped to `processing_status='pending'`), so the cert pipeline runs against a FRESH price with
zero new freshness code — closing SEV-1 #2 (stale-price phantom edge) for free. Resolves:
- SEV-1 #1 (cheap-screen fiction): the screen is NOT `build_event_bound_no_submit_receipt` (which
  runs bootstrap-MC + Platt load — P_posterior is its OUTPUT). The belief cache (SD-2) is a HARD
  PREREQUISITE; the screen reads `cached_posterior[bin] − fresh_price − cost` to decide WHETHER to
  enqueue a re-decision event. P1 (cache) BLOCKS P2 (scan).
- SEV-1 #2 (stale price in SHADOW): solved by routing through pending → just-in-time refresh. PLUS
  add a `quote_age_ms <= max` assertion INSIDE the NoSubmit cert (the shadow receipt must reject a
  stale price exactly as submission would — SHADOW must not be able to persist a phantom-edge receipt).
- SEV-1 #3 (idempotency false): receipt_id is keyed on event_id (changes per cycle) → NO cross-cycle
  dedup today. Author a NEW ledger keyed `(family_id, condition_id, side)` with last-acted edge/price.
  Hard P-phase, not "harden later". (Only over-trade backstop post-unshadow = `max_orders_per_day=1`.)
- SEV-2 (price-capture data-flow inverted): market-channel events INVALIDATE snapshots + feed a
  volatile quote_cache; the SCANNER writes freshness. Correct the plan's §3 prose; the "stop the
  regret flood" goal stays (early-return at reactor.py:223-224 WITHOUT `_write_regret`).
- SEV-2 (double-decision): FSR/day0 become PURE belief-cache updaters; ONLY the scan-enqueued
  re-decision events decide. NOT additive — fix the §7 rollback claim accordingly.
- SEV-2 (belief provenance): cache key = `(family_id, forecast_snapshot_id, calibrator_model_hash,
  bin_labels_hash)`; invalidate on calibrator/forecast advance (Fitz #4 antibody).

## v2.B — EXIT is part of alpha (expanded GOAL — symmetric, evidence-gated)
Entry was the whole v1 plan. The GOAL makes EXIT co-equal: "earn profit may become edge reversal and
lose everything, so exit must also be a part of this; BOTH entry and exit must have enough evidence;
a short price change is NOT edge reversal; rough entry OR exit = anti-alpha."

**SD-6 — Exit re-decision, evidence-gated, symmetric to entry.** A held position is itself a live
`(family, market, side)` pair re-evaluated each cycle. Exit fires when the EVIDENCE (not the price
tick) says the edge has reversed: the current belief × current price implies the position's edge has
gone sufficiently negative AND that signal is evidence-backed (sustained / CI-separated), not a
transient wiggle. Exit uses the SAME FDR/bootstrap-CI machinery as entry — an exit is an entry into
the opposite side, gated by the same evidence bar.

**SD-7 — Edge-reversal vs price-noise discriminator (the anti-anti-alpha gate).** A short price move
must NOT trigger entry or exit. The trigger requires: (a) the belief-implied edge crosses the
threshold by a margin, AND (b) the move is evidence-supported — e.g. bootstrap-CI of the new edge
excludes the prior edge, OR the day0/obs hard-fact changed, OR a forecast cycle moved the belief.
A bare `best_bid_ask_changed` of N cents with unchanged belief is NOT evidence → no action. This is
the operator's "rough entry/exit = anti-alpha" encoded as a gate. day0-math failure is a FIRST-CLASS
reversal source: if the day0 absorbing-mask / obs math is degraded, treat as evidence-unavailable →
do NOT exit on price alone (and do NOT hold blindly) — flag for the heartbeat 守护 check.

## v2.C — Local-DB ↔ chain alignment (precondition for trustworthy exit/PnL; alpha− if broken)
Exit verification + PnL require local position/fill/bankroll state to match on-chain truth (memory:
on-chain wallet is the only bankroll truth; live chain state needs fresh verification). KNOWN defect:
`position_current` missing `chain_avg_price` column → Traceback in
`src/execution/exchange_reconcile.py::_apply_exit_fill_projection_and_execution_fact` (exit/PnL path).
**SD-8 — Chain-alignment gate:** before any exit decision acts, local position state must reconcile
against chain (fill qty, avg price, bankroll); a misalignment is a fail-closed blocker (alpha−), not
a silent proceed. Fix the `chain_avg_price` schema drift as part of the exit path. This is "merely an
example" per the GOAL — treat DB↔chain divergence as a general blocker class, surfaced by the 120-min
守护 heartbeat.

## v2.D — Adjacent alpha unlocks (tracked, not this packet's core)
- LOW-trading unlock (alpha+): the #10 window fix is DONE, but LOW is currently `apply_to=high`
  (not traded). Unlocking LOW as a tradeable product is a DISTINCT alpha source — separate task,
  scope after entry+exit re-decision lands.
- Platt-on-solid-data (alpha+): Platt-49 competition running (41 cities); promote PROMOTE-verdict
  cities into live calibration after operator review. Better calibration → sharper belief → more edge.

## v2.E — Revised relationship tests (RED-first; the design contract)
- R1 entry: cached belief + open market + fresh price, edge>min → re-decision event enqueued →
  receipt persists (q/cost/score), NO new FSR needed.
- R2 sub-edge: price move, edge<min → no enqueue, no receipt, NO regret row.
- R3 no-belief: no COMPLETE cached belief → reject (provenance-blocked, not silent).
- R4 idempotency: two cycles, edge unchanged, DISTINCT synthesized event_ids → NO second receipt
  (proves dedup keyed on (family,market,side,edge), not event_id).
- R5 price-capture: market-channel event → no cert decision, no regret row (intake early-return).
- R6 continuity (one-shot killer): cycle2 price improves past delta → second receipt fires.
- R7 stale-price (critic SEV-1): cached belief + STALE price (freshness_deadline past) → NO receipt
  in SHADOW (cert rejects on quote-age). The phantom-edge guard.
- R8 exit evidence (expanded GOAL): held position + belief-implied edge reversed WITH CI support →
  exit re-decision fires; held position + bare short price move, belief unchanged → NO exit.
- R9 chain-misalign: local position ≠ chain → exit blocked fail-closed (alpha− guard).

Implementation order: P1 belief cache (blocks all) → R1/R3/R7 → P2 enqueue-scan + screen → R2/R5/R6
→ P-dedup → R4 → P-exit SD6/7 → R8 → P-chain SD8 → R9 → opus critic on impl → #24 bias on REAL
receipts → commit. SHADOW throughout; unshadow operator-gated (#12).
