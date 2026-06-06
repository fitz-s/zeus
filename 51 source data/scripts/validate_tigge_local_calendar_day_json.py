#!/usr/bin/env python3
"""Generic validator for local-calendar-day TIGGE composite JSON products."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tigge_local_calendar_day_common import ROOT, load_json_files, now_utc_iso, write_json
from tigge_local_calendar_day_extract import TRACKS, TrackConfig


def _err(path: Path, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"file": str(path), "error": message}
    payload.update(extra)
    return payload


def _validate_payload(path: Path, payload: dict[str, Any], track: TrackConfig) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if payload.get("data_version") != track.data_version:
        errors.append(_err(path, "data_version_mismatch", expected=track.data_version, actual=payload.get("data_version")))
    if payload.get("physical_quantity") != track.physical_quantity:
        errors.append(_err(path, "physical_quantity_mismatch", expected=track.physical_quantity, actual=payload.get("physical_quantity")))
    if int(payload.get("member_count") or 0) != 51:
        errors.append(_err(path, "member_count_mismatch", expected=51, actual=payload.get("member_count")))
    members = payload.get("members")
    if not isinstance(members, list) or len(members) != 51:
        errors.append(_err(path, "members_length_mismatch", actual=len(members) if isinstance(members, list) else None))
        return errors
    ids = [int(m.get("member")) for m in members if isinstance(m, dict) and m.get("member") is not None]
    if sorted(ids) != list(range(51)):
        errors.append(_err(path, "member_ids_mismatch", actual=ids))
    if track.mode == "high":
        bad = [m.get("member") for m in members if m.get("value_native_unit") is None]
        if bad:
            errors.append(_err(path, "high_missing_member_values", members=bad[:20], total=len(bad)))
    else:
        training_allowed = bool(payload.get("training_allowed"))
        boundary_policy = payload.get("boundary_policy") or {}
        if training_allowed and boundary_policy.get("boundary_ambiguous"):
            errors.append(_err(path, "low_training_allowed_but_boundary_ambiguous"))
        if training_allowed:
            bad = [m.get("member") for m in members if m.get("value_native_unit") is None]
            if bad:
                errors.append(_err(path, "low_training_allowed_missing_values", members=bad[:20], total=len(bad)))
    return errors


def validate_root(*, track: TrackConfig, root: Path, max_files: int | None, output: Path | None) -> dict:
    failures: list[dict[str, Any]] = []
    summary_rows = []
    for path in load_json_files(root, max_files=max_files):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            failures.append(_err(path, "json_read_error", detail=repr(exc)))
            continue
        errors = _validate_payload(path, payload, track)
        summary_rows.append({"file": str(path), "ok": len(errors) == 0, "error_count": len(errors)})
        failures.extend(errors)
    result = {
        "generated_at": now_utc_iso(),
        "track": track.name,
        "data_version": track.data_version,
        "physical_quantity": track.physical_quantity,
        "root": str(root),
        "files_checked": len(summary_rows),
        "ok": len(failures) == 0,
        "summary": summary_rows,
        "failures": failures,
    }
    if output is not None:
        write_json(output, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", choices=sorted(TRACKS), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    track = TRACKS[args.track]
    result = validate_root(track=track, root=args.root, max_files=args.max_files, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
