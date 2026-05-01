# Current State

Role: single live control pointer for the repo.

## Status

Zeus is live on `main`. Plan-pre5 R3 engineering hardening merged 2026-05-01.

- Branch: `main`
- Runtime entry: `src/main.py` (ZEUS_MODE=live)
- Posture: Phase 1 canary — `live_safety_cap_usd: 5.0`, `smoke_test_portfolio_cap_usd: 5.0` (config/settings.json)

## Active monitoring

| Packet | Directory |
|--------|-----------|
| Edge observation | `docs/operations/edge_observation/` |
| Attribution drift | `docs/operations/attribution_drift/` |
| WS/poll reaction | `docs/operations/ws_poll_reaction/` |
| Calibration observation | `docs/operations/calibration_observation/` |
| Learning loop | `docs/operations/learning_loop_observation/` |

## Active work items

Tail items in `docs/operations/known_gaps.md` § "Deferred items".

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Operations routing

- `docs/operations/AGENTS.md` — packet/package routing
- `docs/archive_registry.md` — archived packet lookup
- `architecture/history_lore.yaml` — durable lessons
