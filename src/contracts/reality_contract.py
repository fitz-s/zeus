"""P10 — External reality contract layer.

Each RealityContract encodes one external-reality assumption (fee rate, tick size,
settlement source, etc.) with a TTL, criticality, and verification method.

VerificationResult, DriftEvent, Antibody live here so tests can import everything
from a single module. RealityContractVerifier is in reality_verifier.py and
re-exported at the bottom of this file (circular-safe: RealityContract is fully
defined before the re-import executes).

See: docs/zeus_FINAL_spec.md §P10.3 / §P10.5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class RealityContract:
    """A typed, TTL-bound, verifiable external-reality assumption.

    Attributes:
        contract_id: Unique identifier (e.g. "FEE_RATE_WEATHER").
        category: Contract family — "economic" | "execution" | "data" | "protocol".
        assumption: Human-readable description of the assumed fact.
        current_value: The currently-assumed value (any JSON-serialisable type).
        verification_method: How to verify (URL, CLI call, or "manual" with instructions).
        last_verified: UTC datetime of last successful verification.
        ttl_seconds: How long before the contract is considered stale.
        criticality: Impact if the assumption is wrong:
            "blocking"  — system must not trade until reverified.
            "degraded"  — system may continue but at reduced confidence.
            "advisory"  — log-only, no operational impact.
        on_change_handlers: List of handler names to invoke when drift is detected.
    """

    contract_id: str
    category: Literal["economic", "execution", "data", "protocol"]
    assumption: str
    current_value: Any
    verification_method: str
    last_verified: datetime
    ttl_seconds: int
    criticality: Literal["blocking", "degraded", "advisory"]
    on_change_handlers: list[str]

    @property
    def is_stale(self) -> bool:
        """True if more than ttl_seconds have elapsed since last_verified."""
        now = datetime.now(timezone.utc)
        lv = self.last_verified
        if lv.tzinfo is None:
            lv = lv.replace(tzinfo=timezone.utc)
        return (now - lv).total_seconds() > self.ttl_seconds

    @property
    def must_reverify(self) -> bool:
        """True if stale AND criticality is blocking — system must halt until reverified."""
        return self.is_stale and self.criticality == "blocking"


# ---------------------------------------------------------------------------
# Verifier result / event types  (defined here so tests can import from one place)
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """Return value of RealityContractVerifier.verify_all_blocking().

    Attributes:
        can_trade: False if any blocking contract is stale or failed live check.
        failures: The blocking contracts that failed.
    """
    can_trade: bool
    failures: list[RealityContract]


@dataclass
class DriftEvent:
    """Records that a contract's assumed value no longer matches reality.

    Attributes:
        contract_id: Which contract drifted.
        category: Contract family.
        criticality: From the contract.
        old_value: What Zeus believed.
        new_value: What was actually found (None if live check not yet run).
        detected_at: UTC datetime of detection.
        severity: Derived operational severity (auto-set from criticality if not given):
            "critical"  — blocking contracts
            "moderate"  — degraded contracts
            "low"       — advisory contracts
    """
    contract_id: str
    old_value: Any
    category: str = ""
    criticality: str = ""
    new_value: Any = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    severity: str | None = None

    def __post_init__(self) -> None:
        if self.severity is None:
            self.severity = {
                "blocking": "critical",
                "degraded": "moderate",
                "advisory": "low",
            }.get(self.criticality, "low")


@dataclass
class Antibody:
    """Descriptor for a fix that makes a class of drift permanently detectable.

    An antibody is NOT an alert — it is a test, type change, or contract update
    that produces a CI failure the next time the same class of drift occurs.

    Attributes:
        drift_contract_id: Which contract triggered this antibody.
        action_type: "code_change" | "config_change" | "test_addition".
        description: Human-readable prescription for the fix.
        target_file: Source file to change, if action_type=="code_change".
        target_config: Config file to update, if action_type=="config_change".
        suggested_test_name: Pytest name to implement, if action_type=="test_addition".
        generated_at: UTC datetime.
    """
    drift_contract_id: str
    action_type: Literal["code_change", "config_change", "test_addition"]
    description: str
    target_file: str | None = None
    target_config: str | None = None
    suggested_test_name: str | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Convenience loader  (thin wrapper — full implementation in reality_contracts_loader)
# ---------------------------------------------------------------------------

def load_contracts_from_yaml(yaml_path: str | Path) -> list[RealityContract]:
    """Load RealityContracts from a single YAML file.

    The file stem must be a valid category name (economic / execution / data / protocol).

    Args:
        yaml_path: Path to a *.yaml file in config/reality_contracts/.

    Returns:
        List of RealityContract instances from that file.
    """
    import yaml  # soft import — only needed when this function is called

    yaml_path = Path(yaml_path)
    category = yaml_path.stem
    _VALID_CATEGORIES = {"economic", "execution", "data", "protocol"}
    _VALID_CRITICALITIES = {"blocking", "degraded", "advisory"}

    if category not in _VALID_CATEGORIES:
        raise ValueError(
            f"Unrecognised contract category '{category}' from file {yaml_path}."
        )

    with yaml_path.open() as f:
        entries = yaml.safe_load(f) or []

    contracts: list[RealityContract] = []
    for entry in entries:
        lv = entry["last_verified"]
        if isinstance(lv, str):
            lv = datetime.fromisoformat(lv.replace("Z", "+00:00"))
        if lv.tzinfo is None:
            lv = lv.replace(tzinfo=timezone.utc)

        contracts.append(
            RealityContract(
                contract_id=entry["contract_id"],
                category=category,
                assumption=entry["assumption"],
                current_value=entry["current_value"],
                verification_method=entry["verification_method"],
                last_verified=lv,
                ttl_seconds=int(entry["ttl_seconds"]),
                criticality=entry["criticality"],
                on_change_handlers=entry.get("on_change_handlers", []),
            )
        )
    return contracts


# ---------------------------------------------------------------------------
# Re-export RealityContractVerifier from reality_verifier
# (bottom-of-module import is circular-safe: RealityContract is fully defined above)
# ---------------------------------------------------------------------------

from src.contracts.reality_verifier import RealityContractVerifier  # noqa: E402
