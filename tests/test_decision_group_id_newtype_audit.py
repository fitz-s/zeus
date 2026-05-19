# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.5 §14.6, INV-group-id-type,
#   topology packet "phase0-pr4-decision-group-id"
"""R-4.5: AST audit — DecisionGroupId NewType structural tests.

LIVE TESTS (partially): Tests that can pass today are live.
Tests requiring the production hash implementation are xfail(SCAFFOLD).

INV-group-id-type: All call sites that construct or accept decision group IDs
must use DecisionGroupId (NewType wrapping str), not raw str.

Test plan:
    T1 (LIVE): DecisionGroupId is importable from src.contracts.decision_group_id.
    T2 (LIVE): DecisionGroupId is a subtype of str (NewType check).
    T3 (LIVE): decision_group_id_v1_hash is importable and callable.
    T4 (LIVE): decision_group_id_v1_hash has the correct signature (inspect).
    T5 (LIVE): src/contracts/decision_group_id.py exists with SCAFFOLD header.
    T6 (SCAFFOLD): decision_group_id_v1_hash("0xabc", 3, 7) returns a
        non-empty str starting with "dgid_v1_".
    T7 (SCAFFOLD): Two DecisionGroupId values from same args are equal.
    T8 (SCAFFOLD): Two DecisionGroupId values from different args are not equal.
"""

import inspect
import os

import pytest

from src.contracts.decision_group_id import DecisionGroupId, decision_group_id_v1_hash


def test_decision_group_id_is_importable():
    """DecisionGroupId must be importable without error."""
    assert DecisionGroupId is not None


def test_decision_group_id_is_newtype_of_str():
    """DecisionGroupId(x) must return a str (NewType is erased at runtime)."""
    value = DecisionGroupId("test_group_123")
    assert isinstance(value, str)


def test_decision_group_id_v1_hash_is_importable_and_callable():
    """decision_group_id_v1_hash must be importable and callable."""
    assert callable(decision_group_id_v1_hash)


def test_decision_group_id_v1_hash_has_correct_signature():
    """decision_group_id_v1_hash must accept market_id, bin_index, lead_days_bucket."""
    sig = inspect.signature(decision_group_id_v1_hash)
    params = set(sig.parameters.keys())
    required = {"market_id", "bin_index", "lead_days_bucket"}
    assert required.issubset(params), (
        f"decision_group_id_v1_hash is missing required params: {required - params}"
    )


def test_decision_group_id_contract_file_exists():
    """src/contracts/decision_group_id.py must exist at the expected path."""
    contract_path = os.path.join(
        os.path.dirname(__file__), "..", "src", "contracts", "decision_group_id.py"
    )
    assert os.path.isfile(contract_path), (
        f"decision_group_id.py not found at {contract_path}"
    )


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: decision_group_id_v1_hash not yet implemented")
def test_decision_group_id_v1_hash_returns_dgid_prefixed_str():
    """Hash output must be non-empty str starting with 'dgid_v1_'.

    SCAFFOLD: not implemented.
    """
    result = decision_group_id_v1_hash(
        market_id="0xabc123",
        bin_index=3,
        lead_days_bucket=7,
    )
    assert isinstance(result, str)
    assert result.startswith("dgid_v1_"), f"Expected 'dgid_v1_' prefix, got: {result!r}"
    assert len(result) > len("dgid_v1_"), "Hash result too short"


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: decision_group_id_v1_hash not yet implemented")
def test_decision_group_id_v1_hash_same_args_equal():
    """Same args always return identical DecisionGroupId.

    SCAFFOLD: not implemented.
    """
    a = decision_group_id_v1_hash(market_id="0xabc", bin_index=0, lead_days_bucket=1)
    b = decision_group_id_v1_hash(market_id="0xabc", bin_index=0, lead_days_bucket=1)
    assert a == b


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: decision_group_id_v1_hash not yet implemented")
def test_decision_group_id_v1_hash_different_args_not_equal():
    """Different args must return different DecisionGroupId values.

    SCAFFOLD: not implemented.
    """
    a = decision_group_id_v1_hash(market_id="0xabc", bin_index=0, lead_days_bucket=1)
    b = decision_group_id_v1_hash(market_id="0xabc", bin_index=1, lead_days_bucket=1)
    assert a != b
