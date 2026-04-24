# Zeus World-Data Forensic Audit Package

This package is the requested forensic audit output for `zeus-world.db.zip` on 2026-04-23.

## Verdict

The uploaded world DB is valuable as evidence and contains meaningful v2 scaffolding, but it is **not trustworthy as canonical live/replay/calibration/settlement truth**. The largest populated observation tables do not prove causal market readiness. Forecast, ensemble, calibration, market, and replay tables are empty; settlement rows are high-only and missing market identity; WU daily observation provenance is mostly empty; and fallback source rows are not sufficiently separated from canonical truth.

## How to use

1. Start with `00_executive_ruling.md`.
2. Read `01_external_mental_model.md` before judging Zeus-specific design.
3. Run every SQL file in `sql/` against a copy of the DB.
4. Apply fixes in the exact order in `17_apply_order.md`.
5. Use the Codex prompts in `prompts/` as bounded repair packets.

## Current blocker snapshot

| Table | Rows |
|---|---:|
| `calibration_pairs_v2` | 0 |
| `ensemble_snapshots_v2` | 0 |
| `forecasts` | 0 |
| `historical_forecasts_v2` | 0 |
| `market_events_v2` | 0 |
| `observation_instants_v2` | 1,813,662 |
| `observations_empty_provenance` | 39,431 |
| `settlements_market_slug_null` | 1,561 |
| `settlements_v2` | 0 |

## Audit limits

- No DB mutation was performed.
- No rebuild scripts were run.
- `graph.db` was used only as derived code-path context.
- LOCAL_VERIFICATION_REQUIRED remains for exact local checkout at the requested baseline commit and full test execution.
