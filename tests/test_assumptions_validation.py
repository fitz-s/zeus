from scripts.validate_assumptions import run_validation


def test_live_assumptions_manifest_matches_current_code_contracts():
    result = run_validation()
    assert result["valid"], "assumptions.json diverges from current code/config contracts: " + " | ".join(result["mismatches"])
