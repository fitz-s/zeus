from tests.test_replacement_forecast_bundle_reader import _BaselineBundle, _Evidence, _conn, _insert_posterior, _readiness

from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle


def test_bundle_reader_blocks_posterior_without_bin_topology_hash() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    conn.execute("UPDATE forecast_posteriors SET provenance_json = '{}' WHERE posterior_id = ?", (posterior_id,))

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time="2026-06-06T04:00:00+00:00",
        current_bin_topology_hash="test-topology",
    )

    assert result.reason_code == "REPLACEMENT_POSTERIOR_BIN_TOPOLOGY_HASH_MISSING"
