import pytest

from src.engine.event_reactor_adapter import _assert_live_entry_submit_authority


def _qkernel_cert() -> dict:
    return {
        "source": "qkernel_spine",
        "candidate_id": "YES:bin-1:DIRECT_YES:bin-1@proof",
        "bin_id": "bin-1",
        "route_id": "DIRECT_YES:bin-1@proof",
        "side": "YES",
        "payoff_q_point": 0.70,
        "payoff_q_lcb": 0.60,
        "edge_lcb": 0.20,
        "delta_u_at_min": 0.01,
        "optimal_stake_usd": 1.0,
        "optimal_delta_u": 0.02,
        "cost": 0.40,
        "false_edge_rate": 0.01,
        "direction_law_ok": True,
        "coherence_allows": True,
    }


def test_live_entry_qkernel_gate_accepts_stamped_matching_cert():
    _assert_live_entry_submit_authority(
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "selection_authority_applied": "qkernel_spine",
            "direction": "buy_yes",
            "candidate_bin_id": "bin-1",
            "qkernel_execution_economics": _qkernel_cert(),
        }
    )


def test_live_entry_qkernel_gate_rejects_legacy_unstamped_payload():
    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_AUTHORITY_REQUIRED"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": None,
                "direction": "buy_yes",
                "candidate_bin_id": "bin-1",
                "qkernel_execution_economics": _qkernel_cert(),
            }
        )


def test_live_entry_qkernel_gate_rejects_bin_mismatch():
    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_CERT_BIN_MISMATCH"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "candidate_bin_id": "other-bin",
                "qkernel_execution_economics": _qkernel_cert(),
            }
        )


def _day0_payload(**overrides) -> dict:
    payload = {
        "event_type": "DAY0_EXTREME_UPDATED",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    payload.update(overrides)
    return payload


def test_live_entry_day0_gate_accepts_live_observation_authority_without_qkernel():
    _assert_live_entry_submit_authority(_day0_payload(selection_authority_applied=None))


def test_live_entry_day0_gate_rejects_missing_live_observation_authority():
    with pytest.raises(
        ValueError,
        match="LIVE_ENTRY_DAY0_OBSERVATION_AUTHORITY_REQUIRED:live_authority_status=missing",
    ):
        _assert_live_entry_submit_authority(_day0_payload(live_authority_status=None))


def test_live_entry_gate_rejects_unknown_event_type_even_with_qkernel_cert():
    with pytest.raises(ValueError, match="LIVE_ENTRY_AUTHORITY_UNSUPPORTED_EVENT_TYPE"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "EXPERIMENTAL_EVENT",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "candidate_bin_id": "bin-1",
                "qkernel_execution_economics": _qkernel_cert(),
            }
        )
