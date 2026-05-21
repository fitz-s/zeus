# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: architecture/money_path_objects.yaml + architecture/money_path_ci.yaml MP-MD-001/MP-MD-003
"""Money-path semantic CI: market-discovery changes route to relationship tests."""

from __future__ import annotations

from scripts.ci.semantic_diff_classifier import classify


def test_market_scanner_change_selects_tradeability_relationship_tests() -> None:
    diff = """diff --git a/src/data/market_scanner.py b/src/data/market_scanner.py
+++ b/src/data/market_scanner.py
+NEW_ENDPOINT = "https://gamma-api.polymarket.com/markets"
+tradeable = gamma_market["enableOrderBook"]
"""
    objects = {
        "state_machines": {},
        "economic_objects": {},
        "schema_objects": {"authority_fabricating_defaults": []},
        "side_effect_calls": {},
        "external_truth_sources": {
            "gamma": {"endpoint_patterns": ["gamma-api.polymarket.com"]}
        },
    }
    mapping = {
        "risk_rules": {"force_integration_if_changed": ["src/data/market_scanner.py"]},
        "segments": {
            "market_discovery": {
                "files": ["src/data/market_scanner.py"],
                "invariant_ids": ["MP-MD-001", "MP-MD-003"],
                "relationship_tests": [
                    "tests/money_path/test_001_negrisk_tradeability_snapshot_submit.py",
                    "tests/test_market_scanner_negrisk.py",
                ],
            }
        },
        "invariants": {
            "MP-MD-001": {"tests": ["tests/test_executable_market_snapshot_v2.py"]},
            "MP-MD-003": {"tests": ["tests/test_scanner_archived_filter.py"]},
            "MP-EXT-001": {"tests": ["tests/test_market_scanner_provenance.py"]},
            "MP-EXT-002": {"tests": ["tests/test_live_readiness_gates.py"]},
        },
    }

    result = classify(diff, ["src/data/market_scanner.py"], objects, mapping)

    assert result.risk == "P1"
    assert result.run_integration is True
    assert "market_discovery" in result.changed_segments
    assert "MP-MD-001" in result.required_invariants
    assert "MP-EXT-001" in result.required_invariants
    assert "tests/test_market_scanner_negrisk.py" in result.tests


def test_unregistered_external_source_fails_as_unknown_path() -> None:
    diff = """diff --git a/src/data/market_scanner.py b/src/data/market_scanner.py
+++ b/src/data/market_scanner.py
+NEW_ENDPOINT = "https://clob.polymarket.com/markets"
"""
    result = classify(
        diff,
        ["src/data/market_scanner.py"],
        {
            "state_machines": {},
            "economic_objects": {},
            "schema_objects": {"authority_fabricating_defaults": []},
            "side_effect_calls": {},
            "external_truth_sources": {},
        },
        {
            "risk_rules": {"force_integration_if_changed": ["src/data/market_scanner.py"]},
            "segments": {},
            "invariants": {
                "MP-EXT-001": {"tests": ["tests/test_market_scanner_provenance.py"]},
                "MP-EXT-002": {"tests": ["tests/test_live_readiness_gates.py"]},
            },
        },
    )

    assert result.risk == "P1"
    assert result.new_external_calls == ["https://clob.polymarket.com/markets"]
    assert result.unregistered_objects == ["external_endpoint:https://clob.polymarket.com/markets"]
    assert {"MP-EXT-001", "MP-EXT-002"}.issubset(result.required_invariants)
