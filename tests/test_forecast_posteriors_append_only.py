import json
from dataclasses import replace

from tests.test_replacement_forecast_materializer import _conn, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_shadow


def test_forecast_posteriors_identical_identity_reuses_existing_row() -> None:
    conn = _conn()
    first = materialize_replacement_forecast_shadow(conn, replace(_request(), anchor_weight=0.80))
    second = materialize_replacement_forecast_shadow(conn, replace(_request(), anchor_weight=0.80))

    rows = conn.execute("SELECT posterior_id, provenance_json FROM forecast_posteriors ORDER BY posterior_id").fetchall()

    assert first.posterior_id == second.posterior_id
    assert len(rows) == 1
    assert json.loads(rows[0]["provenance_json"])["anchor_weight"] == 0.80


def test_forecast_posteriors_append_new_config_not_do_nothing() -> None:
    conn = _conn()
    first = materialize_replacement_forecast_shadow(conn, replace(_request(), anchor_weight=0.80))
    second = materialize_replacement_forecast_shadow(conn, replace(_request(), anchor_weight=0.20))

    rows = conn.execute("SELECT posterior_id, provenance_json FROM forecast_posteriors ORDER BY posterior_id").fetchall()

    assert first.posterior_id != second.posterior_id
    assert len(rows) == 2
    assert [json.loads(row["provenance_json"])["anchor_weight"] for row in rows] == [0.80, 0.20]
