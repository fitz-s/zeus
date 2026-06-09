from tests.test_replacement_replay_veto_only_not_initiation import _row

from src.data.replacement_forecast_replay import score_replacement_forecast_same_clob_replay


def test_replay_rejects_provisional_truth_authority() -> None:
    result = score_replacement_forecast_same_clob_replay(_row(truth_authority="PROVISIONAL"))

    assert result.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_REQUIRES_OFFICIAL_VERIFIED_TRUTH" in result.reason_codes
