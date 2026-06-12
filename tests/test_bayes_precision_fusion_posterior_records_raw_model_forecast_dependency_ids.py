# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 5 — the fused posterior must record the raw_model_forecast_ids it consumed so the q is reconstructable to exact persisted inputs.
# Reuse: Run with pytest; update if posterior provenance fields or raw_model_forecast_ids recording in the fusion path changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: Fitz Constraint #4 (a traded q must carry the provenance of its inputs);
#   BLOCKER 5 — the posterior must record the raw_model_forecast_ids it fused from, so the q is
#   reconstructable to the exact persisted current rows (which models, URL, params, payload,
#   source_available_at) rather than to ephemeral network values.
"""BLOCKER 5 — the fused posterior records its raw_model_forecast dependency ids.

When the override fuses from the persisted current single_runs rows, the posterior's provenance
must list the raw_model_forecast_ids of those rows. This is the structural link that makes the
traded q reconstructable: from the posterior you can recover the exact persisted inputs.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import src.data.replacement_forecast_materializer as mod
from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _request,
    _reset_override_seams,  # noqa: F401 autouse
    _row,
    _seed_history,
)
from tests.test_bayes_precision_fusion_materializer_uses_persisted_current_rows_not_network import (
    CURRENT_MODELS,
    _seed_current_single_runs,
)


def test_posterior_records_raw_model_forecast_ids(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=CURRENT_MODELS)
    _seed_current_single_runs(conn, request=_request())

    # No network fetch wired -> the persisted-row reader is the only source.
    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"])["bayes_precision_fusion"]

    ids = prov.get("raw_model_forecast_ids")
    assert ids, "the posterior must record the raw_model_forecast_ids it fused from"
    assert isinstance(ids, list) and all(isinstance(i, int) for i in ids)

    # The recorded ids must be REAL persisted single_runs rows for this cycle.
    persisted = {
        r[0]
        for r in conn.execute(
            "SELECT raw_model_forecast_id FROM raw_model_forecasts WHERE endpoint='single_runs'"
        ).fetchall()
    }
    assert set(ids) <= persisted, "recorded ids must reference persisted single_runs rows"
    assert len(ids) >= 2, "at least the anchor + one extra current row must be referenced"
