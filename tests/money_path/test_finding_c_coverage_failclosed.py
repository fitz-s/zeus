# Created: 2026-06-12
# Last reused/audited: 2026-06-12
# Authority basis: external deep code review 2026-06-12 FINDING-C (operator direct-fix
#   order). The settlement-coverage q_lcb shrinker is a SAFETY gate that can only LOWER
#   an unlicensed bound; a STRUCTURAL exception in it used to fail OPEN (keep the
#   UNSHRUNK upstream q_lcb). With the coverage gate ON it must fail CLOSED.
"""FINDING-C relationship invariant: separate "no historical coverage data" (a typed
INSUFFICIENT_DATA verdict that keeps the lcb) from "coverage authority threw" (a
structural fault). With the coverage gate ON, the authority-threw path fails CLOSED with
the typed TRANSIENT reason QLCB_COVERAGE_AUTHORITY_FAULT — never sizing on the unshrunk
bound. With the gate OFF the shrinker is a pure no-op (never raises).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.calibration.settlement_backward_coverage as sbc
from src.calibration.qlcb_provenance import QlcbByDirection, _qlcb_float, _set_qlcb_provenance
from src.engine.event_reactor_adapter import _maybe_apply_settlement_coverage_to_lcb
from src.events.reactor import (
    TRANSIENT_MONEY_PATH_REASONS,
    _is_transient_money_path_reason,
)
from src.types.market import Bin


class _EmptyRowsConn:
    """A forecast_conn whose settlement_outcomes read yields no rows (so the shrink path
    reaches settlement_backward_coverage_check rather than short-circuiting earlier)."""

    def execute(self, *args, **kwargs):
        class _Cursor:
            def fetchall(self_inner):
                return []

        return _Cursor()


def _family_with_one_no_bin():
    bin0 = Bin(10, 10, "C", "10°C")
    candidate = SimpleNamespace(condition_id="cond-1", bin=bin0)
    return SimpleNamespace(
        city="Chicago",
        metric="high",
        target_date="2026-06-14",
        candidates=[candidate],
    )


def _lcb_with_no_side(q_lcb_no: float = 0.80) -> QlcbByDirection:
    lcb = QlcbByDirection()
    _set_qlcb_provenance(lcb, ("cond-1", "buy_no"), q_lcb_no, source="FORECAST_BOOTSTRAP")
    return lcb


@pytest.fixture
def _gate_on(monkeypatch):
    from src.config import settings

    edli = dict(settings._data["edli"])
    edli["q_lcb_settlement_coverage_gate_enabled"] = True
    monkeypatch.setitem(settings._data, "edli", edli)


def test_reason_registered_transient():
    assert "QLCB_COVERAGE_AUTHORITY_FAULT" in TRANSIENT_MONEY_PATH_REASONS
    wrapped = "KELLY_PROOF_MISSING:QLCB_COVERAGE_AUTHORITY_FAULT:city=Chicago:bin=10°C:dir=buy_no:boom"
    assert _is_transient_money_path_reason(wrapped) is True


def test_coverage_authority_threw_fails_closed(_gate_on, monkeypatch):
    """Monkeypatch the coverage check to raise. The candidate must be BLOCKED with the
    typed reason, not sized with the original (unshrunk) q_lcb."""

    def _boom(*args, **kwargs):
        raise RuntimeError("coverage check exploded")

    monkeypatch.setattr(sbc, "settlement_backward_coverage_check", _boom)

    family = _family_with_one_no_bin()
    lcb = _lcb_with_no_side(0.80)
    with pytest.raises(ValueError) as exc:
        _maybe_apply_settlement_coverage_to_lcb(
            family=family,
            forecast_conn=_EmptyRowsConn(),
            lcb_by_direction=lcb,
        )
    msg = str(exc.value)
    assert msg.startswith("QLCB_COVERAGE_AUTHORITY_FAULT:")
    # The wrapped reason (as the sizing envelope surfaces it) is classified TRANSIENT.
    assert _is_transient_money_path_reason("KELLY_PROOF_MISSING:" + msg) is True


def test_setup_fault_fails_closed(_gate_on, monkeypatch):
    """A structural fault in the coverage SETUP (e.g. season derivation) also fails closed
    when the gate is ON, rather than silently keeping the unshrunk bound."""
    import src.contracts.season as season_mod

    def _boom_season(*args, **kwargs):
        raise RuntimeError("season derivation exploded")

    monkeypatch.setattr(season_mod, "season_from_date", _boom_season)

    with pytest.raises(ValueError, match=r"^QLCB_COVERAGE_AUTHORITY_FAULT:setup:"):
        _maybe_apply_settlement_coverage_to_lcb(
            family=_family_with_one_no_bin(),
            forecast_conn=_EmptyRowsConn(),
            lcb_by_direction=_lcb_with_no_side(0.80),
        )


def test_insufficient_data_keeps_lcb_no_raise(_gate_on):
    """No historical coverage data (empty rows -> INSUFFICIENT_DATA verdict) is the
    legitimate no-shrink path: the lcb is kept UNCHANGED and NO exception is raised. This
    is the behavior the fail-closed fix must NOT disturb."""
    lcb = _lcb_with_no_side(0.80)
    # No monkeypatch: real settlement_backward_coverage_check runs, n=0 < min_n -> keep.
    _maybe_apply_settlement_coverage_to_lcb(
        family=_family_with_one_no_bin(),
        forecast_conn=_EmptyRowsConn(),
        lcb_by_direction=lcb,
    )
    assert _qlcb_float(lcb[("cond-1", "buy_no")]) == pytest.approx(0.80)


def test_gate_off_is_noop_no_raise(monkeypatch):
    """Gate OFF: a pure no-op even if the coverage check WOULD raise (never reached)."""
    from src.config import settings

    edli = dict(settings._data["edli"])
    edli["q_lcb_settlement_coverage_gate_enabled"] = False
    monkeypatch.setitem(settings._data, "edli", edli)

    def _boom(*args, **kwargs):
        raise RuntimeError("must never be called when gate OFF")

    monkeypatch.setattr(sbc, "settlement_backward_coverage_check", _boom)

    lcb = _lcb_with_no_side(0.80)
    _maybe_apply_settlement_coverage_to_lcb(
        family=_family_with_one_no_bin(),
        forecast_conn=_EmptyRowsConn(),
        lcb_by_direction=lcb,
    )
    assert _qlcb_float(lcb[("cond-1", "buy_no")]) == pytest.approx(0.80)
