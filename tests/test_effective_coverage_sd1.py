# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC review Blocker D + E + Stat 4 (SD1). coverage_months
#   must be the EFFECTIVE evidence (intersection of active sources), not prior-only;
#   a source that did not influence the fit imposes no constraint; activeness is a
#   fit-time fact (n_opd>=min_live_n, n_paired>=MIN_PAIRED_N), never persisted weight_live.
"""Relationship tests for SD1 effective-coverage intersection semantics.

The producer's effective coverage is the set of months where the row's posterior has
support from EVERY source that actually influenced it. These tests pin the cross-source
relationship (prior x live x paired) at the pure-logic boundary; the read-time rejection
of out-of-coverage / empty-coverage canonical rows is covered in
test_gate_set_hash_antibody.py.
"""
from __future__ import annotations

from scripts.fit_full_transport_error_models import _intersect_active_coverage


def test_inactive_source_imposes_no_constraint():
    # Live covered only May, but live is INACTIVE (n_opd < min_live_n): it did not shape
    # the posterior, so it must NOT shrink coverage. Effective = prior.
    eff = _intersect_active_coverage(
        {3, 4, 5}, {5}, None, live_active=False, paired_active=False
    )
    assert eff == {3, 4, 5}


def test_active_live_intersects_coverage():
    # Live ACTIVE and covered only May: the live update only has support in May, so a
    # March/April target must fall outside coverage. Effective = prior ∩ live.
    eff = _intersect_active_coverage(
        {3, 4, 5}, {5}, None, live_active=True, paired_active=False
    )
    assert eff == {5}


def test_active_paired_intersects_coverage():
    # Transport (paired) ACTIVE and covered only Apr/May: the transport shift has no
    # support in March. Effective = prior ∩ paired.
    eff = _intersect_active_coverage(
        {3, 4, 5}, None, {4, 5}, live_active=False, paired_active=True
    )
    assert eff == {4, 5}


def test_both_active_intersect_all():
    eff = _intersect_active_coverage(
        {3, 4, 5}, {4, 5}, {5}, live_active=True, paired_active=True
    )
    assert eff == {5}


def test_active_but_none_coverage_is_skipped_not_crash():
    # Defensive: if an active source's coverage is None (the composer returns 'invalid'
    # before reaching here, but the pure helper must not crash), it imposes no constraint.
    eff = _intersect_active_coverage(
        {3, 4, 5}, None, None, live_active=True, paired_active=True
    )
    assert eff == {3, 4, 5}


def test_empty_intersection_yields_empty_set():
    # Prior covers only March; active live covers only May -> no overlapping month ->
    # empty effective set. The producer stamps '' and a canonical reader rejects it.
    eff = _intersect_active_coverage(
        {3}, {5}, None, live_active=True, paired_active=False
    )
    assert eff == set()
