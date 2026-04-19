"""Relationship tests for FDR family scope separation — R3.

Phase: 1 (MetricIdentity Spine + FDR Scope Split)
R-numbers covered: R3 (FDR family canonical identity, scope-aware)

These tests MUST FAIL today (2026-04-16) because:
  - make_hypothesis_family_id() and make_edge_family_id() do not yet exist in
    src/strategy/selection_family.py — the imports will raise ImportError.
  - The deprecated make_family_id() wrapper that emits DeprecationWarning does
    not yet exist (today it is a plain function with no deprecation).

First commit that should make this green: executor Phase 1 implementation commit
(splits make_family_id into make_hypothesis_family_id + make_edge_family_id,
adds deprecated wrapper).
"""
from __future__ import annotations

import warnings

import pytest


# ---------------------------------------------------------------------------
# R3 — FDR family canonical identity (scope separation)
# ---------------------------------------------------------------------------

class TestFDRFamilyScopeSeparation:
    """R3: hypothesis-scope and edge-scope family IDs are distinct and deterministic."""

    def _import_new_functions(self):
        """Import the Phase 1 scope-aware functions; fail with a clear message if absent."""
        try:
            from src.strategy.selection_family import (
                make_hypothesis_family_id,
                make_edge_family_id,
            )
            return make_hypothesis_family_id, make_edge_family_id
        except ImportError:
            pytest.fail(
                "Phase 1 not yet implemented: make_hypothesis_family_id and/or "
                "make_edge_family_id not found in src.strategy.selection_family"
            )

    def test_hypothesis_and_edge_family_ids_differ_for_same_candidate_inputs(self):
        """R3 scope separation: hypothesis ID != edge ID for identical candidate × snapshot args.

        The two scopes MUST produce different IDs even when all overlapping
        inputs match — this is the core invariant that prevents BH budget collapse
        across scope boundaries.
        """
        make_hypothesis_family_id, make_edge_family_id = self._import_new_functions()

        cand = dict(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            temperature_metric="high",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        )
        h_id = make_hypothesis_family_id(**cand)
        e_id = make_edge_family_id(**cand, strategy_key="center_buy")

        assert h_id != e_id, (
            "Scope separation violated: hypothesis and edge family IDs must differ "
            "so that BH discovery budgets cannot silently merge across scopes."
        )

    def test_hypothesis_family_id_is_deterministic(self):
        """R3 determinism: make_hypothesis_family_id returns the same ID for the same inputs."""
        make_hypothesis_family_id, _ = self._import_new_functions()

        cand = dict(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            temperature_metric="high",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        )
        assert make_hypothesis_family_id(**cand) == make_hypothesis_family_id(**cand)

    def test_edge_family_id_is_deterministic(self):
        """R3 determinism: make_edge_family_id returns the same ID for the same inputs."""
        _, make_edge_family_id = self._import_new_functions()

        cand = dict(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            temperature_metric="high",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        )
        id_a = make_edge_family_id(**cand, strategy_key="center_buy")
        id_b = make_edge_family_id(**cand, strategy_key="center_buy")
        assert id_a == id_b

    def test_edge_family_id_differs_across_strategy_keys(self):
        """R3: Two different strategy_key values produce different edge family IDs.

        Each strategy has its own BH discovery budget — cross-strategy merging
        via identical IDs is forbidden.
        """
        _, make_edge_family_id = self._import_new_functions()

        cand = dict(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            temperature_metric="high",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        )
        id_center = make_edge_family_id(**cand, strategy_key="center_buy")
        id_shoulder = make_edge_family_id(**cand, strategy_key="shoulder_sell")
        assert id_center != id_shoulder


class TestEdgeFamilyIdValidation:
    """R3: make_edge_family_id validates its strategy_key argument."""

    def _import_edge_fn(self):
        try:
            from src.strategy.selection_family import make_edge_family_id
            return make_edge_family_id
        except ImportError:
            pytest.fail(
                "Phase 1 not yet implemented: make_edge_family_id not found in "
                "src.strategy.selection_family"
            )

    def test_edge_family_refuses_empty_strategy_key(self):
        """R3: make_edge_family_id(strategy_key='') raises ValueError.

        An edge family requires a real strategy_key — passing an empty string is
        the silent-merge bug this split is designed to prevent.
        """
        make_edge_family_id = self._import_edge_fn()

        with pytest.raises(ValueError):
            make_edge_family_id(
                cycle_mode="opening_hunt",
                city="NYC",
                target_date="2026-04-01",
                temperature_metric="high",
                strategy_key="",
                discovery_mode="opening_hunt",
            )

    def test_edge_family_refuses_none_strategy_key(self):
        """R3: make_edge_family_id(strategy_key=None) also raises ValueError.

        None is not a valid strategy key — same semantic as empty string.
        """
        make_edge_family_id = self._import_edge_fn()

        with pytest.raises((ValueError, TypeError)):
            make_edge_family_id(
                cycle_mode="opening_hunt",
                city="NYC",
                target_date="2026-04-01",
                temperature_metric="high",
                strategy_key=None,
                discovery_mode="opening_hunt",
            )


class TestMakeFamilyIdDeprecatedWrapper:
    """R3 migration: the old make_family_id() must survive as a deprecated wrapper.

    The executor creates this wrapper.  These tests assert that:
    1. The old name still exists (no silent breakage of call sites during migration).
    2. Calling it emits a DeprecationWarning.
    3. With strategy_key="" it routes to hypothesis scope.
    4. With a real strategy_key it routes to edge scope.
    """

    def _import_deprecated(self):
        try:
            from src.strategy.selection_family import make_family_id
            return make_family_id
        except ImportError:
            pytest.fail(
                "Phase 1 not yet implemented: make_family_id wrapper not found in "
                "src.strategy.selection_family (it should still exist as deprecated)"
            )

    def test_make_family_id_emits_deprecation_warning(self):
        """R3 migration: calling the old make_family_id() raises DeprecationWarning.

        Today make_family_id() exists but does NOT emit DeprecationWarning — this
        test FAILS today and becomes green after the deprecated wrapper is installed.
        """
        make_family_id = self._import_deprecated()

        with pytest.warns(DeprecationWarning):
            make_family_id(
                cycle_mode="opening_hunt",
                city="NYC",
                target_date="2026-04-01",
                strategy_key="center_buy",
                discovery_mode="opening_hunt",
                decision_snapshot_id="snap-1",
            )

    def test_make_family_id_empty_strategy_key_routes_to_hypothesis_scope(self):
        """R3 migration: make_family_id(strategy_key='') routes to hypothesis scope.

        After Phase 1, this must produce the same ID as make_hypothesis_family_id().
        Today both the DeprecationWarning and make_hypothesis_family_id are absent —
        this test fails on the import of make_hypothesis_family_id.
        """
        make_family_id = self._import_deprecated()

        try:
            from src.strategy.selection_family import make_hypothesis_family_id
        except ImportError:
            pytest.fail(
                "Phase 1 not yet implemented: make_hypothesis_family_id not found"
            )

        kwargs = dict(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            temperature_metric="high",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            deprecated_id = make_family_id(**kwargs, strategy_key="")

        canonical_id = make_hypothesis_family_id(**kwargs)
        assert deprecated_id == canonical_id, (
            "Deprecated wrapper with strategy_key='' must route to hypothesis scope "
            "and produce the same ID as make_hypothesis_family_id."
        )

    def test_make_family_id_real_strategy_key_routes_to_edge_scope(self):
        """R3 migration: make_family_id(strategy_key='center_buy') routes to edge scope.

        After Phase 1, this must produce the same ID as make_edge_family_id().
        """
        make_family_id = self._import_deprecated()

        try:
            from src.strategy.selection_family import make_edge_family_id
        except ImportError:
            pytest.fail(
                "Phase 1 not yet implemented: make_edge_family_id not found"
            )

        kwargs = dict(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            temperature_metric="high",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            deprecated_id = make_family_id(**kwargs, strategy_key="center_buy")

        canonical_id = make_edge_family_id(**kwargs, strategy_key="center_buy")
        assert deprecated_id == canonical_id, (
            "Deprecated wrapper with a real strategy_key must route to edge scope "
            "and produce the same ID as make_edge_family_id."
        )
