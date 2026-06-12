# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 4 — cell_selection and elevation/downscaling are first-class product identity and must be persisted alongside every raw_model_forecasts row.
# Reuse: Run with pytest; update if product-identity columns or cell_selection/elevation handling in the BAYES_PRECISION_FUSION downloader changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 + Fitz Constraint #4. Open-Meteo's cell_selection
#   (nearest vs land vs sea) and elevation/downscaling materially change the returned 2m
#   temperature: the SAME lat/lon with cell_selection=land vs nearest can pick a DIFFERENT
#   grid cell -> a different physical product. These are first-class product identity, not
#   cosmetic params, and MUST be persisted so a stored value proves which cell produced it.
"""BLOCKER 4 — cell_selection + elevation/downscaling are persisted product identity.

The download writer must record cell_selection, elevation_param, downscaling_policy, and
model_domain_hash on every raw_model_forecasts row. Two captures of the same model/city/date at
DIFFERENT cell_selection are DIFFERENT products and must be distinguishable (different
model_domain_hash) so a residual history never mixes two physical cells.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema


def _forecast_db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_shadow_schema(conn)
    conn.commit()
    conn.close()
    return db


def _target():
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return BayesPrecisionFusionDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                             lead_days=1, latitude=48.967, longitude=2.428,
                             timezone_name="Europe/Paris")


def test_cell_selection_and_elevation_persisted(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, tzinfo=UTC), targets=[_target()],
        single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 19.5,
    )
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT cell_selection, elevation_param, downscaling_policy, model_domain_hash,
                  coverage_status
           FROM raw_model_forecasts"""
    ).fetchall()
    conn.close()
    assert rows
    for r in rows:
        assert r["cell_selection"], "cell_selection must be recorded"
        assert r["elevation_param"], "elevation_param must be recorded"
        assert r["downscaling_policy"], "downscaling_policy must be recorded"
        assert r["model_domain_hash"], "model_domain_hash must be recorded"
        assert r["coverage_status"], "coverage_status must be recorded"


def test_different_cell_selection_yields_different_model_domain_hash(tmp_path) -> None:
    """The model_domain_hash binds (provider, model_name, cell_selection, elevation_param,
    downscaling_policy, endpoint_mode). Changing cell_selection changes the hash -> two
    physical cells are never conflated under one identity."""
    from src.data.bayes_precision_fusion_download import _model_domain_hash

    base = dict(
        provider="open-meteo", model_name="gfs_global", cell_selection="nearest",
        elevation_param="requested", downscaling_policy="none",
        endpoint_mode="previous_runs",
    )
    h_nearest = _model_domain_hash(**base)
    h_land = _model_domain_hash(**{**base, "cell_selection": "land"})
    h_elev = _model_domain_hash(**{**base, "elevation_param": "30"})
    assert h_nearest != h_land, "cell_selection must change the domain hash"
    assert h_nearest != h_elev, "elevation must change the domain hash"
    # Deterministic + stable for the same inputs.
    assert h_nearest == _model_domain_hash(**base)
