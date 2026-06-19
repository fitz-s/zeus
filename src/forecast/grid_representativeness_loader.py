# Created: 2026-06-17
# Last reused or audited: 2026-06-17
# Authority basis: operator "finish v3" (2026-06-17) — the live wiring layer for
#   zeus_grid_coordinate_precision_upgrade_v3.md rule 5. Loads the persisted
#   config/grid_representativeness.json (per-(city,model) native-cell d_eff + delta_z,
#   built by scripts/build_grid_representativeness.py) and turns it into the per-instrument
#   sigma_repr^2 the fusion ADDS to the Sigma diagonal. Pure I/O + a call into the pure
#   representativeness_variance engine; NO fusion math here (the engine stays authority).
"""Grid-representativeness loader: config/grid_representativeness.json -> sigma_repr^2.

FAIL-SOFT BY DESIGN: a city or model absent from the table returns sigma_repr^2 = 0.0, so
the fusion is byte-identical for that instrument (no fabricated distance, no widen). The
loader never raises to the fusion path.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from src.forecast.representativeness_variance import (
    COLD_START_REPR_VARIANCE,
    ReprVarianceFit,
    representativeness_variance,
)

_GRID_TABLE_PATH = Path(__file__).resolve().parents[2] / "config" / "grid_representativeness.json"


@lru_cache(maxsize=1)
def load_grid_representativeness(path: str | None = None) -> dict:
    """Load the persisted grid-representativeness table (cached). {} on any read error."""
    p = Path(path) if path else _GRID_TABLE_PATH
    try:
        raw = json.loads(p.read_text())
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def cell_for(city: str, model: str, *, grid_table: dict | None = None) -> dict | None:
    """The {d_eff_m, delta_z_m, cell_*} record for (city, model), or None when absent."""
    tbl = grid_table if grid_table is not None else load_grid_representativeness()
    rec = tbl.get(city)
    if not isinstance(rec, dict):
        return None
    cell = (rec.get("models") or {}).get(model)
    return cell if isinstance(cell, dict) else None


def sigma_repr_sq_for(
    city: str,
    model: str,
    *,
    grid_table: dict | None = None,
    fit: ReprVarianceFit = COLD_START_REPR_VARIANCE,
    coastal: bool = False,
    orography: bool = False,
    urban: bool = False,
) -> float:
    """sigma_repr^2 (degC^2) for (city, model) from the grid table; 0.0 when unknown.

    Looks up the native-cell d_eff_m + delta_z_m and runs the pure
    ``representativeness_variance`` engine. A missing city/model/d_eff yields 0.0 (the
    fusion then behaves byte-identically for that instrument — never a fabricated penalty).
    ``fit`` defaults to the conservative widen-only cold-start until the MLE artifact exists.
    """
    cell = cell_for(city, model, grid_table=grid_table)
    if cell is None:
        return 0.0
    d_eff = cell.get("d_eff_m")
    if d_eff is None:
        return 0.0
    dz = cell.get("delta_z_m")
    try:
        return representativeness_variance(
            float(d_eff),
            float(dz) if dz is not None else 0.0,
            coastal=coastal,
            orography=orography,
            urban=urban,
            fit=fit,
        )
    except (TypeError, ValueError):
        return 0.0
