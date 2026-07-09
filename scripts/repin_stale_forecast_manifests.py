#!/usr/bin/env python3
"""Re-pin stale raw forecast artifact manifests whose byte_size/sha256 drifted from the
on-disk artifact.

Root cause (2026-07-08 posterior blackout): a raw artifact rewritten with a benign
serialization change (the trailing "\\n" _write_json appends, added 2026-06-24 by
e2cd7a9bc) AFTER its manifest was pinned makes RawForecastArtifactManifest.verify_artifact
hard-fail on the byte_size/sha mismatch, aborting materialization for every current
target. This script recomputes byte_size + sha256 from the CURRENT bytes for each
present-and-valid-JSON artifact and rewrites the manifest file + DB row so verify passes.

SAFETY:
  --dry-run (DEFAULT) is fully READ-ONLY: it scans and REPORTS only, mutating nothing.
  --apply performs the live rewrite and is OPERATOR-AUTHORIZED ONLY.
  A drifted artifact that is NOT valid JSON is flagged as a possible-corruption suspect
  and is NEVER re-pinned (that is a real-corruption signal, not a benign rewrite).

Usage:
  python scripts/repin_stale_forecast_manifests.py --dry-run
  python scripts/repin_stale_forecast_manifests.py --apply    # operator only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.data.raw_forecast_artifact_manifest import (  # noqa: E402
    manifest_matches_artifact,
    read_manifest,
    repin_manifest_from_file,
    write_manifest,
    write_manifest_to_db,
)

DEFAULT_RAW_MANIFEST_DIR = _ROOT / "state" / "replacement_forecast_live" / "raw_manifests"
DEFAULT_FORECAST_DB = _ROOT / "state" / "zeus-forecasts.db"


def _resolve_artifact(manifest, manifest_path: Path) -> Path:
    ap = Path(manifest.artifact_path)
    return ap if ap.is_absolute() else (manifest_path.parent / ap).resolve()


def _artifact_is_valid_json(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except Exception:
        return False


def scan(raw_manifest_dir: Path, forecast_db: Path, *, apply: bool) -> dict:
    report: dict = {
        "raw_manifest_dir": str(raw_manifest_dir),
        "mode": "APPLY" if apply else "DRY_RUN",
        "total_manifests": 0,
        "unreadable_manifests": [],       # manifest file could not be parsed under current schema
        "matched": 0,                     # on-disk artifact matches byte_size + sha
        "missing_artifact": [],           # manifest references an artifact that is absent
        "drifted_total": 0,
        "drifted_valid_safe_to_repin": 0,
        "drifted_invalid_json_CORRUPTION_SUSPECTS": [],  # drifted AND unparseable — never re-pinned
        "repinned": 0,                    # only >0 in --apply
        "repin_errors": [],
    }
    if not raw_manifest_dir.exists():
        report["error"] = f"raw_manifest_dir does not exist: {raw_manifest_dir}"
        return report

    conn = None
    if apply:
        from src.state.db import _connect  # noqa: PLC0415

        conn = _connect(forecast_db, write_class="live")
        conn.execute("BEGIN IMMEDIATE")

    try:
        for mpath in sorted(raw_manifest_dir.rglob("*.manifest.json")):
            report["total_manifests"] += 1
            try:
                manifest = read_manifest(mpath)
            except Exception as exc:  # noqa: BLE001
                report["unreadable_manifests"].append({"manifest": str(mpath), "error": str(exc)[:160]})
                continue
            artifact = _resolve_artifact(manifest, mpath)
            if not artifact.exists():
                report["missing_artifact"].append(str(artifact))
                continue
            if manifest_matches_artifact(manifest):
                report["matched"] += 1
                continue
            # Drifted: size and/or sha differ from the on-disk bytes.
            report["drifted_total"] += 1
            if not _artifact_is_valid_json(artifact):
                report["drifted_invalid_json_CORRUPTION_SUSPECTS"].append(
                    {"manifest": str(mpath), "artifact": str(artifact)}
                )
                continue
            report["drifted_valid_safe_to_repin"] += 1
            if apply:
                try:
                    repinned = repin_manifest_from_file(manifest)
                    write_manifest(repinned, mpath)
                    if conn is not None:
                        write_manifest_to_db(conn, repinned, verify_artifact=True)
                    report["repinned"] += 1
                except Exception as exc:  # noqa: BLE001
                    report["repin_errors"].append({"manifest": str(mpath), "error": str(exc)[:160]})
        if apply and conn is not None:
            conn.commit()
    except Exception:
        if apply and conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-pin stale raw forecast artifact manifests.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True, help="Read-only report (DEFAULT).")
    group.add_argument("--apply", action="store_true", help="Rewrite manifest files + DB rows (OPERATOR ONLY).")
    parser.add_argument("--raw-manifest-dir", type=Path, default=DEFAULT_RAW_MANIFEST_DIR)
    parser.add_argument("--forecast-db", type=Path, default=DEFAULT_FORECAST_DB)
    args = parser.parse_args(argv)

    apply = bool(args.apply)  # --apply overrides the default --dry-run
    report = scan(args.raw_manifest_dir, args.forecast_db, apply=apply)
    print(json.dumps(report, indent=2, sort_keys=True))
    # Non-zero exit if corruption suspects exist so a caller/monitor notices.
    return 2 if report.get("drifted_invalid_json_CORRUPTION_SUSPECTS") else 0


if __name__ == "__main__":
    raise SystemExit(main())
