# Zeus — Operating Status

## System Phase: OPERATING

Zeus is a live position management system for Polymarket weather markets. Paper trading validated ($244 profit). Architecture validated (21 invariant tests, 5 structural decisions). Now in pre-live verification.

## 5 Structural Decisions (Live Safety Foundation)

Every live safety mechanism traces to one of these decisions. When a new risk appears, first ask: "Which decision does this belong to?"

| # | Decision | Single Point of Control | Status |
|---|----------|------------------------|--------|
| 1 | **Exit = Order Lifecycle** | `exit_lifecycle.py` | ✅ State machine: sell_placed → fill/retry → backoff_exhausted |
| 2 | **Chain Truth with Uncertainty** | `chain_reconciliation.py` | ✅ Immutable snapshot, incomplete API guard, quarantine timeout |
| 3 | **Entry = Pending State** | `fill_tracker.py` | ✅ pending_tracked → entered/voided |
| 4 | **Provenance** | `config.state_path()` + `Position.env` | ✅ Physical file isolation + env column + contamination guard |
| 5 | **Single-threaded per Mode** | `main.py` cycle lock | ✅ threading.Lock + max_instances=1 |

## Provenance Model (State Architecture)

```
World Data (shared zeus.db, no mode tagging):
  ensemble_snapshots, calibration_pairs, platt_models,
  settlements, observations, market_events, ETL tables

Decision Data (shared zeus.db, env column):
  trade_decisions, chronicle, decision_log
  → env = "paper" | "live" | "backtest" (future)

Process State (physically isolated via state_path()):
  positions-{mode}.json, strategy_tracker-{mode}.json,
  status_summary-{mode}.json, control_plane-{mode}.json,
  risk_state-{mode}.db
```

Paper→live switch: change `settings.json` mode. World data inherits seamlessly. Process state starts fresh. Contamination guard blocks mixed-env positions.

## Architecture

```
CycleRunner (pure orchestrator, calls but does not contain logic)
├── chain_reconciliation (ChainPositionView snapshot → 3 rules + quarantine timeout)
├── exit_lifecycle (sell order state machine, retry/backoff, collateral check)
├── fill_tracker (pending_tracked → entered/voided via CLOB status)
├── monitor (Position.evaluate_exit via exit_triggers, entry-method-aware)
│   ├── buy_yes: 2-consecutive EDGE_REVERSAL with EV gate
│   └── buy_no: cal_std-scaled threshold, near-settlement hold
├── evaluator (ENS→Platt→α→edges→FDR→Kelly→risk→anti-churn)
│   ├── opening_hunt, update_reaction, day0_capture
├── executor (limit orders, dynamic pricing, place_sell_order)
├── decision_chain (CycleArtifact + NoTradeCase → decision_log with env)
├── status_summary (health snapshot, mode-isolated)
└── control_plane (pause_entries, resume — mode-isolated)
```

## Codebase Stats

| Metric | Value |
|--------|-------|
| Source files (src/) | 67 |
| Test files | 29 |
| Tests passing | 216+ |
| Invariant tests | 21 (test_live_safety_invariants.py) |
| New modules this session | exit_lifecycle.py, fill_tracker.py, collateral.py |

## Open Items

### Needed for Live

- [ ] **39 stale test failures** — interface drift (EnsembleSignal, exit_triggers, execute_order). Tests need updating to current API. In progress.
- [ ] **Keychain wallet setup** — `security add-generic-password` for Polygon private key + funder address
- [ ] **2-week paper validation** — verify daemon stability with new exit lifecycle, provenance paths, state isolation

### Deferred (not blocking live)

- **Day0 settlement_capture** — locked + graduated paths. Requires more WU data.
- **Backtest engine** — will use `env="backtest"` in provenance model. Needs TIGGE DJF/JJA/SON data.
- **ECMWF Open Data** — collection operational, needs stability verification.
- **Edge compression alerting** — RiskGuard monitors per-strategy trends. Wire to APScheduler.
- **T2-A: Post-trade chain refresh** — nice-to-have within Decision 3. Chain reconciliation already runs every cycle.
- **T2-F: Multi-phase reconciliation with Gamma metadata** — nice-to-have within Decision 2. Operational convenience for identifying quarantined positions.

### Observations

The original prompt framed the gap as "7/22 Rainstorm mechanisms, missing 15." Structural analysis showed this was 5 decisions, not 22 patches. T2-A and T2-F, originally flagged as "critical next session", are low-priority optimizations within already-complete structural decisions. The actual blocker for live was **provenance** — which wasn't in the original 22 at all.
