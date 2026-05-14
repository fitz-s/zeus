#!/usr/bin/env python3
# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §3 A8 (REV 4)
#   CRITIC_REVIEW_REV2 §3.2 / A8 AST writer-signature audit
"""CI hook: AST audit for writer-function connection-type signatures.

Detects src/ functions that write to Zeus DB tables without declaring
a typed connection parameter. P1 establishes the BASELINE of known violations
for P3 to fix. P1 scope: surface violations, not fix them.

Exit 0 = PASS (no new violations beyond baseline, or --baseline mode).
Exit 1 = FAIL (new violations found vs baseline).
Exit 2 = SETUP ERROR.

Usage:
    python3 scripts/check_writer_signature_typing.py [--verbose] [--baseline]

    --baseline: Print current violations (for establishing P1 baseline). Always exits 0.
    --verbose:  Print all checked functions, not just violations.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent

# DB writer function name patterns — functions that write to Zeus DB tables.
# Conservative subset: functions with "write_", "insert_", "record_", "save_",
# "_append", "_upsert", "_create", "_update" in name that take a "conn" param.
_WRITER_PATTERNS = (
    "write_",
    "insert_",
    "record_",
    "save_",
    "_append",
    "_upsert",
    "append_",
    "upsert_",
)

# Typed connection type names (P1 introduces these; P3 wires them to callsites).
_TYPED_CONN_TYPES = frozenset({
    "WorldConnection",
    "ForecastsConnection",
    "TradeConnection",
    "TypedConnection",
})

# P1 known-baseline violations: functions that write to DB but use untyped conn.
# P3 will fix these one by one. This list is the P1 snapshot; do NOT prune
# without fixing the underlying function signature.
#
# Format: "src/path/to/file.py::function_name"
# Generate fresh baseline via: python3 scripts/check_writer_signature_typing.py --baseline
_P1_BASELINE_VIOLATIONS: frozenset[str] = frozenset({
    # 69 pre-P3 violations captured at P1 completion (2026-05-14, tip 0c10a326e4)
    # P3 will migrate these to TypedConnection. Do not grow this set.
    "src/calibration/effective_sample_size.py::write_decision_groups",
    "src/calibration/retrain_trigger.py::_insert_version",
    "src/calibration/store.py::save_platt_model",
    "src/calibration/store.py::save_platt_model_v2",
    "src/data/daily_obs_append.py::_write_atom_with_coverage",
    "src/data/daily_obs_append.py::append_hko_months",
    "src/data/daily_obs_append.py::append_ogimet_city",
    "src/data/daily_obs_append.py::append_wu_city",
    "src/data/daily_observation_writer.py::_insert_daily_revision",
    "src/data/daily_observation_writer.py::insert_or_update_current_observation",
    "src/data/daily_observation_writer.py::write_daily_observation_with_revision",
    "src/data/ecmwf_open_data.py::_write_source_authority_chain",
    "src/data/entry_readiness_writer.py::write_entry_readiness",
    "src/data/forecasts_append.py::_insert_rows",
    "src/data/forecasts_append.py::append_forecasts_window",
    "src/data/hourly_instants_append.py::_write_row",
    "src/data/hourly_instants_append.py::append_hourly_window",
    "src/data/observation_instants_v2_writer.py::_insert_revision",
    "src/data/observation_instants_v2_writer.py::insert_rows",
    "src/data/solar_append.py::_write_row_with_coverage",
    "src/data/solar_append.py::append_solar_window",
    "src/engine/cycle_runtime.py::_dual_write_canonical_entry_if_available",
    "src/engine/evaluator.py::_record_selection_family_facts",
    "src/engine/evaluator.py::_write_entry_readiness_for_candidate",
    "src/engine/replay.py::_insert_backtest_outcome",
    "src/engine/replay.py::_insert_backtest_run",
    "src/execution/exchange_reconcile.py::_append_linkable_trade_fact_if_missing",
    "src/execution/exchange_reconcile.py::_record_position_drift_findings",
    "src/execution/exchange_reconcile.py::record_finding",
    "src/execution/exit_lifecycle.py::_dual_write_canonical_economic_close_if_available",
    "src/execution/exit_safety.py::_append_cancel_unknown",
    "src/execution/fill_tracker.py::_append_trade_lifecycle_review_required",
    "src/execution/harvester.py::_dual_write_canonical_settlement_if_available",
    "src/execution/harvester.py::_write_settlement_truth",
    "src/execution/harvester.py::maybe_write_learning_pair",
    "src/execution/settlement_commands.py::_append_event",
    "src/execution/wrap_unwrap_commands.py::_append_event",
    "src/ingest/harvester_truth_writer.py::_write_settlement_truth",
    "src/ingest/polymarket_user_channel.py::_append_command_event_if_legal",
    "src/ingest/polymarket_user_channel.py::_append_position_lot",
    "src/state/data_coverage.py::bulk_record_written",
    "src/state/data_coverage.py::record_failed",
    "src/state/data_coverage.py::record_legitimate_gap",
    "src/state/data_coverage.py::record_missing",
    "src/state/data_coverage.py::record_written",
    "src/state/db.py::_insert_forward_market_event",
    "src/state/db.py::_insert_forward_price_history",
    "src/state/db.py::_insert_full_linkage_price_history",
    "src/state/db.py::append_source_contract_audit_events",
    "src/state/db.py::record_token_suppression",
    "src/state/db.py::upsert_control_override",
    "src/state/job_run_repo.py::write_job_run",
    "src/state/ledger.py::append_many_and_project",
    "src/state/market_topology_repo.py::write_market_topology_state",
    "src/state/projection.py::upsert_position_current",
    "src/state/readiness_repo.py::write_readiness_state",
    "src/state/snapshot_repo.py::insert_snapshot",
    "src/state/source_run_coverage_repo.py::write_source_run_coverage",
    "src/state/source_run_repo.py::write_source_run",
    "src/state/uma_resolution_listener.py::record_resolution",
    "src/state/venue_command_repo.py::_append_command_provenance_event",
    "src/state/venue_command_repo.py::append_event",
    "src/state/venue_command_repo.py::append_order_fact",
    "src/state/venue_command_repo.py::append_position_lot",
    "src/state/venue_command_repo.py::append_provenance_event",
    "src/state/venue_command_repo.py::append_trade_fact",
    "src/state/venue_command_repo.py::insert_command",
    "src/state/venue_command_repo.py::insert_submission_envelope",
    "src/strategy/benchmark_suite.py::record_benchmark_run",
})


def _is_writer(name: str) -> bool:
    return any(pat in name for pat in _WRITER_PATTERNS)


def _has_conn_param(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function has a parameter named 'conn'."""
    all_args = (
        func.args.args
        + func.args.posonlyargs
        + func.args.kwonlyargs
        + ([func.args.vararg] if func.args.vararg else [])
        + ([func.args.kwarg] if func.args.kwarg else [])
    )
    return any(arg.arg == "conn" for arg in all_args)


def _conn_param_annotation(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the string annotation for the 'conn' parameter, or None if unannotated."""
    all_args = func.args.args + func.args.posonlyargs + func.args.kwonlyargs
    for arg in all_args:
        if arg.arg == "conn" and arg.annotation is not None:
            return ast.unparse(arg.annotation)
    return None


def scan_violations(src_root: Path, verbose: bool = False) -> list[str]:
    """Return list of 'file::function' strings for untyped writer functions."""
    violations: list[str] = []

    for py_file in sorted(src_root.rglob("*.py")):
        rel = str(py_file.relative_to(_REPO_ROOT))
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_writer(node.name):
                continue
            if not _has_conn_param(node):
                continue

            annotation = _conn_param_annotation(node)
            is_typed = annotation is not None and any(
                t in annotation for t in _TYPED_CONN_TYPES
            )

            if verbose:
                typed_str = f"typed={annotation}" if annotation else "UNTYPED"
                print(f"  {rel}::{node.name} conn={typed_str}")

            if not is_typed:
                violations.append(f"{rel}::{node.name}")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--baseline", action="store_true",
        help="Print current violations (establishes P1 baseline). Always exits 0."
    )
    args = parser.parse_args()

    src_root = _REPO_ROOT / "src"
    violations = scan_violations(src_root, verbose=args.verbose)

    if args.baseline:
        print(f"P1 BASELINE: {len(violations)} writer functions with untyped conn parameter")
        print("(P3 will fix these. Add to _P1_BASELINE_VIOLATIONS in this script.)")
        for v in sorted(violations):
            print(f"  {v}")
        return 0

    # Compare against P1 baseline.
    known = _P1_BASELINE_VIOLATIONS
    new_violations = [v for v in violations if v not in known]
    fixed = [v for v in known if v not in violations]

    if fixed:
        print(f"IMPROVEMENTS: {len(fixed)} previously-untyped writers are now typed:")
        for v in sorted(fixed):
            print(f"  + {v}")

    if new_violations:
        print(f"FAIL: {len(new_violations)} new untyped writer functions (not in P1 baseline):")
        for v in sorted(new_violations):
            print(f"  ! {v}")
        print("\nFIX: Add typed connection annotation to these functions, OR")
        print("add to _P1_BASELINE_VIOLATIONS if this is a known pre-P3 debt item.")
        return 1

    if args.verbose:
        print(f"PASS: {len(violations)} known-baseline violations, 0 new (P3 will fix)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
