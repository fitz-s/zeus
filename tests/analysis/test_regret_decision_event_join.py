# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: MP-LEA-002; architecture code review 2026-05-22 F3 finding
"""Antibody: regret decompositions joined through decision_events (MP-LEA-002).

Cross-strategy contamination enters when regret rows are joined only through
experiment_id, without verifying that the decision_event's strategy_key matches.
The F3 tests in test_p1_findings_evidence_risk.py are the primary antibodies;
this module re-runs them via import to keep this file as a named reference
in money_path_ci.yaml MP-LEA-002.required_tests without duplicating assertions.
"""
from __future__ import annotations

from tests.test_p1_findings_evidence_risk import TestF3RegretJoinCorrectness  # noqa: F401
