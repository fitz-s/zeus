# src/riskguard AGENTS — Zone K1 (Protective)

Module book: `docs/reference/modules/riskguard.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

RiskGuard is the **protective spine** — the fast, threshold-based reflex layer that halts trading before damage accumulates. Risk must change behavior (INV-05): if a risk level doesn't alter evaluator/sizing/execution outcomes, it's theater.

This is the only zone that can **stop Zeus from trading**. Every other zone produces analysis or decisions — riskguard enforces constraints.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `riskguard.py` | Risk computation from portfolio metrics — the main evaluator | HIGH — the enforcement engine |
| `risk_level.py` | RiskLevel enum + LEVEL_ACTIONS mapping | HIGH — behavior definitions |
| `policy.py` | StrategyPolicy — per-strategy gating, allocation/threshold multipliers | MEDIUM |
| `metrics.py` | Brier score and settlement-based metrics for calibration quality | MEDIUM |
| `discord_alerts.py` | Discord webhook alerts for halt/resume/warning (keychain-resolved) | LOW |

## Risk level behavior (INV-05 — each MUST change behavior)

| Level | Behavior |
|-------|----------|
| GREEN | Normal operation |
| YELLOW | No new entries, continue monitoring held positions |
| ORANGE | No new entries, exit positions at favorable prices |
| RED | Cancel all pending orders, exit all positions immediately |

Overall level = max of all individual levels. Computation error → RED (fail-closed).

## Domain rules

- Risk must change behavior, not just record warnings (INV-05)
- Protection must remain strategy-aware when strategy information exists
- Control and risk surfaces may tighten or pause; they may not silently rewrite truth
- 60-second tick cycle for reflex-layer checks

## Common mistakes

- Adding a new risk level that only logs a warning → INV-05 violation
- Coupling protective logic back into experimental math layers (K3) → zone violation
- Hiding portfolio-level heuristics as if they were strategy policy
- Inventing new governance keys (only `strategy_key` exists — INV-02)
- Making risk advisory (recommendations instead of enforcement) → defeats the purpose
