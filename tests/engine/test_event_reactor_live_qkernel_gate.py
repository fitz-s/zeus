import pytest

from src.engine.event_reactor_adapter import _assert_live_entry_uses_qkernel_authority


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
    _assert_live_entry_uses_qkernel_authority(
        {
            "selection_authority_applied": "qkernel_spine",
            "direction": "buy_yes",
            "candidate_bin_id": "bin-1",
            "qkernel_execution_economics": _qkernel_cert(),
        }
    )


def test_live_entry_qkernel_gate_rejects_legacy_unstamped_payload():
    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_AUTHORITY_REQUIRED"):
        _assert_live_entry_uses_qkernel_authority(
            {
                "selection_authority_applied": None,
                "direction": "buy_yes",
                "candidate_bin_id": "bin-1",
                "qkernel_execution_economics": _qkernel_cert(),
            }
        )


def test_live_entry_qkernel_gate_rejects_bin_mismatch():
    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_CERT_BIN_MISMATCH"):
        _assert_live_entry_uses_qkernel_authority(
            {
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "candidate_bin_id": "other-bin",
                "qkernel_execution_economics": _qkernel_cert(),
            }
        )
