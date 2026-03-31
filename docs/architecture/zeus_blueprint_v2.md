# Zeus Architecture Blueprint v2: Position-Centric Design

**Supersedes:** `rainstorm_architecture_blueprint.md` (v1, signal-centric)
**Informed by:** Rainstorm 14-month autopsy, Zeus Sessions 1-6, two independent code reviews (2/10 and 4/10 Phase D readiness), 10 P0 bugs all in lifecycle layer

---

## Why v2 Exists

Blueprint v1 defined the system's data flow as `observe → analyze → decide → execute`. This put Signal at the center. Every system built on v1 — Rainstorm and Zeus — invested 90% of engineering in signal quality and 10% in position management. In both systems, 100% of catastrophic bugs were in position management.

v1's implicit assumption was: if the signal is correct, everything downstream is simple. This assumption is wrong. The signal can be perfect and the system can still lose all its money in 30 minutes — as Zeus's false EDGE_REVERSAL demonstrated.

v2 puts Position at the center. Signal is an input. Execution is an output. The system's primary job is not "find edges" — it is "manage positions from birth to settlement without losing information."

---

## 1. The Core Data Flow

```
                    Signal
                      ↓
Position: DISCOVERED → EVALUATED → ENTERED → HOLDING → OBSERVED → SETTLED
              ↑                                  ↓          ↓
          NoTradeCase                      EDGE_REVERSAL  DAY0_EXIT
              ↓                                  ↓          ↓
          (recorded)                          EXITED      CAPTURED
```

Every arrow is an explicit state transition. Every transition produces an immutable artifact. No transition can happen without the Position object carrying its full context through. A Position that loses its direction, probability space, entry method, or decision snapshot between transitions is an architectural violation.

**The invariant:** at any point in a Position's life, you can answer these questions from the Position object alone, without querying any other module:

- What direction is this? (buy_yes / buy_no)
- What probability space is it in? (YES-space / NO-space for held side)
- How was it entered? (which signal method, which calibration version)
- What data was available at entry time? (decision_snapshot_id)
- What is its current chain state? (PENDING / SYNCED / QUARANTINED / VOIDED)
- What exit strategy applies? (buy_no path vs buy_yes path)
- Has it been through Day0 observation? (observation history)

If any downstream module has to infer any of this, the architecture is broken.

---

## 2. The Position Object

This is the central data structure. Everything else serves it.

```python
@dataclass
class Position:
    # Identity
    position_id: str               # UUID, immutable
    decision_id: str               # Links to DecisionArtifact chain
    
    # Market context (immutable after creation)
    market_id: str
    condition_id: str
    token_id: str                  # YES token for buy_yes, NO token for buy_no
    city: str
    range_label: str
    target_date: date
    timezone: str
    unit: str                      # 'F' or 'C' — carried, never inferred
    
    # Direction (immutable — THE most important field)
    direction: Literal["buy_yes", "buy_no"]
    
    # Probability (always in held-side space — flipped exactly once at creation)
    p_held_side: float             # P(YES) for buy_yes, P(NO) for buy_no
    p_market_held_side: float      # Market price in same space
    
    # Entry context (immutable snapshot)
    entry_method: str              # 'ens_member_counting', 'day0_observation', etc.
    signal_version: str            # Which ensemble_signal code produced p_raw
    calibration_version: str       # Which Platt model calibrated it
    decision_snapshot_id: str      # FK to immutable forecast data at decision time
    entry_price: float
    shares: float
    cost_basis_usd: float
    bankroll_at_entry: float
    edge_at_entry: float
    edge_source: str               # 'settlement_capture', 'shoulder_sell', 'center_buy', 'opening_inertia'
    discovery_mode: str            # 'opening_hunt', 'update_reaction', 'day0_capture'
    
    # Lifecycle state (mutable)
    status: Literal[
        "pending",      # Order placed, not yet filled
        "entered",      # Filled, tracked locally
        "holding",      # Confirmed on chain, actively monitored
        "day0_window",  # Within 6h of settlement, Day0 logic active
        "exiting",      # Exit order placed
        "settled",      # Market resolved, P&L final
        "voided",       # Closed with pnl=0 (unknown exit price)
        "admin_closed", # Ghost/phantom/unfilled — excluded from P&L
    ]
    
    # Chain reconciliation
    chain_state: Literal["unknown", "synced", "local_only", "chain_only", "quarantined"]
    chain_shares: Optional[float]
    chain_verified_at: Optional[datetime]
    
    # Exit state (persisted across monitor cycles)
    exit_strategy: Literal["buy_yes_standard", "buy_no_conservative"]
    neg_edge_count: int = 0        # Consecutive negative edge cycles (buy_no)
    last_monitor_prob: Optional[float] = None
    last_monitor_edge: Optional[float] = None
    last_monitor_at: Optional[datetime] = None
    
    # City-specific exit context
    cal_std: float                 # Calibration std for this city-season
    city_peak_hour: int            # For Day0 phase detection
    
    # Anti-churn state
    recent_exit_at: Optional[datetime] = None
    recent_void_at: Optional[datetime] = None
    
    # Settlement
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    admin_exit_reason: Optional[str] = None  # Separate from economic exit
    
    # Attribution
    market_hours_open_at_entry: Optional[float] = None
    fill_quality: Optional[float] = None     # (exec_price - VWMP) / VWMP
    
    def evaluate_exit(self, fresh_data: MonitorData) -> ExitDecision:
        """Position knows how to exit itself."""
        if self.direction == "buy_no":
            return self._evaluate_buy_no_exit(fresh_data)
        return self._evaluate_buy_yes_exit(fresh_data)
    
    def close(self, exit_price: float, reason: str) -> float:
        """Economic close with real P&L."""
        self.exit_price = exit_price
        self.realized_pnl = (exit_price - self.entry_price) * self.shares
        self.exit_reason = reason
        self.status = "settled" if reason == "SETTLEMENT" else "exiting"
        return self.realized_pnl
    
    def void(self, reason: str):
        """Non-economic close. P&L = 0. Excluded from metrics."""
        self.exit_price = None
        self.realized_pnl = 0.0
        self.admin_exit_reason = reason
        self.status = "voided"
    
    def is_admin_exit(self) -> bool:
        return self.admin_exit_reason is not None
```

---

## 3. The Decision Chain

Every cycle produces artifacts. Even when no trade happens.

```
CycleArtifact
  ├── MarketScan          — which markets were found, which were filtered out (and why)
  ├── for each candidate:
  │   ├── ForecastSnapshot — immutable ENS data at this moment (snapshot_id)
  │   ├── SignalResult     — p_raw, p_cal, p_posterior, spread, bimodal flag
  │   ├── EdgeDecision     — edge value, CI, FDR result, direction
  │   │   ├── if edge found:
  │   │   │   ├── RiskVerdict    — hard_risk, health_risk, model_risk (each independent)
  │   │   │   ├── SizingResult   — kelly_raw, kelly_adjusted, final_usd, rejection reason
  │   │   │   └── OrderIntent    — token_id, price, size, limit_type
  │   │   └── if no edge:
  │   │       └── NoTradeCase    — rejection_stage, specific_reason, market_price, model_prob
  │   └── for each held position:
  │       └── MonitorResult  — fresh_prob, fresh_edge, exit_decision, neg_edge_count
  └── CycleSummary       — trades_placed, edges_found, edges_filtered, positions_monitored
```

**NoTradeCase is not optional.** When Zeus doesn't trade, it must record WHY with the same rigor as when it does trade. The rejection_stage field tells you exactly where the pipeline stopped:

```python
class RejectionStage(Enum):
    MARKET_FILTER = "market_filter"       # City not covered, market too old, etc.
    SIGNAL_QUALITY = "signal_quality"     # ENS stale, bimodal, model conflict
    EDGE_INSUFFICIENT = "edge_insufficient"  # CI crosses zero
    FDR_FILTERED = "fdr_filtered"         # Passed edge test but failed FDR
    RISK_REJECTED = "risk_rejected"       # Hard limit, daily loss, correlation
    SIZING_TOO_SMALL = "sizing_too_small" # Kelly * adjustments < min_order
    EXECUTION_FAILED = "execution_failed" # Order placement or fill failure
    ANTI_CHURN = "anti_churn"            # Range re-entry block, voided cooldown
```

---

## 4. The Cycle Runner

Pure orchestration. Zero logic. 50 lines.

```python
class CycleRunner:
    """Orchestrates one discovery cycle. Contains NO business logic."""
    
    async def run_cycle(self, mode: DiscoveryMode) -> CycleArtifact:
        artifact = CycleArtifact(mode=mode, started_at=utcnow())
        
        # 1. Housekeeping
        self.orphan_order_cleanup()
        self.chain_reconciliation()
        
        # 2. Risk pre-check
        risk_level = self.riskguard.current_level()
        if risk_level in (RiskLevel.ORANGE, RiskLevel.RED):
            artifact.skipped_reason = f"risk_level={risk_level}"
            return artifact
        
        # 3. Monitor held positions FIRST (protect existing value)
        for position in self.portfolio.open_positions():
            monitor_result = position.evaluate_exit(
                self.build_monitor_data(position))
            artifact.add_monitor_result(position, monitor_result)
            if monitor_result.should_exit:
                self.executor.exit_position(position, monitor_result.reason)
        
        # 4. Scan for new opportunities (only if not exit-only mode)
        if risk_level == RiskLevel.GREEN:
            candidates = self.scanner.find_candidates(mode)
            for candidate in candidates:
                decision = self.evaluator.evaluate(candidate)
                artifact.add_decision(candidate, decision)
                if decision.should_trade:
                    position = self.executor.enter(candidate, decision)
                    self.portfolio.add(position)
        
        # 5. Finalize
        artifact.completed_at = utcnow()
        self.chronicler.record(artifact)
        self.status_writer.update(artifact)
        return artifact
```

Every piece of logic — scanning, evaluation, exit decisions, execution — lives in a module that the runner calls. The runner doesn't know what an "edge" is. It doesn't know the difference between buy_yes and buy_no. It doesn't know what Platt scaling is. It just orchestrates the sequence.

**opening_hunt, update_reaction, day0_capture are not separate code paths.** They are different `DiscoveryMode` values passed to the same CycleRunner. The mode affects which markets the scanner finds and what signal method the evaluator uses. The lifecycle logic is identical.

---

## 5. Truth Hierarchy

Three sources of truth exist. They WILL disagree. The hierarchy is:

```
1. Chain (authoritative)   — what Polymarket's blockchain says
2. Chronicler (audit trail) — what Zeus recorded when it happened
3. Portfolio (working state) — what Zeus currently believes

Chain > Chronicler > Portfolio. Always.
```

**Reconciliation rules** (from Rainstorm's sync_engine_v2, battle-tested):

| Local State | Chain State | Action |
|-------------|-------------|--------|
| Position exists | Position exists, shares match | SYNCED — no action |
| Position exists | Position exists, shares differ | Update local from chain |
| Position exists | Position NOT on chain | VOID immediately (don't ask why) |
| No local record | Position on chain | QUARANTINE — low confidence, 48h forced exit eval |

The v1 sync engine tried to reason about WHY positions disappeared. It accumulated ghosts. v2's rule is simpler: chain is truth. If chain says it's gone, it's gone.

---

## 6. Four Independent Strategies

Zeus's edge comes from four sources with different risk profiles, different alpha decay rates, and different optimal management. They must be tracked independently.

| Strategy | Signal Source | Risk | Alpha Decay | Key Metric |
|----------|-------------|------|-------------|------------|
| A: Settlement Capture | Observation (fact) | Near-zero | Very slow | Opportunities per day |
| B: Shoulder Sell | Climatology + ENS | Tail risk | Medium | Shoulder overpricing ratio trend |
| C: Center Buy | ENS + Platt | Model risk | Fast | Model vs market Brier comparison |
| D: Opening Inertia | Market timing | Unknown | Fastest | Edge size vs market age correlation |

**Per-strategy tracking:**
- Separate P&L, win rate, edge size trend (30/60/90 day)
- Separate RiskGuard thresholds (Strategy C failing doesn't halt Strategy A)
- Separate capital allocation (adjustable based on performance)
- Separate alpha decay monitoring (EDGE_COMPRESSION per strategy)

When the edge_compression_monitor detects Strategy C's average edge shrinking for 30+ consecutive days, the correct response is to reduce Strategy C allocation — not to shut down the entire system. Strategy A (settlement capture) might still be printing money.

---

## 7. Exit Architecture: Equal to Entry

This is the single biggest change from v1.

**Entry validation (15 layers, unchanged):**
ENS fetch → MC noise (5000) → member counting → Platt → normalize → α-posterior → bootstrap CI (500) → FDR → Kelly → dynamic mult → correlation check → risk limits → anti-churn → edge recheck at execution → ENTER

**Exit validation (now also multi-layered):**

```python
def _evaluate_buy_no_exit(self, data: MonitorData) -> ExitDecision:
    """Buy-no exit: 87.5% base win rate demands conservative exit logic."""
    
    # Layer 1: Recompute probability with SAME METHOD as entry
    fresh_prob = self._recompute_held_side_prob(data)  # Uses entry_method
    
    # Layer 2: Forward edge in held-side space (no flip)
    forward_edge = fresh_prob - data.current_market_price_held_side
    
    # Layer 3: Dynamic threshold scaled by cal_std
    edge_threshold = -self.cal_std * 0.015  # Noisy cities need deeper reversal
    
    # Layer 4: Consecutive negative cycle requirement
    if forward_edge < edge_threshold:
        self.neg_edge_count += 1
    else:
        self.neg_edge_count = 0  # Reset on ANY non-negative cycle
    
    # Layer 5: N consecutive negatives before exit
    if self.neg_edge_count < 2:
        return ExitDecision.HOLD
    
    # Layer 6: EV gate — don't sell at spread loss when hold EV is positive
    net_sell = self.shares * data.best_bid
    net_hold = self.shares * fresh_prob
    if net_sell < net_hold:
        return ExitDecision.HOLD  # Selling is worse than holding
    
    # Layer 7: Day0 override — observation trumps forecast
    if self.status == "day0_window" and data.day0_signal:
        day0_decision = self._evaluate_day0_exit(data.day0_signal)
        if day0_decision == ExitDecision.HOLD:
            return ExitDecision.HOLD  # Observation says hold
    
    # Layer 8: Near-settlement hold (< 4h, only exit if deeply negative)
    if data.hours_to_settlement < 4.0:
        if forward_edge > -0.20:
            return ExitDecision.HOLD
    
    return ExitDecision.EXIT_EDGE_REVERSAL
```

8 layers. Comparable to entry's validation depth. Each layer addresses a specific failure mode that Rainstorm experienced in live trading.

---

## 8. Day0: The Convergence Point

Day0 is not "Mode C." It is the terminal phase of every position's life.

When a position enters its Day0 window (< 6h to settlement):
1. `status` transitions to `day0_window`
2. Observation data becomes the primary signal (above ENS)
3. Settlement capture checks activate (for new entries)
4. DAY0_OBSERVATION_REVERSAL becomes available as exit trigger (single confirmation, no 2× ENS requirement)

**Day0 exit is the most reliable exit signal in the system.** Observation is ground truth. When observation contradicts your position, one confirmation is enough. When observation confirms your position, HOLD TO SETTLEMENT — no exit trigger should override this.

**Settlement capture priority order within Day0 cycle:**
1. EXIT held positions that observation contradicts (protect capital)
2. CAPTURE settlement on bins where observation has crossed threshold (near-certain profit)
3. GENERAL Day0 edge scan (observation-blended probability)

---

## 9. Type Safety as Architecture

Two type-safe foundations prevent entire categories of bugs:

**Temperature / TemperatureDelta** (from Rainstorm, already ported):
- Makes °C/°F confusion a TypeError, not a silent numerical error
- TemperatureDelta for spreads, biases, thresholds (no +32 offset)
- Delta / Delta → dimensionless float (z-score pattern)
- cdf_probability() gates scipy behind unit-consistency check

**Position object** (defined above):
- Makes direction/probability-space confusion impossible (held_side is always the held side)
- Makes stale-exit impossible (evaluate_exit recomputes with entry_method)
- Makes orphan orders impossible (PENDING_TRACKED status with chain reconciliation)
- Makes P&L corruption impossible (close vs void are distinct operations)

Both work the same way: instead of relying on external code to remember the correct behavior, the type itself enforces it.

---

## 10. Observability: Not Optional

Zeus in production is a black box without these three systems:

**Status Summary** (written every cycle):
```json
{
  "timestamp": "2026-04-01T14:30:00Z",
  "cycle_mode": "opening_hunt",
  "risk_level": "GREEN",
  "open_positions": 3,
  "total_exposure_usd": 4.50,
  "bankroll": 147.20,
  "edges_found": 7,
  "edges_after_fdr": 2,
  "trades_placed": 1,
  "trades_rejected": {"risk": 0, "sizing": 1, "anti_churn": 0},
  "brier_30": 0.19,
  "win_rate_20": 0.55,
  "strategy_pnl": {"settlement_capture": 2.10, "shoulder_sell": 0.80, "center_buy": -0.40}
}
```

**Control Plane** (commands from OpenClaw, not config edits):
```
pause_new_entries     — stop entering, keep monitoring
resume_entries        — resume after pause
tighten_risk          — double edge thresholds temporarily
request_status        — force status_summary write
set_strategy_gate     — enable/disable individual strategies
```

**Decision Audit** (queryable from any trade_id):
```sql
SELECT d.* FROM decision_chain d
WHERE d.trade_id = 'xyz'
ORDER BY d.stage;
-- Returns: MarketScan → ForecastSnapshot → SignalResult → EdgeDecision 
--          → RiskVerdict → SizingResult → OrderIntent → ExecutionReport
```

---

## 11. What Transfers from Rainstorm (Verbatim or Near-Verbatim)

| Rainstorm Component | Zeus Target | Port Method |
|---------------------|-------------|-------------|
| `types/temperature.py` | `types/temperature.py` | Already ported ✓ |
| `state/portfolio.py` void/close/admin | Position.close() / Position.void() | Copy design, adapt to Position object |
| `polymarket/sync_engine_v2.py` | `state/chain_reconciliation.py` | Copy near-verbatim (3 rules) |
| `strategy/dynamic_exit.py` buy_no path | Position._evaluate_buy_no_exit() | Copy logic, embed in Position |
| `strategy/day0_blended.py` observation math | `signal/day0_signal.py` | Copy math verbatim (14-month calibrated) |
| `strategy/settlement_capture.py` | `signal/settlement_capture.py` | Copy locked + graduated paths |
| `schema/` decision artifacts | `state/decision_chain.py` | Copy schema, adapt field names |
| `engine/cycle_runner.py` | `engine/cycle_runner.py` | Copy pattern (pure orchestrator) |
| `control/control_plane.py` | `control/control_plane.py` | Copy design |

## 12. What Zeus Keeps (Superior to Rainstorm)

| Zeus Component | Why It's Better |
|----------------|-----------------|
| `ensemble_signal.py` MC noise | Rainstorm used Gaussian CDF. Zeus uses member counting + instrument noise simulation. Correctly models WU integer rounding. |
| `platt.py` 3-param + bootstrap | Rainstorm's Platt was 2-param without bootstrap params. Zeus's captures parameter uncertainty. |
| `fdr_filter.py` exact p-values | Rainstorm had no FDR control. Zeus has BH with exact bootstrap p-values. |
| `market_analysis.py` double bootstrap | Rainstorm's edge CI only captured σ_ensemble. Zeus captures σ_ensemble + σ_parameter. |
| Anti-churn 8 layers | Already ported from Rainstorm and enhanced with tests. |
| Config strict loader | No .get(key, fallback). Rainstorm had magic numbers. |

---

## 13. Implementation Path

**Phase 2A: Position Object + Lifecycle** (1-2 sessions)
- Implement Position dataclass with all fields
- Replace portfolio's dict-based positions with Position objects
- Implement close() / void() / is_admin_exit()
- Wire evaluate_exit() with buy_no and buy_yes paths
- Add PENDING_TRACKED status for live orders

**Phase 2B: CycleRunner Refactor** (1 session)
- Extract all logic from opening_hunt/update_reaction/day0_capture
- Create CycleRunner as pure orchestrator (< 50 lines)
- Discovery modes become parameters, not separate code paths
- Fix P0-1 (forecast slicing) and P0-3 (GFS member count) during extraction

**Phase 2C: Decision Chain + NoTradeCase** (1 session)
- Implement artifact schema
- Record every decision with full chain
- Record every non-trade with rejection_stage
- Fix P0-7 (harvester dedup) by using decision_snapshot_id
- Fix P0-8 (RiskGuard blindness) by having RiskGuard read decision artifacts

**Phase 2D: Chain Reconciliation** (1 session)
- Port sync_engine_v2's three rules
- Implement QUARANTINED status for unknown chain positions
- Fix P0-5 (pending orders not tracked) through PENDING_TRACKED + reconciliation
- Wire to truth hierarchy: chain > chronicler > portfolio

**Phase 2E: Observability** (1 session)
- Status summary written every cycle
- Control plane for runtime commands
- Per-strategy P&L tracking
- Edge compression monitoring

After Phase 2E: restart paper trading with the new architecture. 2 weeks of clean data before considering live.

---

## 14. The Principle

Rainstorm's math was beautiful. Rainstorm is dead.

Zeus's math is also beautiful. Zeus's survival depends on whether it learns from Rainstorm's death — not by avoiding Rainstorm's math (which was correct), but by inheriting Rainstorm's operational wisdom (which was earned in blood).

The operational wisdom, reduced to one sentence:

**A trading system's correctness is determined not by how well it finds edges, but by how completely it preserves position identity from discovery through settlement.**

Every design in this blueprint exists to serve that sentence.
