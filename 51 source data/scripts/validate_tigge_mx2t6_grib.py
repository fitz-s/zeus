#!/usr/bin/env python3
"""Validate TIGGE mx2t6 GRIB metadata integrity.

Hard checks (plan gate):
- paramId == 121
- shortName == "mx2t6"
- stepType == "max"
- typeOfStatisticalProcessing == 2
- endStep - startStep == 6
- step set matches the step slug encoded in the GRIB filename, or an explicit
  --expected-steps override
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eccodes import codes_get, codes_grib_new_from_file, codes_release


@dataclass
class FileStats:
    path: Path
    field_count: int
    step_values: set[int]
    expected_steps: list[int]
    ok: bool
    errors: list[dict[str, Any]]


def _err(path: Path, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"file": str(path), "error": message}
    payload.update(extra)
    return payload


def _parse_expected_steps_from_filename(path: Path) -> list[int] | None:
    match = re.search(r"_steps_([0-9-]+)\.grib$", path.name)
    if not match:
        return None
    return [int(part) for part in match.group(1).split("-")]


def _validate_file(path: Path, *, cli_expected_steps: list[int] | None) -> FileStats:
    errors: list[dict[str, Any]] = []
    steps_seen: set[int] = set()
    field_count = 0
    expected_steps = cli_expected_steps or _parse_expected_steps_from_filename(path) or []
    expected_step_set = set(expected_steps)

    if not path.exists():
        return FileStats(
            path=path,
            field_count=0,
            step_values=set(),
            expected_steps=expected_steps,
            ok=False,
            errors=[_err(path, "file_missing")],
        )
    if path.stat().st_size <= 0:
        return FileStats(
            path=path,
            field_count=0,
            step_values=set(),
            expected_steps=expected_steps,
            ok=False,
            errors=[_err(path, "file_empty")],
        )

    if not expected_steps:
        errors.append(_err(path, "expected_steps_unresolved"))

    with path.open("rb") as fh:
        while True:
            try:
                gid = codes_grib_new_from_file(fh)
            except Exception as exc:  # noqa: BLE001
                errors.append(_err(path, "grib_read_error", detail=repr(exc), field_index=field_count + 1))
                break
            if gid is None:
                break
            field_count += 1
            try:
                param_id = int(codes_get(gid, "paramId"))
                short_name = str(codes_get(gid, "shortName"))
                step_type = str(codes_get(gid, "stepType"))
                type_of_stat = int(codes_get(gid, "typeOfStatisticalProcessing"))
                start_step = int(codes_get(gid, "startStep"))
                end_step = int(codes_get(gid, "endStep"))
                step_value = int(codes_get(gid, "step"))
                steps_seen.add(step_value)

                if param_id != 121:
                    errors.append(_err(path, "paramId_mismatch", expected=121, actual=param_id, field_index=field_count))
                if short_name != "mx2t6":
                    errors.append(_err(path, "shortName_mismatch", expected="mx2t6", actual=short_name, field_index=field_count))
                if step_type != "max":
                    errors.append(_err(path, "stepType_mismatch", expected="max", actual=step_type, field_index=field_count))
                if type_of_stat != 2:
                    errors.append(
                        _err(
                            path,
                            "typeOfStatisticalProcessing_mismatch",
                            expected=2,
                            actual=type_of_stat,
                            field_index=field_count,
                        )
                    )
                if end_step - start_step != 6:
                    errors.append(
                        _err(
                            path,
                            "aggregation_window_mismatch",
                            expected_delta=6,
                            actual_delta=end_step - start_step,
                            startStep=start_step,
                            endStep=end_step,
                            field_index=field_count,
                        )
                    )
                if expected_steps and step_value not in expected_step_set:
                    errors.append(_err(path, "unexpected_step_value", step=step_value, field_index=field_count))
            finally:
                codes_release(gid)

    missing_steps = sorted(expected_step_set - steps_seen)
    if missing_steps:
        errors.append(_err(path, "missing_expected_steps", missing_steps=missing_steps))

    ok = len(errors) == 0
    return FileStats(
        path=path,
        field_count=field_count,
        step_values=steps_seen,
        expected_steps=expected_steps,
        ok=ok,
        errors=errors,
    )


def _collect_files(root: Path | None, paths: list[Path], max_files: int | None) -> list[Path]:
    out: list[Path] = []
    if root is not None:
        out.extend(sorted(root.rglob("*.grib")))
    out.extend(paths)
    unique = sorted({p.resolve() for p in out})
    if max_files is not None and max_files >= 0:
        return unique[:max_files]
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "raw" / "tigge_ecmwf_ens_regions_mx2t6",
        help="Root directory to scan recursively for *.grib",
    )
    parser.add_argument("--path", action="append", type=Path, default=[], help="Extra explicit GRIB path(s)")
    parser.add_argument("--expected-steps", nargs="+", type=int, default=None, help="Optional explicit expected step list")
    parser.add_argument("--max-files", type=int, default=None, help="Optional cap for validation sample size")
    parser.add_argument(
        "--ignore-recent-read-errors-seconds",
        type=int,
        default=900,
        help="Treat grib_read_error on very recent files as transient (in-progress write)",
    )
    parser.add_argument(
        "--write-ok-markers",
        action="store_true",
        help="Write <file>.ok marker for files that pass effective checks; remove stale marker on fail",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args()

    files = _collect_files(args.root, args.path, args.max_files)
    failures: list[dict[str, Any]] = []
    transient_read_errors: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    total_fields = 0
    now_ts = datetime.now(timezone.utc).timestamp()

    for path in files:
        stats = _validate_file(path, cli_expected_steps=args.expected_steps)
        total_fields += stats.field_count
        effective_errors: list[dict[str, Any]] = []
        for error in stats.errors:
            if (
                error.get("error") == "grib_read_error"
                and args.ignore_recent_read_errors_seconds > 0
                and path.exists()
            ):
                age_seconds = now_ts - path.stat().st_mtime
                if age_seconds <= args.ignore_recent_read_errors_seconds:
                    transient_row = dict(error)
                    transient_row["age_seconds"] = round(age_seconds, 3)
                    transient_read_errors.append(transient_row)
                    continue
            effective_errors.append(error)
        effective_ok = len(effective_errors) == 0
        file_rows.append(
            {
                "file": str(stats.path),
                "field_count": stats.field_count,
                "ok": effective_ok,
                "steps_seen": sorted(stats.step_values),
                "expected_steps": stats.expected_steps,
                "error_count": len(effective_errors),
            }
        )
        marker = Path(str(stats.path) + ".ok")
        if args.write_ok_markers:
            if effective_ok:
                marker.touch()
            else:
                marker.unlink(missing_ok=True)
        failures.extend(effective_errors)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": len(failures) == 0,
        "files_checked": len(files),
        "total_fields_checked": total_fields,
        "expected_steps_mode": "cli_override" if args.expected_steps else "filename_slug",
        "cli_expected_steps": args.expected_steps,
        "summary": file_rows,
        "failures": failures,
        "transient_read_errors": transient_read_errors,
    }

    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
