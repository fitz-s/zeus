#!/usr/bin/env python3
"""Rehearse replacement forecast simple-switch writes on an isolated live-root copy."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.init_replacement_forecast_live_schema import initialize_replacement_forecast_live_schema  # noqa: E402
from scripts.plan_replacement_forecast_current_fact_patch import _write_patch_files  # noqa: E402
from src.data.replacement_forecast_config_switch import apply_replacement_forecast_config_switch  # noqa: E402
from src.data.replacement_forecast_current_fact_patch import (  # noqa: E402
    build_replacement_forecast_current_fact_patch_plan,
    normalize_replacement_forecast_current_fact_evidence,
    read_replacement_forecast_current_fact_patch_plan,
)
from src.data.replacement_forecast_live_dry_run import (  # noqa: E402
    OPTIONAL_DEPENDENCIES,
    ReplacementForecastLiveDryRunInput,
    build_replacement_forecast_live_dry_run_report,
)
from src.data.replacement_forecast_live_switch_surface import REFIT_HANDOFF_FILE  # noqa: E402
from src.data.replacement_forecast_runtime_policy import REQUIRED_FLAGS  # noqa: E402
from src.state.db import _connect, list_sqlite_tables_and_views_read_only  # noqa: E402


LIVE_ROOT_FILES = (
    "config/settings.json",
    "config/cities.json",
    "config/source_release_calendar.yaml",
    "docs/operations/current_source_validity.md",
    "docs/operations/current_data_state.md",
    REFIT_HANDOFF_FILE,
    "state/zeus-forecasts.db",
    "state/zeus-world.db",
    "state/zeus_trades.db",
)
LIVE_DB_FILES = {
    "state/zeus-forecasts.db",
    "state/zeus-world.db",
    "state/zeus_trades.db",
}


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _copy_sqlite_schema_stub(*, source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    names = list_sqlite_tables_and_views_read_only(source)
    dst = _connect(destination, write_class="bulk")
    try:
        for name in names:
            if str(name).startswith("sqlite_"):
                continue
            dst.execute(f"CREATE TABLE IF NOT EXISTS {_quote_identifier(str(name))} (id INTEGER PRIMARY KEY)")
        dst.commit()
    finally:
        dst.close()


def _copy_live_surface(
    *,
    live_root: Path,
    rehearsal_root: Path,
    db_copy_mode: str,
    refit_handoff_json: Path | None = None,
) -> tuple[str, ...]:
    copied: list[str] = []
    for relative in LIVE_ROOT_FILES:
        source = live_root / relative
        if not source.exists():
            if relative == REFIT_HANDOFF_FILE and refit_handoff_json is not None:
                continue
            raise FileNotFoundError(str(source))
        destination = rehearsal_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative in LIVE_DB_FILES and db_copy_mode == "schema-stub":
            _copy_sqlite_schema_stub(source=source, destination=destination)
        elif relative in LIVE_DB_FILES and db_copy_mode == "full":
            shutil.copy2(source, destination)
        elif relative in LIVE_DB_FILES:
            raise ValueError("db_copy_mode must be schema-stub or full")
        else:
            shutil.copy2(source, destination)
        copied.append(relative)
    if refit_handoff_json is not None:
        destination = rehearsal_root / REFIT_HANDOFF_FILE
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(refit_handoff_json, destination)
        if REFIT_HANDOFF_FILE not in copied:
            copied.append(REFIT_HANDOFF_FILE)
    return tuple(copied)


def _runtime_flags_from_settings(settings_path: Path) -> dict[str, object]:
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("settings JSON must decode to an object")
    flags = payload.get("feature_flags")
    if not isinstance(flags, dict):
        raise ValueError("settings JSON must contain feature_flags object")
    return {key: flags.get(key, False) for key in REQUIRED_FLAGS}


def _current_fact_plan_for_rehearsal(
    *,
    rehearsal_root: Path,
    evidence_json: Path,
    refit_handoff_json: Path | None,
):
    if refit_handoff_json is None:
        return read_replacement_forecast_current_fact_patch_plan(
            rehearsal_root,
            evidence_json=evidence_json,
        )
    payload = json.loads(evidence_json.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evidence JSON must decode to an object")
    evidence = dict(normalize_replacement_forecast_current_fact_evidence(payload) or {})
    evidence["live_root_read_files_verified"] = True
    refs = list(evidence.get("evidence_refs") or [])
    refs.append(f"Rehearsal refit handoff injected into isolated copy: {REFIT_HANDOFF_FILE}")
    evidence["evidence_refs"] = refs
    return build_replacement_forecast_current_fact_patch_plan(
        rehearsal_root,
        evidence=evidence,
    )


def rehearse_replacement_forecast_simple_switch(
    *,
    live_root: Path,
    evidence_json: Path,
    rehearsal_root: Path,
    optional_dependencies: tuple[str, ...] = OPTIONAL_DEPENDENCIES,
    db_copy_mode: str = "schema-stub",
    refit_handoff_json: Path | None = None,
) -> dict[str, object]:
    copied = _copy_live_surface(
        live_root=live_root,
        rehearsal_root=rehearsal_root,
        db_copy_mode=db_copy_mode,
        refit_handoff_json=refit_handoff_json,
    )
    settings_path = rehearsal_root / "config" / "settings.json"
    config_plan = apply_replacement_forecast_config_switch(settings_path)
    schema_report = initialize_replacement_forecast_live_schema(
        rehearsal_root / "state" / "zeus-forecasts.db",
        commit=True,
    )
    fact_plan = _current_fact_plan_for_rehearsal(
        rehearsal_root=rehearsal_root,
        evidence_json=evidence_json,
        refit_handoff_json=refit_handoff_json,
    )
    _write_patch_files(fact_plan)
    dry_run = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(
            root=rehearsal_root,
            runtime_flags=_runtime_flags_from_settings(settings_path),
            optional_dependencies=optional_dependencies,
        )
    )
    return {
        "status": "REHEARSAL_READY" if dry_run.ok else "REHEARSAL_BLOCKED",
        "live_root": str(live_root),
        "rehearsal_root": str(rehearsal_root),
        "copied_live_files": list(copied),
        "db_copy_mode": db_copy_mode,
        "live_root_written": False,
        "config_switch": config_plan.as_dict(),
        "schema_commit": schema_report,
        "current_fact_patch": fact_plan.as_dict(),
        "dry_run": dry_run.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse replacement forecast simple-switch on an isolated copy")
    parser.add_argument("--live-root", type=Path, required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--rehearsal-root", type=Path, default=None)
    parser.add_argument("--db-copy-mode", choices=("schema-stub", "full"), default="schema-stub")
    parser.add_argument("--refit-handoff-json", type=Path, default=None, help="Copy a ready refit handoff artifact into the isolated rehearsal root")
    parser.add_argument(
        "--optional-dependency",
        action="append",
        default=None,
        help="Optional dependency module to require in dry-run; may be repeated. Defaults to live dependencies.",
    )
    parser.add_argument("--keep", action="store_true", help="Keep an auto-created rehearsal directory")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.rehearsal_root is not None:
            rehearsal_root = args.rehearsal_root
            rehearsal_root.mkdir(parents=True, exist_ok=True)
            report = rehearse_replacement_forecast_simple_switch(
                live_root=args.live_root,
                evidence_json=args.evidence_json,
                rehearsal_root=rehearsal_root,
                optional_dependencies=tuple(args.optional_dependency or OPTIONAL_DEPENDENCIES),
                db_copy_mode=args.db_copy_mode,
                refit_handoff_json=args.refit_handoff_json,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="replacement-simple-switch-rehearsal-") as tmp:
                rehearsal_root = Path(tmp)
                report = rehearse_replacement_forecast_simple_switch(
                    live_root=args.live_root,
                    evidence_json=args.evidence_json,
                    rehearsal_root=rehearsal_root,
                    optional_dependencies=tuple(args.optional_dependency or OPTIONAL_DEPENDENCIES),
                    db_copy_mode=args.db_copy_mode,
                    refit_handoff_json=args.refit_handoff_json,
                )
                if args.keep:
                    kept_root = Path(tempfile.mkdtemp(prefix="replacement-simple-switch-rehearsal-kept-"))
                    shutil.copytree(rehearsal_root, kept_root, dirs_exist_ok=True)
                    report["rehearsal_root"] = str(kept_root)
        if args.stdout:
            print(json.dumps(report, sort_keys=True))
        else:
            print(f"{report['status']}: rehearsal_root={report['rehearsal_root']}")
        return 0 if report["status"] == "REHEARSAL_READY" else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "live_root_written": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
