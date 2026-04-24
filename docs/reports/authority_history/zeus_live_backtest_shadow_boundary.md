# Zeus Live / Backtest / Shadow Boundary

Status: historical report evidence; superseded by `docs/authority/zeus_current_architecture.md`
Scope: Defines what each execution context may and may not do
Authority basis: Operator-established tribunal precedence (Phase 1, 2026-04-11)

---

## 1. Three execution contexts

| Context | May | May NOT |
|---------|-----|---------|
| **Live** | Act on real money, execute orders, mutate canonical DB truth, settle positions | Use unverified math, promote replay output as evidence, silently fall back to JSON |
| **Backtest** | Evaluate historical performance, compare strategies, report metrics | Authorize live math promotion, mutate live DB, claim promotion authority before replay is honest |
| **Shadow** | Observe live pipeline, collect instrumentation facts, report additive metrics | Block live execution, gate live entries, promote thresholds to live blockers without explicit cutover |

## 2. Non-negotiable rules

1. **Live may act. Backtest may evaluate. Shadow may observe.** No context may exceed its authority.
2. **No silent JSON fallback in live.** DB canonical truth is the only read authority for live position state. JSON exports are derived caches, never promoted back.
3. **No replay-derived promotion authority.** Until replay achieves full market-price linkage across all subjects, active sizing parity, and selection-family parity, its output may inform but NOT authorize live math changes. Market-price linkage is already tracked per-subject as `full`/`partial`/`none`; the remaining gaps are sizing and selection-family parity.
4. **No prestige-math live cutover.** A model that shows better backtested metrics does not earn live deployment. The promotion path is: shadow instrumentation -> candidate evaluation -> explicit operator approval -> live cutover.
5. **Shadow -> candidate -> live promotion path is mandatory.** No math change reaches live without passing through shadow observation and explicit human gate.

## 3. Shadow instrumentation boundary

These modules are shadow-only instrumentation. They collect facts and report metrics but do NOT gate live execution:

- `src/calibration/blocked_oos.py` -- blocked out-of-sample evaluation facts
- `src/calibration/effective_sample_size.py` -- decision-group sample accounting
- Day0 residual fact collection

Promotion thresholds computed by these modules may be reported in status_summary but are NOT live blockers until explicitly promoted via operator approval and a governance packet.

## 4. Replay demotion

`src/engine/replay.py` operates under `diagnostic_non_promotion` authority.

Current limitation reality (code-observed, not blanket):
- Market price linkage: tracked per-subject as `full`, `partial`, or `none` via `_market_price_linkage_limitations()`; PnL availability depends on linkage state
- Sizing: flat $5 (no active Kelly cascade)
- Bootstrap/FDR: not applied in replay path
- Coverage: gaps exist; provenance sources tracked per-outcome

The code is more nuanced than "no market price linkage" — it distinguishes linkage states and reports limitations explicitly. However, active sizing parity (Kelly) and selection-family parity (FDR) remain absent, so replay output remains non-promotional.

Until active sizing parity and selection-family parity are established, replay output carries `authority: "evaluation_only"`. It may:
- Report comparative metrics
- Surface regressions between code versions
- Inform hypothesis generation

It may NOT:
- Serve as promotion evidence for live math changes
- Override live-observed performance data
- Claim statistical significance without selection-family parity

## 5. Current state (Phase 1 complete)

- Live canonical read cutoff: DONE (P4, commit 1fc14ab)
- JSON fallback eliminated from live portfolio loader: DONE
- Paper mode decommissioned: DONE (Phase 1 complete)
- Shadow instrumentation boundary: DECLARED (this doc)
- Replay demotion: DECLARED (this doc)
- Live candidate-family FDR: ACTIVE for candidate/market/snapshot tested families; whole-cycle BH is not claimed

## 6. Promotion protocol

To promote any math change from shadow/backtest to live:

1. Shadow instrumentation must have collected >= 30 days of parallel data
2. Backtest evaluation must use honest replay (full market-price linkage, active sizing parity, and selection-family parity with the live control unit)
3. Candidate evaluation must show statistical improvement on the live-relevant metric
4. Operator must explicitly approve the cutover
5. A governance packet must document the promotion with rollback plan
6. Live cutover must be reversible for at least 7 days
