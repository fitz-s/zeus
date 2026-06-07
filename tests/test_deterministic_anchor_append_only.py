import json

from tests.test_replacement_forecast_materializer import _conn, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_shadow


def test_deterministic_anchor_conflict_does_not_overwrite_existing_value_or_provenance() -> None:
    conn = _conn()
    first = materialize_replacement_forecast_shadow(conn, _request())
    second = materialize_replacement_forecast_shadow(conn, _request(openmeteo_source_run_id="om9-run-new"))

    row = conn.execute("SELECT anchor_id, provenance_json FROM deterministic_forecast_anchors").fetchone()

    assert first.anchor_id == second.anchor_id
    assert json.loads(row["provenance_json"])["source_run_id"] == "om9-run"
