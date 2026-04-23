"""Decision evidence contract — D4 resolution.

D4 gap: Entry uses bootstrap p-value + BH-FDR (α=0.10, n=200+, CI_lower > 0).
Exit uses 2 consecutive negative cycles — a single forward_edge comparison with no
multiple-testing correction and a sample width of 2. The system admits edges
cautiously but exits aggressively, killing true edges before maturation.
The legacy predecessor's 7/8 buy_no false-EDGE_REVERSAL deaths match this pattern exactly.

Resolution: Entry and exit decisions share the same DecisionEvidence contract.
Exit evidence must meet symmetric statistical burden.

See: docs/zeus_FINAL_spec.md §P9.3 D4
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

# T4.1a 2026-04-23 — persistence-format version. Bump on any change to
# the DecisionEvidence field set or field semantics so legacy rows are
# rejected by from_json rather than silently coerced.
DECISION_EVIDENCE_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class DecisionEvidence:
    """Typed evidence record for entry and exit decisions.

    Resolves D4: enforces symmetric statistical burden between entry and exit.
    An exit decision constructed with weaker evidence than entry raises a
    runtime contract violation.

    Attributes:
        evidence_type: Whether this evidence is for entry or exit.
        statistical_method: Algorithm used (e.g. "bootstrap_ci", "bh_fdr",
            "consecutive_observation", "forward_edge_threshold").
        sample_size: Number of observations / bootstrap draws underlying the
            conclusion. Exit with sample_size=2 is the canonical D4 symptom.
        confidence_level: Statistical confidence level (0–1). For FDR-corrected
            methods this is the target FDR α, e.g. 0.10.
        fdr_corrected: True if Benjamini-Hochberg (or equivalent) multiple-testing
            correction was applied.
        consecutive_confirmations: Number of consecutive confirming cycles
            required before the decision fires. Entry uses CI_lower > 0 + FDR
            (effectively ≥1 robust confirmation). Exit using 2 cycles with no
            FDR is the asymmetry.
    """

    evidence_type: Literal["entry", "exit"]
    statistical_method: str
    sample_size: int
    confidence_level: float
    fdr_corrected: bool
    consecutive_confirmations: int

    def __post_init__(self) -> None:
        if self.sample_size < 1:
            raise ValueError(
                f"DecisionEvidence.sample_size must be >= 1, got {self.sample_size}"
            )
        if not 0.0 < self.confidence_level <= 1.0:
            raise ValueError(
                f"DecisionEvidence.confidence_level must be in (0, 1], "
                f"got {self.confidence_level}"
            )
        if self.consecutive_confirmations < 1:
            raise ValueError(
                f"DecisionEvidence.consecutive_confirmations must be >= 1, "
                f"got {self.consecutive_confirmations}"
            )
        if not self.statistical_method:
            raise ValueError(
                "DecisionEvidence.statistical_method must not be empty"
            )

    def to_json(self) -> str:
        """Serialize to a JSON string wrapped with contract_version.

        T4.1a 2026-04-23 (D4 persistence primitive per T4.0 Option E):
        produces the canonical payload shape
        ``{"contract_version": 1, "fields": {...}}`` for storage inside a
        ``position_events.payload_json`` ``decision_evidence`` key. The
        wrapper lets ``from_json`` reject legacy or future-versioned rows
        via ``UnknownContractVersionError`` rather than silently coercing.

        Constraint: every field on this class must remain JSON-native
        (str / int / float / bool). If a future contract_version bump
        adds a nested dataclass or Enum field, this method must pair
        with a custom encoder and the version constant must increment.
        """
        return json.dumps(
            {
                "contract_version": DECISION_EVIDENCE_CONTRACT_VERSION,
                "fields": asdict(self),
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, payload: str) -> "DecisionEvidence":
        """Deserialize a JSON string produced by ``to_json``.

        T4.1a 2026-04-23: validates the ``contract_version`` envelope
        first (strict int equality — rejects ``True`` via
        ``type(version) is int`` guard to avoid the Python
        ``True == 1`` collision), then delegates field validation to
        the dataclass ``__post_init__``. Raises
        ``UnknownContractVersionError`` on version mismatch, missing
        envelope keys, or unknown/missing fields. Raises ``ValueError``
        on malformed JSON or invalid field values (the latter bubbles
        up from ``__post_init__`` unchanged so callers can distinguish
        schema drift from data validation).
        """
        try:
            blob = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"DecisionEvidence.from_json: malformed JSON: {exc}"
            ) from exc
        if not isinstance(blob, dict):
            raise UnknownContractVersionError(
                f"DecisionEvidence.from_json: payload must be a JSON "
                f"object, got {type(blob).__name__}"
            )
        version = blob.get("contract_version")
        # Guard against True == 1 collision: require exact int type.
        if type(version) is not int or version != DECISION_EVIDENCE_CONTRACT_VERSION:
            raise UnknownContractVersionError(
                f"DecisionEvidence.from_json: contract_version="
                f"{version!r} not supported (this runtime expects int "
                f"{DECISION_EVIDENCE_CONTRACT_VERSION}). A schema "
                "migration slice must land before older/newer payloads "
                "can be parsed."
            )
        fields = blob.get("fields")
        if not isinstance(fields, dict):
            raise UnknownContractVersionError(
                f"DecisionEvidence.from_json: 'fields' key missing or "
                f"not an object, got {type(fields).__name__}"
            )
        try:
            return cls(**fields)
        except TypeError as exc:
            # Unknown field key or missing required field — treat as
            # schema drift rather than silent coercion. __post_init__
            # ValueErrors are deliberately NOT caught here; they
            # propagate unchanged as invalid-data signals.
            raise UnknownContractVersionError(
                f"DecisionEvidence.from_json: field set mismatch "
                f"against contract_version="
                f"{DECISION_EVIDENCE_CONTRACT_VERSION}: {exc}"
            ) from exc

    def assert_symmetric_with(self, entry_evidence: "DecisionEvidence") -> None:
        """Raise EvidenceAsymmetryError if this exit evidence is weaker than
        the paired entry evidence.

        Symmetry rules (D4):
        1. Exit sample_size >= entry sample_size × 0.1 (exit can be smaller but
           not trivially so — 2 cycles vs 200 bootstrap draws is a 100× gap).
        2. If entry is fdr_corrected, exit must also be fdr_corrected.
        3. Exit consecutive_confirmations >= entry consecutive_confirmations.
        """
        if self.evidence_type != "exit":
            raise ValueError(
                "assert_symmetric_with must be called on exit evidence, "
                f"got evidence_type='{self.evidence_type}'"
            )
        if entry_evidence.evidence_type != "entry":
            raise ValueError(
                "entry_evidence must have evidence_type='entry', "
                f"got '{entry_evidence.evidence_type}'"
            )

        errors = []

        min_exit_sample = max(2, int(entry_evidence.sample_size * 0.1))
        if self.sample_size < min_exit_sample:
            errors.append(
                f"Exit sample_size={self.sample_size} is too small relative to "
                f"entry sample_size={entry_evidence.sample_size} "
                f"(minimum acceptable: {min_exit_sample}). "
                "This is the D4 asymmetry: 2-cycle exit vs 200-bootstrap entry."
            )

        if entry_evidence.fdr_corrected and not self.fdr_corrected:
            errors.append(
                "Entry used FDR correction but exit does not. "
                "Exit evidence must meet symmetric multiple-testing correction."
            )

        if self.consecutive_confirmations < entry_evidence.consecutive_confirmations:
            errors.append(
                f"Exit requires {self.consecutive_confirmations} confirmation(s) but "
                f"entry required {entry_evidence.consecutive_confirmations}. "
                "Exit must be at least as strict as entry."
            )

        if errors:
            raise EvidenceAsymmetryError(
                "DecisionEvidence symmetry violation (D4):\n"
                + "\n".join(f"  • {e}" for e in errors)
            )


class EvidenceAsymmetryError(Exception):
    """Raised when exit evidence is statistically weaker than entry evidence.
    This is the D4 runtime contract violation — the system would admit edges
    cautiously but exit aggressively, killing true edges before maturation.
    """


class UnknownContractVersionError(ValueError):
    """Raised when DecisionEvidence.from_json receives a payload with an
    unknown or missing contract_version. T4.1a 2026-04-23: prevents silent
    schema drift when the DecisionEvidence field set evolves.
    """
