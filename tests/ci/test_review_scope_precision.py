# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase G
#                  External review-upgrade spec (operator 2026-05-26)
#                  scripts/review_scope_collect.py TIER_RULES
"""
Phase G — tier classification precision.

Anchors the hot-surface → tier mapping so that:
  - src/engine/cycle_runtime.py is Tier 0 (FC-03 root)
  - named forecast/discovery/ingest surfaces stay Tier 1
  - review-instruction files stay Tier 3 (effect-tier note in scope_map)

Regressions caught by this test prevented PR #345-class slips where a
runtime hot path was missing from review_scope_collect.py's Tier 0 list.
"""
from __future__ import annotations

from scripts.review_scope_collect import classify


def test_cycle_runtime_is_tier0():
    """FC-03 root must classify Tier 0 (live submit/reprice boundary)."""
    assert classify("src/engine/cycle_runtime.py") == 0


def test_cycle_runner_remains_tier0():
    """Don't regress sibling Tier 0 classification."""
    assert classify("src/engine/cycle_runner.py") == 0


def test_evaluator_remains_tier0():
    assert classify("src/engine/evaluator.py") == 0


def test_monitor_refresh_remains_tier0():
    assert classify("src/engine/monitor_refresh.py") == 0


def test_forecast_bundle_hot_surfaces_are_tier1():
    """src/data/** is Tier 1; named surfaces stay covered."""
    assert classify("src/data/executable_forecast_reader.py") == 1
    assert classify("src/data/forecast_extrema_authority.py") == 1
    assert classify("src/data/market_scanner.py") == 1


def test_ingest_main_is_tier1_or_below_but_routed_to_forecast_shard():
    """src/ingest_main.py is Tier 1 via existing rules; covered by
    .github/instructions/zeus-forecast-source.instructions.md applyTo."""
    tier = classify("src/ingest_main.py")
    assert tier in (0, 1), (
        f"src/ingest_main.py classified Tier {tier}; expected 0 or 1"
    )


def test_review_instruction_surfaces_are_tier3():
    """Copilot instructions are docs/agent surfaces — Tier 3 by path."""
    assert classify(".github/copilot-instructions.md") == 3
    assert classify(
        ".github/instructions/zeus-forecast-source.instructions.md"
    ) == 3
    assert classify(
        ".github/instructions/tier-scope.instructions.md"
    ) == 3


def test_review_scope_map_doc_is_tier3():
    assert classify("docs/review/review_scope_map.md") == 3


def test_money_path_ci_yaml_is_tier3_by_path():
    """Manifests are Tier 3 by path; effect-tier note in scope_map governs
    when a manifest carries Tier 0/1 effect."""
    assert classify("architecture/money_path_ci.yaml") == 3


def test_db_table_ownership_is_tier3_by_path():
    assert classify("architecture/db_table_ownership.yaml") == 3


def test_archived_paths_are_skipped():
    """Skip lane stays in place."""
    assert classify("docs/archive/2026-Q2/something.md") == 9
    assert classify("state/positions.json") == 9
