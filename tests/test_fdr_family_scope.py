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


class TestMakeFamilyIdRetired:
    """R3 migration completed: the deprecated `make_family_id()` wrapper is RETIRED.

    History: Phase 1 (2026-04-16) introduced two scope-aware helpers
    (`make_hypothesis_family_id`, `make_edge_family_id`) alongside a deprecated
    `make_family_id()` wrapper that emitted DeprecationWarning. Production
    callers were migrated. ultrareview25_remediation 2026-05-01 P1-6 retired
    the wrapper after `tests/test_no_deprecated_make_family_id_calls.py`
    confirmed zero callers in `src/` and `scripts/`.

    INV-22 ("one canonical family grammar") is now satisfied structurally —
    the wrapper does not exist, so a future agent cannot accidentally call
    it. Combined with `test_no_deprecated_make_family_id_calls.py` (which
    blocks new calls) and the type signatures of the two canonical helpers
    (which require explicit scope intent), the family-grammar surface is
    paired-antibody locked: no callers + no definition.
    """

    def test_make_family_id_wrapper_is_retired(self):
        """R3 cleanup: `make_family_id` must NOT exist in selection_family.

        If a future PR re-adds the wrapper (back to the migration-period
        state), this test fails immediately — preventing INV-22 regression.
        Use `make_hypothesis_family_id` (no strategy_key) or
        `make_edge_family_id` (with strategy_key) instead.
        """
        import src.strategy.selection_family as selection_family

        assert not hasattr(selection_family, "make_family_id"), (
            "INV-22 regression: `make_family_id` re-appeared in "
            "src.strategy.selection_family. The deprecated wrapper was "
            "retired 2026-05-01 (ultrareview25_remediation P1-6). Use "
            "make_hypothesis_family_id (no strategy_key, per-candidate scope) "
            "or make_edge_family_id (with strategy_key, per-strategy scope) "
            "instead. Re-introducing the wrapper revives the doc-vs-code "
            "drift that R3 cleanup closed."
        )

    def test_canonical_helpers_remain_present(self):
        """Pair-positive: the two canonical helpers MUST still be present;
        retirement of the wrapper must not be confused with their loss.
        """
        from src.strategy.selection_family import (  # noqa: F401
            make_edge_family_id,
            make_hypothesis_family_id,
        )
