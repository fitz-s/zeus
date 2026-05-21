# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: architecture/money_path_ci.yaml MP-RED-001/MP-RED-002; redeem receipt misroute doctrine
"""Money-path semantic CI tests for redeem-state and receipt classifier drift."""

from __future__ import annotations

from scripts.ci.semantic_diff_classifier import classify


def test_new_redeem_state_without_registry_is_blocked() -> None:
    diff = """diff --git a/src/execution/settlement_commands.py b/src/execution/settlement_commands.py
+++ b/src/execution/settlement_commands.py
+class SettlementState(str, Enum):
+    REDEEM_AUTORETRYABLE_REVIEW = "REDEEM_AUTORETRYABLE_REVIEW"
"""
    objects = {
        "state_machines": {
            "settlement_command": {
                "states": ["REDEEM_OPERATOR_REQUIRED", "REDEEM_CONFIRMED"]
            }
        },
        "economic_objects": {},
        "schema_objects": {"authority_fabricating_defaults": []},
        "side_effect_calls": {},
        "external_truth_sources": {},
    }
    mapping = {
        "risk_rules": {},
        "segments": {
            "settlement_redeem": {
                "files": ["src/execution/settlement_commands.py"],
                "invariant_ids": ["MP-RED-001", "MP-RED-002"],
                "relationship_tests": ["tests/money_path/test_003_redeem_receipt_classifier.py"],
            }
        },
        "invariants": {
            "MP-RED-001": {"tests": ["tests/test_polymarket_v2_adapter_negrisk_redeem.py"]},
            "MP-RED-002": {"tests": ["tests/test_harvester_settlement_redeem.py"]},
        },
    }

    result = classify(diff, ["src/execution/settlement_commands.py"], objects, mapping)

    assert result.risk == "P0"
    assert "REDEEM_AUTORETRYABLE_REVIEW" in result.new_states
    assert "state:REDEEM_AUTORETRYABLE_REVIEW" in result.unregistered_objects
    assert {"MP-RED-001", "MP-RED-002"}.issubset(result.required_invariants)


def test_new_redeem_error_code_routes_report_eligibility_invariant() -> None:
    diff = """diff --git a/src/execution/settlement_commands.py b/src/execution/settlement_commands.py
+++ b/src/execution/settlement_commands.py
+error_payload = {"errorCode": "REDEEM_NEGRISK_MISROUTED"}
"""
    result = classify(
        diff,
        ["src/execution/settlement_commands.py"],
        {
            "state_machines": {"settlement_command": {"states": ["REDEEM_OPERATOR_REQUIRED"]}},
            "economic_objects": {},
            "schema_objects": {"authority_fabricating_defaults": []},
            "side_effect_calls": {},
            "external_truth_sources": {},
        },
        {
            "risk_rules": {},
            "segments": {},
            "invariants": {
                "MP-RED-002": {"tests": ["tests/test_harvester_settlement_redeem.py"]}
            },
        },
    )

    assert result.risk == "P1"
    assert result.new_error_codes == ["REDEEM_NEGRISK_MISROUTED"]
    assert "MP-RED-002" in result.required_invariants
