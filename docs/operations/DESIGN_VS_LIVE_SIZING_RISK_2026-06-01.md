# DESIGN vs LIVE — Sizing / Risk / Strategy / Exit Half (EDLI Reactor)

```
Created: 2026-06-01
Last reused/audited: 2026-06-01
Authority basis: AGENTS.md §"Risk levels"/§"Strategy families";
  docs/reference/zeus_risk_strategy_reference.md;
  docs/reference/zeus_oracle_density_discount_reference.md;
  config/settings.json::_bankroll_doctrine_2026_05_04
Scope: READ-ONLY audit. HEAD 6fcd05a69f. No edits/git/DB-writes.
Half covered: Edge → Fractional Kelly → Position Size + risk levels + DDD oracle
  discount + strategy families + monitoring/exit. (Kelly portfolio-allocation gap
  tracked separately; this confirms the Kelly STAGE from the design side.)
```

## Architecture context (why this matters)

In all four `edli_*` live stages (`edli_shadow_no_submit`,
`edli_submit_disabled_bridge`, `edli_live_canary`, `edli_live`) the daemon runs
the **EDLI event-reactor cycle** (`_edli_event_reactor_cycle`,
`src/main.py:3305`) and **NEVER calls `run_cycle()`**
(`src/engine/cycle_runner.py`). `run_cycle` is the legacy/design-faithful loop;
the entire design Kelly-multiplier stack, regime throttle, RED force-exit sweep,
and `force_exit_review` gate live inside `run_cycle` and are unreachable from the
EDLI path. The EDLI entry sizing flows through
`event_reactor_adapter.py:808-890` → `money_path_adapters.evaluate_kelly` →
`kelly.kelly_size`. The exit lane is the belt-and-suspenders
`_chain_sync_and_exit_monitor_cycle` (`src/main.py:4749`) which runs
`_execute_monitoring_phase` (NOT `run_cycle`), so it inherits the design
monitor/exit triggers BUT none of the risk-level sweep producers.

The design-faithful reference for what the live path *should* compose is
`evaluator.py:5807-6161` (full multiplier stack) + `cycle_runner.py:803-816`
(RED sweep) + `cycle_runner.py:351-398` (risk-level entry gate).

---

## Divergence table (ranked by live-money risk)

| # | Item | DESIGN (doc/AGENTS) | DESIGN-FAITHFUL evaluator | LIVE EDLI | DIVERGENCE | Severity | file:line |
|---|------|---------------------|---------------------------|-----------|------------|----------|-----------|
| **D1** | **RED risk → sweep active positions** | AGENTS.md:95 `RED = Cancel all pending, sweep all active`; AGENTS.md:152 "RED risk must cancel pending and sweep active" | `cycle_runner.py:803-816` `force_exit \| red_risk_sweep → _execute_force_exit_sweep` marks `exit_reason="red_force_exit"` + emits CANCEL proxies | **No sweep anywhere in EDLI cycle.** `get_current_level()` wired ONLY to GREEN entry gate + `evaluate_riskguard`. EDLI exit monitor (`_execute_monitoring_phase`) does NOT call the sweep — sweep lives only in `run_cycle` which EDLI never invokes. The `red_force_exit` marker the exit lane consumes is never produced for any EDLI position. | **CRITICAL** | sweep producer `cycle_runner.py:160,803-816`; EDLI risk wiring `main.py:3500,3533,3544`; reactor adapter `event_reactor_adapter.py:887-890` |
| **D2** | **Dynamic Kelly multiplier (risk-state inputs)** | risk_strategy_ref §3.2: base 0.25 reduced by CI width, lead time, win-rate, portfolio_heat, drawdown | `evaluator.py:5997-6011` calls `dynamic_kelly_mult(base, ci_width=ci_upper−ci_lower, lead_days, portfolio_heat=current_heat, city)`; then `× phase_aware × (1−ddd) × source_quality × risk_throttle` (`6103-6161`) | **FLAT** `settings["sizing"]["kelly_multiplier"]` via `_runtime_kelly_multiplier()` (`event_reactor_adapter.py:4360-4365`), then optional `_maybe_bias_decay_kelly_haircut` (×0.5 flag-gated, `3396-3484`). `dynamic_kelly_mult` is **never imported or called** in the reactor path. | **CRITICAL** | EDLI `event_reactor_adapter.py:815-827`; design `evaluator.py:5997`, `kelly.py:433` |
| **D3** | **ORANGE risk → exit at favorable prices** | AGENTS.md:94 `ORANGE = No new entries, exit at favorable prices` | ORANGE blocks entries (`cycle_runner.py:952`); favorable exits run through monitor/exit lane | **Entry-block only via fail-of-GREEN gate.** No ORANGE-specific favorable-exit trigger for EDLI positions. `continuous_redecision.py` exits are belief-driven (SD6/SD7), **zero** `RiskLevel`/ORANGE references — exit is not risk-level-aware. | **HIGH** | EDLI `continuous_redecision.py` (no RiskLevel refs); design ORANGE `cycle_runner.py:952` |
| **D4** | **Strategy-family routing** | AGENTS.md:130-141: 4 families (Settlement Capture / Shoulder Bin Sell / Center Bin Buy / Opening Inertia); `strategy_key` is "sole governance identity for attribution, risk policy" | `evaluator.py:2853-2965` assigns per-edge `strategy_key` via `_strategy_key_for`; drives `StrategyPolicy` (gated/exit_only/threshold_multiplier `6031-6107`), `strategy_kelly_multiplier`, `_source_quality_kelly_haircut`, entry-price floor | **FLATTENED.** EDLI hardcodes `strategy_key="entry_forecast"` at 2 persistence sites only (`event_reactor_adapter.py:4612,4717`). No family classification, no per-family alpha/risk policy, no per-family Kelly multiplier or gating. All candidates treated uniformly. | **HIGH** | EDLI `event_reactor_adapter.py:4612,4717`; design `evaluator.py:2853-2965,6031-6107` |
| **D5** | **DDD oracle-density discount** | oracle_density_ref §6: v2 Two-Rail — Rail 1 HALT if `cov<0.35 & elapsed>0.5`; Rail 2 `discount=min(0.09, 0.20×shortfall)` × small-sample amp; folded into Kelly | `evaluator.py:5807-5844,6118-6125`: `get_oracle_info` + `evaluate_ddd_for_decision` (`ddd_wiring.DDDFailClosed`); `km *= max(0, 1−ddd_discount)`; oracle penalty folded into phase_aware | **ABSENT.** EDLI imports neither `oracle_penalty` nor `ddd_wiring`. No Rail-1 HALT, no Rail-2 discount in the EDLI Kelly. A coverage outage (vendor stream death) does not down-size or halt an EDLI entry. | **HIGH** | EDLI path (no ddd/oracle import); design `evaluator.py:5807,5844,6118` |
| **D6** | **Regime / portfolio-heat throttle** | risk_strategy_ref §3.2 portfolio_heat + drawdown factors; design also throttles on gross/variance/heat saturation | `evaluator.py:5979-5988`: `risk_throttle ×0.5` per gross>0.10 / variance>0.10 / heat>0.25; applied as `km × risk_throttle` (`6161,6387`) | **ABSENT.** No `risk_throttle`, no `current_heat`, no cluster/gross/variance saturation in EDLI sizing. Concentrated cluster of EDLI entries gets no de-correlation haircut. | **MEDIUM-HIGH** | design `evaluator.py:5979-5988,6161` |
| **D7** | **YELLOW risk → no new entries (granularity)** | AGENTS.md:93 `YELLOW = No new entries, continue monitoring` | YELLOW handled identically to non-GREEN entry block (`cycle_runner.py:952`) | **Behaviorally MET** (any non-GREEN blocks entry via `riskguard_allows_new_entries` GREEN-only, `event_reactor_adapter.py:213-218` + `evaluate_riskguard` GREEN-only `money_path_adapters.py:113`). DATA_DEGRADED also correctly blocks. No divergence on entry-block; the gap is only the missing ORANGE/RED *active-position* actions (D1/D3). | LOW | EDLI `event_reactor_adapter.py:213-218`; `money_path_adapters.py:108-114` |
| **D8** | **Position-size caps** | bankroll_doctrine: on-chain wallet is sole bankroll truth; per-trade hard cap removed 2026-05-04 (risk_strategy_ref §3.1) — discipline lives in posture/RiskGuard/max-exposure | No per-trade cap in `kelly_size`; exposure discipline in posture/RiskGuard/max-exposure gates | EDLI uses `_runtime_bankroll_usd(cached_only=True)` (on-chain truth, doctrine-faithful) + `tiny_live_max_notional_usd=5.0` canary cap (`event_reactor_adapter.py:270`). The $5 cap is an EDLI-canary addition, NOT a design loss. **BUT** the design exposure-discipline layers (max-exposure, posture, per-cycle cluster heat) that *replace* the removed per-trade cap are the same layers absent in D6 — so post-canary, EDLI has no exposure ceiling beyond bankroll + GREEN gate. | MEDIUM (canary), HIGH (post-canary) | EDLI `event_reactor_adapter.py:270,811-814`; doctrine `config/settings.json::_bankroll_doctrine_2026_05_04` |
| **D9** | **Settlement follow-through / monitor exits** | AGENTS.md:39 `exit_triggers.py` monitoring + exits; settlement capture | `_execute_monitoring_phase` + `exit_triggers.py` (FLASH_CRASH_PANIC, VIG_EXTREME, RED_FORCE_EXIT consumers) | **PARTIALLY MET.** EDLI runs `_execute_monitoring_phase` via `_chain_sync_and_exit_monitor_cycle` (`main.py:4749,4830`), so FLASH_CRASH/VIG_EXTREME/belief-decay exits DO fire. `exit_order_submit_enabled` follows `real_order_submit_enabled`. The one consumer with no producer is `RED_FORCE_EXIT` (depends on D1's sweep marker). | MEDIUM | EDLI `main.py:4749-4830`; exit `exit_triggers.py:101,114`; consumer `cycle_runtime.py:179` |

---

## Dropped design risk-state Kelly inputs (D2 detail)

`dynamic_kelly_mult` (`kelly.py:433`) accepts 8 risk-state inputs. The EDLI flat
multiplier drops **all** of them:

| Input | Design effect | EDLI |
|-------|---------------|------|
| `ci_width` (edge CI) | ×0.7 (>0.10), ×0.35 (>0.15) | dropped |
| `lead_days` | ×0.6 (≥5d), ×0.8 (3-5d) | dropped |
| `rolling_win_rate_20` | ×0.5 (<0.40), ×0.7 (<0.45) | dropped |
| `portfolio_heat` | ×max(0.1, 1−heat) when >0.40 | dropped |
| `drawdown_pct/max_drawdown` | ×(1−dd/maxdd) | dropped |
| `city` asymmetric loss | per-city ×factor | dropped (bias-decay haircut is a different, coarser mechanism) |
| `phase_aware_factor` | strategy_phase × oracle × observed_fraction × phase_source | dropped |
| `ddd_discount` | ×(1−ddd) | dropped (D5) |
| `risk_throttle` | ×0.5/cluster saturation | dropped (D6) |

The only EDLI haircut is `_maybe_bias_decay_kelly_haircut` (flag-gated, binary
×0.5 on |bias|>threshold cities, `event_reactor_adapter.py:3396-3484`) — a coarse
proxy for the `city` asymmetric-loss factor, covering one of nine dropped inputs.

---

## 10-LINE VERDICT

1. **D1 (CRITICAL): RED never sweeps EDLI positions.** The `_execute_force_exit_sweep` + `get_force_exit_review` producers live ONLY in `run_cycle` (`cycle_runner.py:803-816`), which `edli_live` never calls. EDLI wires `get_current_level()` solely to a GREEN entry gate. A RED risk level blocks new entries but leaves all open live positions un-swept — the single highest live-capital exposure.
2. **D2 (CRITICAL): Kelly is a flat config constant.** `dynamic_kelly_mult` is never called in the reactor path; EDLI sizes on `settings["sizing"]["kelly_multiplier"]` × optional binary bias-decay. All 9 design risk-state Kelly inputs (CI width, lead, win-rate, heat, drawdown, phase, oracle, DDD, regime throttle) are dropped.
3. **D3 (HIGH): ORANGE has no favorable-exit behavior** for EDLI; exits are belief-driven only (`continuous_redecision.py` has zero `RiskLevel` references) — risk-level is invisible to the exit lane.
4. **D4 (HIGH): strategy-family flattening.** All EDLI candidates carry hardcoded `strategy_key="entry_forecast"`; the 4-family design (per-family alpha/risk policy, gating, Kelly multiplier, entry-price floor) is entirely bypassed.
5. **D5 (HIGH): DDD/oracle discount absent** — neither `oracle_penalty` nor `ddd_wiring` is imported in the EDLI path; a coverage outage neither halts (Rail 1) nor down-sizes (Rail 2) an EDLI entry.
6. **D6 (MED-HIGH): no regime/heat throttle** — concentrated EDLI clusters get no `risk_throttle` de-correlation haircut.
7. **D8 (MED→HIGH): post-canary exposure ceiling missing** — the `$5 tiny_live_max_notional` canary cap is doctrine-faithful, but once lifted, EDLI has no max-exposure/posture/cluster layer (the same layers absent in D6) that the design relies on after removing the per-trade Kelly cap.
8. **Entry-block side is HEALTHY:** GREEN-only gating (D7) correctly blocks YELLOW/ORANGE/RED/DATA_DEGRADED new entries — the divergences are all on the *active-position* and *sizing-magnitude* side, not entry admission.
9. **Exit plumbing partially survives (D9):** `_execute_monitoring_phase` runs for EDLI, so FLASH_CRASH/VIG_EXTREME/belief exits fire — but `RED_FORCE_EXIT` is a consumer with no EDLI producer (depends on D1).
10. **Root pattern (Fitz translation-loss):** EDLI re-implemented only the *admission* spine (FDR → Kelly-as-constant → GREEN gate) and lost the *magnitude* spine (dynamic multiplier, DDD, regime, strategy policy) and the *RED-action* spine (sweep). The fix is structural — route EDLI sizing through the evaluator multiplier stack and wire `risk_level==RED → sweep` into the EDLI cycle — not nine separate patches.

---

## References

- `src/events/money_path_adapters.py:79-114` — `evaluate_kelly` (takes `kelly_multiplier` param, no dynamic compute) + `evaluate_riskguard` (GREEN-only)
- `src/engine/event_reactor_adapter.py:213-218` — `riskguard_allows_new_entries` (GREEN-only entry gate)
- `src/engine/event_reactor_adapter.py:815-890` — live EDLI Kelly + risk call block (flat mult, no sweep)
- `src/engine/event_reactor_adapter.py:4360-4365` — `_runtime_kelly_multiplier` (flat config read)
- `src/engine/event_reactor_adapter.py:3396-3484` — `_maybe_bias_decay_kelly_haircut` (the only EDLI haircut)
- `src/engine/event_reactor_adapter.py:4612,4717` — hardcoded `strategy_key="entry_forecast"`
- `src/engine/evaluator.py:5807-6161` — design-faithful full multiplier stack (oracle, DDD, dynamic_kelly_mult, phase_aware, strategy_policy, source_quality, risk_throttle)
- `src/strategy/kelly.py:433` — `dynamic_kelly_mult` (8 risk-state inputs, none reached by EDLI)
- `src/engine/cycle_runner.py:160-310` — `_execute_force_exit_sweep` (RED sweep producer)
- `src/engine/cycle_runner.py:803-816` — RED sweep + force_exit_review trigger (run_cycle only)
- `src/engine/cycle_runner.py:351-398,952` — risk-level entry gate (design)
- `src/events/continuous_redecision.py` — EDLI exit screens (belief-driven; zero RiskLevel refs)
- `src/main.py:3186-3231` — EDLI cycle does NOT run refresh_global_allocator (documented gap)
- `src/main.py:4749-4830` — `_chain_sync_and_exit_monitor_cycle` runs `_execute_monitoring_phase` (not run_cycle)
- `AGENTS.md:86-153` — risk-level behavior table + strategy-family table (design law)
- `docs/reference/zeus_risk_strategy_reference.md:§3.2` — dynamic multiplier spec
- `docs/reference/zeus_oracle_density_discount_reference.md:§6` — DDD v2 Two-Rail spec
