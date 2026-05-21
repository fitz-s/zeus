# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: architecture/money_path_ci.yaml MP-SCH-001/MP-SCH-002; schema historical truth policy
"""Money-path semantic CI tests for schema authority fabrication."""

from __future__ import annotations

from scripts.ci.semantic_diff_classifier import classify


def test_authority_fabricating_default_is_p0_and_unmergeable() -> None:
    diff = """diff --git a/scripts/migrations/202605_add_truth.py b/scripts/migrations/202605_add_truth.py
+++ b/scripts/migrations/202605_add_truth.py
+conn.execute("ALTER TABLE settlement_commands ADD COLUMN source_authority TEXT DEFAULT 'VERIFIED'")
"""
    objects = {
        "state_machines": {},
        "economic_objects": {},
        "schema_objects": {"authority_fabricating_defaults": ["VERIFIED"]},
        "side_effect_calls": {},
        "external_truth_sources": {},
    }
    mapping = {
        "risk_rules": {"schema_change_requires": ["MP-SCH-001", "MP-SCH-002"]},
        "segments": {},
        "invariants": {
            "MP-SCH-001": {"tests": ["tests/money_path/test_004_schema_live_failclosed.py"]},
            "MP-SCH-002": {"tests": ["tests/state/test_schema_current_invariant.py"]},
        },
    }

    result = classify(diff, ["scripts/migrations/202605_add_truth.py"], objects, mapping)

    assert result.risk == "P0"
    assert "settlement_commands.source_authority" in result.new_db_columns
    assert result.authority_fabricating_defaults
    assert "scripts/migrations/202605_add_truth.py" in result.migration_policy_missing
    assert {"MP-SCH-001", "MP-SCH-002"}.issubset(result.required_invariants)


def test_schema_change_with_semantic_policy_still_requires_schema_invariants() -> None:
    diff = """diff --git a/scripts/migrations/202605_add_safe_column.py b/scripts/migrations/202605_add_safe_column.py
+++ b/scripts/migrations/202605_add_safe_column.py
+# Migration semantic policy:
+# historical_rows:
+#   new_column: unknown_legacy
+#   authority_fabrication_allowed: false
+conn.execute("ALTER TABLE settlement_commands ADD COLUMN provenance_note TEXT")
"""
    # The classifier checks the repository file when present. This synthetic
    # path is absent from the repo, so the diff text is the fallback evidence.
    result = classify(
        diff,
        ["scripts/migrations/202605_add_safe_column.py"],
        {
            "state_machines": {},
            "economic_objects": {},
            "schema_objects": {"authority_fabricating_defaults": ["VERIFIED"]},
            "side_effect_calls": {},
            "external_truth_sources": {},
        },
        {
            "risk_rules": {"schema_change_requires": ["MP-SCH-001", "MP-SCH-002"]},
            "segments": {},
            "invariants": {
                "MP-SCH-001": {"tests": ["tests/money_path/test_004_schema_live_failclosed.py"]},
                "MP-SCH-002": {"tests": ["tests/state/test_schema_current_invariant.py"]},
            },
        },
    )

    assert result.risk == "P1"
    assert result.authority_fabricating_defaults == []
    assert result.migration_policy_missing == []
    assert {"MP-SCH-001", "MP-SCH-002"}.issubset(result.required_invariants)
