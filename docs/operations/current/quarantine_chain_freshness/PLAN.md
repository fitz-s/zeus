# quarantine_chain_freshness -- Plan

Date: 2026-07-11
Branch: `p2-quarantine-chain-freshness`
Status: active

## Background

The current positions API returns positive balances for four quarantined weather
positions, but reconciliation treats every quarantined row as inactive and never
refreshes its positive-observation timestamp.  The global wealth witness correctly
loads current-money-risk quarantine rows, then permanently fails after the 30-minute
freshness bound with `CURRENT_WEALTH_POSITION_CHAIN_EXPIRED`.

## Scope

Preserve quarantine and its authority reason.  When the current chain response
contains the quarantined token, refresh only its chain economics and positive
observation time through the canonical append+projection path.  Do not change the
lifecycle phase, submit/cancel/exit an order, or write a live DB during implementation.

## Deliverables
- Reconciliation admits current-money-risk quarantines to positive chain refresh.
- Repeated fresh observations remain idempotent and do not create an event storm.
- A relationship test proves phase and quarantine authority remain unchanged.

## Verification
- `pytest -q -p no:cacheprovider tests/test_chain_shares_persist_synced.py`
- scoped state invariant tests from `src/state/AGENTS.md`
- probability/solver evaluator remains green
