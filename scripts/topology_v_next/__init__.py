# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.1, §8 P1.3
"""
topology_v_next — P1 complete public API.

Single-import access to the full admission system per SCAFFOLD §4 API contract.
Codex-invocable: no Claude-Code-specific imports, no env-var dependencies.

Usage:
    from scripts.topology_v_next import admit, AdmissionDecision, Intent

    decision = admit(
        intent="create_new",
        files=["scripts/topology_v_next/admission_engine.py"],
    )
    assert isinstance(decision, AdmissionDecision)
"""

__version__ = "0.1.0-p1.3"

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    AdmissionDecision,
    BindingLayer,
    CohortDecl,
    CoverageMap,
    DiagnosisEntry,
    FrictionPattern,
    Intent,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.profile_loader import load_binding_layer, validate_binding_layer
from scripts.topology_v_next.intent_resolver import resolve_intent, is_zeus_intent
from scripts.topology_v_next.severity_overrides import apply_overrides, effective_severity

__all__ = [
    "__version__",
    # primary entry point
    "admit",
    # dataclasses
    "AdmissionDecision",
    "BindingLayer",
    "CohortDecl",
    "CoverageMap",
    "DiagnosisEntry",
    "FrictionPattern",
    "Intent",
    "IssueRecord",
    "Severity",
    # profile_loader
    "load_binding_layer",
    "validate_binding_layer",
    # intent_resolver
    "resolve_intent",
    "is_zeus_intent",
    # severity_overrides
    "apply_overrides",
    "effective_severity",
]
