# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K3 (q_lcb settlement-backward-coverage). Plan R1 made
#   structural: a q_lcb is no longer a bare float at the lcb_by_direction boundary
#   — it carries its calibration provenance (FORECAST_BOOTSTRAP / EMOS_ANALYTIC /
#   SETTLEMENT_ISOTONIC) so an un-provenanced LCB is UNCONSTRUCTABLE at the seam.
#   Antibody discipline: make the error CATEGORY ("a q_lcb with no idea where it
#   came from reached trade_score") a TypeError at assignment, not a patch.
"""Relationship tests for the QlcbProvenance type + QlcbByDirection typed dict.

RELATIONSHIP under test: the q_lcb PRODUCER (event_reactor_adapter, building
lcb_by_direction) hands a value across a boundary to the q_lcb CONSUMER
(trade_score / evaluate_kelly / the ARM coverage gate). Today the carrier is
``dict[tuple, float]`` — a bare float erases the calibration source, so a
forecast-bootstrap LCB and a settlement-isotonic LCB are indistinguishable at the
seam, and the coverage gate cannot tell whether it is allowed to fire. The type
makes "a float with no provenance crossed the boundary" a TypeError at __setitem__.

Written RED-first: src.calibration.qlcb_provenance does not exist yet.
"""
from __future__ import annotations

import pytest


def test_qlcb_provenance_rejects_unknown_calibration_source():
    """calibration_source is a closed vocabulary. An out-of-vocab source is a
    construction error — the three sources are the only ones the coverage gate
    knows how to reason about."""
    from src.calibration.qlcb_provenance import QlcbProvenance

    with pytest.raises(ValueError):
        QlcbProvenance(
            q_lcb=0.5,
            calibration_source="MADE_UP_SOURCE",  # not in the Literal set
            n_settlement_observations=None,
            coverage_ratio=None,
        )


def test_qlcb_provenance_rejects_out_of_range_q_lcb():
    """q_lcb is a probability lower bound; it must live in [0, 1]. A 1.4 is a
    domain error, not a silently-clamped value."""
    from src.calibration.qlcb_provenance import QlcbProvenance

    with pytest.raises(ValueError):
        QlcbProvenance(
            q_lcb=1.4,
            calibration_source="FORECAST_BOOTSTRAP",
            n_settlement_observations=None,
            coverage_ratio=None,
        )


def test_qlcb_provenance_is_frozen():
    """The provenance carrier is a frozen truth object — its q_lcb cannot be
    mutated after the source is recorded (no silent re-write of the band)."""
    from dataclasses import FrozenInstanceError

    from src.calibration.qlcb_provenance import QlcbProvenance

    p = QlcbProvenance(
        q_lcb=0.5,
        calibration_source="FORECAST_BOOTSTRAP",
        n_settlement_observations=None,
        coverage_ratio=None,
    )
    with pytest.raises(FrozenInstanceError):
        p.q_lcb = 0.9  # type: ignore[misc]


def test_qlcb_by_direction_rejects_bare_float_assignment():
    """THE antibody. Assigning a bare float into the q_lcb-by-direction carrier
    raises TypeError at the boundary — the un-provenanced LCB is unconstructable.
    This is the relationship invariant: the producer cannot hand the consumer a
    number whose calibration source has been erased."""
    from src.calibration.qlcb_provenance import QlcbByDirection

    d = QlcbByDirection()
    with pytest.raises(TypeError):
        d[("cond123", "buy_yes")] = 0.62  # bare float — forbidden


def test_qlcb_by_direction_accepts_provenanced_value():
    """A QlcbProvenance value is accepted; the float reads back through .q_lcb so
    every existing consumer can still get the number — but only via the carrier."""
    from src.calibration.qlcb_provenance import QlcbByDirection, QlcbProvenance

    d = QlcbByDirection()
    d[("cond123", "buy_yes")] = QlcbProvenance(
        q_lcb=0.62,
        calibration_source="FORECAST_BOOTSTRAP",
        n_settlement_observations=None,
        coverage_ratio=None,
    )
    got = d[("cond123", "buy_yes")]
    assert isinstance(got, QlcbProvenance)
    assert got.q_lcb == pytest.approx(0.62)
    assert got.calibration_source == "FORECAST_BOOTSTRAP"


def test_qlcb_by_direction_update_also_rejects_bare_float():
    """dict.update is the other write door. It MUST funnel through the same guard
    — otherwise the antibody has a hole and a bare float sneaks in via update()."""
    from src.calibration.qlcb_provenance import QlcbByDirection

    d = QlcbByDirection()
    with pytest.raises(TypeError):
        d.update({("cond123", "buy_no"): 0.4})  # bare float via update — forbidden
