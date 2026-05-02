from pathlib import Path

import pytest

from scripts.validate_assumptions import ASSUMPTIONS_PATH, run_validation


@pytest.mark.skipif(
    not Path(ASSUMPTIONS_PATH).exists(),
    reason="state/assumptions.json absent at test time (not committed; written by ops). "
           "Test runs as a hard gate when the manifest is present; skipped otherwise. "
           "Codex review on PR #40 flagged unconditional xfail as too loose — strict=False let drift ship silently.",
)
def test_live_assumptions_manifest_matches_current_code_contracts():
    result = run_validation()
    assert result["valid"], "assumptions.json diverges from current code/config contracts: " + " | ".join(result["mismatches"])
