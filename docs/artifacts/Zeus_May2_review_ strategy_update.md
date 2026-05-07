## 0. Executive verdict

**Draft strategy plan verdict: `REVISED`, not accepted.** `STRATEGIES_AND_GAPS.md` is directionally useful as a launch-audit hypothesis, but it over-trusts the existing four-key strategy catalog, under-separates model edge from executable edge, and treats lifecycle coverage as if it were strategy quality. The actual code confirms the four canonical strategy keys, but it also shows that native buy-NO economics, executable snapshot repricing, immutable quote evidence, exit lifecycle, and attribution reporting already impose constraints that the draft does not fully model. The branch containing the dossier is `pr37-followup-2026-05-02`, not `main`; it is three commits ahead of `main` and adds the two launch-audit docs plus small config/evaluator/ingest/test changes.

**Launch strategy verdict:** Zeus is not blocked because it lacks more alpha names; it is blocked because strategy identity, market phase, executable quote authority, buy-NO governance, exit/hold economics, and riskguard truth must be aligned before live expansion. The best launch design is **smaller and stricter than the draft suggests**.

**Active strategy verdict:** the current four canonical keys are code-confirmed, but they are **not the best unconditional live-active set**. `settlement_capture`, `center_buy`, and `opening_inertia` can be launch-live only under hard phase and executable-cost gates. `shoulder_sell` should be downgraded to `SHADOW_ONLY` until native NO execution evidence is promoted, because the code has native buy-NO scaffolding but the feature flags are off and the current reporting taxonomy cannot cleanly separate all inverse quadrants.

**Dormant strategy verdict:** `shoulder_buy` and `center_sell` are not merely “unwired.” They are **governance-unsafe without taxonomy changes**. The edge scanner can already produce buy-YES and native buy-NO hypotheses, but the canonical strategy set and classifiers collapse inverse quadrants into existing keys or fallback buckets. They should become `SHADOW_ONLY` after explicit taxonomy/reporting support, not live.

**Sizing verdict:** current sizing is strong at the executable-price boundary but still mostly generic. Launch should keep fractional Kelly as the core, but apply **strategy/phase/liquidity/time-to-resolution multipliers** and forbid Kelly on midpoint/current-price semantics. Bankroll truth now comes from the wallet bankroll provider; per-trade and smoke-test caps are not current launch rails.

**Exit verdict:** current exit has serious infrastructure, but exit policy must become **position-lot and risk-state aware**, with held-token bid/depth as the sell surface. Some hold-value gates are still zero-cost unless exit-cost flags are promoted, so launch must explicitly decide hold-to-resolution versus sell using bid/depth/fee/time/risk, not generic “current price.”

**Killswitch verdict:** global killswitch is necessary but insufficient. Zeus needs layered killswitches: global runtime, adapter/execution, strategy, market-family, city/date/metric, and position-lot. The current runtime already has several global gates, but `REMAINING_TASKS.md` documents a live P0 riskguard flapping issue that can probabilistically block entries.

**Best final launch strategy portfolio:**

| Strategy / role              |                                         Launch status | Live-money role                                                  |
| ---------------------------- | ----------------------------------------------------: | ---------------------------------------------------------------- |
| `settlement_capture`         |                   `LIVE_ALLOWED` after Day0 phase fix | Primary launch alpha: observation/settlement-convergence capture |
| `center_buy`                 |    `LIVE_ALLOWED` only in fresh forecast-update phase | Model-edge alpha on central bins                                 |
| `opening_inertia`            | `LIVE_ALLOWED` only in first-24h quote-verified phase | New-listing / early mispricing alpha                             |
| `shoulder_sell`              |                                         `SHADOW_ONLY` | Native buy-NO/tail-risk diagnostic until evidence promoted       |
| `shoulder_buy`               |                    `DORMANT_REDESIGN` → `SHADOW_ONLY` | Tail YES diagnostic; not live                                    |
| `center_sell`                |                    `DORMANT_REDESIGN` → `SHADOW_ONLY` | Center buy-NO diagnostic; not live                               |
| `middle_state_recheck`       |         `NEW_LAUNCH_REQUIRED` as shadow/coverage mode | Coverage and evidence collection, not new live alpha by default  |
| `price_drift_reaction`       |                                         `NEW_NOT_NOW` | High-upside event-driven improvement                             |
| `family_relative_mispricing` |                       `NEW_NOT_NOW` / shadow research | Complete-family relative mispricing                              |
| `risk_off_exit`              |                      `LIVE_ALLOWED` implicit strategy | Risk-management, not alpha                                       |
| `settlement_hold`            |                  `NEW_LAUNCH_REQUIRED` as exit policy | Hold/redeem versus sell decision                                 |

**Biggest live blocker:** strategy identity and executable economics can diverge at the exact point of live submission: a candidate can look positive in posterior space but fail after current ask/depth/fee/tick/slippage, or it can be attributed to a governance key that does not represent its actual quadrant. Polymarket’s own docs confirm that all orders are limit orders, market orders are marketable limit orders, order types differ materially, and orderbooks expose token-level bids/asks/tick/min size/hash; these are not optional details for Zeus. ([Polymarket Documentation][1])

**Biggest promotion blocker:** reports currently key realized edge and attribution drift around only four strategy keys. That makes dormant/inverse strategies impossible to promote cleanly if they are routed through `opening_inertia` or `shoulder_sell`.

**Biggest improvement opportunity:** event-driven `price_drift_reaction` plus complete-family relative mispricing, fed by market WebSocket orderbook/price events, is the highest-upside post-launch layer. Polymarket’s market WebSocket channel can stream book snapshots, price changes, last trades, top-of-book updates, new markets, market resolution, and tick-size changes, which is exactly the missing substrate for this improvement. ([Polymarket Documentation][2])

**Final decision sentence:** **launch Zeus with a smaller, phase-gated, executable-snapshot-authorized live portfolio; shadow all buy-NO inverse/tail expansion; add middle-state coverage as evidence, not live alpha; defer price-drift/family-relative strategies until quote-stream and reporting evidence are clean.**

---

## 1. Faithful parse of `STRATEGIES_AND_GAPS.md`

### §1 catalog

| Draft item               | Faithful extraction                                                                                                                                                                                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Active DiscoveryMode map | `OPENING_HUNT` every 15 min → `opening_inertia`; `UPDATE_REACTION` at 07:00/09:00/19:00/21:00 UTC → `center_buy`, `shoulder_sell`, or fallback `opening_inertia`; `DAY0_CAPTURE` every 15 min → `settlement_capture` when `hours_to_resolution < 6`. |
| Entry methods            | `ens_member_counting` for normal ensemble-derived entries; `day0_observation` for Day0.                                                                                                                                                              |
| Active strategy keys     | `opening_inertia`, `center_buy`, `shoulder_sell`, `settlement_capture`.                                                                                                                                                                              |
| Dormant strategies       | `shoulder_buy`, `center_sell`.                                                                                                                                                                                                                       |
| Sizing                   | Kelly core: `f* × kelly_mult × bankroll`; default dynamic Kelly multiplier 0.25; cluster exposure, portfolio heat, wallet bankroll, and executable-price gates bound entries.                                                                       |
| Exit                     | Eight automatic exit types: `RED_FORCE_EXIT`, `SETTLEMENT_IMMINENT`, `WHALE_TOXICITY`, `MODEL_DIVERGENCE_PANIC`, `FLASH_CRASH_PANIC`, `VIG_EXTREME`, `DAY0_OBSERVATION_REVERSAL`, `EDGE_REVERSAL`.                                                   |
| Killswitch               | Six global gates: heartbeat lost, WS gap, unknown side effects, reconcile findings, drawdown, reduce-only when risk level is not GREEN.                                                                                                              |

The draft explicitly frames itself as a design-decision dossier and routes non-strategy operations to `REMAINING_TASKS.md`.

### §2 lifecycle map

| Draft lifecycle claim         | Faithful extraction                                                                    |
| ----------------------------- | -------------------------------------------------------------------------------------- |
| Covered opening window        | First 24h after market open covered by `OPENING_HUNT`.                                 |
| Covered update window         | NWP-release windows at 07/09/19/21 UTC covered by `UPDATE_REACTION`.                   |
| Covered final window          | Final 6h before settlement covered by `DAY0_CAPTURE`.                                  |
| Missing middle                | Roughly 20h/day where no active strategy fires unless opening or final window applies. |
| Missing inverse edge space    | `center_buy` and `shoulder_sell` active; `shoulder_buy` and `center_sell` dormant.     |
| Missing price-event reaction  | No strategy wakes up on rapid Polymarket price drift.                                  |
| Missing 24h-before-settlement | Window from 24h before settlement to 6h before settlement not continuously covered.    |

### §3 six gaps

| Gap                                 | Symptom                                                       | Decision required                                                                                                                    | Draft dependency                                        |
| ----------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------- |
| §3.1 Day0 narrow window             | `DAY0_CAPTURE` only fires within 6h of settlement.            | Define Day0 as 24h before UMA settlement versus local target-date boundary; decide coexistence with update reaction; decide cadence. | Draft says depends on §3.4 for final-hour acceleration. |
| §3.2 Middle-state vacuum            | Middle lifecycle covered only by four cron times.             | Add `MIDDLE_STATE_HUNT`, intervalize `UPDATE_REACTION`, or rely on price events.                                                     | Precedes §3.3 and §3.6.                                 |
| §3.3 Update cron should be interval | Fixed UTC releases create dead windows.                       | If middle mode exists, delete/reduce update; otherwise intervalize.                                                                  | Depends on §3.2.                                        |
| §3.4 No price-drift reaction        | No event-driven re-evaluation on CLOB price movement.         | Define threshold, scope, cooldown, composition.                                                                                      | Draft says independent.                                 |
| §3.5 Dormant inverse pair           | `shoulder_buy` and `center_sell` referenced but not produced. | Determine whether intentionally disabled; if wired, define EV thresholds.                                                            | Draft says independent.                                 |
| §3.6 Opening window narrow          | 15m interval changed but 24h cutoff may be too narrow.        | Widen or leave; depends on middle-state decision.                                                                                    | Depends on §3.2.                                        |

### §4 dependency order

Draft graph:

```text
§3.2 MIDDLE_STATE_HUNT
    ├── precedes §3.3 UPDATE_REACTION cron→interval
    └── precedes §3.6 OPENING_HUNT window
§3.1 DAY0_CAPTURE 24h window — independent
§3.4 PRICE_DRIFT_REACTION — independent
§3.5 dormant pairs — independent

Operator decision order:
1. §3.1
2. §3.2
3. §3.5
4. §3.4
5. §3.3 + §3.6
```

### §5 out-of-scope routing

The draft routes these to `REMAINING_TASKS.md`: data-ingest resilience, riskguard DB/proxy errors, wallet-bankroll authority, TIGGE historical backfill, PhysicalBounds/ExpiringAssumption follow-ups. Current code resolves the former config-bankroll fiction through the wallet bankroll provider; TIGGE backfill remains historical-calibration work rather than live-blocking strategy work.

---

## 2. Draft-as-hypothesis challenge ledger

| Draft claim                                             |   Section | Assumption embedded                                                     | Code validation status                                                                                                                               | Market validation status                                                                                          | Strategic verdict           | Preserve / revise / reject / expand | Reason                                                                                                                 |
| ------------------------------------------------------- | --------: | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Four active strategy keys exist.                        |      §1.1 | The live catalog is exactly four keys.                                  | `CONFIRMED`: `KNOWN_STRATEGIES` and `CANONICAL_STRATEGY_KEYS` include only `settlement_capture`, `shoulder_sell`, `center_buy`, `opening_inertia`.   | `PARTIALLY_CONFIRMED`: four keys exist, but keys are too coarse for market reality.                               | `INCOMPLETE`                | Revise                              | The code can generate buy-NO hypotheses and full-family scans, but governance/reporting only recognizes four keys.     |
| `OPENING_HUNT` is 15m and first-24h.                    |      §1.1 | Opening mispricing is confined to first 24h.                            | `CONFIRMED`: config 15m and `MODE_PARAMS` max 24h/min 24h-to-resolution.                                                                             | `PARTIALLY_CONFIRMED`: early liquidity is special, but not all early quotes are executable.                       | `INCOMPLETE`                | Preserve with stricter gates        | Keep 24h for launch; widening is improvement after middle-mode evidence.                                               |
| `UPDATE_REACTION` cron is NWP-release bound.            | §1.1/§3.3 | Forecast release windows are the only valid model-update opportunities. | `CONFIRMED`: configured four UTC cron slots.                                                                                                         | `MISLEADING`: prices move between releases; stale forecasts can still be rechecked but not treated as new signal. | `BETTER_ALTERNATIVE_EXISTS` | Expand                              | Add middle quote-recheck/shadow; do not blindly intervalize model-entry on stale forecasts.                            |
| `DAY0_CAPTURE` is final 6h.                             | §1.1/§3.1 | Day0 means near-settlement, not local target-day.                       | `CONFIRMED`: enum comment and `MODE_PARAMS` max 6h.                                                                                                  | `MISLEADING`: weather Day0 should be local-day/observation semantics, not just UMA resolution proximity.          | `LIVE_BLOCKER`              | Revise                              | Split Day0 forecast-convergence from observed settlement capture.                                                      |
| Two entry methods are wired.                            |      §1.2 | Entry method coverage is enough.                                        | `CONFIRMED`: evaluator/monitor distinguish ensemble and day0 observation paths.                                                                      | `PARTIALLY_CONFIRMED`: methods must be phase-authorized and source-fresh.                                         | `INCOMPLETE`                | Expand                              | Add data freshness, local-day, observation authority, and entry-method/report cohort gates.                            |
| Kelly + generic exposure gates are sufficient sizing.    |      §1.3 | Generic Kelly can size all strategies.                                  | `PARTIALLY_CONFIRMED`: typed executable-price Kelly exists; risk limits generic.                                                                     | `MISLEADING`: strategy phase and liquidity change risk.                                                           | `LIVE_BLOCKER`              | Revise                              | Add strategy/phase/liquidity/time multipliers; keep wallet/RiskGuard exposure authority.                               |
| Exit is fully automatic.                                |      §1.4 | Having triggers means exit is strategy-safe.                            | `PARTIALLY_CONFIRMED`: exit triggers/lifecycle exist and are sophisticated.                                                                          | `INCOMPLETE`: hold-to-resolution economics and held-token depth must be explicit.                                 | `LIVE_BLOCKER`              | Expand                              | Exit must be lot/risk-state aware with bid/depth/fee/time.                                                             |
| Six global killswitch gates are enough.                 |      §1.5 | Failure is global.                                                      | `PARTIALLY_CONFIRMED`: runtime has global governors and reduce-only.                                                                                 | `MISLEADING`: failures can be strategy-, family-, metric-, or adapter-local.                                      | `LIVE_BLOCKER`              | Expand                              | Add local killswitch tiers.                                                                                            |
| Lifecycle vacuum is primarily “20h/day no strategy.”    |   §2/§3.2 | Coverage frequency equals opportunity.                                  | `CONFIRMED` as schedule shape.                                                                                                                       | `MISLEADING`: not every hour has fresh signal; continuous trading on stale forecasts can be fake edge.            | `BETTER_ALTERNATIVE_EXISTS` | Revise                              | Add middle quote-recheck/shadow and only allow live entries on fresh data or sufficiently new executable quote change. |
| Dormant inverse pair means half edge space is unworked. |      §3.5 | Wiring names doubles alpha.                                             | `PARTIALLY_CONFIRMED`: scan supports directions; classifier/reporting do not.                                                                        | `MISLEADING`: inverse pair has different calibration/execution risk.                                              | `LIVE_BLOCKER`              | Revise                              | Shadow first; explicit taxonomy before live.                                                                           |
| Price-drift reaction independent.                       |   §3.4/§4 | Event-driven re-eval can be designed independently.                     | `REVIEW_REQUIRED`: user-channel exists; market-channel implementation not verified in code. Draft claims WS pattern exists.                          | `PARTIALLY_CONFIRMED`: official market WS supports price/book events. ([Polymarket Documentation][2])             | `IMPROVEMENT`               | Revise                              | High-upside, but not launch-critical until quote snapshots/reporting can prove causality.                              |
| §3.1 before §3.2 before §3.5 before §3.4.               |        §4 | Operator decisions can proceed by mode windows first.                   | `PARTIALLY_CONFIRMED`                                                                                                                                | `MISLEADING`                                                                                                      | `BETTER_ALTERNATIVE_EXISTS` | Reject draft order                  | Strategy identity/evidence lock must precede inverse wiring and mode expansion.                                        |
| Out-of-scope routing is non-strategy.                   |        §5 | Ops blockers are separable.                                             | `PARTIALLY_CONFIRMED`: docs route them; riskguard P0 still blocks live entries.                                                                      | `PARTIALLY_CONFIRMED`                                                                                             | `INCOMPLETE`                | Preserve with caveat                | Some non-strategy items are launch blockers even if not strategy-design work.                                          |

---

## 3. Actual repo strategy reconstruction

| Strategy key / implicit strategy     | Doc status             | Code status                                             | Files/functions                                                                                                                                                | Runtime reachability                                                                                           | Inputs                                                                                              | Outputs                                                    | Sizing path                                           | Exit path                                                                | Killswitch interaction                                                             | Reporting path                                                 | Live/shadow/dormant status             | Verdict                                                                                                         |
| ------------------------------------ | ---------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- | -------------------------------------------------------------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `opening_inertia`                    | Active                 | Canonical key                                           | `cycle_runner.KNOWN_STRATEGIES`; `_classify_edge_source`; `MODE_PARAMS.OPENING_HUNT`; evaluator `_strategy_key_for`.                                           | Reachable in `OPENING_HUNT`; also fallback in update mode.                                                     | Fresh market, ensemble probability, executable YES/NO quotes, calibration, FDR.                     | `EdgeDecision.strategy_key=opening_inertia`; order intent. | Generic executable-price Kelly with downstream exposure gates. | Generic exit triggers; opening timeout 4h.                         | Blocked by risk level, freshness, heartbeat, WS, posture, governor.                | Included in four-key realized-edge and drift reporting.        | `LIVE_ALLOWED` only opening-phase      | Keep live only for first-24h quote-verified entries; forbid update fallback from hiding unclassified quadrants. |
| `center_buy`                         | Active                 | Canonical key                                           | `cycle_runner._classify_edge_source`; evaluator `_strategy_key_for`; `MarketAnalysis.find_edges`.                                                              | Reachable in `UPDATE_REACTION` when buy-YES non-shoulder.                                                      | Fresh forecast, central bin, YES quote, FDR, executable snapshot.                                   | Buy YES central bin.                                       | Generic Kelly; ultra-low price block if entry ≤0.02.  | Generic buy-YES exit.                                                    | Same global gates; stale forecast evidence rejects entry.                          | Four-key reporting.                                            | `LIVE_ALLOWED` only fresh-update phase | Good launch canary if executable ask/depth and forecast causality pass.                                         |
| `shoulder_sell`                      | Active in draft        | Canonical key but execution-gated by native NO evidence | `cycle_runner._classify_edge_source` for buy-NO shoulder; evaluator variant labels any shoulder as `shoulder_sell`; `MarketAnalysis.supports_buy_no_edges`.    | Reachable when native NO supported and flags/path allow; live flag default false for native multi-bin buy-NO.  | Native NO token quote, buy-NO bootstrap, tail calibration, executable depth.                        | Buy NO shoulder.                                           | Generic Kelly; buy-NO sizing not strategy-specific.   | Buy-NO exit path has special thresholds and near-settlement hold logic.  | Native buy-NO feature flags and global gates.                                      | Four-key reporting but cannot split `shoulder_buy` collisions. | `SHADOW_ONLY`                          | Downgrade from live until native NO shadow evidence, taxonomy, and exit-cost gates are promoted.                |
| `settlement_capture`                 | Active                 | Canonical key                                           | `DiscoveryMode.DAY0_CAPTURE`; evaluator observation gates; monitor Day0 refresh.                                                                               | Reachable only `hours_to_resolution < 6` currently.                                                            | Day0 observation, city settlement source, local metric, remaining ensemble extrema, quote snapshot. | Day0 observation entry.                                    | Generic Kelly at executable price, then exposure gates. | Day0 observation reversal plus generic exit.                           | Freshness gate can short-circuit Day0; observation source/age gates fail closed.   | Four-key reporting.                                            | `LIVE_ALLOWED` after phase fix         | Best launch alpha, but current 6h window is wrong and must become local-day/observation-phase aware.            |
| `shoulder_buy`                       | Dormant in draft       | Not canonical                                           | Scanner can produce buy-YES shoulder, but evaluator/cycle classifiers collapse shoulders to `shoulder_sell`.                                                   | Economically reachable as buy-YES shoulder but not governed as its own key.                                    | Tail bin YES quote, model tail probability, tail calibration.                                       | Currently misclassified if accepted.                       | Generic Kelly if not blocked.                         | Buy-YES exit.                                                            | No strategy-level kill.                                                            | No reporting cohort.                                           | `DORMANT_REDESIGN`                     | Do not live-wire; create explicit key as shadow only with harsher threshold.                                    |
| `center_sell`                        | Dormant in draft       | Not canonical                                           | Scanner can produce buy-NO central if native NO quote exists; classifier fallback may become `opening_inertia`.                                                | Economically reachable as buy-NO non-shoulder, but not governed.                                               | Native NO quote, central bin overpricing, NO depth.                                                 | Currently fallback/mis-attributed.                         | Generic Kelly if not blocked.                         | Buy-NO exit.                                                             | Native NO flags off.                                                               | No clean reporting cohort.                                     | `DORMANT_REDESIGN`                     | Shadow only after taxonomy/reporting and native NO evidence.                                                    |
| Full-family FDR selection            | Documented indirectly  | Active implicit strategy-control                        | `scan_full_hypothesis_family`, `apply_familywise_fdr`, family IDs.                                                                                             | Used by evaluator path.                                                                                        | All tested bin/direction hypotheses.                                                                | Candidate accepted/rejected post-FDR.                      | Pre-sizing selection control.                         | None direct.                                                             | Not killswitch.                                                                    | Logs family facts.                                             | `LIVE_ALLOWED` as control              | Keep; ensure family IDs include metric/phase and do not shrink tested family.                                   |
| Executable snapshot repricing        | Not framed as strategy | Active implicit execution strategy                      | `_reprice_decision_from_executable_snapshot`; `snapshot_repo`.                                                                                                 | Entry path before submit.                                                                                      | Immutable CLOB snapshot, top bid/ask, depth, tick, fee.                                             | Repriced edge, size, final intent or shadow.               | Recomputes Kelly at executable snapshot VWMP/ask.     | Enables executable proof.                                                | Snapshot freshness gates.                                                          | Evidence sidecar.                                              | `LIVE_ALLOWED` as gate                 | Make mandatory for all live strategies.                                                                         |
| Risk-off / RED force exit            | Out of catalog         | Active implicit risk-management strategy                | `cycle_runner._execute_force_exit_sweep`; exit lifecycle.                                                                                                      | Runtime when RED or force-review.                                                                              | Portfolio positions, command truth, risk state.                                                     | Exit reason, cancel proxy, sell lifecycle.                 | No entry sizing.                                      | Core exit path.                                                          | Global riskguard.                                                                  | State events.                                                  | `LIVE_ALLOWED`                         | Promote as first-class strategy role: risk-management, not alpha.                                               |
| Settlement hold / hold-to-resolution | Under-specified        | Partial implicit exit behavior                          | Exit triggers EV gates; settlement lifecycle.                                                                                                                  | Exit decision time.                                                                                            | Held-token bid, posterior, hours to settlement, fee/time cost.                                      | Hold, sell, backoff, settle/redeem.                        | None.                                                 | Core.                                                                    | Risk-state overrides.                                                              | Position events.                                               | `NEW_LAUNCH_REQUIRED`                  | Define explicit hold/redeem policy before expanding Day0.                                                       |
| Price drift reaction                 | Draft gap              | Not verified as implemented                             | Draft mentions user WS; market WS path not found in fetched code. Official Polymarket market WS exists. ([Polymarket Documentation][2])                        | `REVIEW_REQUIRED` / absent as strategy mode.                                                                   | Market-channel price/book deltas.                                                                   | Triggered re-evaluation.                                   | Requires event-driven sizing.                         | Requires stale/cancel policy.                                            | Needs flood killswitch.                                                            | No cohort.                                                     | `NEW_NOT_NOW`                          | High upside, not launch blocker.                                                                                |
| Family-relative mispricing           | Missing                | Partial support via `MarketPriorDistribution` shadow    | `market_fusion.MarketPriorDistribution`, `YES_FAMILY_DEVIG_SHADOW_MODE`.                                                                                       | Shadow/research only.                                                                                          | Complete family, quote hashes, vig treatment, liquidity/freshness.                                  | Market prior / relative signal.                            | Needs family depth sizing.                            | Needs family exit.                                                       | Family killswitch.                                                                 | No live cohort.                                                | `NEW_NOT_NOW`                          | Do not launch-live; build shadow evidence.                                                                      |

---

## 4. First-principles market opportunity map

| Market lifecycle window             | Plausible inefficiency                                                                     | Data required                                                                                       | Executable evidence required                                                                                                                                                                                               | Strategy family                      | Risk                                                           | Launch suitability                                                                             |
| ----------------------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 1. Market discovery / early listing | Thin/opening books may anchor to stale priors or retail guesses.                           | Gamma market identity, city/date/metric, settlement semantics, forecast snapshot.                   | Token-level orderbook with bid levels, ask levels, depth, tick size, minimum order size, and order-acceptance status. Polymarket docs expose token identifiers and venue metadata for this check. ([Polymarket Documentation][2]) | `opening_inertia`                    | Fake edge from wide spreads, stale listings, LP traps.         | `LIVE_ALLOWED` with tight quote/depth gates. **(fix: PR #44 — hard reject when sweep.filled_shares < submitted_shares)** |
| 2. Early forecast uncertainty       | Model has more calibrated distribution than market, but uncertainty is high.               | Ensemble, Platt calibration, lead time, model agreement.                                            | YES/NO native executable cost after fee/depth.                                                                                                                                                                             | model-edge                           | Calibration overconfidence; tail bins.                         | `SHADOW_ONLY` except strict `opening_inertia` canary.                                          |
| 3. Medium-range forecast update     | Fresh NWP changes not fully priced.                                                        | Fresh forecast evidence with issue/fetch/available timestamps.                                      | Quote snapshot after forecast availability and before submit.                                                                                                                                                              | `center_buy`, shadow `shoulder_sell` | Cron windows miss late market moves; stale forecast reuse.     | `center_buy LIVE_ALLOWED`; buy-NO shadow.                                                      |
| 4. Pre-event convergence            | Forecast confidence rises; market may lag.                                                 | Short-lead ensemble, local-day semantics.                                                           | Depth and spread; time-to-settlement.                                                                                                                                                                                      | Day0 convergence                     | Confusing target-day event with UMA resolution time.           | `SHADOW_ONLY` unless observation-proven.                                                       |
| 5. Day-0 / event-day information    | Observed high/low-so-far plus remaining hours can dominate market.                         | Authorized observation source, source timestamp, local diurnal context, remaining ensemble extrema. | Current bid/ask/depth, settlement-source compatibility.                                                                                                                                                                    | `settlement_capture`                 | Observation certainty ≠ settlement certainty; source mismatch. | `LIVE_ALLOWED` after Day0 phase fix.                                                           |
| 6. Near-close stale-book            | Resting quotes stale versus near-certain outcome.                                          | Observation/final forecast, market close/resolution clock.                                          | Immediate fill or FAK/FOK depth; cancel/fill truth.                                                                                                                                                                        | near-close stale-book                | Cancel race, partial fill, tick changes, resolution halt.      | `SHADOW_ONLY` until fill evidence; subset of `settlement_capture` live only with strict gates. |
| 7. Post-event pre-resolution        | Market may discount certain settlement because capital locked or uncertainty about oracle. | Final observation/settlement source, UMA timing, market status.                                     | Held-token bid/depth; redeem timing.                                                                                                                                                                                       | settlement-hold / capture            | Wrong settlement source; locked capital; resolution delay.     | `LIVE_ALLOWED` as hold policy, not necessarily new entry.                                      |
| 8. Settlement / hold-to-resolution  | Selling may be inferior to redemption.                                                     | Settlement confidence, bid, fee, time cost, bankroll heat.                                          | Held-token bid/depth and settlement status.                                                                                                                                                                                | settlement-hold                      | Premature exit at discount.                                    | `NEW_LAUNCH_REQUIRED` exit policy.                                                             |
| 9. Emergency de-risk / exit         | Riskguard/adapters may fail locally or globally.                                           | Runtime risk state, command truth, chain positions.                                                 | Sell-token snapshot and command capability.                                                                                                                                                                                | risk-off exit                        | Global vs local halt mismatch; unknown side effects.           | `LIVE_ALLOWED` risk-management.                                                                |
| 10. Report / learning / promotion   | Shadow/live evidence identifies robust cohorts.                                            | Canonical events, strategy_key, metric, phase, fill truth, settlement truth.                        | Executable snapshot hashes and order/fill status.                                                                                                                                                                          | diagnostic/promotion                 | Cohort contamination, legacy diagnostic evidence promoted as live.        | `NEW_LAUNCH_REQUIRED` evidence policy.                                                         |

---

## 5. Candidate strategy family analysis

### 5.1 Pure model-edge strategy

| Field                   | Design                                                                                                                                                                                   |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Thesis                  | Trade when calibrated model posterior exceeds **executable** cost after fee, spread, slippage, depth, tick, and order policy.                                                            |
| Real market edge source | Market underreacts to forecast distributions, especially central bins after fresh NWP updates.                                                                                           |
| Repo support            | `MarketAnalysis`, `compute_alpha`, model-only posterior, bootstrap CI, FDR, typed Kelly, executable snapshot repricing.                                                                  |
| Missing components      | Strategy/phase-aware sizing; stale forecast throttle; reporting split by phase and posterior mode.                                                                                       |
| Execution constraints   | Must buy at ask/depth or marketable limit; midpoint/current display is not executable. Official docs distinguish best ask for BUY and best bid for SELL. ([Polymarket Documentation][2]) |
| Sizing requirements     | Kelly only after executable repricing; smaller multipliers for long lead or wide CI.                                                                                                     |
| Exit requirements       | Recompute with same entry method; sell only against held-token bid/depth; hold if settlement EV superior.                                                                                |
| Killswitch requirements | Forecast stale, quote stale, calibration unverified, riskguard not GREEN, snapshot mismatch.                                                                                             |
| Evidence requirements   | Forecast causality envelope, executable snapshot, FDR family, fill status, settlement outcome.                                                                                           |
| Launch verdict          | `LIVE_ALLOWED` for `center_buy` fresh-update only; not blanket middle-state entries.                                                                                                     |

### 5.2 Family-relative mispricing strategy

| Field                   | Design                                                                                                                              |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Thesis                  | Compare all bins in a complete market family and exploit relative misalignment after devig/liquidity filters.                       |
| Real market edge source | Retail/liquidity imbalance across adjacent bins, stale tail bins, family sum inconsistencies.                                       |
| Repo support            | Full-family scan and `MarketPriorDistribution` with family completeness, quote hashes, vig treatment, freshness/liquidity status.   |
| Missing components      | Complete family identity, robust devig estimator, depth-weighted family liquidity, live promotion evidence.                         |
| Execution constraints   | Cannot assume NO from YES complement; must use native executable token side.                                                        |
| Sizing requirements     | Family-level exposure and correlated bin caps.                                                                                      |
| Exit requirements       | Family drift monitor and held-token bid.                                                                                            |
| Killswitch requirements | Family incomplete, sibling stale, quote hash mismatch, vig outlier.                                                                 |
| Evidence requirements   | Complete family snapshots, all tested hypotheses, orderbook hashes, shadow-vs-live cohorts.                                         |
| Launch verdict          | `NEW_NOT_NOW`; shadow after launch spine.                                                                                           |

### 5.3 Day-0 / event-day convergence strategy

| Field                   | Design                                                                                                                                                                     |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Thesis                  | Use authorized real-time observation plus remaining-day forecast to capture event-day market lag.                                                                          |
| Real market edge source | Market lags high/low-so-far and remaining-day physical constraints.                                                                                                        |
| Repo support            | Day0 observation source/quality gates, `Day0Router`, monitor Day0 refresh, settlement semantics.                                                                           |
| Missing components      | Correct Day0 phase definition; split forecast-day0 from observed settlement capture; final-hour cadence.                                                                   |
| Execution constraints   | Marketable limit/FAK/FOK preferred near close; stale bids/asks can vanish. Polymarket FOK and FAK execute immediately, while GTC/GTD rest. ([Polymarket Documentation][1]) |
| Sizing requirements     | High confidence but high execution/cancel risk; cap until fill evidence.                                                                                                   |
| Exit requirements       | Observation reversal, hold-to-resolution, settlement-source uncertainty.                                                                                                   |
| Killswitch requirements | Observation stale, source unauthorized, local-day mismatch, settlement source mismatch.                                                                                    |
| Evidence requirements   | Observation timestamp/source, city local time, remaining hours, executable snapshot, outcome settlement.                                                                   |
| Launch verdict          | `settlement_capture LIVE_ALLOWED` after phase fix; Day0 forecast-only portion `SHADOW_ONLY`.                                                                               |

### 5.4 Near-close stale-book strategy

| Field                   | Design                                                                                                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Thesis                  | Capture stale resting quotes immediately before close/resolution when outcome is near-certain.                                  |
| Real market edge source | Resting orders not cancelled in time; market-maker latency.                                                                     |
| Repo support            | Day0 loop, executable snapshot, mode timeout, final-intent machinery.                                                           |
| Missing components      | Market-channel quote staleness, cancel-race model, partial-fill handling in evidence cohorts.                                   |
| Execution constraints   | Requires immediate order type and depth. Passive GTC is unsafe because fill is not guaranteed and may become adverse selection. |
| Sizing requirements     | Hard cap; FAK preferred over FOK if partial fill acceptable.                                                                    |
| Exit requirements       | Usually hold/redeem; do not sell at discounted bid unless risk state forces.                                                    |
| Killswitch requirements | Tick-size change, WS gap, delayed/unmatched orders, resolution halt.                                                            |
| Evidence requirements   | Exact quote age, order type, partial fill, cancel/fill status.                                                                  |
| Launch verdict          | `SHADOW_ONLY` as separate family; can be a strict subcase of `settlement_capture` only with FAK/FOK/depth proof.                |

### 5.5 Liquidity/spread-aware strategy

| Field                   | Design                                                                                                                                                                     |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Thesis                  | Avoid bad trades or selectively take liquidity only when executable edge survives spread/depth/fee.                                                                        |
| Real market edge source | Not alpha by itself; prevents fake alpha.                                                                                                                                  |
| Repo support            | Executable snapshots, VWMP, depth sweep, slippage checks, fee details.                                                                                                     |
| Missing components      | Consistent per-strategy liquidity thresholds and maker/passive policy.                                                                                                     |
| Execution constraints   | Polymarket exposes tick/min order/depth/hash and fee rates; taker fees vary by market category and Weather fee rate is documented as 0.05. ([Polymarket Documentation][2]) |
| Sizing requirements     | Size cannot exceed depth at positive-edge limit.                                                                                                                           |
| Exit requirements       | Held-token bid/depth mandatory.                                                                                                                                            |
| Killswitch requirements | Snapshot stale, spread too wide, depth insufficient, fee unavailable.                                                                                                      |
| Evidence requirements   | Quote hash, sweep payload, final intent.                                                                                                                                   |
| Launch verdict          | `LIVE_ALLOWED` as mandatory gate, not independent alpha.                                                                                                                   |

### 5.6 Risk-off / exit / de-risk strategy

| Field                   | Design                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------- |
| Thesis                  | Manage existing positions under updated risk, quote, and settlement state.            |
| Real market edge source | None; capital preservation.                                                           |
| Repo support            | RED sweep, exit lifecycle, collateral checks, pending-exit recovery, command truth.   |
| Missing components      | Strategy-aware/local killswitch routing and hold-to-resolution policy.                |
| Execution constraints   | Sell requires held token inventory, current snapshot, and live command truth.         |
| Sizing requirements     | Not applicable to entries; exit size = held lot shares.                               |
| Exit requirements       | Core strategy itself.                                                                 |
| Killswitch requirements | Global RED, strategy pause, adapter fault, position quarantine.                       |
| Evidence requirements   | Exit intent, order posted, fill, retry/backoff, settlement.                           |
| Launch verdict          | `LIVE_ALLOWED`; first-class risk-management role.                                     |

### 5.7 Settlement-hold strategy

| Field                   | Design                                                                          |
| ----------------------- | ------------------------------------------------------------------------------- |
| Thesis                  | Decide whether to hold to resolution/redeem versus sell now.                    |
| Real market edge source | Avoid selling winning tokens at a discount when settlement probability is high. |
| Repo support            | Settlement lifecycle, exit EV gate, monitor quote refresh.                      |
| Missing components      | Fee/time/crowding-cost flag promotion; strategy-specific hold thresholds.       |
| Execution constraints   | Sell value is held-token bid, not midpoint.                                     |
| Sizing requirements     | Entry sizing not applicable; affects capital lock.                              |
| Exit requirements       | Hold/redeem decision based on bid, probability, time, risk.                     |
| Killswitch requirements | RED can override hold; source uncertainty can force review.                     |
| Evidence requirements   | Bid/depth, estimated redemption value, settlement status.                       |
| Launch verdict          | `NEW_LAUNCH_REQUIRED` as exit policy.                                           |

### 5.8 Dormant/research strategies

| Field                   | Design                                                               |
| ----------------------- | -------------------------------------------------------------------- |
| Thesis                  | Strategies with plausible edge but insufficient governance/evidence. |
| Real market edge source | Tail mispricing, inverse center overpricing, family misalignment.    |
| Repo support            | Scanners and hypotheses exist; canonical keys/reporting do not.      |
| Missing components      | Keys, gates, evidence cohorts, native NO feature promotion.          |
| Execution constraints   | Especially native NO depth and quote freshness.                      |
| Sizing requirements     | Zero live size until promotion.                                      |
| Exit requirements       | Direction-specific exits.                                            |
| Killswitch requirements | Strategy-specific pause.                                             |
| Evidence requirements   | Shadow-only matrix.                                                  |
| Launch verdict          | `DORMANT_REDESIGN` → `SHADOW_ONLY`; not live.                        |

### 5.9 Diagnostic/shadow-only strategies

| Field                   | Design                                                                  |
| ----------------------- | ----------------------------------------------------------------------- |
| Thesis                  | Collect evidence without risking live money.                            |
| Real market edge source | Same as candidate alpha, but no live authority.                         |
| Repo support            | Shadow flags, report modules, immutable snapshots.                      |
| Missing components      | Explicit shadow/live cohort rules and no accidental promotion.          |
| Execution constraints   | Shadow must still cite executable snapshots; no simulated midpoint fantasy. |
| Sizing requirements     | Compute would-size, but live size = 0.                                  |
| Exit requirements       | Simulated/diagnostic only; no live exit assumption.                     |
| Killswitch requirements | Promotion gate.                                                         |
| Evidence requirements   | Must label as diagnostic, not live.                                     |
| Launch verdict          | Required for dormant inverse, middle, price-drift, family-relative.     |

---

## 6. Current strategies vs best possible strategy set

| Item                                 |  Current status | Best action                                           | Reason                                                                          |
| ------------------------------------ | --------------: | ----------------------------------------------------- | ------------------------------------------------------------------------------- |
| `opening_inertia`                    |          Active | Keep as `LIVE_ALLOWED` only in opening phase          | Opening liquidity can be exploitable, but fallback use masks taxonomy problems. |
| `center_buy`                         |          Active | Keep as `LIVE_ALLOWED` only in fresh update phase     | Best current model-edge launch candidate; must clear executable repricing.      |
| `shoulder_sell`                      | Active in draft | Downgrade to `SHADOW_ONLY`                            | Native buy-NO feature flags are off, and inverse/tail reporting is not clean.   |
| `settlement_capture`                 |          Active | Keep as `LIVE_ALLOWED` after Day0 phase correction    | Highest real launch value; current 6h window is too narrow/misframed.           |
| `shoulder_buy`                       |         Dormant | `DORMANT_REDESIGN` → `SHADOW_ONLY`                    | Tail YES calibration and taxonomy risk.                                         |
| `center_sell`                        |         Dormant | `DORMANT_REDESIGN` → `SHADOW_ONLY`                    | Native NO center strategy needs explicit key and evidence.                      |
| Update fallback to `opening_inertia` |        Implicit | Quarantine or relabel as `UNCLASSIFIED_SHADOW`        | Fallback can hide unrepresented strategies.                                     |
| Executable snapshot repricing        |        Implicit | Preserve and make mandatory                           | Prevents posterior-only edge from becoming trade.                               |
| Risk-off exit                        |        Implicit | Promote as risk-management role                       | It affects capital and should be auditable separately from alpha.               |
| Settlement hold                      |         Partial | Add before launch                                     | Day0 expansion is unsafe without hold/redeem policy.                            |
| `MIDDLE_STATE_HUNT`                  |         Missing | Add as shadow/coverage mode, not immediate live alpha | Solves coverage evidence without stale-forecast overtrading.                    |
| `PRICE_DRIFT_REACTION`               |         Missing | `NEW_NOT_NOW`                                         | Requires market WS, quote snapshots, flood control, and event causality.        |
| Family-relative mispricing           |         Missing | `NEW_NOT_NOW` / shadow research                       | Requires complete-family identity and devig validation.                         |

---

## 7. Six design gaps tribunal

### §3.1 Gap: `DAY0_CAPTURE` narrow window

| Field                 | Tribunal                                                                                                                                                                                                        |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Original symptom      | `DAY0_CAPTURE` only fires when `hours_to_resolution < 6`.                                                                                                                                                       |
| Deeper market meaning | The code confuses “near UMA resolution” with “weather event day / local physical observation period.”                                                                                                           |
| Framing correctness   | `PARTIALLY_CONFIRMED`: the 6h window is wrong, but the fix is not simply `24h_to_resolution`.                                                                                                                   |
| Decision required     | Define Day0 by city-local target date and settlement source. Split `day0_forecast_convergence` from `settlement_capture_observed`.                                                                              |
| Dependencies          | Local-day semantics; observation source authorization; settlement-hold exit; executable quote freshness.                                                                                                        |
| Affected keys         | `settlement_capture`; possibly future `day0_convergence`.                                                                                                                                                       |
| Affected phases       | Day0 event-day, near-close, post-event pre-resolution, settlement hold.                                                                                                                                         |
| Options               | A: 24h before UMA close; B: city-local target-day window; C: observed-extrema capture only; D: split B+C.                                                                                                       |
| Recommended decision  | **D: split phase semantics.** Keep current key `settlement_capture` for observed/near-certain capture; create internal phase gate for city-local Day0 forecast convergence as shadow unless observation-proven. |
| Rejected alternatives | Flat 24h-before-UMA: wrong for non-UTC cities. Pure `<6h`: misses event-day. Pure forecast Day0 live: too risky without observation evidence.                                                                   |
| Implementation tasks  | Add `MarketPhase`/`WeatherPhase`; compute city-local Day0; update `MODE_PARAMS`; gate Day0 entry by observation freshness/source; persist phase.                                                                |
| Tests                 | Local timezone phase tests; source-stale rejection; high/low metric tests; no entry outside phase.                                                                                                              |
| Rollback              | Set Day0 max window back to 6h and disable widened phase flag.                                                                                                                                                  |
| Final severity        | `LIVE_BLOCKER`                                                                                                                                                                                                  |

### §3.2 Gap: middle-state strategy vacuum

| Field                 | Tribunal                                                                                                                                                                |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Original symptom      | Only four update cron windows cover middle lifecycle.                                                                                                                   |
| Deeper market meaning | There is a monitoring/quote-recheck vacuum, but not necessarily a fresh-alpha vacuum.                                                                                   |
| Framing correctness   | `MISLEADING`: “no strategy fires” is true, but “trade middle continuously” does not follow.                                                                             |
| Decision required     | Add `middle_state_recheck` as shadow/diagnostic first; live only when forecast evidence is fresh enough or quote movement creates executable edge with causal snapshot. |
| Dependencies          | Forecast freshness gates; executable snapshot; reporting cohorts.                                                                                                       |
| Affected keys         | `center_buy`, `opening_inertia` fallback, shadow buy-NO strategies.                                                                                                     |
| Affected phases       | Medium-range, pre-event, middle lifecycle.                                                                                                                              |
| Options               | A: new `MIDDLE_STATE_HUNT`; B: intervalize `UPDATE_REACTION`; C: price-events only; D: shadow interval recheck plus fresh-update live only.                             |
| Recommended decision  | **D.** Add middle recheck for evidence and exit/monitor coverage, but do not create a new live alpha key before evidence.                                               |
| Rejected alternatives | Blind interval update reaction; it risks stale forecast overtrading.                                                                                                    |
| Implementation tasks  | Add phase-aware candidate scanner; mark decisions `shadow_reason=middle_stale_forecast` unless forecast/quote trigger qualifies.                                        |
| Tests                 | No live entry on stale forecast; shadow facts persist; quote reprice rejects non-executable edge.                                                                       |
| Rollback              | Disable middle shadow scheduler.                                                                                                                                        |
| Final severity        | `PROMOTION_BLOCKER` / partial `LIVE_BLOCKER` for coverage evidence, not alpha.                                                                                          |

### §3.3 Gap: `UPDATE_REACTION` cron should be interval

| Field                 | Tribunal                                                                                                      |
| --------------------- | ------------------------------------------------------------------------------------------------------------- |
| Original symptom      | Four fixed UTC times create dead windows.                                                                     |
| Deeper market meaning | Update reaction should be tied to forecast availability and quote evidence, not arbitrary cron alone.         |
| Framing correctness   | `PARTIALLY_CONFIRMED`.                                                                                        |
| Decision required     | Keep release-aware live windows; add interval shadow rechecks; do not delete update reaction.                 |
| Dependencies          | §3.2 middle recheck; forecast evidence freshness; scheduler semantics.                                        |
| Affected keys         | `center_buy`, `shoulder_sell` shadow, future inverse keys.                                                    |
| Affected phases       | Forecast update, medium-range.                                                                                |
| Options               | A: convert to 30m interval; B: keep cron; C: release-aware plus misfire grace plus shadow recheck.            |
| Recommended decision  | **C.**                                                                                                        |
| Rejected alternatives | Full interval live mode.                                                                                      |
| Implementation tasks  | Add forecast-availability trigger/misfire grace; persist `forecast_age_minutes`; shadow every 30m if desired. |
| Tests                 | Missed cron still runs within grace; stale forecast no live; fresh forecast can live.                         |
| Rollback              | Restore four cron windows only.                                                                               |
| Final severity        | `IMPROVEMENT` with launch evidence implications.                                                              |

### §3.4 Gap: no `PRICE_DRIFT_REACTION`

| Field                 | Tribunal                                                                                                    |
| --------------------- | ----------------------------------------------------------------------------------------------------------- |
| Original symptom      | No event-driven re-evaluation on rapid CLOB price moves.                                                    |
| Deeper market meaning | This is a high-upside quote-event strategy and a risk-control trigger, but it needs strict event causality. |
| Framing correctness   | `CONFIRMED` conceptually; implementation status `REVIEW_REQUIRED`.                                          |
| Decision required     | Shadow first with market-channel events, per-token cooldown, snapshot hash, and no live submit.             |
| Dependencies          | Market WS, quote snapshot persistence, flood control, strategy-level kill, reporting cohorts.               |
| Affected keys         | All live keys; especially opening/day0.                                                                     |
| Affected phases       | All quote-observation phases; near-close most valuable.                                                     |
| Options               | A: live price drift now; B: shadow price drift; C: ignore.                                                  |
| Recommended decision  | **B: `NEW_NOT_NOW` for live, shadow in Stage 5.**                                                           |
| Rejected alternatives | Live now: too much cancel/fill/staleness risk.                                                              |
| Implementation tasks  | Subscribe to market WS; trigger shadow re-eval on `best_bid_ask`/`price_change`; enforce cooldown.          |
| Tests                 | Event dedupe; cooldown; stale snapshot; tick-size change rejection.                                         |
| Rollback              | Disable market WS trigger.                                                                                  |
| Final severity        | `IMPROVEMENT`                                                                                               |

### §3.5 Gap: `shoulder_buy` + `center_sell` dormant

| Field                 | Tribunal                                                                                                          |
| --------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Original symptom      | Inverse pair referenced but not produced as strategy keys.                                                        |
| Deeper market meaning | The scanner can represent these economic hypotheses, but governance taxonomy/reporting cannot.                    |
| Framing correctness   | `PARTIALLY_CONFIRMED` and too narrow.                                                                             |
| Decision required     | Add explicit strategy-key taxonomy or keep blocked; no silent fallback. **(fix: PR #44 — now returns None; runtime rejects with entries_blocked_reason="strategy_key_unclassified")** |
| Dependencies          | Native NO promotion, reporting, FDR family, sizing/exit policies.                                                 |
| Affected keys         | `shoulder_sell`, `opening_inertia`, future `shoulder_buy`, `center_sell`.                                         |
| Affected phases       | All non-Day0 candidate phases.                                                                                    |
| Options               | A: wire live; B: wire shadow keys; C: block/quarantine inverse edges.                                             |
| Recommended decision  | **B + C:** create shadow keys and block live until evidence.                                                      |
| Rejected alternatives | “Wire live with same thresholds.” Calibration/execution risk differs.                                             |
| Implementation tasks  | Add `CANONICAL_STRATEGY_KEYS_SHADOW`; add classifier for four quadrants; forbid unknown fallback; update reports. |
| Tests                 | buy-YES shoulder → `shoulder_buy`; buy-NO center → `center_sell`; live size zero; reports separate.               |
| Rollback              | Map shadow keys to blocked decisions, not live fallback.                                                          |
| Final severity        | `LIVE_BLOCKER`                                                                                                    |

### §3.6 Gap: `opening_hunt` 24h window may be narrow

| Field                 | Tribunal                                                                                            |
| --------------------- | --------------------------------------------------------------------------------------------------- |
| Original symptom      | 15m interval fixed, but 24h cutoff may miss continuing opening-style inefficiency.                  |
| Deeper market meaning | Opening-edge decay is empirical; widening without phase distinction contaminates strategy identity. |
| Framing correctness   | `INCOMPLETE`.                                                                                       |
| Decision required     | Keep 24h for launch; evaluate widening only after middle-state shadow evidence.                     |
| Dependencies          | §3.2 middle recheck, reporting, alpha-decay evidence.                                               |
| Affected keys         | `opening_inertia`; fallback from update.                                                            |
| Affected phases       | Discovery/opening, early/mid transition.                                                            |
| Options               | A: keep 24h; B: widen to 48h; C: use liquidity/quote-age instead of time.                           |
| Recommended decision  | **A for launch, C for research.**                                                                   |
| Rejected alternatives | Widen now: overfits current key and hides middle-state design.                                      |
| Implementation tasks  | Ensure 15m restart; persist hours-since-open; collect alpha-decay by age bucket.                    |
| Tests                 | Opening mode boundaries; no overlap with Day0; report age cohort.                                   |
| Rollback              | Config remains current 24h.                                                                         |
| Final severity        | `IMPROVEMENT`                                                                                       |

---

## 8. Dependency order validation

### Draft order

```text
§3.1 → §3.2 → §3.5 → §3.4 → §3.3 + §3.6
```

### Confirmed dependencies

| Dependency                                        |                Status | Reason                                                                                               |
| ------------------------------------------------- | --------------------: | ---------------------------------------------------------------------------------------------------- |
| §3.2 before §3.3                                  |           `CONFIRMED` | Whether middle-state exists determines whether update cron should change.                            |
| §3.2 before §3.6                                  |           `CONFIRMED` | Opening-window widening overlaps middle design.                                                      |
| §3.5 can be designed separately from mode cadence | `PARTIALLY_CONFIRMED` | Taxonomy can be designed independently, but live promotion depends on reporting and native NO gates. |
| §3.4 complements all strategies                   |           `CONFIRMED` | Price events can trigger all phases.                                                                 |

### Invalid dependencies

| Draft dependency/framing                       |      Verdict | Correction                                                                                           |
| ---------------------------------------------- | -----------: | ---------------------------------------------------------------------------------------------------- |
| §3.1 requires §3.4 for final-hour acceleration | `MISLEADING` | Day0 phase correctness is launch-critical without price drift. Price drift is improvement.           |
| §3.1 independent                               | `MISLEADING` | It depends on local-day semantics, observation authority, settlement-hold exit, and quote freshness. |
| §3.5 after §3.2                                | `MISLEADING` | Strategy identity lock should happen before any new mode can emit/route candidates.                  |
| §3.4 before §3.3/§3.6                          | `MISLEADING` | Price drift should be shadow after launch spine.                                                     |

### Missing dependencies

| Missing dependency                                  | Why it matters                                                                             |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Evidence lock before strategy changes               | Prevents draft or stale code from becoming false authority.                                |
| Riskguard P0 before live acceptance                 | `REMAINING_TASKS.md` says entries can be probabilistically blocked by riskguard flapping.  |
| Strategy taxonomy before dormant wiring             | Prevents inverse edges from polluting `opening_inertia`/`shoulder_sell`.                   |
| Executable snapshot authority before live decisions | Posterior edge must survive CLOB cost.                                                     |
| Exit/hold policy before Day0 widening               | Wider Day0 creates positions that may need hold/redeem decisions.                          |
| Reporting cohort policy before promotion            | Shadow/backtest/live cohorts must not mix.                                                 |

### Corrected dependency graph

```text
0. Evidence lock / catalog truth
   ├── verify branch, docs, canonical keys, feature flags, runtime reachability
   └── freeze “draft is hypothesis” status

1. Live safety preconditions
   ├── riskguard-live flap non-strategy P0
   ├── daemon restart for 15m opening_hunt
   └── verify wallet-bankroll authority and RiskGuard/exposure gates

2. Strategy taxonomy and routing
   ├── block unknown fallback live
   ├── distinguish four economic quadrants in shadow
   └── report cohorts by key/phase/direction/bin role

3. Day0/local-day/settlement phase design (§3.1)
   ├── observed settlement_capture live
   ├── day0 forecast-convergence shadow
   └── settlement-hold exit policy

4. Fresh update and middle coverage (§3.2 + §3.3)
   ├── keep release-aware live center_buy
   └── add middle shadow recheck

5. Buy-NO / dormant promotion (§3.5)
   ├── native NO shadow evidence
   └── live flag remains false

6. Opening window evidence (§3.6)
   └── keep 24h launch, evaluate after middle evidence

7. Price-drift reaction (§3.4)
   └── shadow market-WS event layer post-launch
```

### Final implementation order

1. Stage 0 evidence/catalog truth.
2. Stage 1 critical live blockers and hard gates.
3. Stage 2 minimal portfolio.
4. Stage 3 sizing/exit/killswitch integration.
5. Stage 4 promotion/reporting evidence.
6. Stage 5 real-market improvement.
7. Stage 6 dormant/out-of-scope routing.

---

## 9. Final launch strategy architecture

### Final strategy taxonomy

| Class                         | Strategy                      | Role                                                            | Live rule                                                           |
| ----------------------------- | ----------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------- |
| Alpha, model-edge             | `center_buy`                  | Buy YES central bin when fresh forecast indicates underpricing. | Live only in fresh forecast-update phase.                           |
| Alpha, opening                | `opening_inertia`             | New-listing/early-market mispricing.                            | Live only in first 24h and ≥24h to resolution.                      |
| Alpha, observation/settlement | `settlement_capture`          | Day0 observed/near-certain capture.                             | Live only when observation and settlement semantics are authorized. |
| Alpha, native NO/tail         | `shoulder_sell`               | Buy NO shoulder.                                                | Shadow only until native NO promotion.                              |
| Alpha, dormant inverse        | `shoulder_buy`, `center_sell` | Tail YES / center NO.                                           | Shadow after redesign; no live.                                     |
| Diagnostic                    | `middle_state_recheck`        | Middle lifecycle quote/signal evidence.                         | Shadow by default.                                                  |
| Diagnostic/improvement        | `price_drift_reaction`        | Quote-event trigger.                                            | Shadow post-launch.                                                 |
| Risk-management               | `risk_off_exit`               | Reduce-only/RED/adapter de-risk.                                | Live allowed.                                                       |
| Exit policy                   | `settlement_hold`             | Hold/redeem vs sell.                                            | Launch-required policy.                                             |

### Market phase taxonomy

| Phase                       | Definition                                                                             | Allowed live alpha                                                     |
| --------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `DISCOVERY_OPENING`         | Market open to 24h since open, and ≥24h to resolution.                                 | `opening_inertia`                                                      |
| `FRESH_FORECAST_UPDATE`     | Forecast evidence fresh and causally available after issue/fetch/available timestamps. | `center_buy`; buy-NO shadow                                            |
| `MIDDLE_STALE_FORECAST`     | No fresh forecast, market still active.                                                | Shadow recheck only                                                    |
| `CITY_LOCAL_DAY0_FORECAST`  | City-local target date/event-day but no decisive observation.                          | Shadow unless extremely strict future design                           |
| `DAY0_OBSERVED`             | Authorized observation with high/low-so-far/current temp, age ≤1h, source compatible.  | `settlement_capture`                                                   |
| `NEAR_CLOSE`                | Close/resolution imminent; quote staleness/cancel risk high.                           | Strict `settlement_capture` only with immediate-depth proof            |
| `POST_EVENT_PRE_RESOLUTION` | Physical event complete, market unresolved.                                            | Hold/redeem policy; possible capture only with settlement-source proof |
| `RESOLUTION_SETTLEMENT`     | Resolved/redeem.                                                                       | No new alpha; settlement workflow                                      |

### Lifecycle phase taxonomy

| Lifecycle layer      | Required gate                                                               |
| -------------------- | --------------------------------------------------------------------------- |
| Discovery            | Market active, accepting orders, supported city/date/metric, full identity. |
| Forecast/data        | Causal, fresh, role-authorized, degradation `OK`.                           |
| Posterior            | Model-only unless corrected complete-family prior is shadow/promoted.       |
| Market quote         | Immutable executable snapshot with quote hash.                              |
| Candidate generation | Strategy key matches economic quadrant and phase.                           |
| FDR                  | Full tested family; metric/phase included.                                  |
| Cost                 | Ask/depth/fee/tick/slippage; no midpoint.                                   |
| Sizing               | Strategy/phase/liquidity/time-aware Kelly cap.                              |
| Order policy         | FAK/FOK/GTC/GTD per phase; no passive live without maker authority.         |
| Submission           | Capability proof and command journal.                                       |
| Fill/cancel          | Partial fill/cancel/unknown side effect handled.                            |
| Monitor              | Same entry method, held-token quote.                                        |
| Exit                 | Bid/depth/hold/redeem policy.                                               |
| Settlement           | Canonical events, no economic-close confusion.                              |
| Report/promotion     | Cohort-clean evidence.                                                      |

### Eligibility gates

| Gate         | Live requirement                                                                        |
| ------------ | --------------------------------------------------------------------------------------- |
| Strategy key | Must be in live-allowed catalog for that phase.                                         |
| Phase        | Strategy must be phase-compatible.                                                      |
| Forecast     | Fresh, causal, source-role `entry_primary`, degradation `OK`, authority `FORECAST`.     |
| Observation  | Source allowed for settlement type; fields finite; timestamp not stale/future.          |
| Quote        | Snapshot fresh; token/outcome matches; bid/ask/depth valid; tick/min size valid.        |
| Family       | Complete for family-relative or devig prior; otherwise shadow/forbid.                   |
| Native NO    | Native NO quote available; feature flags/prom evidence for live.                        |
| Order policy | Marketable final intent for taker live; passive only if post-only/maker support exists. |
| Sizing       | Positive edge after fee/depth; cap/multiplier passes.                                   |
| Exit         | Held-token bid/depth known or hold policy says hold.                                    |
| Killswitch   | Global GREEN; no local strategy/family/metric/adapter halt.                             |
| Reporting    | Decision evidence envelope and strategy cohort fields persisted.                        |

---

## 10. Minimal launch-safe strategy portfolio

| Strategy                     |                Status | Market phase                                                                        | Edge source                        | Required gates                                                                                                                  | Required tests                                                                     | Reason                                                                   | Rollback                                              |
| ---------------------------- | --------------------: | ----------------------------------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------- |
| `settlement_capture`         |        `LIVE_ALLOWED` | `DAY0_OBSERVED`, strict `NEAR_CLOSE`, `POST_EVENT_PRE_RESOLUTION` with source proof | Observation/settlement convergence | Local-day phase, source authorization, obs age ≤1h, finite high/low/current fields, executable snapshot, held-token exit policy | Day0 window tests; stale obs reject; source mismatch reject; high/low metric tests | Best real-market launch alpha; current code has source/quality gates.    | Disable widened Day0 flag; revert to `<6h` strict.    |
| `center_buy`                 |        `LIVE_ALLOWED` | `FRESH_FORECAST_UPDATE`                                                             | Model-edge central buy YES         | Forecast evidence causality, FDR, executable ask/depth, fee, positive edge after reprice, no ultra-low price                    | Fresh/stale forecast tests; executable reprice tests; FDR family tests             | Cleanest current model-edge strategy.                                    | Pause strategy key via policy; keep shadow decisions. |
| `opening_inertia`            |        `LIVE_ALLOWED` | `DISCOVERY_OPENING` only                                                            | Early listing mispricing           | `hours_since_open <24`, `hours_to_resolution >=24`, quote/depth/spread, no update fallback                                      | Boundary tests; quote reject tests; fallback quarantine test                       | Useful launch canary with limited phase.                                 | Disable `opening_hunt`; keep monitor/exit.            |
| `shoulder_sell`              |         `SHADOW_ONLY` | Forecast update / middle shadow                                                     | Native buy-NO tail                 | Native NO quote, feature flag shadow, no live flag, report cohort                                                               | Native NO quote tests; live-size-zero tests                                        | Current code supports native NO but flags off and evidence insufficient. | Leave flags false.                                    |
| `shoulder_buy`               |    `DORMANT_REDESIGN` | Tail shadow                                                                         | Tail YES                           | Explicit key, harsher thresholds, shadow reporting                                                                              | Classifier test: buy-YES shoulder → `shoulder_buy`                                 | Not safe as `shoulder_sell` collision.                                   | Block unknown quadrant.                               |
| `center_sell`                |    `DORMANT_REDESIGN` | Central buy-NO shadow                                                               | Native NO center overpricing       | Explicit key, native NO, shadow reporting                                                                                       | Classifier test: buy-NO center → `center_sell`                                     | Not safe as `opening_inertia` fallback.                                  | Block unknown quadrant.                               |
| `middle_state_recheck`       | `NEW_LAUNCH_REQUIRED` | Middle stale/fresh distinction                                                      | Diagnostic quote/signal evidence   | Shadow-only unless fresh forecast; strategy phase persisted                                                                     | No live on stale forecast; shadow evidence persists                                | Closes coverage evidence gap without overtrading.                        | Disable scheduler.                                    |
| `price_drift_reaction`       |         `NEW_NOT_NOW` | All phases, event-driven                                                            | Stale quote/market lag             | Market WS, snapshot hash, cooldown, flood kill                                                                                  | Event tests; cooldown; tick-size change                                            | Highest upside, but not launch-safe now.                                 | Disable WS trigger.                                   |
| `family_relative_mispricing` |         `NEW_NOT_NOW` | Complete family                                                                     | Relative mispricing                | Family completeness, devig/liquidity, quote hashes                                                                              | Family completeness tests                                                          | Valuable but complex.                                                    | Keep research doc only.                               |
| `risk_off_exit`              |        `LIVE_ALLOWED` | Emergency/reduce-only                                                               | Risk management                    | RED/DATA_DEGRADED/global/local kills                                                                                            | RED sweep tests; command truth tests                                               | Mandatory capital control.                                               | Manual review / reduce-only.                          |
| `settlement_hold`            | `NEW_LAUNCH_REQUIRED` | Near close/settlement                                                               | Exit policy                        | Held-token bid/depth, settlement probability, fee/time                                                                          | Hold vs sell tests                                                                 | Prevents premature sale of near-winners.                                 | Default conservative hold unless RED.                 |

---

## 11. Sizing design

### Current sizing reality

| Current element              | Status                                                                                                            |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Kelly core                   | `CONFIRMED`: fractional Kelly uses typed `ExecutionPrice` and rejects unsafe bare float boundaries.               |
| Dynamic multiplier           | `CONFIRMED`: reduces for CI width, lead time, win rate, portfolio heat, drawdown.                                 |
| Risk limits                  | `CONFIRMED`: min order, single-position, portfolio heat, city exposure.                                           |
| Fee-adjusted sizing boundary | `CONFIRMED`: evaluator applies taker fee before Kelly.                                                            |
| Strategy awareness           | `INCOMPLETE`: no explicit strategy/phase multiplier.                                                              |
| Liquidity awareness          | `PARTIALLY_CONFIRMED`: executable reprice/depth checks exist, but sizing policy should make them universal.       |
| Bankroll truth               | `CONFIRMED`: wallet bankroll provider is the current live bankroll authority; risk remains bounded by RiskGuard and exposure gates.       |

### Best sizing design for launch

**Decision:** keep generic Kelly core, but add **strategy-aware exposure gates and multipliers**. Do not replace Kelly. Do not reintroduce a per-trade cap; validate RiskGuard, wallet bankroll, and first full entry→fill→settlement lifecycle evidence before widening exposure policy.

| Input                              | Required source                                                   |
| ---------------------------------- | ----------------------------------------------------------------- |
| `strategy_key`                     | Canonical classifier, no fallback ambiguity                       |
| `market_phase`                     | New phase taxonomy                                                |
| `p_posterior`                      | Model-only or authorized Day0 posterior                           |
| `entry_price`                      | Executable ask/depth/VWMP boundary after snapshot reprice         |
| `fee_rate`                         | Token/market fee lookup; do not hardcode except fallback contract |
| `spread`                           | Executable snapshot                                               |
| `depth_at_limit`                   | CLOB sweep                                                        |
| `time_to_settlement`               | Market/phase clock                                                |
| `forecast_age` / `observation_age` | Forecast/observation evidence                                     |
| `bankroll`                         | Wallet bankroll provider; derived portfolio projections cannot override it |
| `city/metric exposure`             | Portfolio state                                                   |
| `risk level`                       | Riskguard/current level                                           |

### Per-strategy launch sizing rules

| Strategy               | Multiplier/exposure rule                                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `settlement_capture`   | Use Kelly at executable price, then apply RiskGuard/exposure gates; require observed-edge confidence; no size if settlement-source mismatch. |
| `center_buy`           | Base dynamic Kelly, then apply RiskGuard/exposure gates; require fresh forecast and CI lower > 0 after FDR; no stale middle live. |
| `opening_inertia`      | Dynamic Kelly × opening risk multiplier, recommended 0.5 of base until evidence; stricter spread/depth.                  |
| `shoulder_sell`        | Live size = 0; shadow would-size only.                                                                                   |
| `shoulder_buy`         | Live size = 0; shadow would-size with harsher tail threshold.                                                            |
| `center_sell`          | Live size = 0; shadow would-size only.                                                                                   |
| `middle_state_recheck` | Live size = 0 unless it reuses fresh-update authority; shadow would-size.                                                |
| `price_drift_reaction` | Live size = 0.                                                                                                           |
| `risk_off_exit`        | Size = held lot shares, not Kelly.                                                                                       |
| `settlement_hold`      | No entry size; affects capital lock and exit size.                                                                       |

### Liquidity/depth/spread/fee constraints

A trade is not sizeable unless:

```text
edge_after_fee_and_depth > 0
AND executable snapshot token/outcome matches decision
AND orderbook depth at final limit >= submitted notional/shares
AND spread <= strategy_phase_limit
AND tick/min order constraints pass
AND fee rate known or explicitly zero
```

Polymarket fees are applied at match time, makers are not charged, takers pay fees on fee-enabled markets, and Weather’s documented taker fee rate is 0.05, so Zeus must continue reading token/market fee evidence instead of treating costs as static. ([Polymarket Documentation][3])

### Tests

| Test                                     | Purpose                                |
| ---------------------------------------- | -------------------------------------- |
| Kelly rejects midpoint/bare float        | Ensure executable-price boundary only. |
| Strategy multiplier applied              | Verify per-key/per-phase sizing.       |
| Stale forecast live size zero            | Prevent middle overtrading.            |
| Native NO live size zero when flag false | Prevent buy-NO accidental live.        |
| Depth-constrained order rejected         | Avoid partial fake edge.               |
| Fee unavailable rejection                | Prevent under-costed entries.          |
| No per-trade cap parameter               | Prevent reintroduction of a sizing cap. |

---

## 12. Exit design

### Current exit reality

| Current element          | Status                                                                                                                     |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| Exit triggers            | `CONFIRMED`: settlement imminent, whale toxicity, divergence panic, flash crash, edge reversal, vig, buy-NO special path.  |
| Exit lifecycle           | `CONFIRMED`: sell intent, sell placed/pending/filled, retry/backoff, economic close only after confirmed fill.             |
| Held-token quote refresh | `CONFIRMED`: monitor refresh fetches held-token best bid/ask and logs microstructure.                                      |
| Exit snapshot            | `CONFIRMED`: exit lifecycle can use latest or capture fresh executable snapshot.                                           |
| Hold value costs         | `PARTIALLY_CONFIRMED`: zero-cost hold exists in triggers; exit-cost feature flag currently off.                            |

### Best exit design for launch

**Decision:** exit should be **position-lot-specific first**, then risk-state-specific, then strategy-informed. Strategy alone is too coarse because the same strategy can be held in different quote, settlement, and risk states.

| Exit dimension | Rule                                                                                   |
| -------------- | -------------------------------------------------------------------------------------- |
| Held token     | Always sell the held token side: YES token for buy-YES, NO token for buy-NO.           |
| Sell value     | Use held-token best bid/depth, not midpoint/current displayed probability.             |
| Snapshot       | Exit order must cite fresh executable snapshot or fail closed.                         |
| Risk state     | RED can force exit/cancel sweep; DATA_DEGRADED blocks new entries and may reduce-only. |
| Lot state      | Respect pending exit, retries, unknown side effects, collateral, chain truth.          |
| Strategy       | Influences thresholds and hold preference, not token semantics.                        |

### Strategy-specific exit posture

| Strategy                            | Exit posture                                                                                                                                                              |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `settlement_capture`                | Prefer hold/redeem when settlement probability is high and bid is discounted; exit only on observation reversal, source uncertainty, RED, or bid sufficiently attractive. |
| `center_buy`                        | Standard edge reversal with confirmation; exit before near-settlement only if sell EV exceeds hold.                                                                       |
| `opening_inertia`                   | Faster decay watch; lower tolerance for quote deterioration and no fill.                                                                                                  |
| `shoulder_sell` shadow              | No live exit; simulate held-token NO bid.                                                                                                                                 |
| `shoulder_buy`/`center_sell` shadow | No live exit; diagnostic only.                                                                                                                                            |
| `risk_off_exit`                     | Overrides alpha logic when global/local risk demands.                                                                                                                     |

### Day-0, near-close, and hold-to-resolution

| Case                       | Decision                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Day0 observation reversal  | Immediate review/exit if new authorized observation flips edge and sell EV beats hold, or risk state forces. |
| Near-close winning token   | Hold unless best bid after fee/time exceeds redemption EV or risk state forces exit.                         |
| Near-close uncertain token | Use conservative forward edge plus settlement-source certainty; avoid passive orders.                        |
| Post-event pre-resolution  | No midpoint exits; decide bid/depth versus expected redeem.                                                  |
| RED                        | Force-exit sweep can override but still uses command truth and lifecycle.                                    |

### Tests

| Test                                               | Purpose                            |
| -------------------------------------------------- | ---------------------------------- |
| Sell uses held-token bid                           | Prevent current/midpoint exit bug. |
| Buy-NO exit native probability                     | Prevent double inversion.          |
| Day0 reversal requires authorized source           | Avoid bad observation exits.       |
| Hold-to-resolution beats discounted bid            | Prevent premature sale.            |
| RED sweep marks exit reason and emits cancel proxy | Risk-off integrity.                |
| Exit snapshot missing fails closed                 | No stale exit.                     |
| Partial fill/retry/backoff                         | Lifecycle evidence.                |

---

## 13. Killswitch design

### Current killswitch reality

| Current gate                                  | Code/doc status                                                               |
| --------------------------------------------- | ----------------------------------------------------------------------------- |
| Risk level not GREEN blocks new entries       | Runtime-level confirmed.                                                      |
| RED force-exit sweep                          | Confirmed.                                                                    |
| Heartbeat, WS gap, posture, cutover, governor | Confirmed in runtime summary/gates.                                           |
| Unknown side effect/reconcile                 | Present in draft and command recovery surfaces.                               |
| Riskguard flapping                            | P0 documented in `REMAINING_TASKS.md`; non-strategy but live-entry blocking.  |
| Strategy-local killswitch                     | `INCOMPLETE`                                                                  |
| Family/city/metric/position killswitch        | `INCOMPLETE`                                                                  |

### Best launch killswitch architecture

| Layer             | Trigger examples                                                                                               | Action                                      |
| ----------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Global runtime    | Riskguard RED/DATA_DEGRADED, heartbeat lost, WS gap, cutover blocked, governor unavailable                     | Block entries; RED sweeps exits             |
| Adapter/execution | CLOB proxy down, fee lookup unavailable, snapshot mismatch, unknown side effect, delayed/unmatched order burst | Block submit; cancel/reconcile; reduce-only |
| Strategy          | Alpha decay, drift rate, native NO evidence failure, stale forecast strategy                                   | Pause strategy key live; keep shadow        |
| Market family     | Incomplete siblings, inconsistent family identity, quote hash mismatch, vig extreme                            | Block family entries                        |
| City/date/metric  | Source stale, local-day mismatch, high/low ambiguity, calibration unverified                                   | Block matching candidates                   |
| Position lot      | Exit pending missing, collateral missing, quarantine, stale sell order unknown                                 | Block replacement/entry; review or retry    |
| Operator override | Manual pause/exit-only/clear quarantine                                                                        | Controlled action with audit evidence       |

### Required triggers

| Trigger                                        | Severity                   |
| ---------------------------------------------- | -------------------------- |
| Forecast evidence missing/after decision       | `LIVE_BLOCKER`             |
| Day0 observation stale/source mismatch         | `LIVE_BLOCKER`             |
| Native NO flag false and buy-NO live candidate | `LIVE_BLOCKER`             |
| Strategy key unknown or fallback quadrant      | `LIVE_BLOCKER`             |
| Executable snapshot expired/mismatch           | `LIVE_BLOCKER`             |
| Depth below submitted size                     | `LIVE_BLOCKER`             |
| Fee unavailable for fee-enabled market         | `LIVE_BLOCKER`             |
| Riskguard not GREEN                            | `LIVE_BLOCKER` for entries |
| Family incomplete for family strategy          | `LIVE_BLOCKER`             |
| Report cohort unknown                          | `PROMOTION_BLOCKER`        |

### Tests

| Test                                                         | Purpose                                         |
| ------------------------------------------------------------ | ----------------------------------------------- |
| Strategy pause blocks live but allows shadow                 | Local kill works.                               |
| City/date/metric stale source blocks matching candidates     | Prevent local source failure from global noise. |
| Adapter unavailable blocks submit before command side effect | Execution fail-closed.                          |
| Native NO feature flag blocks buy-NO live                    | Dormant safety.                                 |
| RED only exits nonterminal positions                         | Avoid terminal-state churn.                     |
| Operator override audited                                    | Manual control safe.                            |

---

## 14. Lifecycle coverage final map

| Phase                                | Covered by which strategy/system                         | Missing design                               | Launch requirement        | Improvement item          | Associated tests            |
| ------------------------------------ | -------------------------------------------------------- | -------------------------------------------- | ------------------------- | ------------------------- | --------------------------- |
| 1. discovery                         | Market scanner, `OPENING_HUNT`, `find_weather_markets`   | Phase taxonomy persistence                   | Required                  | Market WS `new_market`    | Discovery identity tests    |
| 2. identity                          | Settlement semantics, metric identity, bin topology      | Complete family identity for family strategy | Required for live         | Family graph              | High/low/bin topology tests |
| 3. forecast/data                     | Ensemble client, evidence errors, Day0 observation gates | Middle freshness policy                      | Required                  | Source degradation tiers  | Causality/stale tests       |
| 4. posterior                         | Model-only posterior, Platt, alpha                       | Corrected family prior promotion             | Required: model-only live | Devig shadow              | Posterior-mode tests        |
| 5. market prior / quote observation  | Executable snapshots, VWMP, orderbook                    | Quote-event stream causality                 | Required                  | Price drift WS            | Snapshot hash tests         |
| 6. candidate generation              | MarketAnalysis, full-family scan                         | Four-quadrant taxonomy                       | Required                  | Family mispricing         | Quadrant classifier tests   |
| 7. FDR/family selection              | Full tested family, BH                                   | Cohort phase family IDs                      | Required                  | Cross-market FDR research | FDR family tests            |
| 8. executable cost                   | Reprice snapshot, CLOB sweep                             | Uniform mandatory gate                       | Required                  | Maker policy              | Depth/slippage tests        |
| 9. sizing/risk                       | Kelly, risk limits, cap                                  | Strategy/phase multipliers                   | Required                  | Empirical Kelly tuning    | Sizing tests                |
| 10. order policy                     | Executor limit-only, timeouts                            | New mode timeouts; FAK/FOK launch rules      | Required                  | Post-only maker           | Order policy tests          |
| 11. final intent                     | FinalExecutionIntent when supported                      | Passive submit authority                     | Required for live         | Maker liquidity           | Final-intent tests          |
| 12. submission                       | Command bus, capability proof                            | Adapter-local kill                           | Required                  | None                      | Capability tests            |
| 13. fill/cancel/recovery             | Pending reconciliation, command recovery                 | Partial-fill promotion policy                | Required                  | Fill simulator            | Unknown side-effect tests   |
| 14. position lot                     | Portfolio/lifecycle manager                              | Strategy role on lot                         | Required                  | Per-lot risk score        | Position event tests        |
| 15. monitor                          | Monitor refresh, held-token quote                        | Middle quote cadence                         | Required                  | Price-event monitor       | Monitor provenance tests    |
| 16. exit                             | Exit triggers/lifecycle                                  | Settlement hold explicit policy              | Required                  | Correlation exit cost     | Exit bid/depth tests        |
| 17. settlement/redeem                | Harvester/settlement lifecycle                           | UMA timing shadow                            | Required as ops evidence  | Settlement delay model    | Settlement tests            |
| 18. report/replay/promotion          | Edge observation, attribution drift                      | Shadow/live cohort rules for new keys        | Required                  | Event replay              | Report cohort tests         |
| 19. killswitch/operator intervention | Riskguard, governors, control plane                      | Local strategy/family/city kills             | Required                  | Auto-pausing by drift     | Kill tests                  |

---

## 15. Promotion/reporting/evidence policy

### Strategy evidence matrix

| Evidence type      | Live promotion value                                             | Required fields                                                                    |
| ------------------ | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Diagnostic-only    | No live promotion                                                | Posterior, quote snapshot, would-size, rejection reason                            |
| Shadow executable  | Can promote after cohort-clean analysis                          | Strategy key, phase, quote hash, executable cost, would-order policy, no live fill |
| Live canary        | Promotion only with fills/settlements                            | Order id, order type, fill status, partials, fees, exit/settlement                 |
| Backtest           | Diagnostic only unless point-in-time orderbook/fill model exists | Forecast snapshot, market snapshot, fill simulation assumptions                    |
| Settlement outcome | Required for realized edge                                       | Outcome, settlement source, p_posterior, strategy, phase                           |

### Shadow evidence rules

| Rule                                           | Policy                                    |
| ---------------------------------------------- | ----------------------------------------- |
| Shadow must cite executable quote              | No midpoint-only shadow promotion.        |
| Shadow must include would-order type           | FAK/FOK/GTC differences matter.           |
| Shadow cannot be mixed with live               | Separate cohorts.                         |
| Shadow buy-NO must use native NO quote         | No YES complement as executable evidence. |
| Shadow dormant keys must remain live-size zero | Promotion requires explicit flag.         |

### Live evidence rules

| Rule                                            | Policy                            |
| ----------------------------------------------- | --------------------------------- |
| Live entry must have decision evidence envelope | Required.                         |
| Live fill must have command and venue state     | Required.                         |
| Partial fill is a separate outcome              | Do not treat as full fill.        |
| Exit must be included                           | Entry-only PnL is invalid.        |
| Settlement must be canonical                    | Economic close is not settlement. |

### Report cohort rules

| Cohort axis    | Required values                                          |
| -------------- | -------------------------------------------------------- |
| Strategy       | Exact key, no fallback ambiguity                         |
| Role           | alpha / risk-management / diagnostic                     |
| Discovery mode | opening/update/day0/middle/price-event                   |
| Market phase   | discovery/fresh-update/middle/day0/near-close/post-event |
| Direction      | buy_yes / buy_no                                         |
| Bin role       | center / shoulder / unknown                              |
| Metric         | high / low                                               |
| Posterior mode | model-only / corrected-prior-shadow / legacy             |
| Execution      | live / shadow / diagnostic                               |
| Order policy   | FOK/FAK/GTC/GTD/post-only                                |
| Fill           | no submit / rejected / pending / partial / full          |
| Exit           | hold / sell / force / settlement                         |

### No invalid promotion

Do not promote a strategy if:

```text
strategy_key was inferred by fallback
OR discovery_mode missing from report row
OR native NO used YES complement
OR quote snapshot absent/stale
OR only midpoint/current price exists
OR shadow/live cohorts mixed
OR entry has no exit/settlement evidence
OR family completeness was assumed but not proven
OR fill model lacks point-in-time depth
```

### Tests

| Test                                               | Purpose                       |
| -------------------------------------------------- | ----------------------------- |
| Report rejects unknown strategy key                | Prevent cohort contamination. |
| Dormant shadow not counted in live realized edge   | No accidental promotion.      |
| Attribution drift catches quadrant mismatch        | Governance integrity.         |
| Backtest labeled diagnostic without orderbook/fill | No fake promotion.            |
| Live cohort requires fill + settlement             | End-to-end evidence.          |

---

## 16. Out-of-scope / `REMAINING_TASKS.md` routing

| Item                          | Why not launch-critical strategy design    | Dependency before revisit               | Risk if pulled into launch                            | Target doc/section              |
| ----------------------------- | ------------------------------------------ | --------------------------------------- | ----------------------------------------------------- | ------------------------------- |
| Data-ingest daemon resilience | Ops/data availability, not strategy choice | Structural PR #13                       | Strategy scope creep                                  | `REMAINING_TASKS.md` §B         |
| Riskguard-live flapping       | Non-strategy P0, but live-entry blocker    | Fix duplicate PIDs/proxy/DB path        | False strategy diagnosis when entries blocked by risk | `REMAINING_TASKS.md` §A #45/#61 |
| Wallet-bankroll authority                             | Structural bankroll truth                  | Maintain wallet provider + gate checks  | Misusing derived portfolio projections as bankroll    | Current RiskGuard/exposure gates |
| TIGGE historical backfill     | Calibration/history, not immediate live    | Data-ingest resilience                  | Delays launch on non-live data                        | `REMAINING_TASKS.md` §A #44     |
| PhysicalBounds fallback       | Ingestion resilience                       | Small try/except fix                    | Misclassified as strategy failure                     | `REMAINING_TASKS.md` §C #33     |
| Oracle path centralization    | Infrastructure                             | PR #40/#25                              | Conflates path cleanup with strategy design           | `REMAINING_TASKS.md` §C/D       |
| Price-drift live trading      | Real strategy but not launch-critical      | Market WS, snapshots, flood control     | Cancel/fill/staleness live loss                       | New Stage 5 strategy backlog    |
| Family-relative live trading  | Real strategy but high complexity          | Complete family identity/devig evidence | False relative edge                                   | Stage 5/6 strategy backlog      |
| Opening window widening       | Improvement                                | Middle-state shadow evidence            | Contaminates opening cohort                           | Stage 5/6                       |
| Native NO live promotion      | Strategy evidence                          | Shadow cohort, flags, reports           | Buy-NO live losses/misattribution                     | Stage 4/5                       |

---

## 17. Implementation roadmap

### Stage 0 — Evidence lock / catalog truth

| Field                  | Packet                                                                                                                                                                                                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Objective              | Verify actual strategy keys, modes, feature flags, docs, branch truth; prevent draft from becoming false authority.                                                                                                                                                |
| Dependency             | None.                                                                                                                                                                                                                                                              |
| Files                  | `STRATEGIES_AND_GAPS.md`, `REMAINING_TASKS.md`, `src/engine/discovery_mode.py`, `src/engine/cycle_runner.py`, `src/engine/cycle_runtime.py`, `src/engine/evaluator.py`, `config/settings.json`, `src/state/edge_observation.py`, `src/state/attribution_drift.py`. |
| Forbidden files        | Execution adapter live submission files unless read-only.                                                                                                                                                                                                          |
| Tasks                  | Add a catalog truth table; assert four live canonical keys; list shadow/dormant candidates; document feature flags; flag branch/ref.                                                                                                                               |
| Tests                  | Static test verifying known strategy keys and feature flags match docs.                                                                                                                                                                                            |
| Commands               | `pytest tests/test_architecture_contracts.py tests/test_edge_observation.py tests/test_attribution_drift.py`                                                                                                                                                       |
| Launch impact          | Prevents unsafe taxonomy drift.                                                                                                                                                                                                                                    |
| Rollback               | Revert doc/test additions.                                                                                                                                                                                                                                         |
| Hidden branches closed | Draft accepted without challenge; code-reachable strategy not documented; dormant accidentally live.                                                                                                                                                               |

### Stage 1 — Critical strategy live blockers

| Field                  | Packet                                                                                                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Objective              | Prevent wrong live trade, wrong phase, wrong sizing, wrong exit, unsafe killswitch behavior.                                                                                                |
| Dependency             | Stage 0.                                                                                                                                                                                    |
| Files                  | `src/engine/evaluator.py`, `src/engine/cycle_runner.py`, `src/engine/cycle_runtime.py`, `src/strategy/market_analysis.py`, `src/execution/exit_triggers.py`, `config/settings.json`, tests. |
| Forbidden files        | Major DB migrations unless necessary; PR should be minimal.                                                                                                                                 |
| Tasks                  | Enforce no live unknown/fallback quadrant; block native NO live if flag false; keep `shoulder_sell` shadow; require executable reprice; add phase tags.                                     |
| Tests                  | Quadrant classification; native NO live blocked; stale forecast blocked; executable snapshot missing rejects.                                                                               |
| Commands               | `pytest tests/test_strategy*.py tests/test_execution*.py tests/test_ws_poll_reaction.py`                                                                                                    |
| Launch impact          | Removes biggest live-money strategy risks.                                                                                                                                                  |
| Rollback               | Feature flag to re-block all new taxonomy while keeping old three live keys.                                                                                                                |
| Hidden branches closed | Active strategy only works in model space; strategy lacks phase/quote gates.                                                                                                                |

### Stage 2 — Minimal launch strategy portfolio

| Field                  | Packet                                                                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Objective              | Define final live/shadow/dormant set.                                                                                                      |
| Dependency             | Stage 1.                                                                                                                                   |
| Files                  | Strategy policy config, evaluator classifier, discovery mode docs/tests, reporting docs.                                                   |
| Forbidden files        | Price WS implementation.                                                                                                                   |
| Tasks                  | `settlement_capture`, `center_buy`, `opening_inertia` live with gates; `shoulder_sell` shadow; dormant inverse shadow only after taxonomy. |
| Tests                  | Live allowed matrix; shadow live-size-zero; phase compatibility.                                                                           |
| Commands               | `pytest tests/test_evaluator*.py tests/test_riskguard.py tests/test_db.py`                                                                 |
| Launch impact          | Produces launch-safe active set.                                                                                                           |
| Rollback               | Pause all alpha except `settlement_capture`.                                                                                               |
| Hidden branches closed | Weak strategy preserved because it exists; better missing strategy ignored.                                                                |

### Stage 3 — Sizing / exit / killswitch integration

| Field                  | Packet                                                                                                                                                                                  |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Objective              | Make sizing, exit, and killswitch strategy/phase-aware without overbuilding.                                                                                                            |
| Dependency             | Stage 2.                                                                                                                                                                                |
| Files                  | `src/strategy/kelly.py`, `src/strategy/risk_limits.py`, `src/execution/exit_triggers.py`, `src/execution/exit_lifecycle.py`, `src/engine/monitor_refresh.py`, risk policy config/tests. |
| Forbidden files        | CLOB adapter internals unless tests reveal boundary bug.                                                                                                                                |
| Tasks                  | Add per-strategy multipliers; hold-to-resolution policy; local killswitch layers; preserve no-cap Kelly plus exposure gates.                                                            |
| Tests                  | Sizing matrix; hold vs sell; local strategy pause; RED sweep.                                                                                                                           |
| Commands               | `pytest tests/test_riskguard.py tests/test_pnl_flow_and_audit.py tests/test_exit*.py`                                                                                                   |
| Launch impact          | Safe live money and exit.                                                                                                                                                               |
| Rollback               | Revert to generic cap with all buy-NO live blocked.                                                                                                                                     |
| Hidden branches closed | Sizing generic when risk differs; exit generic when lifecycle differs; global kill only.                                                                                                |

### Stage 4 — Promotion/reporting evidence

| Field                  | Packet                                                                                                      |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| Objective              | Ensure strategy evidence is valid and cohort-clean.                                                         |
| Dependency             | Stage 3.                                                                                                    |
| Files                  | `src/state/edge_observation.py`, `src/state/attribution_drift.py`, `src/state/db.py`, report scripts/tests. |
| Forbidden files        | Entry execution logic unless report needs field.                                                            |
| Tasks                  | Add phase/direction/bin-role/order-policy cohorts; split shadow/live/diagnostic; forbid dormant promotion.  |
| Tests                  | Cohort isolation; unknown key quarantine; drift detection; no shadow in live metrics.                       |
| Commands               | `pytest tests/test_edge_observation.py tests/test_attribution_drift.py tests/test_pnl_flow_and_audit.py`    |
| Launch impact          | Prevents invalid promotion.                                                                                 |
| Rollback               | Reports read old four-key cohorts but mark inverse strategies blocked.                                      |
| Hidden branches closed | Shadow promoted accidentally; reporting mixes cohorts.                                                      |

### Stage 5 — Real-market improvement layer

| Field                  | Packet                                                                                          |
| ---------------------- | ----------------------------------------------------------------------------------------------- |
| Objective              | Add non-critical but high-upside strategy improvements.                                         |
| Dependency             | Stage 4.                                                                                        |
| Files                  | Market WS ingestion, scanner, shadow evaluator, strategy docs/tests.                            |
| Forbidden files        | Live submit path initially.                                                                     |
| Tasks                  | Shadow `price_drift_reaction`; shadow family-relative mispricing; collect quote-event evidence. |
| Tests                  | WS event dedupe; cooldown; quote hash causality; no live submit.                                |
| Commands               | `pytest tests/test_user_channel_ingest.py tests/test_ws_poll_reaction.py tests/test_market*.py` |
| Launch impact          | Improves opportunity capture after safe spine.                                                  |
| Rollback               | Disable market-channel listener.                                                                |
| Hidden branches closed | Better missing strategy never considered.                                                       |

### Stage 6 — Dormant/research/out-of-scope routing

| Field                  | Packet                                                                                         |
| ---------------------- | ---------------------------------------------------------------------------------------------- |
| Objective              | Route weak/premature/high-complexity ideas.                                                    |
| Dependency             | Stage 5.                                                                                       |
| Files                  | `REMAINING_TASKS.md`, strategy backlog doc, launch audit docs.                                 |
| Forbidden files        | Live trading code.                                                                             |
| Tasks                  | Move not-now items; document revisit gates; close false launch dependencies.                   |
| Tests                  | Documentation/static grep for no live dormant flags.                                           |
| Commands               | `pytest tests/test_no_deprecated_make_family_id_calls.py tests/test_architecture_contracts.py` |
| Launch impact          | Keeps launch focused.                                                                          |
| Rollback               | Restore backlog entries.                                                                       |
| Hidden branches closed | Out-of-scope silently required for launch.                                                     |

---

## 18. Codex/local-agent prompts

### Stage 0 prompt — Evidence lock / catalog truth

```text
Role: Zeus strategy-catalog auditor.

Read first:
- AGENTS.md
- src/AGENTS.md
- src/strategy/AGENTS.md
- src/execution/AGENTS.md
- src/state/AGENTS.md
- docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md
- docs/operations/task_2026-05-02_full_launch_audit/REMAINING_TASKS.md
- src/engine/discovery_mode.py
- src/engine/cycle_runner.py
- src/engine/cycle_runtime.py
- src/engine/evaluator.py
- config/settings.json
- src/state/edge_observation.py
- src/state/attribution_drift.py

Allowed files:
- docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md
- docs/operations/task_2026-05-02_full_launch_audit/REMAINING_TASKS.md
- tests/test_architecture_contracts.py or a new strategy catalog test

Forbidden files:
- src/execution/executor.py
- live adapter files
- DB migration files

Invariants:
- Do not assume STRATEGIES_AND_GAPS.md is authority.
- Do not add live strategy keys.
- Do not change trading behavior.
- strategy_key remains sole governance identity.

Tasks:
1. Add an “evidence lock” section listing actual canonical strategy keys, dormant candidates, feature flags, and runtime modes.
2. Mark all current draft claims as CONFIRMED / PARTIALLY_CONFIRMED / MISLEADING / INCOMPLETE / REVIEW_REQUIRED.
3. Add a static test proving docs’ canonical live keys match cycle_runner/cycle_runtime and edge_observation.
4. Document that native multi-bin buy-NO live flag is false by default.

Tests:
- pytest tests/test_architecture_contracts.py tests/test_edge_observation.py tests/test_attribution_drift.py

Commands:
- git diff -- docs/operations/task_2026-05-02_full_launch_audit tests
- pytest tests/test_architecture_contracts.py tests/test_edge_observation.py tests/test_attribution_drift.py

Closeout evidence:
- Show canonical keys found in code.
- Show dormant keys are not live.
- Show test output.

Rollback:
- Revert docs/test changes only.

Not-now constraints:
- Do not implement MIDDLE_STATE_HUNT.
- Do not wire shoulder_buy or center_sell live.
- Do not touch executor.
```

### Stage 1 prompt — Critical strategy live blockers

```text
Role: Zeus live-money strategy safety engineer.

Read first:
- Stage 0 evidence lock output
- src/engine/evaluator.py
- src/engine/cycle_runner.py
- src/engine/cycle_runtime.py
- src/strategy/market_analysis.py
- src/strategy/market_analysis_family_scan.py
- src/strategy/selection_family.py
- config/settings.json
- tests covering evaluator/strategy/executable reprice

Allowed files:
- src/engine/evaluator.py
- src/engine/cycle_runner.py
- src/engine/cycle_runtime.py
- config/settings.json only for flags/comments if needed
- tests/*strategy*, tests/*evaluator*, tests/*execution_price*

Forbidden files:
- src/venue/*
- production DB migrations
- report promotion code

Invariants:
- No live order may be created from an unclassified economic quadrant.
- Native buy-NO live remains disabled unless NATIVE_MULTIBIN_BUY_NO_LIVE is true and shadow flag is true.
- Existing live strategies must still pass executable snapshot repricing.
- Unknown/fallback classification must fail closed or shadow, never hide as opening_inertia.

Tasks:
1. Add explicit quadrant classification for buy_yes_center, buy_yes_shoulder, buy_no_center, buy_no_shoulder.
2. Keep live authority only for current allowed strategies: opening_inertia opening phase, center_buy fresh update, settlement_capture Day0. **(fix: PR #44 — added _LIVE_ALLOWED_STRATEGIES allowlist {settlement_capture, center_buy, opening_inertia}; shoulder_sell shadow-only)**
3. Downgrade buy-NO shoulder to shadow unless feature flags and evidence gates pass.
4. Block or shadow buy_yes_shoulder and buy_no_center; do not route them to existing live keys.
5. Persist rejection/shadow reasons.

Tests:
- buy_yes non-shoulder -> center_buy live-eligible only in update/fresh phase.
- buy_yes shoulder -> shoulder_buy shadow/blocked.
- buy_no shoulder -> shoulder_sell shadow unless flags live.
- buy_no center -> center_sell shadow/blocked.
- unknown fallback cannot produce live opening_inertia outside opening mode.
- NATIVE_MULTIBIN_BUY_NO_LIVE requires shadow flag.

Commands:
- pytest tests/test_evaluator.py tests/test_strategy*.py tests/test_architecture_contracts.py

Closeout evidence:
- Classification truth table.
- Test output.
- Diff showing no executor/venue changes.

Rollback:
- Revert classifier changes and keep all inverse quadrants blocked.

Not-now constraints:
- Do not add price-drift live strategy.
- Do not widen Day0 window in this stage.
```

### Stage 2 prompt — Minimal launch strategy portfolio

```text
Role: Zeus launch portfolio implementer.

Read first:
- Stage 1 closeout
- docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md
- src/engine/discovery_mode.py
- src/engine/cycle_runner.py
- src/engine/evaluator.py
- src/control/control_plane.py
- src/riskguard/policy.py

Allowed files:
- strategy policy config/code
- cycle/evaluator gates
- tests for launch matrix
- launch audit docs

Forbidden files:
- market WebSocket implementation
- CLOB adapter internals
- DB schema unless absolutely required

Invariants:
- Launch-live alpha set is settlement_capture, center_buy, opening_inertia only.
- shoulder_sell is shadow unless native NO promotion is explicitly enabled.
- shoulder_buy and center_sell are not live.
- risk_off_exit remains live risk-management, not alpha.

Tasks:
1. Encode launch strategy matrix: live/shadow/dormant statuses.
2. Gate settlement_capture by Day0 observation evidence, but leave full Day0 window change to Stage 3 if necessary.
3. Gate center_buy by fresh forecast-update phase.
4. Gate opening_inertia by opening phase only.
5. Add tests proving shadow strategies produce no execution intent.

Tests:
- Launch matrix tests.
- No live intent for shoulder_sell/shoulder_buy/center_sell by default.
- Live center_buy blocked on stale forecast.
- Live opening_inertia blocked outside opening mode.

Commands:
- pytest tests/test_evaluator.py tests/test_riskguard.py tests/test_architecture_contracts.py

Closeout evidence:
- Matrix table in docs.
- Passing tests.
- Feature flags unchanged or safer.

Rollback:
- Pause all alpha except settlement_capture via strategy policy.

Not-now constraints:
- Do not implement middle-state live entries.
- Do not promote native NO live.
```

### Stage 3 prompt — Sizing / exit / killswitch integration

```text
Role: Zeus sizing-exit-killswitch architect.

Read first:
- src/strategy/kelly.py
- src/strategy/risk_limits.py
- src/execution/exit_triggers.py
- src/execution/exit_lifecycle.py
- src/engine/monitor_refresh.py
- src/engine/cycle_runner.py
- config/settings.json
- src/riskguard/policy.py

Allowed files:
- sizing/risk policy modules
- exit triggers/lifecycle
- monitor refresh only if needed for held-token bid/depth evidence
- tests

Forbidden files:
- venue adapter internals
- unrelated data ingest
- report docs except closeout notes

Invariants:
- Kelly must only consume typed execution price.
- Per-trade safety cap removed; wallet bankroll plus RiskGuard/exposure gates remain the live sizing boundary.
- Sell exits must use held-token bid/depth.
- RED force-exit still works.
- DATA_DEGRADED blocks entries.

Tasks:
1. Add per-strategy/per-phase sizing multipliers without weakening existing cap. **(fix: PR #44 — per-strategy multiplier table per §11; live keys size, opening_inertia=0.5, shadow/dormant/unknown=0)**
2. Add settlement-hold policy: compare held-token bid/depth versus expected redemption/hold value with fee/time risk.
3. Add local killswitch states: strategy, market-family, city/date/metric, adapter, position.
4. Add operator-visible reasons for local blocks.
5. Ensure buy-NO exits stay native-space only.

Tests:
- Sizing multiplier matrix.
- Hold-to-resolution beats discounted bid.
- RED overrides hold.
- Local strategy pause blocks live but allows shadow.
- Held-token bid required for sell EV.
- Native buy-NO no double inversion.

Commands:
- pytest tests/test_riskguard.py tests/test_exit*.py tests/test_pnl_flow_and_audit.py tests/test_execution_price.py

Closeout evidence:
- Test output.
- Example block reasons.
- No exposure-policy bypass.

Rollback:
- Disable new multipliers via config defaults and retain existing RiskGuard/exposure gates.

Not-now constraints:
- Do not bypass wallet-bankroll or exposure policy without explicit operator approval.
- Do not make passive maker orders live.
```

### Stage 4 prompt — Promotion/reporting evidence

```text
Role: Zeus promotion-evidence and reporting engineer.

Read first:
- src/state/edge_observation.py
- src/state/attribution_drift.py
- src/state/db.py
- src/state/snapshot_repo.py
- src/state/venue_command_repo.py
- tests/test_edge_observation.py
- tests/test_attribution_drift.py
- tests/test_pnl_flow_and_audit.py

Allowed files:
- reporting/projection modules
- DB read/projection helpers
- tests
- docs/operations/task_2026-05-02_full_launch_audit/*

Forbidden files:
- live entry/execution logic
- CLOB adapter
- strategy classifier unless needed only to import canonical table

Invariants:
- No shadow/diagnostic row may count as live realized edge.
- Unknown strategy keys are quarantined.
- Dormant inverse strategies must have separate cohorts before promotion.
- Backtests without point-in-time orderbook/depth/fill are diagnostic only.

Tasks:
1. Add cohort axes: phase, discovery_mode, direction, bin_role, metric, posterior_mode, execution_mode, order_type, fill_status.
2. Separate live/shadow/diagnostic evidence in reports.
3. Extend attribution drift to detect quadrant-key mismatches where evidence exists.
4. Add promotion gate checks for native NO and dormant strategies.

Tests:
- Shadow excluded from live realized edge.
- Unknown key quarantined.
- buy_yes_shoulder not counted as shoulder_sell.
- buy_no_center not counted as opening_inertia.
- Backtest diagnostic label enforced.

Commands:
- pytest tests/test_edge_observation.py tests/test_attribution_drift.py tests/test_pnl_flow_and_audit.py tests/test_db.py

Closeout evidence:
- Example report rows showing cohort fields.
- Test output.

Rollback:
- Keep old reports but mark dormant/inverse cohorts non-promotable.

Not-now constraints:
- Do not enable native NO live.
- Do not change entry behavior.
```

### Stage 5 prompt — Real-market improvement layer

```text
Role: Zeus real-market opportunity shadow-strategy engineer.

Read first:
- Polymarket market WebSocket/orderbook docs
- src/ingest/polymarket_user_channel.py
- src/data/market_scanner.py
- src/state/snapshot_repo.py
- src/engine/evaluator.py
- src/strategy/market_fusion.py
- src/strategy/selection_family.py

Allowed files:
- market WebSocket shadow ingestion
- shadow evaluator trigger code
- snapshot/report tests
- docs strategy backlog

Forbidden files:
- live submit path
- executor live order placement
- native NO live flags

Invariants:
- Price-drift and family-relative strategies are shadow only.
- Every triggered re-eval must cite event timestamp and executable snapshot hash.
- Cooldown/flood control required.
- Tick-size changes must block stale order assumptions.

Tasks:
1. Implement shadow price_drift_reaction trigger on market-channel price/book/top-of-book events.
2. Persist event cause, token_id, old/new bid/ask, quote hash, and cooldown reason.
3. Add complete-family relative mispricing shadow calculator using MarketPriorDistribution only when family complete.
4. Produce no live execution intents.

Tests:
- WS event dedupe.
- Per-token cooldown.
- Tick-size change rejects stale snapshot. **(fix: PR #44 — 5s MAX_SNAPSHOT_AGE_SECONDS cap; rejects with executable_snapshot_stale)**
- Shadow only: live size zero and no submit.
- Family incomplete blocks relative strategy.

Commands:
- pytest tests/test_user_channel_ingest.py tests/test_ws_poll_reaction.py tests/test_strategy*.py

Closeout evidence:
- Shadow event samples.
- No live command rows.
- Test output.

Rollback:
- Disable market-channel shadow listener.

Not-now constraints:
- No live price drift.
- No live family-relative strategy.
```

### Stage 6 prompt — Dormant/research/out-of-scope routing

```text
Role: Zeus launch-scope curator.

Read first:
- docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md
- docs/operations/task_2026-05-02_full_launch_audit/REMAINING_TASKS.md
- Stage 0-5 closeouts

Allowed files:
- docs/operations/task_2026-05-02_full_launch_audit/*
- strategy backlog docs
- static architecture tests if needed

Forbidden files:
- live trading code
- execution adapter
- DB migrations

Invariants:
- Do not silently move live blockers to backlog.
- Do not treat improvement work as launch-critical.
- Do not let dormant strategy names imply live eligibility.

Tasks:
1. Route all not-now items with dependency-before-revisit.
2. Mark riskguard P0 and bankroll truth as non-strategy but launch-relevant.
3. Mark price drift, family-relative, opening widening, and native NO live promotion as post-launch improvements.
4. Add acceptance gates summary.

Tests:
- Static grep: no dormant live flag enabled.
- Architecture contract test still passes.

Commands:
- pytest tests/test_architecture_contracts.py

Closeout evidence:
- Updated REMAINING_TASKS.md sections.
- Strategy doc no longer implies dormant live eligibility.

Rollback:
- Revert docs only.

Not-now constraints:
- Do not implement code.
```

---

## 19. Hidden branch register

| Branch                                                            | Risk                              | Stage | Decision                                       | Test/gate             | Rollback                      | Validation status     |
| ----------------------------------------------------------------- | --------------------------------- | ----: | ---------------------------------------------- | --------------------- | ----------------------------- | --------------------- |
| Draft strategy accepted without challenge                         | Preserves weak live set           |     0 | Treat draft as hypothesis                      | Challenge ledger      | Revert doc authority language | `CONFIRMED` risk      |
| Active strategy not code-reachable                                | False launch assumption           |     0 | Verify reachability                            | Static catalog test   | Mark `REVIEW_REQUIRED`        | `PARTIALLY_CONFIRMED` |
| Code-reachable strategy not documented                            | Silent live behavior              |     0 | Search code paths                              | Catalog diff test     | Quarantine                    | `REVIEW_REQUIRED`     |
| Dormant strategy accidentally live                                | Live ungoverned risk              |     1 | Shadow/block inverse quadrants                 | Classifier tests      | Flags false                   | `CONFIRMED` risk      |
| Active strategy only works in model space                         | Fake edge                         |     1 | Require executable reprice                     | Snapshot tests        | Block submit                  | `CONFIRMED` risk      |
| Strategy lacks market phase gate                                  | Wrong lifecycle trade             |   1/2 | Add phase taxonomy                             | Phase tests           | Disable widened mode          | `CONFIRMED`           |
| Strategy lacks quote/liquidity gate                               | Bad fill economics                |     1 | Mandatory snapshot/depth                       | Depth tests           | Block live                    | `PARTIALLY_CONFIRMED` |
| Strategy has sizing but no exit                                   | Capital trap                      |     3 | Position-lot exit policy                       | Exit tests            | Hold-only conservative        | `PARTIALLY_CONFIRMED` |
| Strategy has entry but no killswitch                              | Local failure becomes global loss |     3 | Strategy/local kills                           | Pause tests           | Global pause                  | `INCOMPLETE`          |
| Sizing generic when risk differs                                  | Overbet tail/day0                 |     3 | Strategy multipliers                           | Sizing matrix         | Base Kelly cap                | `CONFIRMED`           |
| Exit generic when lifecycle differs                               | Premature/late exit               |     3 | Lot/risk/phase exit                            | Hold tests            | Existing exit triggers        | `CONFIRMED`           |
| Killswitch global when failure is local                           | Over/under halt                   |     3 | Layered kills                                  | Local kill tests      | Global reduce-only            | `INCOMPLETE`          |
| Stale forecast used as signal                                     | Fake model edge                   |   1/2 | Freshness gate                                 | Stale forecast tests  | Shadow only                   | `CONFIRMED`           |
| Stale quote used as signal                                        | Fake executable edge              |     1 | Snapshot freshness                             | Snapshot expiry test  | Reject                        | `CONFIRMED`           |
| Family incomplete but strategy assumes completeness               | Bad relative pricing              |     5 | Family completeness gate                       | Family tests          | Shadow block                  | `CONFIRMED`           |
| FDR selects candidate but strategy identity changes before submit | Evidence drift                    |   1/4 | Persist strategy identity in decision envelope | Drift tests           | Block submit                  | `REVIEW_REQUIRED`     |
| Model-edge ignores spread/fee/depth                               | Bad EV                            |     1 | Reprice at executable snapshot                 | Depth/fee tests       | Reject                        | `CONFIRMED`           |
| Day0 strategy uses early-window gates                             | Wrong source/phase                |     3 | Day0 phase split                               | Local-day tests       | `<6h` strict                  | `CONFIRMED`           |
| Near-close ignores cancel/fill risk                               | Adverse selection                 |     5 | Immediate order policy shadow                  | FAK/FOK tests         | Disable near-close live       | `CONFIRMED`           |
| Dormant strategy pollutes reports                                 | Invalid promotion                 |     4 | Separate cohorts                               | Report tests          | Exclude dormant               | `CONFIRMED`           |
| Shadow strategy promoted accidentally                             | Live loss                         |     4 | Promotion gate                                 | Shadow exclusion test | Mark diagnostic               | `CONFIRMED`           |
| Exit uses current price instead of held-token bid                 | Bad exit EV                       |     3 | Held-token bid required                        | Exit bid test         | Hold                          | `PARTIALLY_CONFIRMED` |
| Strategy reporting mixes semantic cohorts                         | Bad decisions                     |     4 | Cohort axes                                    | Report tests          | Quarantine                    | `CONFIRMED`           |
| Out-of-scope item silently required for launch                    | Blocker missed                    |     6 | Routing with launch relevance                  | Acceptance gates      | Move back to blocker          | `CONFIRMED`           |
| Better missing strategy never considered                          | Overfit catalog                   |     5 | Candidate search                               | Strategy review       | Backlog                       | `CONFIRMED`           |
| Overfitting launch strategy to current code                       | Weak architecture                 |   0-6 | First-principles map                           | Tribunal              | Manual review                 | `CONFIRMED`           |

---

## 20. Acceptance gates

| Gate                            | Acceptance criteria                                                                                                                                      |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Strategy catalog accepted       | Code, docs, reports, and feature flags agree on live/shadow/dormant keys; unknown keys quarantine.                                                       |
| Active launch strategy accepted | `settlement_capture`, `center_buy`, and `opening_inertia` only live under phase/executable gates; `shoulder_sell` shadow unless native NO live promoted. |
| Shadow strategy accepted        | Live size zero; executable snapshot evidence; separate report cohort; no live command rows.                                                              |
| Dormant strategy safe           | No runtime live path; no fallback live attribution; explicit revisit gate.                                                                               |
| Sizing accepted                 | Typed executable price; fee/depth/slippage/tick; strategy/phase multipliers; no per-trade cap; RiskGuard/exposure gates retained.                           |
| Exit accepted                   | Held-token bid/depth; lot-specific; hold-to-resolution policy; RED override; command truth.                                                              |
| Killswitch accepted             | Global and local layers; strategy/family/city/metric/adapter/position gates; operator override audited.                                                  |
| Reporting/promotion accepted    | Shadow/live/diagnostic separated; fill/exit/settlement required; no midpoint-only promotion.                                                             |
| Out-of-scope routing accepted   | Non-strategy blockers listed with launch relevance; improvements not blocking safe launch.                                                               |

---

## 21. Final verification loop

| Question                                                                                | Answer                                                                                                                                                          |
| --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Did you treat `STRATEGIES_AND_GAPS.md` as a draft hypothesis rather than authority?  | Yes. The final plan revises its catalog, order, and strategy statuses.                                                                                          |
| 2. Did you read the actual file?                                                        | Yes; the parse in §1 is based on the fetched dossier.                                                                                                           |
| 3. Did you verify the strategy catalog against code?                                    | Yes; four canonical keys are confirmed in `cycle_runner`, `cycle_runtime`, and reporting modules.                                                               |
| 4. Did you reconstruct the real market opportunity space independently?                 | Yes; §4 and §5 map weather lifecycle windows and CLOB-executable requirements.                                                                                  |
| 5. Did you compare current strategies against better possible strategies?               | Yes; §6 downgrades, keeps, redesigns, and adds shadow/not-now strategies.                                                                                       |
| 6. Did you preserve all six design gaps while challenging their framing?                | Yes; §7 covers §3.1–§3.6 individually.                                                                                                                          |
| 7. Did you validate or revise §4 dependency order?                                      | Yes; §8 rejects the draft order and provides a corrected graph.                                                                                                 |
| 8. Did you define the best launch strategy portfolio?                                   | Yes; §10 defines the minimal launch-safe portfolio.                                                                                                             |
| 9. Did you separate live blockers, promotion blockers, improvements, and out-of-scope?  | Yes; labels are used throughout §§7, 16, 19, and 20.                                                                                                            |
| 10. Did you define sizing, exit, and killswitch at the right level?                     | Yes; sizing is strategy/phase/liquidity-aware, exit is lot/risk-state-aware, killswitch is layered.                                                             |
| 11. Did you prevent strategy-level live-money errors?                                   | The design gates prevent unclassified quadrant live trades, stale forecast live entries, native NO accidental live trades, and posterior-only edge submissions. |
| 12. Did you produce Codex-executable implementation prompts?                            | Yes; §18 provides staged paste-ready prompts.                                                                                                                   |
| 13. Did you avoid optimizing around the existing draft instead of the real best design? | Yes; the final portfolio is smaller, stricter, and more execution-aware than the draft’s implied active set.                                                    |

[1]: https://docs.polymarket.com/developers/CLOB/orders/onchain-order-info "https://docs.polymarket.com/developers/CLOB/orders/onchain-order-info"
[2]: https://docs.polymarket.com/trading/orderbook "https://docs.polymarket.com/trading/orderbook"
[3]: https://docs.polymarket.com/trading/fees "https://docs.polymarket.com/trading/fees"
