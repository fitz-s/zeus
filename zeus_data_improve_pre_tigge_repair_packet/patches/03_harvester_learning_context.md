# Patch 03 — harvester grouped learning context

## Goal

When settlement learning happens, write grouped-sample truth at the same time.

## Imports to add

```python
from src.execution.calibration_group_writer import make_group_id
```

## Patch idea

Inside the settlement loop, after the per-bin `add_calibration_pair(...)` writes complete, calculate:

```python
group_id = make_group_id(city.name, target_date, forecast_available_at)
```

Then insert/update `calibration_decision_group` using the same fields already present in `calibration_pairs`:

- city
- target_date
- forecast_available_at
- cluster
- season
- lead_days
- settlement_value
- winning_range_label
- bias_corrected
- n_pair_rows
- n_positive_rows

## Also patch this semantic gap

The branch still has an open gap where harvester learning does not reliably know whether the upstream probability path was bias-corrected. Add an explicit boolean in the learning path and persist it into both:

- `calibration_pairs.bias_corrected`
- `calibration_decision_group.bias_corrected`

## Minimal compatibility rule

Do **not** switch active calibration maturity to grouped counts in the same patch.
First write the grouped truth, then compare grouped-vs-pair maturity for one shadow window.
