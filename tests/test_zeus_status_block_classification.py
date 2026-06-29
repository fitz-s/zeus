# Created: 2026-06-28
# Last reused/audited: 2026-06-28

from __future__ import annotations

from scripts.zeus_status import classify_block


def test_shadow_only_residue_is_not_classified_as_transient() -> None:
    assert classify_block("READINESS", "SHADOW_ONLY") == "unknown"


def test_missing_live_input_remains_transient() -> None:
    assert classify_block("READINESS", "LIVE_INFERENCE_INPUTS_MISSING") == "transient"
