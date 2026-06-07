from tests.test_replacement_forecast_materializer import _conn, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_shadow


def test_materializer_leaves_q_lcb_null_when_only_point_q_exists() -> None:
    conn = _conn()
    result = materialize_replacement_forecast_shadow(conn, _request())

    row = conn.execute("SELECT q_json, q_lcb_json FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()

    assert result.ok is True
    assert row["q_json"]
    assert row["q_lcb_json"] is None
