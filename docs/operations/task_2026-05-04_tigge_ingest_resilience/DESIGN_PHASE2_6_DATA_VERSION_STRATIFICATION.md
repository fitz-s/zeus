# Phase 2.6 Design: Data-Version Stratification — TIGGE vs ECMWF Open Data Source Asymmetry

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04 (post-merge — PR #55 + #56 both landed)
**Author:** Claude Opus 4.7
**Authority basis:** critic-opus BLOCKER 3 (2026-05-04) — "Training/serving data-source asymmetry is deeper than 00z/12z; Phase 2 does not solve it."

> **Status (2026-05-04 post-merge):** Phase 2.6 source_family +
> data_version stratification landed on main via PR #55 (schema,
> `MetricIdentity.source_family`, `derive_phase2_keys_from_ens_result`,
> `UNKNOWN_FORECAST_SOURCE_FAMILY` rejection gate).  References to
> **Phase 2.5 transfer policy** in this doc are historical — that
> piece was replaced by PR #56's `MarketPhaseEvidence` +
> `oracle_evidence_status`.  See `POST_PR55_PR56_REALIGNMENT.md`.

---

## What Phase 2 alone misses

Phase 2 (cycle stratification within Platt buckets) splits `(metric, city, season, source_id, cycle_hour, horizon_profile, data_version)` correctly **per source**. But it does not address the fact that:

```
Current Platt training corpus:
  platt_models_v2 active rows: 199 high + 204 low = 403 rows
  ALL have data_version IN ('tigge_mx2t6_local_calendar_day_max_v1',
                            'tigge_mn2t6_local_calendar_day_min_v1')
  100% TIGGE archive (0.5° resolution, multi-center, 48h embargoed)

Current live serving:
  src/types/metric_identity.py:78-90 hardcodes:
    HIGH_LOCALDAY_MAX.data_version = 'tigge_mx2t6_local_calendar_day_max_v1'
    LOW_LOCALDAY_MIN.data_version  = 'tigge_mn2t6_local_calendar_day_min_v1'
  src/calibration/manager.py:239-260 selects Platt by metric_identity.data_version
  → EVERY live forecast (even ECMWF Open Data 0.25° rows) is calibrated against TIGGE-trained Platt

Live forecast actual provenance:
  ensemble_snapshots_v2 rows from data daemon's Open Data path:
    data_version IN ('ecmwf_opendata_mx2t6_local_calendar_day_max_v1',
                     'ecmwf_opendata_mn2t6_local_calendar_day_min_v1')
  These rows NEVER form calibration_pairs_v2 in the current pipeline.
  calibration_pairs_v2 with data_version LIKE 'ecmwf_opendata_%': 0 rows.
```

## Why this is a foundation-layer bug, not a stratification gap

TIGGE archive and ECMWF Open Data come from the same model family (IFS/ENS) but are **different physical products**:

| Property | TIGGE | ECMWF Open Data |
|---|---|---|
| Native resolution | 0.5° (~50 km) | 0.25° / 0.4° (~25-40 km) |
| Member count | 51 | 51 |
| Embargo | 48h | none |
| Format | GRIB1 | GRIB2 |
| Latency to availability | 48h (post-embargo) | ~6-8h |
| Spatial interpolation to city grid | TIGGE-side post-processing | ECMWF-side post-processing |

For city-grid extracted ensemble members (the actual `members_json` we store), the resolution difference translates to:

- Different ensemble spread at the same lead and target_date (smaller spread typical at higher resolution)
- Different bin-boundary `p_raw` (since the city-grid temperature distribution discretization differs)
- Different residual-error structure (resolution-dependent biases, e.g., orographic underspecification at 0.5°)

**A Platt model fit on TIGGE residuals is mathematically inappropriate for OpenData forecasts even at exactly the same (city, season, cycle, lead, metric).** The error term has different variance and potentially different mean.

This is not a small ε. It is a categorical foundation-layer error in the calibration pipeline.

## Decision

Add `source_family` (or use `data_version` prefix directly) as a **bucket-level stratifier** in Platt models AND make `MetricIdentity.data_version` data-source-aware at the live evaluator boundary.

### Three changes (all required jointly)

#### Change 1: `MetricIdentity` is no longer hardcoded to TIGGE

```python
# src/types/metric_identity.py — REVISED
@dataclass(frozen=True)
class MetricIdentity:
    temperature_metric: str
    physical_quantity: str
    observation_field: str
    data_version: str  # No longer a class constant — passed by caller

    @classmethod
    def for_high_localday_max(cls, source_family: str) -> "MetricIdentity":
        DV_MAP = {
            "tigge": "tigge_mx2t6_local_calendar_day_max_v1",
            "ecmwf_opendata": "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        }
        if source_family not in DV_MAP:
            raise ValueError(f"Unknown source_family: {source_family}")
        return cls(
            temperature_metric="high",
            physical_quantity="temperature_2m_max",
            observation_field="high_temp",
            data_version=DV_MAP[source_family],
        )
```

Removes the constants `HIGH_LOCALDAY_MAX` and `LOW_LOCALDAY_MIN` (or makes them call-site-bound to a default that the live evaluator overrides). All callers must pass `source_family`.

#### Change 2: Live evaluator threads source_family through

```python
# src/engine/evaluator.py — calibration lookup site
forecast_source_family = (
    "ecmwf_opendata" if executable_forecast.data_version.startswith("ecmwf_opendata_")
    else "tigge" if executable_forecast.data_version.startswith("tigge_")
    else None
)
if forecast_source_family is None:
    return reject(stage="UNKNOWN_FORECAST_SOURCE_FAMILY",
                  reason=executable_forecast.data_version)

metric_identity = MetricIdentity.for_high_localday_max(forecast_source_family)
calibrator = get_calibrator(
    temperature_metric=metric_identity.temperature_metric,
    cluster=city.cluster,
    season=season,
    data_version=metric_identity.data_version,  # source-aware
    cycle=executable_forecast.cycle_hour_utc,
    source_id=executable_forecast.source_id,
    horizon_profile=executable_forecast.horizon_profile,
    input_space=DEFAULT_INPUT_SPACE,
)
```

#### Change 3: Calibration pairs flow from BOTH sources

The current pair-formation pipeline builds pairs only from TIGGE-rooted forecasts. Add an Open-Data pair builder:

```python
# src/calibration/pair_builder.py — NEW
def build_pairs_from_opendata(
    *,
    target_date_range: tuple[str, str],
    target_metric: str,
) -> list[CalibrationPair]:
    """
    Build calibration_pairs_v2 rows from ECMWF Open Data forecasts paired
    with verified observed temperatures (settlements_v2).
    
    Mirrors build_pairs_from_tigge but:
    - Sources from ensemble_snapshots_v2 WHERE data_version LIKE 'ecmwf_opendata_%'
    - Uses settlements_v2 with authority='VERIFIED' as outcome ground truth
    - Joins via (city, target_date, metric)
    - Writes pairs with data_version='ecmwf_opendata_*_v1' so they form
      a separate Platt bucket
    """
```

Run the pair builder backwards from the day Open Data ingest started → today. Result: calibration_pairs_v2 grows non-zero `ecmwf_opendata_*` rows. Refit Platt on the OpenData bucket independently.

## Bucket maturity reality check

The current critic-opus audit shows 15 LOW-track buckets between n=15 and n=39 samples for TIGGE. Adding a parallel `ecmwf_opendata_*` data_version dimension means OpenData buckets START at n=0 (since calibration_pairs_v2 has 0 OpenData rows today).

**OpenData buckets will be `level=4 immature` (< 15 samples) for at least 15 trading days per (city, season, metric, cycle).** During this period:

| Forecast source | Cycle | Calibration availability | Live decision |
|---|---|---|---|
| TIGGE archive | 00z | Mature TIGGE 00z Platt (n > 1080 high, > 15 low) | LIVE eligible (after Phase 2 cycle stratification + 90-day 12z backfill matures TIGGE 12z buckets) |
| TIGGE archive | 12z | Maturing as 90-day backfill lands | SHADOW until n > 15 per bucket |
| ECMWF Open Data | 00z | n=0 today | SHADOW until pair builder + n > 15 |
| ECMWF Open Data | 12z | n=0 today | SHADOW until pair builder + n > 15 |

**The unlock cannot proceed until OpenData buckets mature**, OR the system explicitly accepts SHADOW-only on OpenData (in which case live trading is impossible since live forecasts ARE OpenData).

There is no shortcut. The TIGGE-trained Platt cannot be promoted to OpenData live by string mapping (Phase 2.5 already documented this).

## Sequencing

Phase 2.6 is **a precondition for unlock**, not a post-hoc improvement:

```
Phase 1   (12z code + 90-day backfill — sonnet running)
   ↓
Phase 2   (cycle/source/horizon Platt stratification schema + refit)
   ↓
Phase 2.5 (calibration_transfer_policy → ForecastCalibrationDomain + validated_transfers table)
   ↓
Phase 2.6 (data-version stratification + OpenData pair builder + initial OpenData calibration window)  ← THIS DOC
   ↓
Phase 2.75 (robust lower-bound Kelly)
   ↓
Phase 3   (ENSEMBLE_MODEL_SOURCE_MAP routing + ecmwf_open_data registry authorization)
   ↓
Operator unlock with all six unlock checklist items
```

## Out of scope for Phase 2.6

- Backfilling OpenData calibration pairs back to 2024-01-01 (hard — Open Data archive is shorter than TIGGE; check ECMWF retention)
- Cross-source ensemble blending (research)
- Live-time downscaling from 0.5° TIGGE to 0.25° OpenData city grid (would require model-specific physics; deferred)

## Implementation tasks

| # | Task | Owner | Acceptance |
|---|---|---|---|
| 1 | Refactor MetricIdentity to take source_family | sonnet | unit tests pass; legacy callers explicit-default `tigge` |
| 2 | Add OpenData pair builder | sonnet | `WHERE data_version LIKE 'ecmwf_opendata_%'` produces non-zero pair count after run |
| 3 | Refit Platt for OpenData bucket | sonnet | platt_models_v2 has rows with `data_version LIKE 'ecmwf_opendata_%'` |
| 4 | Live evaluator threads source_family | sonnet | integration test: OpenData forecast → OpenData Platt; TIGGE forecast → TIGGE Platt |
| 5 | Add UNKNOWN_FORECAST_SOURCE_FAMILY reject path | sonnet | rejection_reason_json carries the unknown data_version string |
| 6 | Validation: Brier comparison TIGGE-on-OpenData vs OpenData-on-OpenData | operator + sonnet | OpenData-on-OpenData Brier ≤ TIGGE-on-OpenData Brier; otherwise revert |

## Open questions

1. **OpenData calibration history depth**: how far back does ECMWF Open Data archive go? If it's only 30 days, we hit the 15-sample threshold in 15 days but with weak per-bucket stats. Worth confirming archive retention.

2. **Settlement availability**: pairs need `settlements_v2.authority='VERIFIED'` outcomes. Does that table have rows for the same target_date range as the Open Data ingest history? Sample-check before running pair builder.

3. **Bucket collision**: if the v2 schema's existing unique index on platt_models_v2 includes `data_version`, no migration needed. If not, the index must extend to include `data_version` to allow distinct OpenData and TIGGE rows.

## Critic re-review trigger

After Phase 2.6 implementation lands, dispatch critic-opus a second time with the explicit prompt: "verify TIGGE-vs-OpenData asymmetry per BLOCKER 3 is now resolved at the math layer (not just the schema layer)." Acceptance is critic explicit APPROVE on BLOCKER 3.
