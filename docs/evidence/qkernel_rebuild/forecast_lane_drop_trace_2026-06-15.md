# Forecast-Harvest Drop Trace — 2026-06-15 ~18:30 CDT (23:35 UTC)

READ-ONLY causal trace. Daemon PID 69668, main tree `/Users/leofitz/zeus`,
branch `live/iteration-2026-06-13`. No edits applied.

## TL;DR

**The forecast harvest is NOT dropping out of the pipeline. It runs end-to-end
through DECISION.** The two probe families (London 06-17 high, Tokyo 06-16 high)
are emitted (FSR), claimed, captured (fresh EMS with `orderbook_top_ask` in-band),
priced by the spine, and admitted by `family_decision_engine._select` — orders
reach `DecisionProofAccepted` → `VenueSubmitAttempted`.

They die at a **single global pre-submit gate**: the portfolio governor's
**automatic kill switch latched on `unknown_side_effect_threshold`**. One venue
command (`command_id=01049c6a357d4f97`, the operator-cited **11:30 @0.76** order
on `highest-temperature-in-chengdu-on-june-17-2026`) returned an indeterminate
venue response at **16:28–16:30 UTC**, landing in state
`SUBMIT_UNKNOWN_SIDE_EFFECT` with `venue_order_id=NULL`. With
`unknown_side_effect_limit: 0` (config/risk_caps.yaml), one unresolved unknown
trips the switch, which forces `reduce_only` / `NO_TRADE` and **blocks every new
buy_no submit across all families** until the unknown is reconciled or cleared.

**Root-cause stage: STAGE 5+ → pre-submit risk-allocator gate (NOT the forecast/FSR
lane, NOT topology discovery).** The "spine went dark at 15:41" and "DAY0
starvation" observations are real but are *downstream symptoms / red herrings*,
not the block. The block is a latched safety kill switch.

---

## Critical correction to the brief's pointers (cost-saving for next session)

The K1 DB split routes these tables to DIFFERENT files than the brief stated:

| Table | LIVE DB (writer) | Note |
|---|---|---|
| `opportunity_events` | **state/zeus-world.db** | 6.96M rows, current (last 23:33 UTC) |
| `market_events` | **state/zeus-forecasts.db** | brief said zeus-world (0 rows there) |
| `executable_market_snapshots` | **state/zeus_trades.db** | brief said zeus-world (0 rows there) |
| `venue_commands` | **state/zeus_trades.db** | the kill-switch source of truth |
| `edli_live_order_events` | **state/zeus-world.db** | event-sourced order lifecycle |
| `decision_certificates` | **state/zeus-world.db** | live (last 22:54 UTC) |
| `decision_events` | (dead everywhere — 0 rows) | legacy; use edli_live_order_events |

`entity_key` for FSR = `City|target_date|metric|source_run_id`
(e.g. `London|2026-06-17|high|ecmwf_open_data:mx2t6_high:2026-06-15T12Z`) — NOT
`City|date|metric`. Match with `LIKE 'City|date|metric|%'`, not exact equality.

Reads used `?mode=ro` (WAL-aware). `?immutable=1` ignores the WAL and on this
live 45GB DB returns stale/empty pages — do not use it here.

Clock: local = CDT (UTC-5). 23:35 UTC = 18:35 CDT.

---

## Stage-by-stage trace (probe families: London 06-17 high, Tokyo 06-16 high)

### STAGE 1 — EMISSION (FSR): PRESENT (London current; Tokyo slightly stale, still emitted)
`opportunity_events` in zeus-world.db, FSR by target_date index:
- **London 06-17 high**: last FSR **23:33:20 UTC (~2 min ago)**, cycle `2026-06-15T12Z`. FRESH.
- **Tokyo 06-16 high**: last FSR **14:59:15 UTC (~8.5h ago)**, cycle `2026-06-15T00Z`
  (no 12Z cycle yet). Stale-ish but emitted.
- Other cities (Amsterdam high, Miami low, NYC low, HK low) emitted FSR at 23:33 UTC.

→ The FSR/forecast lane is **NOT dark at emission**. The "spine went dark at
15:41" is a logging artifact of the spine NOT BEING REACHED (see Stage 5), not of
FSR emission stopping. **Hypothesis "FSR-lane starvation by DAY0": FALSIFIED at
the emission stage** (FSR events for London high are 2 minutes old).

### STAGE 2 — CLAIM/SCOPE: PASSING (fair-lane interleave active; families captured)
- `_fair_lane_interleave` IS invoked in the live process loop:
  `src/events/reactor.py:673` (right after `fetch_pending` at L653). It round-robins
  the forecast-decision lane 1:1 against the DAY0 lane, so DAY0 cannot starve the
  budget (the #92 fix). This protection is live.
- Evidence the families are claimed and worked: their EMS rows are being refreshed
  (Stage 3) and post-latch buy_no DECISIONS are produced (Stage 5) — both require
  the events to be claimed.
- DAY0 noise is real (reactor cycles show `TRADE_SCORE_NON_POSITIVE`, `FDR_REJECTED`
  — the legacy day0 lane) but it is interleaved, not monopolizing. **Hypothesis
  "DAY0 saturates the claim rotation": WEAKENED** — forecast decisions still occur
  post-latch, so the lane is reached.

### STAGE 3 — CAPTURE (EMS): PRESENT and FRESH for BOTH families
`executable_market_snapshots` in zeus_trades.db (keyed by condition_id):
- **London 06-17 high** 25°C bin (`0x6849b64c…`): NO ask **0.67**, accepting_orders=1,
  closed=0, captured **23:24:29 UTC (~10 min ago)**. Matches operator API probe (best ask 0.650).
- **Tokyo 06-16 high** 26°C bin (`0xb292da2e…`): NO ask **0.58**; 27°C (`0x18694c19…`)
  NO ask **0.66**; accepting_orders=1, closed=0, captured **23:28:19 UTC (~7 min ago)**.
  Matches operator probe (best 0.590).

→ Capture is LIVE and in-band. **Hypothesis "incomplete topology / Gamma discovery
never completed (#122)": FALSIFIED for these families** — both have COMPLETE
11-bin topology in `market_events` (London range 23–31 captured 06-15 04:02 UTC;
Tokyo range 22–30 captured 06-14 04:02 UTC) AND fresh EMS with top_ask set. The
#122 warm-backoff is not implicated for these two families.

### STAGE 4 — REACH/PRICING (spine): REACHED (spine prices; decisions accepted)
- `zeus.spine_edge SPINE_NOTRADE_EDGE_DIAG` last fired 15:41:30 — but that logger
  only fires on a spine NO_TRADE *with no positive edge*. Its silence after 15:41
  means recent forecast families are NOT producing a no-edge no-trade — they are
  producing POSITIVE-edge decisions (which don't log that diag) that then get
  blocked at submit. Silence here is a symptom of success-then-block, not of the
  spine being unreached.
- Proof the spine reaches & prices post-15:41: **5 buy_no `DecisionProofAccepted`
  events between 17:39 and 22:52 UTC** (edli_live_order_events) on distinct
  token_ids/families. A DecisionProofAccepted requires the full spine →
  family_decision_engine chain to succeed.

### STAGE 5 — DECISION (`_select`): PASSING (buy_no admitted, proof built)
- Each of the 5 post-latch orders: `DecisionProofAccepted → SubmitPlanBuilt →
  PreSubmitRevalidated → LiveCapReserved → ExecutionCommandCreated →
  VenueSubmitAttempted`. The direction-law + (NO & edge_lcb>0) + coherence +
  edge_lcb>0/optimal_delta_u>0 filter (commit 3c4aeecc75) ADMITS them. Stage 5 is healthy.

### STAGE 5+ — PRE-SUBMIT RISK GATE: **BLOCK (ROOT CAUSE)**
- All 5 attempts end in `SubmitRejected` with
  `reason_code = "risk_allocator_pre_submit_blocked: unknown_side_effect_threshold"`.
- Seam: `src/execution/executor.py:3195` calls
  `_assert_risk_allocator_allows_submit(intent)`; it raises; L3200 wraps it into
  the SubmitRejected reason. This gate is **global and intent-agnostic** — it never
  inspects the family, so it blocks every buy_no equally.

---

## The latched kill switch — mechanism (file:line)

`src/risk_allocator/governor.py`:
- `kill_switch_reason()` L234–248: returns `"unknown_side_effect_threshold"` when
  `governor_state.unknown_side_effect_count > policy.unknown_side_effect_limit` (L242–243).
- `maker_or_taker()` L201–203: kill_switch_reason truthy → `"NO_TRADE"`.
- `reduce_only_mode_active()` L223–231: `unknown_side_effect_count > 0` → True.
- Count source `count_unknown_side_effects()` L575–595: counts `venue_commands`
  rows whose `state IN _UNRESOLVED_SIDE_EFFECT_STATES`
  (`{SUBMIT_UNKNOWN_SIDE_EFFECT, UNKNOWN, REVIEW_REQUIRED}`, L37–41).
- Policy default `unknown_side_effect_limit: int = 0` (L51), confirmed not
  overridden — `config/risk_caps.yaml:12` sets it to **0**.
  → ANY single unresolved unknown latches the switch.
- Refresh seam: `refresh_global_allocator()` L445–475 recomputes the count each cycle.

## The one stuck row (canonical artifact — Tier-1 provenance)

`state/zeus_trades.db` → `venue_commands`, the ONLY unresolved row:

```
command_id = 01049c6a357d4f97
market_id  = 2549604  (= highest-temperature-in-chengdu-on-june-17-2026)
token_id   = 9527066421711386897…  side=BUY  size=75.149  price=0.76  intent=ENTRY
venue_order_id = NULL
state      = SUBMIT_UNKNOWN_SIDE_EFFECT
created_at = 2026-06-15T16:29:23 UTC   updated_at = 2026-06-15T16:30:36 UTC
```

This is the operator's **"11:30 @0.76"** order (16:29 UTC = 11:29 CDT; price 0.76).
Its `SubmitUnknown` edli event is at 16:28:10 UTC — the ONLY SubmitUnknown today.
`venue_order_id` is NULL, so the venue response was indeterminate. The 3-min
`_edli_command_recovery_cycle` (running, log 18:40–18:51 CDT) cannot auto-clear a
NULL-venue_order_id unknown — resolution needs a venue reconcile-by-idempotency
match or operator clearance (command_recovery.py:4543 `_submit_unknown_command`;
escalates to REVIEW_REQUIRED at 4396–4434, also an unresolved state). The single
`Reconciled` event at 19:40 UTC did not clear it.

## Timeline (UTC)

| Time | Event |
|---|---|
| 13:48, 14:29 | buy_no → `VenueSubmitAcknowledged` (morning fills, 08:49/09:30 CDT) |
| **16:28:10** | 11:30-CDT buy_no → `SubmitUnknown` (chengdu, price 0.76) |
| 16:29–16:30 | `venue_commands` row → `SUBMIT_UNKNOWN_SIDE_EFFECT`, venue_order_id NULL |
| (next refresh) | `count_unknown_side_effects`=1 > limit 0 → kill switch LATCHED |
| 15:41:30 | last `SPINE_NOTRADE_EDGE_DIAG` (coincidental; spine still prices after) |
| **17:39:11** | first `SubmitRejected: unknown_side_effect_threshold` (blocked) |
| 17:56, 21:52, 22:41, 22:52 | 4 more buy_no, all `SubmitRejected` (distinct families) |
| 19:40:41 | one `Reconciled` event — did NOT clear the unknown |

---

## Competing hypotheses — evidence ledger

| # | Hypothesis | Verdict | Decisive evidence |
|---|---|---|---|
| H1 | FSR/forecast lane dark; starved by DAY0 | **FALSIFIED** | London 06-17 high FSR 2 min old (23:33 UTC); 5 buy_no DecisionProofAccepted post-latch |
| H2 | Incomplete topology / Gamma discovery never finished (#122) | **FALSIFIED** | Both probes have 11-bin market_events + fresh in-band EMS (top_ask set) |
| H3 | Spine NOTRADE (genuine no edge) | **FALSIFIED** | spine_edge diag silent post-15:41 *because* decisions are POSITIVE-edge and proceed to submit |
| H4 | DAY0 saturates claim rotation | **WEAKENED** | `_fair_lane_interleave` live (reactor.py:673); forecast decisions still occur post-latch |
| H5 | **Pre-submit kill switch latched on `unknown_side_effect_threshold`** | **CONFIRMED (root cause)** | 5/5 SubmitRejected w/ that exact reason_code; 1 stuck `SUBMIT_UNKNOWN_SIDE_EFFECT` venue_command (NULL venue_order_id); limit=0 |

### Rebuttal round
- *Strongest challenge to H5:* "Maybe each family separately fails at decision and
  the gate is incidental." → Refuted: all 5 rejections carry the IDENTICAL global
  reason `unknown_side_effect_threshold` (not a per-family edge/coherence reason),
  on 3 distinct token_ids, and `_assert_risk_allocator_allows_submit` is
  family-agnostic (executor.py:3195). The block is provably global.
- *Could the unknown be legitimately dangerous (real open exposure)?* Possibly — the
  switch exists to stop double-spend on an indeterminate submit. The fix must
  RESOLVE the unknown (reconcile the chengdu order's true venue state), not blindly
  raise the limit. See fix options.

### Convergence / separation
- H1 and H3 converge: both are explained by the SAME root mechanism — decisions
  succeed then get blocked, so the no-edge diag is silent and the lane *looks* dark.
- H5 is the single upstream cause of the observed "no orders since 11:30": the 11:30
  order is literally the unknown that latched the switch.

---

## Critical unknown
The TRUE venue state of order `01049c6a357d4f97` (chengdu 06-17, 0.76, size 75.15):
did it actually rest/fill on Polymarket (open exposure) or was it never accepted?
`venue_order_id` is NULL, so Zeus cannot tell. This determines whether clearing the
unknown is safe (never-accepted → safe to mark terminal) or requires booking a real
position first (accepted → reconcile fill before clearing).

## Discriminating probe (read-only, fastest)
Query Polymarket for any order/trade on token
`95270664217113868972136578664883734435134590015380289656395332208452169177126`
(chengdu NO) around 16:28–16:30 UTC for this wallet — via the CLOB
`GET /data/orders` / `/data/trades` by maker, or the on-chain CTF transfer log.
- If NO venue order/trade exists → the submit never landed → mark the command
  terminal (NO_SIDE_EFFECT) → count→0 → switch clears → harvest resumes.
- If an order/trade exists → reconcile the real fill into a position lot first,
  THEN mark the command terminal.

---

## Minimal proposed fix (NOT applied)

Two layers; the operator chooses based on the discriminating probe.

### Fix A (immediate, correct, surgical) — resolve the one stuck unknown
The kill switch is doing its job; the bug is that the unknown is *stuck* because
recovery cannot auto-resolve a NULL-`venue_order_id` `SUBMIT_UNKNOWN_SIDE_EFFECT`.
After the discriminating probe establishes ground truth, transition
`command_id=01049c6a357d4f97` to a terminal state via the existing recovery seam:
- Seam: `src/execution/command_recovery.py` `_submit_unknown_command` (L4543) →
  the geoblock/operator-clearance terminal transition path (L4551+, which writes a
  terminal CommandEvent and moves the row OUT of `_UNRESOLVED_SIDE_EFFECT_STATES`).
- Effect: next `refresh_global_allocator` cycle (governor.py:462) recomputes
  `count_unknown_side_effects → 0`, `kill_switch_reason → None`, `reduce_only` clears,
  buy_no submits resume. No code change — an operator/recovery action on live state.

### Fix B (durable, prevents indefinite latch) — auto-escalate, don't silently latch forever
The real defect is that a single indeterminate submit can latch the GLOBAL switch
indefinitely with no automatic resolution path when `venue_order_id` is NULL. The
3-min recovery cycle runs but no-ops on this row. Minimal durable change, at the
recovery seam (`src/execution/command_recovery.py` `_edli_command_recovery_cycle`):
add a bounded venue **reconcile-by-idempotency-key** for `SUBMIT_UNKNOWN_SIDE_EFFECT`
rows with NULL `venue_order_id` (query CLOB `/data/orders`/`/data/trades` by the
command's `idempotency_key`/token), then drive the row to FILLED / NO_SIDE_EFFECT /
REVIEW_REQUIRED based on the result. This closes the "NULL venue_order_id unknown =
permanent global block" trap that produced today's 7-hour stall.

Do NOT "fix" this by raising `unknown_side_effect_limit` above 0 — that would let
genuine double-spend risk through. The limit-0 policy is correct; the gap is the
missing auto-reconcile for NULL-id unknowns.

### Seam summary
- Block site: `src/execution/executor.py:3195,3200`
- Switch logic: `src/risk_allocator/governor.py:234–248, 575–595`; `config/risk_caps.yaml:12`
- Stuck row: `state/zeus_trades.db` `venue_commands.command_id=01049c6a357d4f97`
- Fix seam: `src/execution/command_recovery.py:4543+` (resolve) / `_edli_command_recovery_cycle` (auto-reconcile)
