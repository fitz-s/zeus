# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""TransitionalIndependentProduct — single-family joint-atom rail (consult REV-2 ruling 2).

Single-family passthrough (one-belief law: served samples become q_draws verbatim over one
atom per bin), fail-closed multi-family, deterministic schema-covering hash.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from src.solve.scenario_service import (
    MultiFamilyJointUnavailableError,
    ScenarioService,
    TransitionalIndependentProduct,
)


def _band(bin_ids, samples, sample_hash, alpha=0.05):
    bins = [SimpleNamespace(bin_id=b) for b in bin_ids]
    joint_q = SimpleNamespace(omega=SimpleNamespace(bins=bins))
    return SimpleNamespace(
        samples=np.asarray(samples, dtype=np.float64),
        joint_q=joint_q,
        sample_hash=sample_hash,
        alpha=alpha,
    )


def test_protocol_conformance():
    assert isinstance(TransitionalIndependentProduct(), ScenarioService)


def test_single_family_passthrough_is_verbatim():
    samples = np.random.default_rng(1).dirichlet([5, 5, 5], size=64)
    band = _band(("a", "b", "c"), samples, "hash_abc")
    result = TransitionalIndependentProduct().scenarios({"tokyo": band})
    # atoms are one per bin, canonical id "family=bin"
    assert result.atom_ids == ("tokyo=a", "tokyo=b", "tokyo=c")
    # one-belief law: the served samples pass through unchanged as q_draws
    assert np.array_equal(result.q_draws, samples)
    assert result.semantics == "POSTERIOR_Q_DRAWS"
    assert result.alpha == 0.05
    assert result.band_hashes_by_family == {"tokyo": "hash_abc"}
    assert result.family_projections == {"tokyo": (0, 1, 2)}


def test_multi_family_fails_closed():
    b1 = _band(("a", "b"), np.random.default_rng(1).dirichlet([4, 4], size=16), "h1")
    b2 = _band(("c", "d"), np.random.default_rng(2).dirichlet([4, 4], size=16), "h2")
    with pytest.raises(MultiFamilyJointUnavailableError, match="single-family only"):
        TransitionalIndependentProduct().scenarios({"famA": b1, "famB": b2})


def test_empty_families_rejected():
    with pytest.raises(ValueError, match="at least one family"):
        TransitionalIndependentProduct().scenarios({})


def test_hash_is_deterministic_and_schema_sensitive():
    samples = np.random.default_rng(2).dirichlet([4, 4], size=16)
    h1 = TransitionalIndependentProduct().scenarios({"fam": _band(("a", "b"), samples, "h1")}).scenario_hash
    h1b = TransitionalIndependentProduct().scenarios({"fam": _band(("a", "b"), samples, "h1")}).scenario_hash
    assert h1 == h1b
    # different band hash -> different scenario hash (band provenance is covered)
    h2 = TransitionalIndependentProduct().scenarios({"fam": _band(("a", "b"), samples, "h2")}).scenario_hash
    assert h1 != h2
    # different alpha -> different scenario hash (ambiguity metadata is covered)
    h3 = TransitionalIndependentProduct().scenarios({"fam": _band(("a", "b"), samples, "h1", alpha=0.1)}).scenario_hash
    assert h1 != h3
