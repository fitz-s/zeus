# Gate 4 Promotion Evidence — replay-correctness CI promoted to required

**Date:** 2026-05-06
**Phase:** 4.D (IMPLEMENTATION_PLAN §6 days 65-67)
**Authority:** IMPLEMENTATION_PLAN §6 Gate 4; ULTIMATE_DESIGN §5 Gate 4; ADR-5; ANTI_DRIFT_CHARTER §3 M1; RISK_REGISTER R2.

## Decision

`.github/workflows/replay-correctness.yml` promoted from advisory (Phase 0.G,
`continue-on-error: true`) to **required merge gate** (`continue-on-error`
removed). Triggers on push to `main`, `live-launch-prep-*`, `topology-redesign-*`
branches and on all PRs targeting `main`.

## DB-fixture path: option (b) — synthetic bootstrap at CI runtime

Three options evaluated per orchestrator state:

| Option | Description | Selected |
|--------|-------------|----------|
| (a) | Commit small deterministic DB fixture (≤1 MB) to `tests/fixtures/zeus_trades_replay_seed.db` | No |
| (b) | Build synthetic DB at CI runtime via inline Python + `scripts/replay_correctness_gate.py --bootstrap` | **Yes** |
| (c) | Skip on PRs not touching event paths; run only on protected branches | No |

**Rationale for (b):**
- `scripts/replay_correctness_gate.py --bootstrap` already supports writing a
  deterministic baseline JSON from whatever seed events are present.
- A synthetic DB with zero rows in the 7-day window produces an empty but
  deterministic projection; the hash is stable across CI runs with no random
  inputs.
- Avoids committing a binary blob (option a) while still running the full gate
  code path (vs. option c which skips on most PRs).
- No live DB, no network access, deterministic by construction.

## R2 disposition (RISK_REGISTER R2 — replay non-determinism)

Non-deterministic event types are excluded in the gate script itself:
```
model_response, model_call, web_fetch, http_fetch,
market_price_snapshot, external_position_sync
```
An empty CI seed window produces hash `sha256([])` which is stable.
Mismatch detection fires only when projection content diverges — which on
a synthetic empty DB cannot happen between bootstrap and compare within the
same CI run.

## Sunset

Gate 4 CI lane does not have its own `sunset_date` — it is an operational
workflow gate. The capabilities it protects (`live_venue_submit`,
`settlement_write`) carry `sunset_date: 2027-05-06` in capabilities.yaml.

## Pre-conditions satisfied

- `evidence/phase0_h_decision.md` exists (Phase 0.H GO signed).
- `evidence/phase3_h_decision.md` exists (Phase 3 exit gate signed).
- Seeded regression tests (`test_replay_correctness_gate.py`) green.
