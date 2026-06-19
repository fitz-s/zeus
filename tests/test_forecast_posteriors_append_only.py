import json
from dataclasses import replace

import pytest

from tests.test_replacement_forecast_materializer import _conn, _install_live_fusion, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live


def test_forecast_posteriors_identical_identity_reuses_existing_row(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    first = materialize_replacement_forecast_live(conn, replace(_request(), anchor_weight=0.80))
    second = materialize_replacement_forecast_live(conn, replace(_request(), anchor_weight=0.80))

    rows = conn.execute("SELECT posterior_id, provenance_json FROM forecast_posteriors ORDER BY posterior_id").fetchall()

    assert first.posterior_id == second.posterior_id
    assert len(rows) == 1
    assert json.loads(rows[0]["provenance_json"])["anchor_weight"] == 0.80


def test_forecast_posteriors_append_new_config_not_do_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    first = materialize_replacement_forecast_live(conn, replace(_request(), anchor_weight=0.80))
    second = materialize_replacement_forecast_live(conn, replace(_request(), anchor_weight=0.20))

    rows = conn.execute("SELECT posterior_id, provenance_json FROM forecast_posteriors ORDER BY posterior_id").fetchall()

    assert first.posterior_id != second.posterior_id
    assert len(rows) == 2
    assert [json.loads(row["provenance_json"])["anchor_weight"] for row in rows] == [0.80, 0.20]
