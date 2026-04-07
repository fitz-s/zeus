"""P10 — Reality contract verifier.

Runs before any trading decision (verify_all_blocking) and on Venus heartbeat
(detect_drift). Generates antibodies when drift is detected.

NOTE: VerificationResult, DriftEvent, Antibody are defined in reality_contract.py
so tests can import everything from one place. This module imports them from there.

See: docs/zeus_FINAL_spec.md §P10.5
"""

from __future__ import annotations

# Import support types from reality_contract (defined there to avoid circular imports
# and to give tests a single import point).
# reality_contract imports RealityContractVerifier from here at the bottom — that
# is safe because this file's class definition only depends on reality_contract types
# which are fully defined before the re-import runs.
from src.contracts.reality_contract import (
    Antibody,
    DriftEvent,
    RealityContract,
    VerificationResult,
)


class RealityContractVerifier:
    """Verifies a set of RealityContracts and generates antibodies on drift.

    Live verification (network calls) is intentionally kept out of this class.
    Each contract's `verification_method` field describes how to verify it;
    callers that need live checks should implement a subclass or adapter.

    This class enforces staleness checks only. That is sufficient to block
    trading when a blocking contract hasn't been re-verified within its TTL.
    """

    def __init__(self, contracts: list[RealityContract]) -> None:
        self.contracts = contracts

    def verify_all_blocking(self) -> VerificationResult:
        """Called before any trading decision.

        A blocking contract that is stale (is_stale=True) counts as a failure:
        Zeus cannot confirm the assumption still holds, so must not trade.
        """
        failures = [
            c for c in self.contracts
            if c.criticality == "blocking" and c.is_stale
        ]
        return VerificationResult(can_trade=not bool(failures), failures=failures)

    def detect_drift(self) -> list[DriftEvent]:
        """Called by Venus heartbeat to find stale contracts across all criticalities.

        Returns a DriftEvent for every contract whose TTL has elapsed.
        The caller is responsible for re-verifying the actual external value and
        updating `new_value` before recording or acting on the event.
        """
        events = []
        for contract in self.contracts:
            if contract.is_stale:
                events.append(
                    DriftEvent(
                        contract_id=contract.contract_id,
                        category=contract.category,
                        criticality=contract.criticality,
                        old_value=contract.current_value,
                        new_value=None,  # unknown until caller does live check
                    )
                )
        return events

    def generate_antibody(self, drift: DriftEvent) -> Antibody:
        """Translate a DriftEvent into a fix descriptor.

        Routing by severity (§P11.6):
          severity=critical  → code_change  (halt path must be tested in CI)
          severity=moderate  → config_change (TTL or value update in YAML)
          severity=low       → test_addition (log verification via test)
        """
        cid_lower = drift.contract_id.lower()

        if drift.severity == "critical":
            return Antibody(
                drift_contract_id=drift.contract_id,
                action_type="code_change",
                description=(
                    f"Blocking contract '{drift.contract_id}' drifted "
                    f"(was {drift.old_value!r}, now {drift.new_value!r}). "
                    "Add a test asserting verify_all_blocking() returns "
                    "can_trade=False when this contract is stale, and update "
                    "current_value + last_verified after live re-check."
                ),
                target_file="src/contracts/reality_contract.py",
                suggested_test_name=f"test_{cid_lower}_blocks_trading_when_stale",
            )
        elif drift.severity == "moderate":
            return Antibody(
                drift_contract_id=drift.contract_id,
                action_type="config_change",
                description=(
                    f"Degraded contract '{drift.contract_id}' is stale "
                    f"(was {drift.old_value!r}, now {drift.new_value!r}). "
                    "Re-verify externally and update current_value + last_verified in YAML."
                ),
                target_config=f"config/reality_contracts/{drift.category}.yaml",
            )
        else:
            return Antibody(
                drift_contract_id=drift.contract_id,
                action_type="test_addition",
                description=(
                    f"Advisory contract '{drift.contract_id}' is stale. "
                    "Add a test that logs when this contract has not been verified "
                    "within its TTL, so future drift is surfaced in CI."
                ),
                suggested_test_name=f"test_{cid_lower}_logs_when_stale",
            )
