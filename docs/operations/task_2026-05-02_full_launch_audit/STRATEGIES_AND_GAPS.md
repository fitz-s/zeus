# Zeus Trading Strategies — Current State & Design Gaps

**Created**: 2026-05-02
**Authority basis**: haiku audit `af0f937c20bb11c33` (DiscoveryMode + edge_source + strategy_key + EntryMethod + Sizing + Exit + Killswitch full catalog) cross-referenced with live evidence (logs/zeus-live.err post-restart) and `ensemble_snapshots_v2` / `position_current` schemas.

> **Purpose**: Each item below is a **design decision**, not a quick fix. This file is for thinking through what Zeus actually does today vs what it would need to do for "full live launch" alpha capture. Decisions belong to the operator; agents are not authorized to invent these.

---

## 1. Current strategies (what's actually running)

### 1.1 DiscoveryMode → strategy_key map

| DiscoveryMode | Schedule | strategy_key produced | Sub-classification logic |
|---|---|---|---|
| `OPENING_HUNT` | every 15 min (was 30, lowered 2026-05-02) | `opening_inertia` | Always — when `hours_since_open < 24` AND `hours_to_resolution >= 24` |
| `UPDATE_REACTION` | cron at **07:00 / 09:00 / 19:00 / 21:00 UTC** (4×/day) | `center_buy` | `edge.direction == "buy_yes"` AND `not edge.bin.is_shoulder` |
| `UPDATE_REACTION` | (same cron) | `shoulder_sell` | `edge.direction == "buy_no"` AND `edge.bin.is_shoulder` |
| `UPDATE_REACTION` | (same cron) | `opening_inertia` | Fallback when neither center_buy nor shoulder_sell matches |
| `DAY0_CAPTURE` | every 15 min | `settlement_capture` | when `hours_to_resolution < 6` (see gap §3.1) |

### 1.2 EntryMethod (2 wired)

- `ens_member_counting` — standard ensemble probability derivation (default for OPENING_HUNT / UPDATE_REACTION)
- `day0_observation` — high-frequency Day0 observation refresh (DAY0_CAPTURE)

### 1.3 Sizing pipeline (Kelly + 4 cap layers + 2 throttling branches)

**Core**: `kelly_size = f* × kelly_mult × bankroll`

**Dynamic adjustment**:
- `dynamic_kelly_mult` (default 0.25 = ¼-Kelly) — adjusts based on CI width and portfolio heat

**Throttling**:
- Cluster exposure > 10% → halve sizing
- Total portfolio heat > 25% → halve sizing

**Hard caps** (in $):
- `live_safety_cap_usd: $5.00` — Phase 1 hard ceiling per order (was added 2026-04-12 after smoke test placed unintended $60)
- `max_per_market_micro: $250.00`
- `max_per_event_micro: $500.00`
- `max_correlated_exposure_micro: $1000.00`
- `taker_min_depth_micro: $50.00` — required orderbook depth for taker orders

### 1.4 Exit policies (8 types, fully automatic)

| ExitDecision | Trigger |
|---|---|
| `RED_FORCE_EXIT` | global risk_level → RED forces exit of non-Day0 positions |
| `SETTLEMENT_IMMINENT` | `< 1.0h` to market resolution |
| `WHALE_TOXICITY` | adversarial orderbook pressure detection |
| `MODEL_DIVERGENCE_PANIC` | high divergence between probability sources |
| `FLASH_CRASH_PANIC` | market velocity below `-0.15/hr` |
| `VIG_EXTREME` | vig > 1.08 or < 0.92 |
| `DAY0_OBSERVATION_REVERSAL` | new Day0 obs flips entry edge (with EV gate) |
| `EDGE_REVERSAL` | conservative forward edge < CI threshold for 2+ consecutive cycles |

### 1.5 Killswitch / defensive policies (6 global gates)

| Gate | Trigger |
|---|---|
| `heartbeat_lost` | 2+ consecutive venue heartbeat failures → tombstone file |
| `ws_gap` | user-channel WS gap > 15s |
| `unknown_side_effect_threshold` | orders in unknown venue state |
| `reconcile_finding_threshold` | open exchange reconcile findings in DB |
| `drawdown_threshold` | current drawdown ≥ 10% |
| `reduce_only` | activated whenever risk_level ≠ GREEN (DATA_DEGRADED, YELLOW, ORANGE, RED) |

---

## 2. Lifecycle coverage today

```
[market open] ───── 24h ───── [middle: 24h+ open AND 6h+ to settle] ───── 24h ───── 6h ───── [SETTLE]
       ↑                                  ↑                                                    ↑
OPENING_HUNT                       UPDATE_REACTION                                      DAY0_CAPTURE
opening_inertia                    center_buy / shoulder_sell / opening_inertia        settlement_capture
(every 15 min)                     ⚠ ONLY at 4 cron times: 07/09/19/21 UTC             (every 15 min, but
                                   ❌ ~20h/day with NO active strategy                    only when <6h —
                                                                                          design gap §3.1)
```

### 2.1 Where alpha is captured today
- **First 24h after market opens**: OPENING_HUNT covers it well (15 min granularity)
- **Final 6h before settle**: DAY0_CAPTURE covers it (15 min granularity) — but the 6h window is too narrow (§3.1)
- **NWP-release windows** (07/09/19/21 UTC, ~30-min slots each): UPDATE_REACTION reacts to fresh ECMWF/GFS forecasts

### 2.2 Where alpha is dropped today
- **Middle 20h/day** (between 09 UTC → 19 UTC and 21 UTC → 07 UTC): no strategy fires unless a market just opened or is about to settle
- **Asymmetric edge capture**: only `center_buy` (buy YES central) + `shoulder_sell` (buy NO shoulder) are wired. The inverse pair `shoulder_buy` (buy YES shoulder) + `center_sell` (buy NO center) is referenced in code but never produced
- **Price-event reactivity**: zero. If a market price moves 5% in 30 seconds, nothing wakes up until next cron / interval
- **24h-before-settlement window**: structurally uncovered. Day0 thinks "near-settle" means <6h; everything between 24h-out and 6h-out is only seen by UPDATE_REACTION's 4 cron times

---

## 3. Design gaps (each is its own decision)

### 3.1 Gap: DAY0_CAPTURE narrow window — `hours_to_resolution < 6` is wrong (task #37)

**Symptom**: DAY0_CAPTURE only fires for markets within 6 hours of settlement. Most days, no markets are in that window for ~17h/day.

**Operator framing**: "顾名思义 day0 应该交易所有当地市场 0 点前的 24 个小时"

**Current code path**: `src/engine/cycle_runtime.py:1950` filter `hours_to_resolution < params["max_hours_to_resolution"]`, params from MODE_PARAMS in `src/engine/cycle_runner.py:335`.

**Decision required**:
- Window definition: "24h before settle" (UMA 10:00 UTC) OR "24h before target_date midnight in city's local timezone"? They differ for non-UTC cities.
- Should DAY0_CAPTURE **replace** UPDATE_REACTION's near-settle role, or coexist?
- Cadence: 15 min interval is fine, but is it enough for the final 6h sub-window where price action accelerates?

**Dependencies**: requires §3.4 (PRICE_DRIFT_REACTION) for the final-hour acceleration; otherwise we trade the 24h window at flat cadence.

---

### 3.2 Gap: middle-state has 20h/day strategy vacuum (task #51)

**Symptom**: After OPENING_HUNT's 24h-since-open window closes and before DAY0_CAPTURE's near-settle window opens, only UPDATE_REACTION's 4 cron times cover the middle. Markets sit unwatched for 10h+ stretches.

**Why it exists**: UPDATE_REACTION cron is bound to NWP model release times (ECMWF 00z and 12z, GFS 06z and 18z release after lag). The original assumption: "no new forecast = no new edge to capture." That assumption is wrong for two reasons:
1. Polymarket prices move continuously even when the underlying physical forecast is unchanged (toxic flow, retail flow, news shocks)
2. Existing snapshots can re-evaluate edge as time decays — the same forecast at 50h-to-settle vs 30h-to-settle has different implied probability under the model

**Decision required**:
- New mode `MIDDLE_STATE_HUNT` fired every 15-30 min in the gap window?
- OR convert UPDATE_REACTION to interval-based (5-15 min) and absorb middle-state into it?
- OR skip a dedicated mode and only react to price events (§3.4)?

**Sub-strategy implications**: if added, MIDDLE_STATE_HUNT would naturally produce all 4 strategy_keys (`center_buy` / `shoulder_sell` / `opening_inertia` / and once §3.5 lands, `shoulder_buy` / `center_sell`).

---

### 3.3 Gap: UPDATE_REACTION is cron, should be interval (task #53)

**Symptom**: 4 fixed UTC times (07/09/19/21) means tight fire windows but huge dead windows. Tied to NWP model release schedule.

**Decision required** — depends on §3.2:
- If MIDDLE_STATE_HUNT (§3.2) is added, UPDATE_REACTION becomes redundant (its sub-strategies already in MIDDLE_STATE_HUNT)
- If §3.2 is rejected, UPDATE_REACTION should at least gain misfire-grace and run e.g. every 30 min

---

### 3.4 Gap: no PRICE_DRIFT_REACTION (event-driven re-eval) (task #52)

**Symptom**: All current strategies are time-driven (cron + interval). None reacts to a Polymarket price move. If the price drifts 5% in 30 seconds (e.g., toxic flow, news), our edge can flip in seconds, but we don't notice until the next scheduled cycle.

**Existing infra**: WS user channel exists (`src/ingest/polymarket_user_channel.py`) and WS gap guard already tracks subscription state. Adding a price-channel listener that triggers re-eval on `|delta_price| > threshold` is a similar pattern.

**Decision required**:
- Threshold: 1% / 2% / 5% delta-price?
- Re-eval scope: just the moved market, or all correlated markets in the same city?
- Cooldown: avoid re-eval flood when a market is volatile (e.g., min 60s between fires per market)
- Compose with other modes or replace?

---

### 3.5 Gap: shoulder_buy + center_sell dormant (task #55)

**Symptom**: Only `center_buy` (buy YES central) + `shoulder_sell` (buy NO shoulder) are wired in `_classify_edge_source`. The inverse pair is referenced but never produced.

**Alpha implication**: half the edge space is unworked.

| Direction × Bin region | YES (BUY) | NO (BUY) |
|---|---|---|
| **Center bins** | `center_buy` ✓ | `center_sell` ❌ dormant |
| **Shoulder bins** | `shoulder_buy` ❌ dormant | `shoulder_sell` ✓ |

**Decision required**:
- Was the inverse pair intentionally disabled (some reason like asymmetric calibration trust)?
- Or just not wired? Need to trace `_classify_edge_source` history.
- If we wire them, what's the EV gate threshold? Maybe higher than the existing pair if calibration is more uncertain on those quadrants.

---

### 3.6 Gap: opening_hunt's 24h-window may be too narrow (task #38, partially addressed)

**Status**: opening_hunt_interval_min was 30, lowered to 15 in `config/settings.json` 2026-05-02. Live daemon needs restart for it to take effect (task #47).

**Open question**: should the `hours_since_open < 24` cutoff be widened? E.g., 48h after open could still have OPENING_HUNT-style mispricings. But then it overlaps with MIDDLE_STATE_HUNT — needs §3.2 decision first.

---

## 4. Dependency / decision order

```
§3.2 (MIDDLE_STATE_HUNT)
    ├── precedes §3.3 (UPDATE_REACTION cron→interval) — §3.3 may be deleted depending on §3.2
    └── precedes §3.6 (OPENING_HUNT window) — overlap analysis needs §3.2 first
§3.1 (DAY0_CAPTURE 24h window) — independent
§3.4 (PRICE_DRIFT_REACTION) — independent, complements all
§3.5 (wire dormant pairs) — independent of mode design but blocks alpha doubling
```

Operator decisions needed in this order:
1. §3.1 (DAY0 window definition)
2. §3.2 (introduce MIDDLE_STATE_HUNT yes/no)
3. §3.5 (wire dormant pairs yes/no)
4. §3.4 (price-driven mode yes/no)
5. §3.3 + §3.6 fall out from §3.2

---

## 5. Out-of-scope here (handled separately)

These are NOT strategy design — they're operational/infrastructure issues, see `REMAINING_TASKS.md`:
- Data-ingest daemon resilience (sonnet currently working)
- Riskguard daemon DB+proxy errors
- $150 hardcode bankroll fiction
- TIGGE backfill failure
- Fail-closed gate audit follow-ups (PhysicalBounds, ExpiringAssumption)
