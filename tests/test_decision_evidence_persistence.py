# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: T4.1a of midstream remediation packet
# (docs/operations/task_2026-04-23_midstream_remediation/plan.md);
# T4.0 design doc Option E pins the payload format as
# {contract_version: 1, fields: {...}}; this slice adds the primitive,
# T4.1b will wire it into the entry path.

"""T4.1a antibody — DecisionEvidence.to_json / from_json round-trip + version guard.

T4.0 design doc (rev2) pinned Option E: store DecisionEvidence payloads
as nested ``decision_evidence`` keys inside ``position_events.payload_json``
on the existing ``ENTRY_ORDER_POSTED`` event. The persistence primitive
must therefore:

1. Produce a canonical JSON shape
   ``{"contract_version": 1, "fields": {...}}`` via ``to_json()``.
2. Deserialize the same shape back into an equal ``DecisionEvidence``
   instance via the ``from_json(cls, payload)`` classmethod.
3. Reject legacy or future-versioned payloads with a clear
   ``UnknownContractVersionError``, preventing silent schema drift.
4. Reject malformed JSON, missing envelope keys, and unknown field keys
   under the same error class (field-drift is schema drift).

These tests exercise all four contracts.
"""

from __future__ import annotations

import json

import pytest

from src.contracts.decision_evidence import (
    DECISION_EVIDENCE_CONTRACT_VERSION,
    DecisionEvidence,
    UnknownContractVersionError,
)


def _entry_evidence() -> DecisionEvidence:
    """Canonical entry-side evidence matching T4.1's planned construction
    site at ``src/engine/evaluator.py`` (bootstrap_ci + BH-FDR + 1-cycle
    confirmation)."""
    return DecisionEvidence(
        evidence_type="entry",
        statistical_method="bootstrap_ci_bh_fdr",
        sample_size=5000,
        confidence_level=0.10,
        fdr_corrected=True,
        consecutive_confirmations=1,
    )


class TestToJsonShape:
    def test_to_json_wraps_with_contract_version(self) -> None:
        payload = _entry_evidence().to_json()
        blob = json.loads(payload)
        assert blob["contract_version"] == DECISION_EVIDENCE_CONTRACT_VERSION
        assert isinstance(blob["fields"], dict)

    def test_to_json_fields_match_dataclass(self) -> None:
        ev = _entry_evidence()
        blob = json.loads(ev.to_json())
        assert blob["fields"] == {
            "evidence_type": "entry",
            "statistical_method": "bootstrap_ci_bh_fdr",
            "sample_size": 5000,
            "confidence_level": 0.10,
            "fdr_corrected": True,
            "consecutive_confirmations": 1,
        }

    def test_to_json_is_stable_sorted(self) -> None:
        # Deterministic ordering is required so equal evidence produces
        # equal payloads (critical for idempotency when ENTRY_ORDER_POSTED
        # retries re-emit the same payload).
        ev = _entry_evidence()
        a = ev.to_json()
        b = ev.to_json()
        assert a == b


class TestFromJsonRoundTrip:
    def test_entry_round_trip_equals_original(self) -> None:
        original = _entry_evidence()
        recovered = DecisionEvidence.from_json(original.to_json())
        assert recovered == original

    def test_exit_round_trip_equals_original(self) -> None:
        original = DecisionEvidence(
            evidence_type="exit",
            statistical_method="consecutive_negative_ci",
            sample_size=500,
            confidence_level=0.10,
            fdr_corrected=True,
            consecutive_confirmations=1,
        )
        recovered = DecisionEvidence.from_json(original.to_json())
        assert recovered == original


class TestFromJsonRejectsVersionDrift:
    def test_missing_version_raises(self) -> None:
        payload = json.dumps({"fields": {
            "evidence_type": "entry",
            "statistical_method": "bootstrap_ci_bh_fdr",
            "sample_size": 5000,
            "confidence_level": 0.10,
            "fdr_corrected": True,
            "consecutive_confirmations": 1,
        }})
        with pytest.raises(UnknownContractVersionError, match="contract_version"):
            DecisionEvidence.from_json(payload)

    def test_wrong_version_raises(self) -> None:
        payload = json.dumps({
            "contract_version": 99,
            "fields": {
                "evidence_type": "entry",
                "statistical_method": "bootstrap_ci_bh_fdr",
                "sample_size": 5000,
                "confidence_level": 0.10,
                "fdr_corrected": True,
                "consecutive_confirmations": 1,
            },
        })
        with pytest.raises(UnknownContractVersionError, match="99"):
            DecisionEvidence.from_json(payload)

    def test_string_version_raises(self) -> None:
        # JSON may carry the version as a string if a future consumer
        # deserializes inconsistently. Equality against an integer
        # constant catches this category.
        payload = json.dumps({
            "contract_version": "1",
            "fields": {
                "evidence_type": "entry",
                "statistical_method": "bootstrap_ci_bh_fdr",
                "sample_size": 5000,
                "confidence_level": 0.10,
                "fdr_corrected": True,
                "consecutive_confirmations": 1,
            },
        })
        with pytest.raises(UnknownContractVersionError):
            DecisionEvidence.from_json(payload)

    def test_null_version_raises(self) -> None:
        # JSON `null` → Python `None` must fail the type-strict check.
        payload = json.dumps({
            "contract_version": None,
            "fields": {
                "evidence_type": "entry",
                "statistical_method": "bootstrap_ci_bh_fdr",
                "sample_size": 5000,
                "confidence_level": 0.10,
                "fdr_corrected": True,
                "consecutive_confirmations": 1,
            },
        })
        with pytest.raises(UnknownContractVersionError):
            DecisionEvidence.from_json(payload)

    def test_boolean_true_version_does_not_collide_with_int_one(self) -> None:
        # CRITICAL Python gotcha: True == 1 is True at the `==` level.
        # Without a `type(version) is int` strict guard, a payload with
        # `contract_version: true` would masquerade as version 1 and
        # silently parse. The strict-type guard in from_json must reject
        # this.
        payload = json.dumps({
            "contract_version": True,
            "fields": {
                "evidence_type": "entry",
                "statistical_method": "bootstrap_ci_bh_fdr",
                "sample_size": 5000,
                "confidence_level": 0.10,
                "fdr_corrected": True,
                "consecutive_confirmations": 1,
            },
        })
        with pytest.raises(UnknownContractVersionError):
            DecisionEvidence.from_json(payload)


class TestFromJsonRejectsMalformed:
    def test_malformed_json_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="malformed JSON"):
            DecisionEvidence.from_json("{not valid json")

    def test_non_object_payload_raises(self) -> None:
        with pytest.raises(UnknownContractVersionError, match="JSON object"):
            DecisionEvidence.from_json("[1, 2, 3]")

    def test_missing_fields_key_raises(self) -> None:
        payload = json.dumps({"contract_version": 1})
        with pytest.raises(UnknownContractVersionError, match="fields"):
            DecisionEvidence.from_json(payload)

    def test_fields_not_object_raises(self) -> None:
        payload = json.dumps({"contract_version": 1, "fields": "not-an-object"})
        with pytest.raises(UnknownContractVersionError, match="fields"):
            DecisionEvidence.from_json(payload)

    def test_unknown_field_raises_version_error(self) -> None:
        # A field that the current dataclass does not accept is treated
        # as schema drift rather than passed through.
        payload = json.dumps({
            "contract_version": 1,
            "fields": {
                "evidence_type": "entry",
                "statistical_method": "bootstrap_ci_bh_fdr",
                "sample_size": 5000,
                "confidence_level": 0.10,
                "fdr_corrected": True,
                "consecutive_confirmations": 1,
                "mystery_new_field": "hello",
            },
        })
        with pytest.raises(UnknownContractVersionError, match="field set mismatch"):
            DecisionEvidence.from_json(payload)

    def test_missing_required_field_raises(self) -> None:
        payload = json.dumps({
            "contract_version": 1,
            "fields": {
                "evidence_type": "entry",
                # missing statistical_method
                "sample_size": 5000,
                "confidence_level": 0.10,
                "fdr_corrected": True,
                "consecutive_confirmations": 1,
            },
        })
        with pytest.raises(UnknownContractVersionError, match="field set mismatch"):
            DecisionEvidence.from_json(payload)


class TestPostInitValueErrorsPropagate:
    """__post_init__ ValueErrors signal invalid DATA (not schema drift)
    and must bubble up UNCHANGED from from_json so callers can
    distinguish "row is malformed per current contract" from "row was
    written under a different contract version". The from_json
    implementation catches only TypeError (kwarg-level schema mismatch);
    any ValueError from __post_init__ propagates as a bare ValueError.
    """

    def test_sample_size_zero_propagates_valueerror(self) -> None:
        payload = json.dumps({
            "contract_version": 1,
            "fields": {
                "evidence_type": "entry",
                "statistical_method": "bootstrap_ci_bh_fdr",
                "sample_size": 0,  # invalid — must be >= 1
                "confidence_level": 0.10,
                "fdr_corrected": True,
                "consecutive_confirmations": 1,
            },
        })
        with pytest.raises(ValueError, match="sample_size must be >= 1"):
            DecisionEvidence.from_json(payload)
        # Specifically NOT the UnknownContractVersionError subclass —
        # verify the raised exception is a plain ValueError, not the
        # schema-drift sentinel.
        try:
            DecisionEvidence.from_json(payload)
        except UnknownContractVersionError:
            pytest.fail(
                "sample_size=0 is invalid-data, not schema-drift; "
                "from_json must propagate the ValueError unchanged."
            )
        except ValueError:
            pass  # correct

    def test_confidence_level_out_of_range_propagates_valueerror(self) -> None:
        payload = json.dumps({
            "contract_version": 1,
            "fields": {
                "evidence_type": "entry",
                "statistical_method": "bootstrap_ci_bh_fdr",
                "sample_size": 5000,
                "confidence_level": 1.5,  # invalid — must be in (0, 1]
                "fdr_corrected": True,
                "consecutive_confirmations": 1,
            },
        })
        with pytest.raises(ValueError, match="confidence_level must be in"):
            DecisionEvidence.from_json(payload)
        try:
            DecisionEvidence.from_json(payload)
        except UnknownContractVersionError:
            pytest.fail(
                "confidence_level=1.5 is invalid-data, not schema-drift."
            )
        except ValueError:
            pass  # correct
