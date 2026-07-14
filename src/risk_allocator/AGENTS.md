# src/risk_allocator AGENTS

R3 A2 capital-allocation governor. This package converts canonical exposure,
control-health, reconciliation, and drawdown evidence into blocking allocation
decisions before executor submission.

## File registry

| File | Purpose |
|------|---------|
| `AGENTS.md` | Local package routing and safety rules. |
| `__init__.py` | Public allocator/governor API exports. |
| `governor.py` | `CapPolicy`, `GovernorState`, `RiskAllocator`, `PortfolioGovernor`, and read-only canonical exposure helpers. |

## Rules

- This package may block, reduce-only, or summarize risk; it must never submit,
  cancel, redeem, or mutate production DB/state artifacts.
- `position_current` plus chain-confirmed shares/cost is canonical current
  exposure. Append-only `position_lots` contributes only active exposure not
  yet represented by that runtime position and backed by a live command row;
  de-duplicate by runtime `position_id`. Read both only through read-only seams.
- Preserve NC-NEW-I: keep OPTIMISTIC_EXPOSURE and CONFIRMED_EXPOSURE separate;
  optimistic exposure may have a lower configured capacity weight, confirmed
  exposure counts at full weight.
- Kill-switch behavior must be behavior-changing, not advisory-only.
- Do not change `RiskLevel` or lifecycle grammar from this package.
