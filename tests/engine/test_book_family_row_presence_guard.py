"""A manifest family with no book rows is a typed exclusion, not a reason to abort the cut.

`actionable_family_payoff_bindings` (src/solve/solver.py:1305-1315) filters a
`DeterministicBinPayoffWitness`'s bindings down to the bins its `exact_yes_payoffs` covers.
When that intersection is empty the family contributes ZERO rows to the book epoch's
`asset_states`, yet nothing removes its key from the probabilities mapping the book-epoch
provider returns. The receipt then compares the two sets
(`_book_native_side_receipt`, global_batch_runtime.py:2376) and raises
GLOBAL_AUCTION_RECEIPT_BOOK_SIDE_COVERAGE_INVALID — inside `select_once`, so the whole
economic cut aborts before any winner is claimed, not merely the receipt. 20 such aborts
were observed live on 2026-09-17.

`_book_native_side_receipt`'s own docstring states the contract it is meant to prove: every
bound side became a candidate OR a typed current-book exclusion. The strict set equality did
not honour the second half, even though the call site deliberately merges
`materialization_excluded_by_family` for precisely this case ("instead of re-demanding full
coverage against a family it never scored", global_batch_runtime.py:9316-9321). The clause now
consults that map. The reverse direction — a row whose family is absent from the manifest —
stays invalid, since nothing explains it.
"""
from __future__ import annotations

import pytest

from src.engine.global_batch_runtime import (
    _BOOK_NATIVE_SIDE_STATE_FIELDS,
    _book_native_side_receipt,
)


def _state(family_key: str, *, bin_id: str = "b1", side: str = "YES") -> tuple[str, ...]:
    """One asset_states row in the receipt's own field order."""
    values = {
        "family_key": family_key,
        "bin_id": bin_id,
        "condition_id": f"cond-{family_key}",
        "side": side,
        "token_id": f"tok-{family_key}-{bin_id}-{side}",
        "status": "EXECUTABLE",
        "book_hash": "hash",
        "market_event_id": f"evt-{family_key}",
        "gamma_market_id": f"gamma-{family_key}",
        "neg_risk": "False",
    }
    return tuple(values[field] for field in _BOOK_NATIVE_SIDE_STATE_FIELDS)


def test_row_less_family_with_a_typed_exclusion_is_accepted():
    """The fix: an excluded family that produced no rows must not abort the cut."""
    receipt = _book_native_side_receipt(
        asset_states=[_state("fam-a")],
        probability_keys=("fam-a", "fam-b"),
        buy_candidate_index=[],
        # fam-a is excluded so its own EXECUTABLE row needs no candidate (the
        # separate BUY materialization check); fam-b is the row-less family
        # under test.
        excluded_by_family={
            "fam-a": "book_side_excluded",
            "fam-b": "candidate_materialization_failed",
        },
    )
    assert receipt["book_native_side_state_count"] == 1
    assert receipt["book_native_side_candidate_coverage_status"] == "COMPLETE"


def test_row_less_family_without_an_exclusion_still_refuses():
    """An unexplained absence keeps failing closed; the fix narrows, never removes."""
    with pytest.raises(ValueError) as excinfo:
        _book_native_side_receipt(
            asset_states=[_state("fam-a")],
            probability_keys=("fam-a", "fam-b"),
            buy_candidate_index=[],
            excluded_by_family={},
        )
    assert "GLOBAL_AUCTION_RECEIPT_BOOK_SIDE_COVERAGE_INVALID" in str(excinfo.value)


def test_fully_covered_manifest_is_accepted():
    """The ordinary case stays accepted with no exclusion needed."""
    receipt = _book_native_side_receipt(
        asset_states=[_state("fam-a")],
        probability_keys=("fam-a",),
        buy_candidate_index=[],
        excluded_by_family={"fam-a": "book_side_excluded"},
    )
    assert receipt["book_native_side_state_count"] == 1


def test_extra_book_rows_beyond_the_manifest_still_refuse():
    """The set equality stays two-sided; this fix must not weaken the other direction."""
    with pytest.raises(ValueError) as excinfo:
        _book_native_side_receipt(
            asset_states=[_state("fam-a"), _state("fam-b")],
            probability_keys=("fam-a",),
            buy_candidate_index=[],
            excluded_by_family={},
        )
    assert "GLOBAL_AUCTION_RECEIPT_BOOK_SIDE_COVERAGE_INVALID" in str(excinfo.value)


def test_coverage_predicate_reads_the_family_key_column():
    """The clause derives its set from state[0]; pin that the field order says so."""
    assert _BOOK_NATIVE_SIDE_STATE_FIELDS[0] == "family_key"
    assert _state("fam-z")[0] == "fam-z"


def test_deterministic_witness_with_no_covered_bin_yields_no_binding():
    """The upstream cause: an empty exact/binding intersection produces zero bindings."""
    from src.solve.solver import actionable_family_payoff_bindings

    class _Binding:
        def __init__(self, bin_id: str) -> None:
            self.bin_id = bin_id

    from src.solve.solver import DeterministicBinPayoffWitness

    bindings = (_Binding("b1"), _Binding("b2"))
    witness = DeterministicBinPayoffWitness.__new__(DeterministicBinPayoffWitness)
    object.__setattr__(witness, "bindings", bindings)
    object.__setattr__(witness, "exact_yes_payoffs", (("b9", 1.0),))
    assert actionable_family_payoff_bindings(witness) == ()
