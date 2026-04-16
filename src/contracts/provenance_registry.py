"""Provenance registry — INV-13 enforcement machinery.

Every numeric constant that participates in a sizing/edge/probability cascade
must be registered here with its optimization target, data basis, and replacement
criteria. An unregistered constant in a flagged cascade path raises
UnregisteredConstantError at runtime (unless emergency-bypassed, which is logged
and auto-expires after 7 days).

Registry is loaded from config/provenance_registry.yaml at module import time.

See: docs/zeus_FINAL_spec.md §P9.5
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data contract — must match §P9.5 exactly
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProvenanceRecord:
    """Provenance metadata for a single hardcoded numeric constant.

    Attributes:
        constant_name: Canonical identifier, e.g. "kelly_mult" or
            "market_fusion.TAIL_ALPHA_SCALE".
        file_location: Source file and variable path, e.g.
            "src/strategy/kelly.py::kelly_size.kelly_mult".
        declared_target: Optimization objective this constant was fit against.
            One of: "brier_score" | "ev" | "risk_cap" | "physical_constraint".
        data_basis: Human-readable description of how the value was chosen
            (e.g. "sweep [0.5, 1.0] on 2026-03-31 Brier dataset, Δ=−0.042").
        validated_at: ISO date (YYYY-MM-DD) of last empirical validation.
        replacement_criteria: What evidence or event triggers re-fitting this
            constant (e.g. "500+ settlements with empirical edge-error measurement").
        cascade_bound: Optional (min, max) tuple bounding the multiplicative
            contribution of this constant inside a Kelly cascade. None if the
            constant is not in a multiplicative cascade.
    """

    constant_name: str
    file_location: str
    declared_target: str
    data_basis: str
    validated_at: str
    replacement_criteria: str
    cascade_bound: Optional[tuple[float, float]] = None

    def __post_init__(self) -> None:
        valid_targets = {"brier_score", "ev", "risk_cap", "physical_constraint"}
        if self.declared_target not in valid_targets:
            raise ValueError(
                f"ProvenanceRecord.declared_target='{self.declared_target}' is not "
                f"one of the valid targets: {valid_targets}"
            )
        if not self.constant_name:
            raise ValueError("ProvenanceRecord.constant_name must not be empty")
        if not self.validated_at:
            raise ValueError("ProvenanceRecord.validated_at must not be empty")
        if self.cascade_bound is not None:
            lo, hi = self.cascade_bound
            if lo >= hi:
                raise ValueError(
                    f"ProvenanceRecord.cascade_bound lower={lo} must be < upper={hi}"
                )


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def _load_registry(yaml_path: Path) -> tuple[dict[str, ProvenanceRecord], bool]:
    """Load provenance_registry.yaml and return (registry_dict, degraded_flag)."""
    try:
        import yaml  # pyyaml
    except ImportError:
        logger.error(
            "PyYAML not available. ProvenanceRegistry will be empty. "
            "Install pyyaml to enable INV-13 enforcement."
        )
        return {}, True

    if not yaml_path.exists():
        logger.error(
            "provenance_registry.yaml not found at %s. "
            "INV-13 enforcement is disabled until the file exists.",
            yaml_path,
        )
        return {}, True

    try:
        with open(yaml_path) as f:
            raw = yaml.safe_load(f)

        if not raw or "constants" not in raw:
            logger.error("provenance_registry.yaml has no 'constants' key; registry is empty.")
            return {}, True

        registry: dict[str, ProvenanceRecord] = {}
        for entry in raw["constants"]:
            cb_raw = entry.get("cascade_bound")
            cascade_bound = tuple(cb_raw) if cb_raw else None
            record = ProvenanceRecord(
                constant_name=entry["constant_name"],
                file_location=entry["file_location"],
                declared_target=entry["declared_target"],
                data_basis=entry["data_basis"],
                validated_at=entry["validated_at"],
                replacement_criteria=entry["replacement_criteria"],
                cascade_bound=cascade_bound,  # type: ignore[arg-type]
            )
            if record.constant_name in registry:
                raise ValueError(f"Duplicate provenance constant_name: {record.constant_name}")
            registry[record.constant_name] = record

        logger.info("ProvenanceRegistry loaded %d records", len(registry))
        return registry, False
    except Exception as exc:
        logger.error("Failed to parse provenance_registry.yaml (%s). Falling back to degraded state.", exc)
        return {}, True


# ---------------------------------------------------------------------------
# Emergency bypass — auto-expiring, logged
# ---------------------------------------------------------------------------

# Maps constant_name → expiry epoch (float)
_emergency_bypasses: dict[str, float] = {}
_BYPASS_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def register_emergency_bypass(constant_name: str) -> None:
    """Allow an unregistered constant to pass INV-13 checks for up to 7 days.

    This is a last-resort escape hatch. Every bypass is logged. Bypasses
    auto-expire after 7 days and cannot be renewed without explicit re-registration.
    """
    expiry = time.time() + _BYPASS_TTL_SECONDS
    _emergency_bypasses[constant_name] = expiry
    logger.warning(
        "INV-13 EMERGENCY BYPASS registered for constant '%s'. "
        "Expires at epoch %.0f (7 days). This bypass is logged and will not be renewed automatically.",
        constant_name,
        expiry,
    )


def _is_bypass_active(constant_name: str) -> bool:
    expiry = _emergency_bypasses.get(constant_name)
    if expiry is None:
        return False
    if time.time() > expiry:
        del _emergency_bypasses[constant_name]
        logger.warning(
            "INV-13 emergency bypass for '%s' has expired. "
            "Constant is now subject to normal INV-13 enforcement.",
            constant_name,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Runtime check function
# ---------------------------------------------------------------------------

def require_provenance(constant_name: str, requires_provenance: bool = True) -> Optional[ProvenanceRecord]:
    """Assert that constant_name is registered in REGISTRY.

    Use this at any cascade entry point where a hardcoded constant is consumed.
    Raises UnregisteredConstantError if the constant is not in REGISTRY and
    no active emergency bypass exists.

    Args:
        constant_name: The name to look up in REGISTRY.
        requires_provenance: If False, skip the check (for constants explicitly
            flagged as outside the provenance requirement).

    Returns:
        The ProvenanceRecord for the constant, or None if the check was skipped
        or an emergency bypass is active.
    """
    if not requires_provenance:
        return None

    if REGISTRY_DEGRADED:
        raise UnregisteredConstantError(
            f"INV-13: provenance registry failed to load — governance disabled. "
            f"Constant '{constant_name}' cannot be validated and fail-close is enforced."
        )

    record = REGISTRY.get(constant_name)
    if record is not None:
        return record

    if _is_bypass_active(constant_name):
        logger.warning(
            "INV-13: constant '%s' not in REGISTRY but emergency bypass is active.",
            constant_name,
        )
        return None

    raise UnregisteredConstantError(
        f"Constant '{constant_name}' is not registered in provenance_registry.yaml "
        "and no active emergency bypass exists. "
        "Add a ProvenanceRecord to config/provenance_registry.yaml or call "
        "register_emergency_bypass() with documented justification. "
        "(INV-13 violation)"
    )


# ---------------------------------------------------------------------------
# Module-level REGISTRY (loaded once at import)
# ---------------------------------------------------------------------------

_DEFAULT_YAML = (
    Path(__file__).parent.parent.parent  # zeus repo root
    / "config"
    / "provenance_registry.yaml"
)

_REGISTRY_LOAD_RESULT = _load_registry(_DEFAULT_YAML)
REGISTRY: dict[str, ProvenanceRecord] = _REGISTRY_LOAD_RESULT[0]
REGISTRY_DEGRADED: bool = _REGISTRY_LOAD_RESULT[1]


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class UnregisteredConstantError(Exception):
    """Raised when a cascade-path constant is not in the provenance registry
    and no active emergency bypass is present. This is the INV-13 runtime
    violation — hardcoded constants must have declared provenance before
    participating in sizing/edge/probability cascades.
    """
