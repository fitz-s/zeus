# config/reality_contracts AGENTS

External assumption contracts (INV-11). These YAML files define what Zeus assumes about external systems. When assumptions break, the contract system flags it.

## File registry

| File | Purpose |
|------|---------|
| `data.yaml` | Data source assumptions — ECMWF availability, WU reporting, Open-Meteo behavior |
| `economic.yaml` | Economic/market assumptions — vig rates, settlement rules, market liquidity |
| `execution.yaml` | Execution assumptions — Polymarket CLOB behavior, fill rates, order types |
| `protocol.yaml` | Protocol assumptions — Polymarket protocol rules, CTF token mechanics |

## Rules

- Each contract must have an expiry date or review trigger
- When an assumption is violated at runtime, the contract system must surface it (not silently degrade)
- Changes here affect trading behavior indirectly — review with domain context
- These contracts are tested by `tests/test_reality_contracts.py` (INV-11)
