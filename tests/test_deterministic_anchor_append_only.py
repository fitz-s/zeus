import json

from tests.test_replacement_forecast_materializer import _conn, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_shadow


def test_deterministic_anchor_identical_identity_reuses_existing_row() -> None:
    conn = _conn()
    first = materialize_replacement_forecast_shadow(conn, _request())
    second = materialize_replacement_forecast_shadow(conn, _request())

    row = conn.execute("SELECT anchor_id, provenance_json FROM deterministic_forecast_anchors").fetchone()

    assert first.anchor_id == second.anchor_id
    assert json.loads(row["provenance_json"])["source_run_id"] == "om9-run"


def test_deterministic_anchor_append_new_source_run_not_do_nothing() -> None:
    conn = _conn()
    first = materialize_replacement_forecast_shadow(conn, _request())
    second = materialize_replacement_forecast_shadow(conn, _request(openmeteo_source_run_id="om9-run-new"))

    rows = conn.execute("SELECT anchor_id, provenance_json FROM deterministic_forecast_anchors ORDER BY anchor_id").fetchall()

    assert first.anchor_id != second.anchor_id
    assert [json.loads(row["provenance_json"])["source_run_id"] for row in rows] == ["om9-run", "om9-run-new"]
