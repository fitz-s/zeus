# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.1, §8 P1.1
"""
topology_v_next — admission system structures (P1.1 stub).

Full public re-export (admit, AdmissionDecision, etc.) ships in P1.3 after
admission_engine.py is implemented. This stub exposes only the P1.1 data
layer and profile loader, which is sufficient for unit tests and Codex import.

P1.3 will expand __all__ to include admit and the full API surface per §1.1.
"""

__version__ = "0.1.0-p1.1"

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

__all__ = [
    "__version__",
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
]
