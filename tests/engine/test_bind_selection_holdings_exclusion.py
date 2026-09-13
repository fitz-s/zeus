# Created: 2026-09-12
# Context: GLOBAL_AUCTION_FAILED:ValueError:current omega has missing or
#   duplicate native token identity was aborting the entire global auction
#   epoch (all families, not just the offending one) whenever any single
#   family's outcome space carried an untokenized (or duplicated) bin -- see
#   docs trace T-omega. ``_bind_selection_holdings`` now tolerates a
#   per-family binding failure, when given ``binding_failure_reason_by_family``,
#   by excluding just that family with a typed reason instead of raising --
#   matching the existing ``preflight_excluded_by_family`` mechanism that
#   ``select_once`` / ``select_prepared_global_auction``
#   (src/engine/global_batch_runtime.py, src/engine/global_single_order_auction.py)
#   already use for candidate-local preflight rejections. Callers that omit
#   the new parameter keep the original fail-closed behavior.
"""Unit tests for the native-holdings binding exclusion path."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.engine.global_batch_runtime as global_batch_runtime
from src.engine.native_holdings import NativeHoldingsSnapshot


@dataclass(frozen=True)
class _Prepared:
    probability_witness: object
    holdings_snapshot: object | None = None


def _binding(*, bin_id, condition_id, yes_token_id, no_token_id):
    return SimpleNamespace(
        bin_id=bin_id,
        condition_id=condition_id,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )


def _wealth_witness(*, ledger_snapshot_id="ledger-current", native_holdings_micro=()):
    return SimpleNamespace(
        ledger_snapshot_id=ledger_snapshot_id,
        native_holdings_micro=native_holdings_micro,
        pending_entry_endowments_micro=(),
    )


def _position(*, position_id, condition_id, token_id, no_token_id, shares):
    return SimpleNamespace(
        position_id=position_id,
        condition_id=condition_id,
        direction="buy_yes",
        token_id=token_id,
        no_token_id=no_token_id,
        chain_shares=shares,
    )


def test_family_with_untokenized_bin_is_excluded_not_fatal():
    """One family's bin with a ``None`` (not-yet-tokenized) token id is
    excluded with a typed reason naming the bin; the other two families
    still bind normally and remain represented in the returned mapping."""

    good_a = _Prepared(
        probability_witness=SimpleNamespace(
            family_key="family-a",
            bindings=(
                _binding(
                    bin_id="a1",
                    condition_id="cond-a1",
                    yes_token_id="yes-a1",
                    no_token_id="no-a1",
                ),
            ),
        )
    )
    bad = _Prepared(
        probability_witness=SimpleNamespace(
            family_key="family-bad",
            bindings=(
                _binding(
                    bin_id="b1",
                    condition_id="cond-b1",
                    yes_token_id="yes-b1",
                    no_token_id="no-b1",
                ),
                # Fresh Day0 extreme bin: not yet tokenized by Gamma.
                _binding(
                    bin_id="b2",
                    condition_id="cond-b2",
                    yes_token_id="yes-b2",
                    no_token_id=None,
                ),
            ),
        )
    )
    good_c = _Prepared(
        probability_witness=SimpleNamespace(
            family_key="family-c",
            bindings=(
                _binding(
                    bin_id="c1",
                    condition_id="cond-c1",
                    yes_token_id="yes-c1",
                    no_token_id="no-c1",
                ),
            ),
        )
    )
    prepared_by_event = {
        "event-a": good_a,
        "event-bad": bad,
        "event-c": good_c,
    }
    positions = (
        _position(
            position_id="pos-a",
            condition_id="cond-a1",
            token_id="yes-a1",
            no_token_id="no-a1",
            shares=Decimal("5"),
        ),
    )
    failures: dict[str, str] = {}
    rebound = global_batch_runtime._bind_selection_holdings(
        prepared_by_event,
        portfolio_state=SimpleNamespace(positions=positions),
        wealth_witness=_wealth_witness(native_holdings_micro=(("yes-a1", 5_000_000),)),
        binding_failure_reason_by_family=failures,
    )

    # Every event remains represented -- the offending family is excluded,
    # not dropped, matching select_prepared_global_auction's invariant that
    # an excluded family's holdings snapshot must still be present.
    assert set(rebound) == {"event-a", "event-bad", "event-c"}

    assert set(failures) == {"family-bad"}
    reason = failures["family-bad"]
    assert reason.startswith("GLOBAL_NATIVE_HOLDINGS_BINDING_FAILED:")
    assert "missing native token identity" in reason
    assert "cond-b2" in reason

    good_snapshot = rebound["event-a"].holdings_snapshot
    assert good_snapshot.family_key == "family-a"
    assert good_snapshot.holdings[0].position_id == "pos-a"
    assert good_snapshot.holdings[0].shares == Decimal("5")

    bad_snapshot = rebound["event-bad"].holdings_snapshot
    assert isinstance(bad_snapshot, NativeHoldingsSnapshot)
    assert bad_snapshot.family_key == "family-bad"
    assert bad_snapshot.ledger_snapshot_id == "ledger-current"
    assert bad_snapshot.holdings == ()
    assert bad_snapshot.pending_endowments == ()

    other_good_snapshot = rebound["event-c"].holdings_snapshot
    assert other_good_snapshot.family_key == "family-c"


def test_family_with_duplicate_token_across_bins_is_excluded_not_fatal():
    """A token id reused across two bins in one family excludes that family
    with a typed reason naming both bins; the other families still bind."""

    good_a = _Prepared(
        probability_witness=SimpleNamespace(
            family_key="family-a",
            bindings=(
                _binding(
                    bin_id="a1",
                    condition_id="cond-a1",
                    yes_token_id="yes-a1",
                    no_token_id="no-a1",
                ),
            ),
        )
    )
    bad = _Prepared(
        probability_witness=SimpleNamespace(
            family_key="family-bad",
            bindings=(
                _binding(
                    bin_id="b1",
                    condition_id="cond-b1",
                    yes_token_id="dup-token",
                    no_token_id="no-b1",
                ),
                _binding(
                    bin_id="b2",
                    condition_id="cond-b2",
                    yes_token_id="dup-token",
                    no_token_id="no-b2",
                ),
            ),
        )
    )
    good_c = _Prepared(
        probability_witness=SimpleNamespace(
            family_key="family-c",
            bindings=(
                _binding(
                    bin_id="c1",
                    condition_id="cond-c1",
                    yes_token_id="yes-c1",
                    no_token_id="no-c1",
                ),
            ),
        )
    )
    prepared_by_event = {
        "event-a": good_a,
        "event-bad": bad,
        "event-c": good_c,
    }
    failures: dict[str, str] = {}
    rebound = global_batch_runtime._bind_selection_holdings(
        prepared_by_event,
        portfolio_state=SimpleNamespace(positions=()),
        wealth_witness=_wealth_witness(),
        binding_failure_reason_by_family=failures,
    )

    assert set(rebound) == {"event-a", "event-bad", "event-c"}
    assert set(failures) == {"family-bad"}
    reason = failures["family-bad"]
    assert reason.startswith("GLOBAL_NATIVE_HOLDINGS_BINDING_FAILED:")
    assert "duplicate native token identity" in reason
    assert "cond-b1" in reason and "cond-b2" in reason

    bad_snapshot = rebound["event-bad"].holdings_snapshot
    assert bad_snapshot.family_key == "family-bad"
    assert bad_snapshot.holdings == ()

    assert rebound["event-a"].holdings_snapshot.family_key == "family-a"
    assert rebound["event-c"].holdings_snapshot.family_key == "family-c"


def test_binding_failure_without_out_param_still_raises():
    """Callers that do not opt in keep the original fail-closed behavior."""

    bad = _Prepared(
        probability_witness=SimpleNamespace(
            family_key="family-bad",
            bindings=(
                _binding(
                    bin_id="b1",
                    condition_id="cond-b1",
                    yes_token_id="yes-b1",
                    no_token_id=None,
                ),
            ),
        )
    )
    with pytest.raises(ValueError, match="missing native token identity"):
        global_batch_runtime._bind_selection_holdings(
            {"event-bad": bad},
            portfolio_state=SimpleNamespace(positions=()),
            wealth_witness=_wealth_witness(),
        )
