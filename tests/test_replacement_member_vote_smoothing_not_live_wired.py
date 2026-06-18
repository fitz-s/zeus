# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: live/experiment separation cleanup; AIFS member-vote smoothing is not a live config knob.
"""AIFS member-vote smoothing must stay out of the live replacement materializer."""

from pathlib import Path
import json


_REPO = Path(__file__).resolve().parents[1]


def test_member_vote_smoothing_key_not_in_live_settings() -> None:
    settings = json.loads((_REPO / "config" / "settings.json").read_text(encoding="utf-8"))

    assert "replacement_0_1_member_vote_smoothing_enabled" not in settings["edli"]
    assert "replacement_0_1_member_vote_smoothing_alpha" not in settings["edli"]


def test_live_materializer_does_not_read_member_vote_smoothing_switch() -> None:
    materializer = (_REPO / "src" / "data" / "replacement_forecast_materializer.py").read_text(
        encoding="utf-8"
    )

    assert "replacement_0_1_member_vote_smoothing_enabled" not in materializer
    assert "_replacement_member_vote_smoothing_alpha" not in materializer
