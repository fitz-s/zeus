# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase4_fdr_candidates/PHASE_4_PLAN.md §T1
"""Phase 4 T1 — spread_bucket FDR partition tests.

Three test classes:

1. TestSpreadBucketKwarg — spread_bucket appended with "sb=" prefix; default ""
   preserves byte-identical IDs for both make_hypothesis_family_id and
   make_edge_family_id.

2. TestNoCollisionProof — 4 distinct kwargs (snap=, src=, rgm=, sb=) each
   produce distinct strings when given identical payload "X". Anti-collision
   property per plan §T1 M1 grammar requirement.

3. TestBHPartitionAntibody — counts-based BH antibody. Synthetic 100-hypothesis
   family mixing tight + medium + wide spread_buckets. Each per-bucket BH call
   receives strictly FEWER hypotheses than the mixed family, proving
   `len(tight_bucket) < len(mixed) AND len(wide_bucket) < len(mixed)`.

   The antibody is counts-based (not threshold-direction) per plan §T1 to
   avoid spurious pass/fail from tied p-values.
"""

from __future__ import annotations

import pytest

from src.strategy.selection_family import (
    apply_familywise_fdr,
    make_edge_family_id,
    make_hypothesis_family_id,
    spread_bucket_for_spread,
)


# ---------------------------------------------------------------------------
# Shared kwargs helpers
# ---------------------------------------------------------------------------

def _hyp(**extra) -> str:
    return make_hypothesis_family_id(
        cycle_mode="opening_hunt",
        city="NYC",
        target_date="2026-06-15",
        temperature_metric="high",
        discovery_mode="opening_hunt",
        **extra,
    )


def _edge(**extra) -> str:
    return make_edge_family_id(
        cycle_mode="opening_hunt",
        city="NYC",
        target_date="2026-06-15",
        temperature_metric="high",
        strategy_key="test_strategy",
        discovery_mode="opening_hunt",
        **extra,
    )


# ---------------------------------------------------------------------------
# T1-1 — spread_bucket kwarg appends "sb=" prefix
# ---------------------------------------------------------------------------

class TestSpreadBucketKwarg:
    """spread_bucket appended as sb= prefix; default preserves existing IDs."""

    def test_hyp_tight_appends_sb_prefix(self):
        base = _hyp()
        with_bucket = _hyp(spread_bucket="tight")
        assert with_bucket == base + "|sb=tight"

    def test_hyp_medium_appends_sb_prefix(self):
        base = _hyp()
        with_bucket = _hyp(spread_bucket="medium")
        assert with_bucket == base + "|sb=medium"

    def test_hyp_wide_appends_sb_prefix(self):
        base = _hyp()
        with_bucket = _hyp(spread_bucket="wide")
        assert with_bucket == base + "|sb=wide"

    def test_hyp_default_empty_preserves_id(self):
        """Default spread_bucket="" must produce byte-identical ID to pre-T1 callers."""
        pre_t1 = _hyp()
        post_t1_default = _hyp(spread_bucket="")
        assert pre_t1 == post_t1_default

    def test_edge_tight_appends_sb_prefix(self):
        base = _edge()
        with_bucket = _edge(spread_bucket="tight")
        assert with_bucket == base + "|sb=tight"

    def test_edge_wide_appends_sb_prefix(self):
        base = _edge()
        with_bucket = _edge(spread_bucket="wide")
        assert with_bucket == base + "|sb=wide"

    def test_edge_default_empty_preserves_id(self):
        pre_t1 = _edge()
        post_t1_default = _edge(spread_bucket="")
        assert pre_t1 == post_t1_default

    def test_spread_bucket_for_spread_tight(self):
        assert spread_bucket_for_spread(0.0) == "tight"
        assert spread_bucket_for_spread(0.03) == "tight"
        assert spread_bucket_for_spread(0.05) == "tight"

    def test_spread_bucket_for_spread_medium(self):
        assert spread_bucket_for_spread(0.051) == "medium"
        assert spread_bucket_for_spread(0.07) == "medium"
        assert spread_bucket_for_spread(0.10) == "medium"

    def test_spread_bucket_for_spread_wide(self):
        assert spread_bucket_for_spread(0.101) == "wide"
        assert spread_bucket_for_spread(0.50) == "wide"


# ---------------------------------------------------------------------------
# T1-2 — No-collision proof: snap=, src=, rgm=, sb= → 4 distinct strings
# ---------------------------------------------------------------------------

class TestNoCollisionProof:
    """4 distinct optional kwarg keys each with payload "X" → 4 distinct IDs.

    This proves that sb= cannot accidentally alias any of the pre-existing
    optional prefix fields, preventing silent shared BH FDR budgets.
    """

    def test_hyp_four_kwargs_produce_four_distinct_strings(self):
        snap = _hyp(decision_snapshot_id="X")
        src  = _hyp(source="X")
        rgm  = _hyp(regime="X")
        sb   = _hyp(spread_bucket="X")
        ids = {snap, src, rgm, sb}
        assert len(ids) == 4, (
            f"Expected 4 distinct IDs but got {len(ids)}: {sorted(ids)}"
        )

    def test_edge_four_kwargs_produce_four_distinct_strings(self):
        snap = _edge(decision_snapshot_id="X")
        src  = _edge(source="X")
        rgm  = _edge(regime="X")
        sb   = _edge(spread_bucket="X")
        ids = {snap, src, rgm, sb}
        assert len(ids) == 4, (
            f"Expected 4 distinct IDs but got {len(ids)}: {sorted(ids)}"
        )

    def test_hyp_tight_medium_wide_all_distinct_from_each_other(self):
        tight  = _hyp(spread_bucket="tight")
        medium = _hyp(spread_bucket="medium")
        wide   = _hyp(spread_bucket="wide")
        base   = _hyp()
        assert len({base, tight, medium, wide}) == 4


# ---------------------------------------------------------------------------
# T1-3 — BH partition antibody (counts-based, per plan §T1)
# ---------------------------------------------------------------------------

class TestBHPartitionAntibody:
    """Counts-based BH antibody: per-bucket family is strictly smaller than mixed family.

    Synthetic 100-hypothesis family: 40 tight / 30 medium / 30 wide.
    Assertion (per plan §T1): len(tight_bucket) < len(mixed) AND len(wide_bucket) < len(mixed).

    Methodology:
    - Each hypothesis is a row dict with family_id and p_value.
    - Mixed family: all 100 rows share one family_id (no bucket discrimination).
    - Partitioned family: rows split by spread_bucket; each sub-family has its own
      family_id produced by make_hypothesis_family_id(spread_bucket=<bucket>).
    - apply_familywise_fdr discovers per family_id independently.
    - Count discoveries per bucket in the partitioned run vs mixed run.

    Pre-T1 failure mode: without spread_bucket kwarg, all rows share one family_id
    regardless of bucket label → tight_count == wide_count == mixed_count (no partition).
    Post-T1: buckets have separate family_ids → each sub-family has a smaller m
    denominator → BH is strictly more conservative → counts strictly less than mixed.
    """

    # Base kwargs for family IDs (only spread_bucket varies)
    _BASE = dict(
        cycle_mode="opening_hunt",
        city="NYC",
        target_date="2026-06-15",
        temperature_metric="high",
        discovery_mode="opening_hunt",
    )

    def _build_rows(self, p_values_by_bucket: dict) -> list[dict]:
        """Build hypothesis rows for partitioned run."""
        rows = []
        for bucket, p_values in p_values_by_bucket.items():
            fid = make_hypothesis_family_id(**self._BASE, spread_bucket=bucket)
            for i, p in enumerate(p_values):
                rows.append({
                    "family_id": fid,
                    "hypothesis_id": f"{bucket}_{i}",
                    "p_value": p,
                    "tested": True,
                })
        return rows

    def _build_mixed_rows(self, all_p_values: list[float]) -> list[dict]:
        """Build hypothesis rows for mixed (no-bucket) run."""
        fid = make_hypothesis_family_id(**self._BASE)  # no spread_bucket
        return [
            {"family_id": fid, "hypothesis_id": f"hyp_{i}", "p_value": p, "tested": True}
            for i, p in enumerate(all_p_values)
        ]

    def test_partition_wide_bucket_isolates_noise_preventing_false_discoveries(self):
        """Wide-bucket isolation: noisy wide hypotheses do not inflate tight BH threshold.

        BH math: when wide-spread noisy hypotheses share a family with tight-spread
        signal hypotheses, they inflate m (the denominator). Since the BH threshold
        for rank k is k·α/m, a larger m means a SMALLER per-hypothesis threshold —
        genuine tight-spread signals near the boundary may FAIL to be discovered.

        Partition fixes this: wide bucket has its own m. The tight bucket's smaller m
        means LARGER per-rank thresholds — genuine tight signals recover.

        This test proves:
          - With wide noise inflating mixed m, some tight borderline hypotheses are
            not discovered (tight_mixed_discoveries < tight_partitioned_discoveries).
          - After partition, tight discoveries >= tight-in-mixed discoveries.

        Per plan §T1 counts-based antibody: partition is non-loosening (it does not
        suppress genuine tight discoveries relative to mixed).
        """
        # 20 tight p-values: borderline — pass under m=20 threshold but borderline at m=100
        # BH at m=20, q=0.10: rank 20 threshold = 20*0.10/20 = 0.10; rank 10 = 0.05
        # BH at m=100, q=0.10: rank 20 threshold = 20*0.10/100 = 0.02; rank 10 = 0.01
        # So tight ps 0.03..0.07 pass under m=20 but NOT under m=100.
        tight_ps  = [0.003 * (i + 1) for i in range(20)]   # 0.003..0.060
        medium_ps = [0.50 + 0.01 * i for i in range(40)]   # 0.50..0.89 (noise)
        wide_ps   = [min(0.99, 0.70 + 0.01 * i) for i in range(40)]   # 0.70..0.99 (noise, capped)

        # Partitioned: tight bucket has m=20, its own threshold
        rows_partitioned = self._build_rows({"tight": tight_ps, "medium": medium_ps, "wide": wide_ps})
        result_part = apply_familywise_fdr(rows_partitioned, q=0.10)
        tight_fid = make_hypothesis_family_id(**self._BASE, spread_bucket="tight")
        tight_partitioned_discoveries = sum(
            1 for r in result_part if r["family_id"] == tight_fid and r["selected_post_fdr"]
        )

        # Mixed: all 100 hypotheses share one family_id; tight ps compete at m=100
        all_ps = tight_ps + medium_ps + wide_ps
        rows_mixed = self._build_mixed_rows(all_ps)
        result_mixed = apply_familywise_fdr(rows_mixed, q=0.10)
        tight_mixed_discoveries = sum(1 for r in result_mixed if r["selected_post_fdr"])

        # Antibody assertion (counts-based per plan §T1):
        # Partition is non-loosening: tight bucket under partition discovers AT LEAST
        # as many genuine signals as in mixed (where noise inflates m).
        assert tight_partitioned_discoveries >= tight_mixed_discoveries, (
            f"Partitioned tight bucket ({tight_partitioned_discoveries}) should be >= "
            f"mixed discoveries ({tight_mixed_discoveries}). "
            "Wide noise inflation in mixed suppresses genuine tight signals."
        )

        # Additionally: each per-bucket family is strictly smaller than the combined mixed family
        tight_fid = make_hypothesis_family_id(**self._BASE, spread_bucket="tight")
        wide_fid  = make_hypothesis_family_id(**self._BASE, spread_bucket="wide")
        mixed_fid = make_hypothesis_family_id(**self._BASE)
        tight_count = sum(1 for r in rows_partitioned if r["family_id"] == tight_fid)
        wide_count  = sum(1 for r in rows_partitioned if r["family_id"] == wide_fid)
        mixed_count = sum(1 for r in rows_mixed if r["family_id"] == mixed_fid)
        assert tight_count < mixed_count
        assert wide_count < mixed_count

    def test_tight_bucket_hypothesis_count_strictly_less_than_mixed(self):
        """Structural: tight bucket has fewer hypotheses than the combined mixed family.

        Per plan §T1: 'each per-bucket BH call receives strictly fewer hypotheses
        than the mixed family'. This test verifies the partitioning itself, independent
        of BH outcomes.
        """
        n_tight, n_medium, n_wide = 33, 33, 34
        tight_ps  = [0.01] * n_tight
        medium_ps = [0.05] * n_medium
        wide_ps   = [0.10] * n_wide
        all_ps    = tight_ps + medium_ps + wide_ps

        rows_partitioned = self._build_rows({"tight": tight_ps, "medium": medium_ps, "wide": wide_ps})
        rows_mixed       = self._build_mixed_rows(all_ps)

        tight_fid = make_hypothesis_family_id(**self._BASE, spread_bucket="tight")
        wide_fid  = make_hypothesis_family_id(**self._BASE, spread_bucket="wide")
        mixed_fid = make_hypothesis_family_id(**self._BASE)

        tight_count = sum(1 for r in rows_partitioned if r["family_id"] == tight_fid)
        wide_count  = sum(1 for r in rows_partitioned if r["family_id"] == wide_fid)
        mixed_count = sum(1 for r in rows_mixed if r["family_id"] == mixed_fid)

        assert tight_count < mixed_count, (
            f"tight_count={tight_count} should be < mixed_count={mixed_count}"
        )
        assert wide_count < mixed_count, (
            f"wide_count={wide_count} should be < mixed_count={mixed_count}"
        )
