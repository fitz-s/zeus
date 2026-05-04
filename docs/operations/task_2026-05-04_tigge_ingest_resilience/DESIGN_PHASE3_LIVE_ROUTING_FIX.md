# Phase 3 Design: Live Entry Routing Fix (#136)

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04
**Author:** Claude Opus 4.7
**Authority basis:** Operator directive 2026-05-04 — "下单数据要和训练数据对齐" + haiku data alignment research (ECMWF Open Data is the only training-aligned live source for TIGGE-trained models).

---

## Problem

Current `ENSEMBLE_MODEL_SOURCE_MAP[ecmwf_ifs025]` routes to `openmeteo_ensemble_ecmwf_ifs025`. The forecast_source_registry rejects this source with `degradation_level=DEGRADED_FORECAST_FALLBACK` for `entry_primary` role. Result: 74/200 SIGNAL_QUALITY rejections.

Even if we authorized openmeteo, that would create training/serving skew because:
- Open-Meteo applies 1-hour temporal interpolation
- Open-Meteo is a third-party broker, not the source authority
- Member identity may differ from native ECMWF ENS

## Decision

Route `ecmwf_ifs025` model to `ecmwf_open_data` source. This is the public ECMWF feed already ingested by the data daemon — same model, same 51-member ENS, raw GRIB2, no third-party post-processing.

## Files affected

### `src/data/forecast_source_registry.py`

Currently (line ~142, per haiku #2 finding):
```python
"openmeteo_ensemble_ecmwf_ifs025": SourceProfile(
    allowed_roles=("monitor_fallback", "diagnostic"),
    degradation_level="DEGRADED_FORECAST_FALLBACK",
    ...
)
```

Add `ecmwf_open_data` profile authorized for `entry_primary`:
```python
"ecmwf_open_data": SourceProfile(
    allowed_roles=("entry_primary", "training_archive_alignment", "monitor_fallback"),
    degradation_level="OK",
    description="ECMWF Open Data IFS/ENS (real-time public feed). 4 cycles/day, 51 members at 0.25°. Training-aligned with TIGGE archive (same model, same ensemble structure).",
    authority_basis="2026-05-04 operator directive — training/serving alignment",
)
```

### `src/data/ENSEMBLE_MODEL_SOURCE_MAP` (location TBD; may be in evaluator or a config)

Currently:
```python
ENSEMBLE_MODEL_SOURCE_MAP = {
    "ecmwf_ifs025": "openmeteo_ensemble_ecmwf_ifs025",
    ...
}
```

Change to:
```python
ENSEMBLE_MODEL_SOURCE_MAP = {
    "ecmwf_ifs025": "ecmwf_open_data",
    ...
}
```

If openmeteo is still wanted as a fallback/diagnostic (e.g., to shadow-trade for monitoring), keep its profile but with non-entry roles only.

## Verification

After the change:

1. SQL: `SELECT COUNT(*) FROM ensemble_snapshots_v2 WHERE data_version LIKE 'ecmwf_opendata%' AND issue_time >= datetime('now','-12 hours');` — recent rows must exist
2. Smoke test: trigger a live cycle (after lock lifted) and verify `opportunity_fact` rejection_reason_json no longer contains `DEGRADED_FORECAST_FALLBACK`
3. Authority audit: `forecast_source_registry.gate_source_role('ecmwf_open_data', 'entry_primary')` returns OK

## Tests

```python
# tests/test_entry_primary_routes_to_ecmwf_open_data.py

def test_ensemble_model_source_map_for_ifs025():
    """ecmwf_ifs025 model must route to ecmwf_open_data source for training/serving alignment."""
    from src.data.forecast_source_registry import ENSEMBLE_MODEL_SOURCE_MAP
    assert ENSEMBLE_MODEL_SOURCE_MAP["ecmwf_ifs025"] == "ecmwf_open_data"

def test_ecmwf_open_data_authorized_for_entry_primary():
    from src.data.forecast_source_registry import gate_source_role
    result = gate_source_role("ecmwf_open_data", "entry_primary")
    assert result.ok, f"Expected OK, got {result}"

def test_openmeteo_remains_unauthorized_for_entry_primary():
    """Openmeteo is third-party; must not feed entry_primary even if it returns data."""
    from src.data.forecast_source_registry import gate_source_role
    result = gate_source_role("openmeteo_ensemble_ecmwf_ifs025", "entry_primary")
    assert not result.ok
    assert "monitor_fallback" in result.allowed_roles or "diagnostic" in result.allowed_roles
```

## Risk

- **Race with Phase 1 + Phase 2**: This Phase 3 fix unblocks live routing. If applied BEFORE Platt cycle stratification (Phase 2), the routing succeeds but evaluator applies a 00z-only-trained Platt to whichever cycle the live forecast happens to be (likely 00z since live cron pulls 00z most often, but 12z forecasts will be miscalibrated). Therefore Phase 3 must NOT be merged before Phase 2.
- **Data availability**: ECMWF Open Data ingest is currently working (verified by source_run table showing 3 SUCCESS runs at 2026-05-04 01:11). If that pipeline dies, we need monitoring; but that's a Phase-1-style boot resilience question, separate from this routing fix.
- **Authority semantics**: Adding `entry_primary` to ecmwf_open_data's allowed_roles is operator-level decision. Document clearly.

## Sequencing within PR #55

This Phase 3 doc lives on PR #55 as a sibling to Phase 1 (Sonnet code) and Phase 2 (Platt stratification). The actual code change for Phase 3 is small (~10 lines) but should NOT land until:

1. Phase 1 merged
2. Phase 2 schema migration + refit done
3. Critic-opus review passed for the whole PR

Then Phase 3 lands as the final unlock-enabling change. Order matters: cycle stratification must be in place before the routing fix exposes 12z forecasts to the evaluator.

## Out of scope

- Replacing the entire ENSEMBLE_MODEL_SOURCE_MAP architecture (e.g., dispatcher-pattern)
- Adding additional centers (NCEP GFS, JMA, etc.) for ensemble diversification
- Multi-model fusion at evaluation time
