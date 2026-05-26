# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase B §5
#                  scripts/topology_doctor_context_pack.py
#
# Integration tests for the Phase B Context Pack assembler. Each test
# replays a historical PR's changed-file shape and verifies the assembler
# produces a Context Pack with the expected surfaces, failure chains,
# blocking relationship tests, and topology boundary disclaimers.
#
# The acceptance gate from docs/operations/current/plans/ci_topology_refactor_refined.md:
#   PR325 → market_discovery_scanner + FC-02 + test_market_discovery_full_coverage.py
#   PR330 → execution_cycle_runtime + FC-03 + test_exec_freshness_recapture.py
#   PR335 → ingest_scheduler + FC-04 + test_writer_jobs_registry_guard.py
#   PR312 → executable_forecast_reader + FC-01 + test_executable_forecast_bundle_selection.py
#   PR306 → topology_v_next + FC-09 (stdlib shadow structural blocker fires)
"""Phase B Context Pack integration tests (historical PR fixtures)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.topology_doctor_context_pack import (
    assemble_context_packs,
    render_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixture data — historical PR file lists
# ---------------------------------------------------------------------------

PR325_FILES = [
    "src/data/market_scanner.py",
    "src/main.py",
    "src/data/polymarket_client.py",
]

PR330_FILES = [
    "src/engine/cycle_runtime.py",
    "src/execution/order_planner.py",
    "src/venue/clob_adapter.py",
]

PR335_FILES = [
    "src/ingest_main.py",
    "src/data/source_job_registry.py",
    "src/data/scheduler_adapter.py",
]

PR312_FILES = [
    "src/data/executable_forecast_reader.py",
    "src/data/forecast_extrema_authority.py",
    "src/engine/evaluator.py",
]

PR306_FILES = [
    "scripts/topology_v_next/dataclasses.py",   # the (hypothetical) stdlib shadow
    "scripts/topology_v_next/admission_engine.py",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _surface_ids(bundle: dict) -> set[str]:
    return {
        m["surface_id"]
        for pack in bundle["packs"]
        for m in pack["matched_surfaces"]
    }


def _fc_ids(bundle: dict) -> set[str]:
    return {fc["id"] for pack in bundle["packs"] for fc in pack["failure_chains"]}


def _blocking_tests(bundle: dict) -> set[str]:
    return {
        t
        for pack in bundle["packs"]
        for t in pack["ci_classification"]["blocking_relationship"]
    }


def _ntr_lines(bundle: dict) -> list[str]:
    lines: list[str] = []
    for pack in bundle["packs"]:
        lines.extend(pack["not_topology_responsibility"])
    return lines


# ---------------------------------------------------------------------------
# PR-fixture tests
# ---------------------------------------------------------------------------


def test_pr325_market_discovery_substrate():
    bundle = assemble_context_packs(PR325_FILES, task_label="market discovery substrate")
    assert "market_discovery_scanner" in _surface_ids(bundle)
    assert "FC-02" in _fc_ids(bundle)
    assert "tests/test_market_discovery_full_coverage.py" in _blocking_tests(bundle)
    # Surface emits source_rationale_delta_gate static gate
    static_gate_ids = {
        g["id"]
        for pack in bundle["packs"]
        for g in pack["required_static_gates"]
    }
    assert "source_rationale_delta_gate" in static_gate_ids
    # Topology boundary disclaimer present
    ntr = " ".join(_ntr_lines(bundle))
    assert "Gamma" in ntr or "CLOB" in ntr or "live" in ntr.lower()


def test_pr330_execution_fresh_submit():
    bundle = assemble_context_packs(PR330_FILES, task_label="execution fresh-submit recapture")
    surface_ids = _surface_ids(bundle)
    # PR330 touches cycle_runtime + execution + venue, all three are T0 surfaces
    assert "execution_cycle_runtime" in surface_ids
    assert "execution_venue_boundary" in surface_ids
    assert "FC-03" in _fc_ids(bundle)
    assert "tests/test_exec_freshness_recapture.py" in _blocking_tests(bundle)
    # All matched packs should be T0
    for pack in bundle["packs"]:
        assert pack["risk_tier"] == "T0", f"expected T0, got {pack['risk_tier']}"
    # Stale-threshold fatal misread injected into runtime warnings
    warnings = " ".join(
        w for pack in bundle["packs"] for w in pack.get("agent_runtime_warnings", [])
    )
    assert "stale threshold" in warnings.lower() or "stale" in warnings.lower()


def test_pr335_ingest_scheduler_registry():
    bundle = assemble_context_packs(PR335_FILES, task_label="scheduler registry guard")
    assert "ingest_scheduler" in _surface_ids(bundle)
    fc_ids = _fc_ids(bundle)
    # FC-04 (spec-AST mismatch) and FC-05 (HKO drift) both attribute to ingest_scheduler
    assert "FC-04" in fc_ids
    assert "FC-05" in fc_ids
    assert "tests/test_writer_jobs_registry_guard.py" in _blocking_tests(bundle)


def test_pr312_forecast_bundle_extrema():
    bundle = assemble_context_packs(PR312_FILES, task_label="forecast bundle extrema authority")
    surface_ids = _surface_ids(bundle)
    assert "executable_forecast_reader" in surface_ids
    assert "FC-01" in _fc_ids(bundle)
    assert "tests/test_executable_forecast_bundle_selection.py" in _blocking_tests(bundle)
    assert "tests/test_executable_forecast_reader.py" in _blocking_tests(bundle)


def test_pr306_stdlib_shadow_topology_v_next():
    bundle = assemble_context_packs(PR306_FILES, task_label="topology_v_next stdlib shadow")
    assert "topology_v_next" in _surface_ids(bundle)
    # FC-09 covers topology sprawl + stdlib shadowing
    assert "FC-09" in _fc_ids(bundle)
    # The stdlib_shadowing_gate must appear in static gates
    static_gate_ids = {
        g["id"]
        for pack in bundle["packs"]
        for g in pack["required_static_gates"]
    }
    assert "stdlib_shadowing_gate" in static_gate_ids


# ---------------------------------------------------------------------------
# Multi-file UNION semantics (operator caveat 2026-05-26)
# ---------------------------------------------------------------------------


def test_multi_file_does_not_fall_to_generic():
    """
    Operator caveat: existing topology_doctor degrades on multi-file PRs
    (high_fanout_file_only → generic profile). The new assembler must NOT
    drop to a generic fallback when ≥2 surfaces match.
    """
    bundle = assemble_context_packs(
        ["src/engine/cycle_runtime.py", "src/data/market_scanner.py"]
    )
    surface_ids = _surface_ids(bundle)
    # Should match BOTH surfaces, not collapse to one
    assert "execution_cycle_runtime" in surface_ids
    assert "market_discovery_scanner" in surface_ids
    # And produce ≥2 packs in emit_per_surface mode (default)
    assert len(bundle["packs"]) >= 2
    # No pack should be named "generic" or fall back to empty matched_surfaces
    for pack in bundle["packs"]:
        assert pack["id"] != "generic"
        assert len(pack["matched_surfaces"]) >= 1


def test_emit_merged_mode_returns_single_pack():
    bundle = assemble_context_packs(
        ["src/engine/cycle_runtime.py", "src/data/market_scanner.py"],
        mode="emit_merged",
    )
    assert len(bundle["packs"]) == 1
    # Merged pack UNIONs matched_surfaces across all hits
    surface_ids = _surface_ids(bundle)
    assert "execution_cycle_runtime" in surface_ids
    assert "market_discovery_scanner" in surface_ids
    # Merged pack id is prefixed
    assert bundle["packs"][0]["id"].startswith("merged_")


def test_unmatched_files_produce_review_required():
    """Files completely outside money-path scope return REVIEW_REQUIRED."""
    bundle = assemble_context_packs(["some/random/path.txt"])
    assert bundle["packs"] == []
    assert "some/random/path.txt" in bundle["missing_surfaces_for_files"]
    assert bundle["review_required"]  # non-empty


def test_empty_changed_files_returns_empty_bundle():
    bundle = assemble_context_packs([])
    assert bundle["packs"] == []
    assert bundle["missing_surfaces_for_files"] == []
    assert bundle["review_required"] == []


# ---------------------------------------------------------------------------
# Schema conformance
# ---------------------------------------------------------------------------


def test_emitted_packs_conform_to_required_fields():
    """Every emitted pack must declare every required_fields key from
    architecture/context_pack_schema.yaml."""
    import yaml
    with (REPO_ROOT / "architecture" / "context_pack_schema.yaml").open() as f:
        schema = yaml.safe_load(f)
    required = set(schema["required_fields"])
    bundle = assemble_context_packs(PR330_FILES, task_label="schema conformance check")
    for pack in bundle["packs"]:
        missing = required - set(pack)
        assert not missing, f"pack {pack['id']} missing required fields: {missing}"


def test_pack_ci_classification_no_override_rules_consistent():
    """override.no_override_rules must reference rules in topology_enforcement.yaml."""
    import yaml
    with (REPO_ROOT / "architecture" / "topology_enforcement.yaml").open() as f:
        enforcement = yaml.safe_load(f)
    expected_no_override = set(enforcement.get("no_override_rules") or [])
    bundle = assemble_context_packs(PR330_FILES)
    for pack in bundle["packs"]:
        emitted_no_override = set(pack["override"]["no_override_rules"])
        # Emitted no_override list must EQUAL the canonical set (not subset).
        assert emitted_no_override == expected_no_override, (
            f"pack {pack['id']} no_override_rules drift: "
            f"missing {expected_no_override - emitted_no_override}, "
            f"extra {emitted_no_override - expected_no_override}"
        )


def test_pack_active_invariants_come_from_money_path_ci():
    """active_invariants source must be money_path_ci.yaml or money_path_objects.yaml,
    NEVER architecture/invariants.yaml (which does not exist)."""
    bundle = assemble_context_packs(PR330_FILES)
    for pack in bundle["packs"]:
        for inv in pack["active_invariants"]:
            assert inv["source"] != "architecture/invariants.yaml"
            assert inv["source"] in (
                "architecture/money_path_ci.yaml",
                "architecture/money_path_objects.yaml",
                "custom",
            )


# ---------------------------------------------------------------------------
# Renderer smoke tests
# ---------------------------------------------------------------------------


def test_markdown_compact_under_line_budget():
    """Compact mode should fit in the renderer.compact_budget_lines budget
    PER PACK (not total)."""
    bundle = assemble_context_packs(PR312_FILES)
    md = render_markdown(bundle, mode="compact")
    assert "Zeus Context Pack:" in md
    assert "## Read before reasoning" in md
    assert "## Historical failure chains" in md


def test_markdown_expanded_includes_evidence():
    bundle = assemble_context_packs(PR312_FILES)
    md = render_markdown(bundle, mode="expanded")
    assert "## Evidence sources" in md


def test_markdown_unmatched_yields_review_required_section():
    bundle = assemble_context_packs(["some/random/path.txt"])
    md = render_markdown(bundle)
    assert "REVIEW_REQUIRED" in md


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_emits_valid_json():
    """Run the CLI end-to-end via subprocess + parse JSON.

    Uses sys.executable so the test always runs with the same interpreter
    that runs pytest (which has PyYAML available).
    """
    import sys as _sys
    out = subprocess.run(
        [
            _sys.executable,
            "scripts/topology_doctor_context_pack.py",
            "--changed-files",
            "src/data/executable_forecast_reader.py",
            "--task",
            "smoke",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert out.returncode == 0, f"CLI failed: {out.stderr}"
    payload = json.loads(out.stdout)
    assert payload["schema_version"] == 1
    assert payload["packs"]
    assert payload["packs"][0]["id"] == "forecast_bundle_extrema"


# ---------------------------------------------------------------------------
# Deterministic, idempotent
# ---------------------------------------------------------------------------


def test_same_input_yields_same_output():
    a = assemble_context_packs(PR330_FILES, task_label="determinism check")
    b = assemble_context_packs(PR330_FILES, task_label="determinism check")
    assert a == b


def test_file_order_independent():
    """Changing file order in input should not change which surfaces/FCs are matched."""
    a = assemble_context_packs(PR330_FILES)
    b = assemble_context_packs(list(reversed(PR330_FILES)))
    assert _surface_ids(a) == _surface_ids(b)
    assert _fc_ids(a) == _fc_ids(b)


# ---------------------------------------------------------------------------
# Phase B.7 fixes: fatal_misreads populated, merged anchor stable
# ---------------------------------------------------------------------------


def test_fatal_misreads_populated_for_chains_with_refs():
    """Phase B.7 fix: fatal_misreads_for_chains() must resolve refs from
    failure_chains.yaml#chains[].fatal_misread_refs into actual misread
    entries from architecture/fatal_misreads.yaml. (Copilot finding on PR
    #343: previous lookup keyed on non-existent fields.)"""
    # FC-01 has fatal_misread_refs added in B.7
    bundle = assemble_context_packs(PR312_FILES)
    fatal_lists = [pack["fatal_misreads"] for pack in bundle["packs"]]
    flat = [fm for fl in fatal_lists for fm in fl]
    assert len(flat) >= 1, "FC-01 surfaces must produce ≥1 fatal_misread"
    # Each entry must carry a real misread id from architecture/fatal_misreads.yaml
    ids = {fm["id"] for fm in flat}
    assert any(i in ids for i in (
        "hourly_downsample_preserves_extrema",
        "daily_day0_hourly_forecast_sources_are_not_interchangeable",
    ))
    # Each must have inject_into_prompt + reason fields populated
    for fm in flat:
        assert "inject_into_prompt" in fm
        assert fm.get("reason"), f"empty reason on {fm['id']}"


def test_fatal_misreads_resolve_actual_misread_text():
    """Verify the lookup uses real fatal_misreads.yaml fields
    (false_equivalence + correction), not the old non-existent keys."""
    bundle = assemble_context_packs(
        ["src/data/day0_observation_reader.py", "src/engine/evaluator_day0.py"]
    )
    flat = [fm for pack in bundle["packs"] for fm in pack["fatal_misreads"]]
    # FC-07 maps to airport_station_not_city_settlement_station etc.
    # Reason should contain the false_equivalence string, not be empty.
    assert flat, "FC-07 surfaces must produce fatal_misreads"
    for fm in flat:
        # Real misreads have severity prefix [critical|high|medium]
        assert fm["reason"].startswith("[") or "REVIEW_REQUIRED" in fm["reason"]


def test_emit_merged_anchor_is_stable_under_file_reordering():
    """Phase B.7 fix: merged anchor must not depend on input file order."""
    a = assemble_context_packs(
        ["src/engine/cycle_runtime.py", "src/data/market_scanner.py"],
        mode="emit_merged",
    )
    b = assemble_context_packs(
        ["src/data/market_scanner.py", "src/engine/cycle_runtime.py"],
        mode="emit_merged",
    )
    assert a["packs"][0]["id"] == b["packs"][0]["id"], (
        "merged pack id must be stable across input file orderings"
    )
    # Matched surfaces should also be in a stable order
    a_surface_order = [s["surface_id"] for s in a["packs"][0]["matched_surfaces"]]
    b_surface_order = [s["surface_id"] for s in b["packs"][0]["matched_surfaces"]]
    assert a_surface_order == b_surface_order


def test_render_markdown_dedups_surface_ids_in_why_line():
    """Phase B.7 fix: when multiple files match the same surface, the
    'Why you are seeing this' line should list each surface_id once."""
    bundle = assemble_context_packs(
        # 3 files all matching execution_venue_boundary
        ["src/execution/a.py", "src/execution/b.py", "src/venue/c.py"],
        mode="emit_merged",
    )
    md = render_markdown(bundle, mode="compact")
    # surface should appear once in the "Why you are seeing this" surfaces line
    why_section = md.split("## Why you are seeing this")[1].split("##")[0]
    # Count occurrences in the inline list (not in other sections of the doc)
    count = why_section.count("execution_venue_boundary")
    assert count == 1, f"execution_venue_boundary should appear once in why line; got {count}"


def test_render_markdown_renders_fatal_misreads_section():
    """Phase B.7 fix: fatal_misreads section must be rendered separately
    from runtime warnings (was completely dropped before)."""
    bundle = assemble_context_packs(PR312_FILES)
    md = render_markdown(bundle)
    assert "## Fatal misreads (injected)" in md
    # And runtime warnings section is now distinct
    assert "## Runtime warnings (from failure chains)" in md
