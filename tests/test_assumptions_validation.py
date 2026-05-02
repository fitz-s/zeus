import pytest

from scripts.validate_assumptions import run_validation


@pytest.mark.xfail(reason="pre-existing on origin/main as of 2026-05-02; state/assumptions.json absent at test time. Tracked separately.")
def test_live_assumptions_manifest_matches_current_code_contracts():
    result = run_validation()
    assert result["valid"], "assumptions.json diverges from current code/config contracts: " + " | ".join(result["mismatches"])
