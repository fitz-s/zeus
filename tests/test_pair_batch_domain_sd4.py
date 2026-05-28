# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC review Blocker H + F (SD4). fit_signature_hash must
#   include gate_set_hash + coverage + source-row digest so two rows under different gate
#   generations cannot collide on signature; pair batches must record domain identity.
"""Relationship tests for SD4: fit-signature domain identity (Blocker H).

The pair-batch manifest tests (Blocker F) live alongside once the calibration_pair_batch
table lands. Here we pin the signature invariant: a change to the gate set, the coverage
scope, or the underlying source residuals MUST change fit_signature_hash; identical inputs
MUST map to one signature.
"""
from __future__ import annotations

from scripts.fit_full_transport_error_models import _fit_signature_hash


def _sig(**over):
    base = dict(
        city="NYC", metric="high", season="MAM", live_dv="dvL", prior_dv="dvP",
        kappa=1.0, n_tig=2, n_opd=1,
        gate_set_hash="G1", coverage_months="3,4,5",
        tig_residuals=[1.0, 2.0], opd_residuals=[0.5],
    )
    base.update(over)
    return _fit_signature_hash(
        base["city"], base["metric"], base["season"], base["live_dv"], base["prior_dv"],
        base["kappa"], base["n_tig"], base["n_opd"],
        gate_set_hash=base["gate_set_hash"], coverage_months=base["coverage_months"],
        tig_residuals=base["tig_residuals"], opd_residuals=base["opd_residuals"],
    )


def test_signature_is_deterministic():
    assert _sig() == _sig()
    assert len(_sig()) == 16


def test_gate_set_change_changes_signature():
    assert _sig() != _sig(gate_set_hash="G2")


def test_coverage_change_changes_signature():
    assert _sig() != _sig(coverage_months="5")


def test_source_residual_change_changes_signature():
    assert _sig() != _sig(tig_residuals=[1.0, 2.1])
    assert _sig() != _sig(opd_residuals=[0.6])


def test_same_n_different_values_do_not_collide():
    # Two fits with identical (city, metric, season, dv, kappa, n) but different source
    # residuals must NOT share a signature — the pre-SD4 hole.
    a = _sig(tig_residuals=[1.0, 2.0])
    b = _sig(tig_residuals=[3.0, 4.0])
    assert a != b
