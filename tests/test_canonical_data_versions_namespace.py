# Lifecycle: created=2026-04-24; last_reviewed=2026-04-24; last_reused=never
# Purpose: M3 antibody — pins the CANONICAL_DATA_VERSIONS →
#          CANONICAL_ENSEMBLE_DATA_VERSIONS rename + parallel observation
#          + settlement allowlists. Renames drift fast; this test catches
#          accidental re-merge of the namespaces or silent removal of
#          parallel sets.
# Reuse: Covers src/contracts/ensemble_snapshot_provenance.py public
#        constants only. Not a write-path test; no DB connection needed.
#        Originating handoff: POST_AUDIT_HANDOFF_2026-04-24.md §3.1 M3.
# Authority basis: T2-S4 M3 namespace disambiguation.
"""Pin the CANONICAL_*_DATA_VERSIONS namespace after M3 rename."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts import ensemble_snapshot_provenance as mod
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


def test_ensemble_set_contains_metric_tracks_and_source_variants():
    """CANONICAL_ENSEMBLE_DATA_VERSIONS covers HIGH/LOW source variants."""
    ensemble = mod.CANONICAL_ENSEMBLE_DATA_VERSIONS
    assert HIGH_LOCALDAY_MAX.data_version in ensemble
    assert LOW_LOCALDAY_MIN.data_version in ensemble
    assert mod.ECMWF_OPENDATA_HIGH_DATA_VERSION in ensemble
    assert mod.ECMWF_OPENDATA_LOW_DATA_VERSION in ensemble
    assert mod.TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION in ensemble
    assert mod.ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION in ensemble
    # 2026-05-08: frozenset has 9 entries — 6 named constants + 3 legacy
    # bridge entries (_ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY,
    # _ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY,
    # _ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION_LEGACY) added for
    # historical row compatibility per PR #85.
    assert len(ensemble) == 9


def test_deprecation_alias_is_identity():
    """CANONICAL_DATA_VERSIONS alias must be `is` identical to ensemble set."""
    # Use `is` not `==` so that a future copy-paste that shadows the alias
    # with a separate frozenset is caught.
    assert mod.CANONICAL_DATA_VERSIONS is mod.CANONICAL_ENSEMBLE_DATA_VERSIONS, (
        "Deprecation alias must remain identical to the ensemble set. Drop "
        "the alias in a dedicated cleanup slice once all callers migrate; "
        "until then, the alias MUST be object-identical (no silent divergence)."
    )


def test_observation_set_disjoint_from_ensemble():
    """Observation + ensemble namespaces must not overlap."""
    observation = mod.CANONICAL_OBSERVATION_DATA_VERSIONS
    ensemble = mod.CANONICAL_ENSEMBLE_DATA_VERSIONS
    assert observation.isdisjoint(ensemble), (
        f"Observation + ensemble sets must be disjoint. Overlap: "
        f"{observation & ensemble}. If a version is both, it was likely "
        f"added to the wrong set."
    )


def test_settlement_set_disjoint_from_ensemble():
    """Settlement + ensemble namespaces must not overlap (settlements use
    source-scoped data_versions, ensembles use metric-scoped)."""
    settlement = mod.CANONICAL_SETTLEMENT_DATA_VERSIONS
    ensemble = mod.CANONICAL_ENSEMBLE_DATA_VERSIONS
    assert settlement.isdisjoint(ensemble), (
        f"Settlement + ensemble sets must be disjoint. Overlap: "
        f"{settlement & ensemble}."
    )


def test_observation_set_non_empty_and_native_tagged():
    """Observation set enumerates the primary observation source paths."""
    observation = mod.CANONICAL_OBSERVATION_DATA_VERSIONS
    assert len(observation) >= 1
    # All entries follow the 'v1.<source>-native' convention so new
    # contributors recognize the pattern.
    for dv in observation:
        assert dv.startswith("v1."), dv
        assert dv.endswith("-native"), dv


def test_settlement_set_non_empty_and_source_tagged():
    """Settlement set enumerates the settlement-writer source data_versions."""
    settlement = mod.CANONICAL_SETTLEMENT_DATA_VERSIONS
    assert len(settlement) >= 1
    # All entries are source-scoped (not metric-scoped) per harvester
    # live-write path at src/execution/harvester.py.
    # Sanity-check the known harvester sources are present.
    assert "wu_icao_history_v1" in settlement
    assert "hko_daily_api_v1" in settlement
    assert "ogimet_metar_v1" in settlement


def test_assert_data_version_allowed_uses_ensemble_set():
    """Regression-bar: the contract enforcement reads the ensemble set,
    not the deprecation alias or another set. A future refactor that
    accidentally points the assertion at a broader set (e.g., the union
    of ensemble + settlement) would silently accept cross-domain writes
    into ensemble_snapshots_v2."""
    import inspect
    src = inspect.getsource(mod.assert_data_version_allowed)
    assert "CANONICAL_ENSEMBLE_DATA_VERSIONS" in src, (
        "assert_data_version_allowed must reference the ensemble set by "
        "its renamed symbol post-M3. If the deprecation alias is used, "
        "the intent becomes unclear."
    )


def test_assert_data_version_allowed_rejects_observation_data_version():
    """Behavioral regression-bar (con-nyx T2-S4 NICE-TO-HAVE #5): the
    ensemble write gate must reject a well-formed observation
    data_version even though the observation set is declared parallel.
    Disjoint-namespace design would silently fail if a future merge
    pointed assert_data_version_allowed at the union."""
    from src.contracts.ensemble_snapshot_provenance import (
        DataVersionQuarantinedError,
        assert_data_version_allowed,
    )
    import pytest

    observation_dv = "v1.wu-native"  # production-grounded observation DV
    assert observation_dv in mod.CANONICAL_OBSERVATION_DATA_VERSIONS
    assert observation_dv not in mod.CANONICAL_ENSEMBLE_DATA_VERSIONS

    with pytest.raises(DataVersionQuarantinedError):
        assert_data_version_allowed(observation_dv, context="T2-S4_behavioral_test")


def test_observation_allowlist_authority_tier_documentation():
    """Con-nyx T2-S4 finding 1: docstring must flag 4/5 aspirational
    entries so future consumer-wiring does not treat set membership as
    production verification.

    Fitz Constraint #4 (Data Provenance > Code Correctness): a symbol
    named CANONICAL implies verified authority; 4 of 5 entries are
    known-planned but production-unverified. The docstring must make
    that heterogeneity explicit.
    """
    import inspect
    src = inspect.getsource(mod)
    # Require both classification markers to appear in the module text.
    assert "PRODUCTION-GROUNDED" in src, (
        "Observation allowlist must document its production-grounded vs "
        "aspirational authority tiers (con-nyx T2-S4 finding 1)."
    )
    assert "ASPIRATIONAL" in src, (
        "Observation allowlist must flag aspirational entries explicitly."
    )
