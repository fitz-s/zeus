#!/usr/bin/env python3
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Shared sidecar completeness manifests for observation backfill scripts.
# Reuse: Keep this helper side-effect-free except for explicit manifest writes.
# Created: 2026-04-25
# Last reused/audited: 2026-04-25
# Authority basis: P2 4.4.B-lite backfill completeness guardrail packet.
"""Shared completeness manifest and threshold helpers for backfill scripts."""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


STATE_DIR = Path(__file__).resolve().parents[1] / "state"
MANIFEST_SCHEMA_VERSION = 1


def non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return value


def threshold_percent(raw: str) -> float:
    value = float(raw)
    if value < 0.0 or value > 100.0:
        raise argparse.ArgumentTypeError("value must be between 0 and 100")
    return value


def add_completeness_args(
    parser: argparse.ArgumentParser,
    *,
    manifest_prefix: str,
) -> None:
    """Add common completeness guardrail flags to a script parser."""
    parser.add_argument(
        "--completeness-manifest",
        type=Path,
        default=None,
        help=(
            "Write a JSON completeness manifest. Default: "
            f"state/{manifest_prefix}_<run_id>.json."
        ),
    )
    parser.add_argument(
        "--expected-count",
        type=non_negative_int,
        default=None,
        help=(
            "Expected successful unit count. When set, shortfall contributes "
            "to completeness failure."
        ),
    )
    parser.add_argument(
        "--fail-threshold-percent",
        type=threshold_percent,
        default=0.0,
        help=(
            "Maximum allowed failed/shortfall percentage before the script "
            "returns non-zero. Default: 0.0."
        ),
    )


def resolve_manifest_path(
    explicit_path: Path | None,
    *,
    manifest_prefix: str,
    run_id: str | None,
) -> Path:
    if explicit_path is not None:
        return explicit_path
    suffix = _safe_suffix(run_id) if run_id else datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    return STATE_DIR / f"{manifest_prefix}_{suffix}.json"


def evaluate_completeness(
    *,
    actual_count: int,
    failed_count: int,
    attempted_count: int | None,
    expected_count: int | None,
    fail_threshold_percent: float,
    legitimate_gap_count: int = 0,
) -> dict[str, Any]:
    """Compute pass/fail from observed counters.

    `actual_count` is the successful unit count for the script's declared
    unit kind. In dry-run mode that should mean "would write" or "ready" units,
    not persisted rows. `failed_count` is unresolved units at the same grain.
    `legitimate_gap_count` is terminal-but-non-error units, such as HKO
    incomplete/unavailable days. If `expected_count` is provided, expected
    shortfall is measured against attempted terminal units, not only successes,
    so legitimate gaps do not become fake failures.
    """
    if actual_count < 0 or failed_count < 0:
        raise ValueError("actual_count and failed_count must be >= 0")
    if attempted_count is not None and attempted_count < 0:
        raise ValueError("attempted_count must be >= 0")
    if expected_count is not None and expected_count < 0:
        raise ValueError("expected_count must be >= 0")
    if legitimate_gap_count < 0:
        raise ValueError("legitimate_gap_count must be >= 0")
    if fail_threshold_percent < 0.0 or fail_threshold_percent > 100.0:
        raise ValueError("fail_threshold_percent must be between 0 and 100")

    terminal_count = (
        attempted_count
        if attempted_count is not None
        else actual_count + failed_count + legitimate_gap_count
    )
    expected_shortfall = max(expected_count - terminal_count, 0) if expected_count is not None else 0
    if expected_count is not None:
        denominator = expected_count
        failure_units = failed_count + expected_shortfall
    elif attempted_count is not None:
        denominator = attempted_count
        failure_units = failed_count
    else:
        denominator = terminal_count
        failure_units = failed_count

    if denominator <= 0:
        failure_rate_percent = 100.0 if failure_units > 0 else 0.0
    else:
        failure_rate_percent = (failure_units / denominator) * 100.0

    reasons: list[str] = []
    if failed_count:
        reasons.append("failed_count_nonzero")
    if expected_shortfall:
        reasons.append("expected_count_shortfall")
    if failure_rate_percent > fail_threshold_percent:
        reasons.append("failure_rate_exceeded_threshold")

    passed = failure_rate_percent <= fail_threshold_percent
    return {
        "passed": passed,
        "actual_count": actual_count,
        "failed_count": failed_count,
        "attempted_count": attempted_count,
        "expected_count": expected_count,
        "expected_shortfall": expected_shortfall,
        "legitimate_gap_count": legitimate_gap_count,
        "terminal_count": terminal_count,
        "failure_units": failure_units,
        "denominator": denominator,
        "failure_rate_percent": failure_rate_percent,
        "fail_threshold_percent": fail_threshold_percent,
        "exit_code": 0 if passed else 1,
        "reasons": reasons,
    }


def write_manifest(
    path: Path,
    *,
    script_name: str,
    run_id: str,
    dry_run: bool,
    inputs: dict[str, Any],
    counters: dict[str, Any],
    completeness: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    """Write a deterministic JSON sidecar manifest."""
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script_name": script_name,
        "run_id": run_id,
        "mode": "dry_run" if dry_run else "apply",
        "dry_run": dry_run,
        "inputs": _jsonable(inputs),
        "counters": _jsonable(counters),
        "completeness": _jsonable(completeness),
    }
    if extra:
        payload["extra"] = _jsonable(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def emit_manifest_footer(path: Path, completeness: dict[str, Any]) -> None:
    status = "PASS" if completeness["passed"] else "FAIL"
    print(f"Completeness manifest: {path}")
    print(
        "Completeness: "
        f"{status} failure_rate={completeness['failure_rate_percent']:.4f}% "
        f"threshold={completeness['fail_threshold_percent']:.4f}%"
    )


def _safe_suffix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "run"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)
