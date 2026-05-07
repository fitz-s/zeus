# Cloud Extract Patch — 2026-05-07

**File**: `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/extract_open_ens_localday.py`
**Backup**: `extract_open_ens_localday.py.bak_pre_mx2t3_2026_05_07` (same directory)
**No git** (cloud path, outside zeus repo)

## Rationale

ECMWF Open Data enfo stream deprecated mx2t6/mn2t6 (6h sliding aggregations).
The stream now serves mx2t3/mn2t3 (3h native) as the native product.
The extractor TRACKS dict was using old paramId/short_name, causing 0 GRIB records matched.

Authority: `architecture/zeus_grid_resolution_authority_2026_05_07.yaml` A1+3h decision.

## Diff (lines 89–110)

```diff
92,94c92,94
<         open_data_param="mx2t6",
<         short_name="mx2t6",
<         paramId=121,
---
>         open_data_param="mx2t3",
>         short_name="mx2t3",
>         paramId=228026,
96,97c96,97
<         data_version="ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
<         physical_quantity="mx2t6_local_calendar_day_max",
---
>         data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
>         physical_quantity="mx2t3_local_calendar_day_max",
103,105c103,105
<         open_data_param="mn2t6",
<         short_name="mn2t6",
<         paramId=122,
---
>         open_data_param="mn2t3",
>         short_name="mn2t3",
>         paramId=228027,
107,108c107,108
<         data_version="ecmwf_opendata_mn2t6_local_calendar_day_min_v1",
<         physical_quantity="mn2t6_local_calendar_day_min",
---
>         data_version="ecmwf_opendata_mn2t3_local_calendar_day_min_v1",
>         physical_quantity="mn2t3_local_calendar_day_min",
```

## Track names preserved

- `mx2t6_high` — zeus-internal label, not changed
- `mn2t6_low` — zeus-internal label, not changed

## Also patched (same session)

- `download_ecmwf_open_ens.py`: default `--param` updated `["mx2t6","mn2t6"]` → `["mx2t3","mn2t3"]`
