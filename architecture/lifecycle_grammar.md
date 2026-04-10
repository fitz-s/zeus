# Lifecycle Grammar — Canonical Status Strings

**Status**: DRAFT — documents current usage, NOT yet enforced.  
**Scope**: 155 instances across 14 files use status strings inconsistently.  
**Action**: This document maps current strings to canonical meanings. A migration PR will follow.

## Current Status Strings (as-is)

| String | Canonical Meaning | Files Using (count) |
|--------|------------------|---------------------|
| `"entered"` | Position opened, fill confirmed | evaluator, cycle_runner, chronicler |
| `"filled"` | Order fill received | strategy_tracker, chronicler |
| `"exited"` | Position closed via signal exit | exit_lifecycle, harvester, cycle_runner |
| `"settled"` | Position closed via settlement | harvester, chronicler |
| `"expired"` | **AMBIGUOUS** — used for both "order timed out" and "market expired" | cycle_runner, exit_lifecycle |
| `"cancelled"` | Order cancelled before fill | executor, cycle_runner |
| `"pending"` | Order submitted, awaiting fill | executor |
| `"rejected"` | Order rejected by exchange | executor |

## Proposed Canonical Grammar

| Canonical | Old Aliases | Meaning |
|-----------|------------|---------|
| `ENTERED` | `entered`, `filled` | Position open with confirmed fill |
| `EXITED_SIGNAL` | `exited` | Position closed by signal-driven exit trigger |
| `EXITED_SETTLEMENT` | `settled` | Position closed by market settlement |
| `ORDER_EXPIRED` | `expired` (order context) | Limit order timed out without fill |
| `MARKET_EXPIRED` | `expired` (market context) | Market reached resolution deadline |
| `CANCELLED` | `cancelled` | Order cancelled before fill |
| `PENDING` | `pending` | Order submitted, awaiting exchange response |
| `REJECTED` | `rejected` | Order rejected by exchange |

## Migration Plan

1. Create `src/state/lifecycle_status.py` with `LifecycleStatus` enum
2. Add string normalization function that maps old → new
3. Write code path by path, starting with `chronicler.py` (lowest risk)
4. Add integration tests that verify DB writes use canonical strings
5. After all code paths updated, add structural lint rule
6. Remove old string literals in a final cleanup commit

## Tech Debt Notes

- F1 (exit authority consolidation) must be completed BEFORE grammar migration — changing status strings while exit paths are split across 3 files would create combinatorial risk
- `"expired"` is the most dangerous ambiguity — an order expiring vs a market expiring have opposite implications for position state
