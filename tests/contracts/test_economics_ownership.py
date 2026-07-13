# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md (LX-0R) +
#   docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt

"""Tests for src.contracts.economics_ownership — the forbidden economics-column
contract and trade-DB truth-epoch vocabulary."""

from __future__ import annotations

import pytest

from src.contracts.economics_ownership import (
    FORBIDDEN_COLUMNS_BY_TABLE,
    FORBIDDEN_ECONOMICS_COLUMNS,
    EconomicsWriterRole,
    ForbiddenEconomicsColumn,
    TruthEpoch,
    is_forbidden_economics_column,
    is_writer_role_permitted,
    permitted_writer_role,
    truth_epoch_rank,
)


# --------------------------------------------------------------------------- #
# TruthEpoch vocabulary + monotonic rank                                      #
# --------------------------------------------------------------------------- #

def test_truth_epoch_is_exactly_three_states() -> None:
    assert {e.value for e in TruthEpoch} == {"LEGACY", "PREPARE", "ACTIVE_NEW"}


def test_truth_epoch_rank_is_monotonic_increasing() -> None:
    assert truth_epoch_rank(TruthEpoch.LEGACY) < truth_epoch_rank(TruthEpoch.PREPARE)
    assert truth_epoch_rank(TruthEpoch.PREPARE) < truth_epoch_rank(TruthEpoch.ACTIVE_NEW)


def test_truth_epoch_rank_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        truth_epoch_rank("NOT_A_REAL_EPOCH")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Writer-role permission (epoch-scoped)                                       #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("epoch", [TruthEpoch.LEGACY, TruthEpoch.PREPARE])
def test_legacy_and_prepare_permit_legacy_writer(epoch: TruthEpoch) -> None:
    assert permitted_writer_role(epoch) is EconomicsWriterRole.LEGACY_PROJECTION_WRITER


def test_active_new_permits_only_deterministic_reducer() -> None:
    assert permitted_writer_role(TruthEpoch.ACTIVE_NEW) is EconomicsWriterRole.DETERMINISTIC_REDUCER


def test_is_writer_role_permitted_matches_permitted_writer_role() -> None:
    assert is_writer_role_permitted(
        epoch=TruthEpoch.LEGACY, role=EconomicsWriterRole.LEGACY_PROJECTION_WRITER
    )
    assert not is_writer_role_permitted(
        epoch=TruthEpoch.LEGACY, role=EconomicsWriterRole.DETERMINISTIC_REDUCER
    )
    assert is_writer_role_permitted(
        epoch=TruthEpoch.ACTIVE_NEW, role=EconomicsWriterRole.DETERMINISTIC_REDUCER
    )
    assert not is_writer_role_permitted(
        epoch=TruthEpoch.ACTIVE_NEW, role=EconomicsWriterRole.LEGACY_PROJECTION_WRITER
    )


# --------------------------------------------------------------------------- #
# Forbidden-column set — completeness against the task's seed spec            #
# --------------------------------------------------------------------------- #

_EXPECTED_POSITION_CURRENT_COLUMNS = {
    "shares", "cost_basis_usd", "entry_price", "size_usd",
    "chain_shares", "chain_avg_price", "chain_cost_basis_usd",
    "realized_pnl_usd", "exit_price", "settlement_price",
}

_EXPECTED_EDLI_COLUMNS = {
    "pnl_usd", "realized_edge", "edge_value_usd",
    "settlement_outcome", "promotion_eligible",
}


def test_forbidden_columns_cover_position_current_seed_set() -> None:
    assert FORBIDDEN_COLUMNS_BY_TABLE["position_current"] == frozenset(
        _EXPECTED_POSITION_CURRENT_COLUMNS
    )


def test_forbidden_columns_cover_edli_seed_set() -> None:
    assert FORBIDDEN_COLUMNS_BY_TABLE["edli_live_profit_audit"] == frozenset(
        _EXPECTED_EDLI_COLUMNS
    )


def test_forbidden_columns_exactly_two_tables() -> None:
    assert set(FORBIDDEN_COLUMNS_BY_TABLE) == {"position_current", "edli_live_profit_audit"}


def test_forbidden_columns_no_duplicate_entries() -> None:
    pairs = [(c.table, c.column) for c in FORBIDDEN_ECONOMICS_COLUMNS]
    assert len(pairs) == len(set(pairs))


def test_every_forbidden_column_has_nonempty_description() -> None:
    for col in FORBIDDEN_ECONOMICS_COLUMNS:
        assert isinstance(col, ForbiddenEconomicsColumn)
        assert col.description.strip()


def test_is_forbidden_economics_column_true_for_known_pairs() -> None:
    assert is_forbidden_economics_column("position_current", "realized_pnl_usd")
    assert is_forbidden_economics_column("edli_live_profit_audit", "pnl_usd")


def test_is_forbidden_economics_column_false_for_unknown_pairs() -> None:
    assert not is_forbidden_economics_column("position_current", "phase")
    assert not is_forbidden_economics_column("position_current", "order_status")
    assert not is_forbidden_economics_column("edli_live_profit_audit", "audit_id")
    assert not is_forbidden_economics_column("some_other_table", "shares")
