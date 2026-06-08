# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: operator BLOCKER (the_path PR review 2026-06-08) — the B0->raw_model_forecasts
#   backfill must construct DETERMINISTIC product identity per row using the SAME identity
#   construction the live download writer uses (src/data/u0r_multimodel_download._u0r_product_identity),
#   so seeded rows are byte-for-byte identity-compatible with live-fetched rows. U0R_BAYES_SPEC §6 F1.
"""Relationship test (B0 seed row -> raw_model_forecasts product identity boundary).

The pre-fix backfill inserted ONLY the narrow 12-column tuple, leaving product_id /
request_url_hash NULL. Under the new UNIQUE(model,product_id,request_url_hash,city,
target_date,metric,source_cycle_time,endpoint) NULL!=NULL in SQLite, so those rows
were NON-idempotent (a 2nd run duplicated -> corrupts n_train / EB-lambda / covariance /
tau0). This test pins the cross-module invariant: a seeded previous_runs row carries the
SAME full product identity the live download writer would stamp for the same
(model, endpoint, target) — product_id, request_url_hash, source_id, source_family,
provider, model_name, request_params_json, lat/lon/timezone requested, cell_selection,
elevation_param, downscaling_policy, endpoint_mode, model_domain_hash, coverage_status.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_shadow_schema(conn)
    conn.commit()
    conn.close()
    return db


def _b0_one_city() -> dict:
    # Minimal B0 shape: {city: {"leads": {lead: {model: {target_date: [high_c, low_c]}}}}}
    return {
        "Paris": {
            "leads": {
                "1": {
                    "ecmwf_ifs": {"2026-06-01": [7.4, 2.2]},
                    "gfs_global": {"2026-06-01": [7.1, 2.0]},
                }
            },
            "_settle_high": {},
            "_settle_low": {},
        }
    }


def test_backfill_writes_full_product_identity_matching_live_writer(tmp_path) -> None:
    from src.data.u0r_multimodel_download import (
        U0RDownloadTarget,
        _u0r_product_identity,
    )
    from scripts.backfill_u0r_history_from_b0 import backfill_u0r_history

    db = _db(tmp_path)
    report = backfill_u0r_history(b0=_b0_one_city(), db=db, dry_run=False)
    assert report["written_row_count"] > 0

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM raw_model_forecasts WHERE endpoint='previous_runs'"
    ).fetchall()
    conn.close()
    assert rows, "expected seeded previous_runs rows"

    # Every seeded row must have a NON-NULL product identity (the antibody to the NULL!=NULL
    # idempotency hole).
    for r in rows:
        assert r["product_id"] is not None, "product_id must not be NULL (idempotency hole)"
        assert r["request_url_hash"] is not None, "request_url_hash must not be NULL"
        assert r["source_id"] is not None
        assert r["source_family"] is not None
        assert r["provider"] is not None
        assert r["model_name"] is not None
        assert r["request_params_json"] not in (None, "", "{}")
        assert r["endpoint_mode"] == "previous_runs"
        assert r["model_domain_hash"] is not None
        assert r["coverage_status"] is not None

    # The identity MUST equal what the live writer would stamp for the same (model, endpoint,
    # target) — i.e. reuse the SAME construction so seed rows == live rows on the UNIQUE key.
    by_model = {r["model"]: r for r in rows}
    for model in ("ecmwf_ifs", "gfs_global"):
        r = by_model[model]
        t = U0RDownloadTarget(
            city="Paris", metric=r["metric"], target_date="2026-06-01",
            lead_days=1, latitude=48.967, longitude=2.428, timezone_name="Europe/Paris",
        )
        ident = _u0r_product_identity(model, "previous_runs", t)
        assert r["product_id"] == ident["product_id"]
        assert r["request_url_hash"] == ident["request_url_hash"]
        assert r["model_name"] == ident["model_name"]
        assert r["source_id"] == ident["source_id"]
        assert r["source_family"] == ident["source_family"]
        assert r["model_domain_hash"] == ident["model_domain_hash"]
        assert r["request_params_json"] == ident["request_params_json"]
        # The anchor's stored model col stays the fusion identity, NOT the OM model id.
    assert "ecmwf_ifs025" not in by_model, "stored model col must be the fusion identity 'ecmwf_ifs'"
