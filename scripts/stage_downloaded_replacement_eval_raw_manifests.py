#!/usr/bin/env python3
"""Stage downloaded replacement-eval raw files as queryable artifact manifests."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.ecmwf_aifs_sampled_2t_localday import HIGH_DATA_VERSION as AIFS_HIGH_DATA_VERSION  # noqa: E402
from src.data.ecmwf_aifs_sampled_2t_localday import LOW_DATA_VERSION as AIFS_LOW_DATA_VERSION  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_anchor import LOW_DATA_VERSION as OPENMETEO_LOW_DATA_VERSION  # noqa: E402
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest, write_manifest_to_db  # noqa: E402
from src.state.db import _connect  # noqa: E402
from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema  # noqa: E402


OPENMETEO_RE = re.compile(r"^(?P<city>.+)_(?P<stamp>20\d{6}T\d{2})Z\.json$")
METRIC_TO_AIFS_VERSION = {"high": AIFS_HIGH_DATA_VERSION, "low": AIFS_LOW_DATA_VERSION}
METRIC_TO_OPENMETEO_VERSION = {"high": OPENMETEO_HIGH_DATA_VERSION, "low": OPENMETEO_LOW_DATA_VERSION}


def _to_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"datetime must be timezone-aware: {value!r}")
    return parsed.astimezone(UTC)


def _file_captured_at(path: Path, *, available_at: datetime) -> datetime:
    captured = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return max(captured, available_at)


def _filename_city(city: str) -> str:
    return str(city).replace(" ", "_").replace("/", "_")


def _raw_roots(eval_json: Path, explicit_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    candidates: list[Path] = list(explicit_roots)
    for parent in (eval_json.parent, *eval_json.parents):
        local = parent / ".local" / "replacement_raw"
        if local.exists():
            candidates.append(local)
    sibling = ROOT.parent / "zeus-ecmwf-replacement-tournament" / ".local" / "replacement_raw"
    if sibling.exists():
        candidates.append(sibling)
    own = ROOT / ".local" / "replacement_raw"
    if own.exists():
        candidates.append(own)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(candidate.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(candidate)
    return tuple(unique)


def _decision_time(target_date: str, *, cutoff_hour_utc: int) -> datetime:
    from datetime import date, time

    target = date.fromisoformat(target_date)
    return datetime.combine(target - timedelta(days=1), time(cutoff_hour_utc, 0), tzinfo=UTC)


def _source_available_at(run_time: datetime, *, release_lag_hours: float) -> datetime:
    return run_time + timedelta(hours=release_lag_hours)


def _latest_run_before(runs: tuple[datetime, ...], decision_time: datetime, *, release_lag_hours: float) -> datetime | None:
    eligible = [run for run in runs if _source_available_at(run, release_lag_hours=release_lag_hours) <= decision_time]
    return max(eligible) if eligible else None


def _load_eval_rows(path: Path) -> tuple[Mapping[str, Any], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("eval JSON must contain rows[]")
    return tuple(row for row in rows if isinstance(row, Mapping))


def _aifs_downloads(payload: Mapping[str, Any], *, eval_json: Path, raw_roots: tuple[Path, ...]) -> dict[datetime, Path]:
    out: dict[datetime, Path] = {}
    for item in payload.get("downloads", []):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("type") or "").lower() != "pf":
            continue
        path_text = str(item.get("path") or "")
        if "aifs" not in path_text.lower():
            continue
        run = _to_utc(str(item.get("run") or ""))
        candidates = []
        raw_path = Path(path_text)
        if raw_path.is_absolute():
            candidates.append(raw_path)
        candidates.append(eval_json.parent / raw_path)
        for root in raw_roots:
            candidates.append(root / raw_path.name)
            candidates.append(root / "aifs_jun3_jun5_preday" / raw_path.name)
        for candidate in candidates:
            if candidate.exists():
                out[run] = candidate
                break
    return out


def _openmeteo_files(raw_roots: tuple[Path, ...]) -> dict[tuple[str, datetime], Path]:
    out: dict[tuple[str, datetime], Path] = {}
    for root in raw_roots:
        for path in root.glob("openmeteo*/**/*.json"):
            match = OPENMETEO_RE.match(path.name)
            if match is None:
                continue
            run = datetime.strptime(match.group("stamp"), "%Y%m%dT%H").replace(tzinfo=UTC)
            out[(match.group("city"), run)] = path
    return out


def _manifest(
    artifact: Path,
    *,
    source_id: str,
    product_id: str,
    data_version: str,
    source_cycle_time: datetime,
    source_available_at: datetime,
    request_url: str,
    request_params: Mapping[str, Any],
    product_metadata: Mapping[str, Any],
) -> RawForecastArtifactManifest:
    return RawForecastArtifactManifest.from_file(
        artifact,
        source_id=source_id,
        product_id=product_id,
        data_version=data_version,
        source_cycle_time=source_cycle_time.isoformat(),
        source_available_at=source_available_at.isoformat(),
        captured_at=_file_captured_at(artifact, available_at=source_available_at).isoformat(),
        request_url=request_url,
        request_params=request_params,
        product_metadata=product_metadata,
    )


def stage_downloaded_replacement_eval_raw_manifests(
    *,
    eval_json: Path,
    output_dir: Path,
    raw_roots: tuple[Path, ...] = (),
    forecast_db: Path | None = None,
    write_db: bool = False,
    decision_cutoff_hour_utc: int = 8,
    release_lag_hours: float = 1.0,
) -> dict[str, object]:
    payload = json.loads(eval_json.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("eval JSON must decode to an object")
    rows = _load_eval_rows(eval_json)
    roots = _raw_roots(eval_json, raw_roots)
    aifs_by_run = _aifs_downloads(payload, eval_json=eval_json, raw_roots=roots)
    openmeteo_by_city_run = _openmeteo_files(roots)
    output_dir.mkdir(parents=True, exist_ok=True)

    aifs_scope: dict[tuple[str, datetime], dict[str, set[str]]] = defaultdict(lambda: {"cities": set(), "target_dates": set()})
    openmeteo_scope: dict[tuple[str, str, datetime], dict[str, str]] = {}
    skipped: list[dict[str, object]] = []
    for row in rows:
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        metric = str(row.get("metric") or "")
        if metric not in {"high", "low"} or not city or not target_date:
            continue
        decision = _decision_time(target_date, cutoff_hour_utc=decision_cutoff_hour_utc)
        aifs_run = _latest_run_before(tuple(aifs_by_run), decision, release_lag_hours=release_lag_hours)
        if aifs_run is None:
            skipped.append({"city": city, "target_date": target_date, "metric": metric, "reason": "AIFS_RAW_RUN_NOT_FOUND"})
        else:
            key = (metric, aifs_run)
            aifs_scope[key]["cities"].add(city)
            aifs_scope[key]["target_dates"].add(target_date)
        om_city = _filename_city(city)
        om_runs = tuple(run for indexed_city, run in openmeteo_by_city_run if indexed_city == om_city)
        om_run = _latest_run_before(om_runs, decision, release_lag_hours=release_lag_hours)
        if om_run is None:
            skipped.append({"city": city, "target_date": target_date, "metric": metric, "reason": "OPENMETEO_RAW_RUN_NOT_FOUND"})
        else:
            openmeteo_scope[(metric, om_city, om_run)] = {"city": city, "target_date": target_date}

    manifests: list[RawForecastArtifactManifest] = []
    for (metric, run), scope in sorted(aifs_scope.items(), key=lambda item: (item[0][0], item[0][1])):
        artifact = aifs_by_run[run]
        source_available = _source_available_at(run, release_lag_hours=release_lag_hours)
        manifests.append(
            _manifest(
                artifact,
                source_id="ecmwf_aifs_ens",
                product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
                data_version=METRIC_TO_AIFS_VERSION[metric],
                source_cycle_time=run,
                source_available_at=source_available,
                request_url="ecmwf-opendata://aifs-ens/2t/downloaded-eval",
                request_params={"model": "aifs-ens", "param": "2t", "type": "pf", "run": run.isoformat(), "metric": metric},
                product_metadata={
                    "artifact_class": "aifs_sampled_2t_grib_downloaded_eval",
                    "cities": sorted(scope["cities"]),
                    "target_dates": sorted(scope["target_dates"]),
                    "metric": metric,
                    "source_run_id": f"aifs-downloaded-eval-{metric}-{run.strftime('%Y%m%dT%H%M%SZ')}",
                },
            )
        )
    for (metric, om_city, run), scope in sorted(openmeteo_scope.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        artifact = openmeteo_by_city_run[(om_city, run)]
        source_available = _source_available_at(run, release_lag_hours=release_lag_hours)
        city = scope["city"]
        target_date = scope["target_date"]
        manifests.append(
            _manifest(
                artifact,
                source_id="openmeteo_ecmwf_ifs_9km",
                product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                data_version=METRIC_TO_OPENMETEO_VERSION[metric],
                source_cycle_time=run,
                source_available_at=source_available,
                request_url="https://single-runs-api.open-meteo.com/v1/forecast",
                request_params={"models": "ecmwf_ifs", "run": run.isoformat(), "metric": metric},
                product_metadata={
                    "artifact_class": "openmeteo_ecmwf_ifs9_anchor_downloaded_eval",
                    "city": city,
                    "cities": [city],
                    "target_date": target_date,
                    "target_dates": [target_date],
                    "metric": metric,
                    "source_run_id": f"openmeteo-downloaded-eval-{_filename_city(city)}-{metric}-{run.strftime('%Y%m%dT%H%M%SZ')}",
                    "openmeteo_payload_json": artifact.name,
                },
            )
        )

    written_paths: list[str] = []
    db_artifact_ids: list[int] = []
    conn: sqlite3.Connection | None = None
    if write_db:
        if forecast_db is None:
            raise ValueError("forecast_db is required with write_db")
        conn = _connect(forecast_db, write_class="live")
        ensure_replacement_forecast_shadow_schema(conn)
        conn.execute("BEGIN")
    try:
        for manifest in manifests:
            safe_name = (
                f"{manifest.source_id}.{manifest.data_version}."
                f"{manifest.source_cycle_time.strftime('%Y%m%dT%H%M%SZ')}.{manifest.sha256[:12]}.manifest.json"
            )
            path = output_dir / safe_name
            write_manifest(manifest, path)
            written_paths.append(str(path))
            if conn is not None:
                db_artifact_ids.append(write_manifest_to_db(conn, manifest, verify_artifact=True))
        if conn is not None:
            conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()

    return {
        "status": "DOWNLOADED_REPLACEMENT_EVAL_RAW_MANIFESTS_STAGED",
        "eval_json": str(eval_json),
        "raw_roots": [str(root) for root in roots],
        "output_dir": str(output_dir),
        "manifest_count": len(manifests),
        "written_manifests": written_paths,
        "write_db": write_db,
        "db_artifact_ids": db_artifact_ids,
        "skipped_count": len(skipped),
        "skipped": skipped[:50],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage downloaded replacement eval raw manifests")
    parser.add_argument("--eval-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, action="append", default=[])
    parser.add_argument("--forecast-db", type=Path)
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--decision-cutoff-hour-utc", type=int, default=8)
    parser.add_argument("--release-lag-hours", type=float, default=1.0)
    parser.add_argument("--receipt-json", type=Path)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = stage_downloaded_replacement_eval_raw_manifests(
            eval_json=args.eval_json,
            output_dir=args.output_dir,
            raw_roots=tuple(args.raw_root),
            forecast_db=args.forecast_db,
            write_db=bool(args.write_db),
            decision_cutoff_hour_utc=args.decision_cutoff_hour_utc,
            release_lag_hours=args.release_lag_hours,
        )
        if args.receipt_json is not None:
            args.receipt_json.parent.mkdir(parents=True, exist_ok=True)
            args.receipt_json.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(receipt, sort_keys=True))
    else:
        print(receipt["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
