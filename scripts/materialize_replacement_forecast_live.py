#!/usr/bin/env python3
# Lifecycle: created=2026-06-06; last_reviewed=2026-07-17; last_reused=2026-07-17
# Purpose: Materialize replacement live forecast posteriors and publish commit wakes.
# Reuse: Inspect forecast materialization and reactor-wake contracts before changing.
"""Materialize Open-Meteo ECMWF IFS 9km + Bayes fusion posterior."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: E402
    build_anchor_request,
    extract_openmeteo_ecmwf_ifs9_localday_anchor,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload,
)
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (  # noqa: E402
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_materializer import (  # noqa: E402
    ReplacementForecastMaterializeRequest,
    materialize_replacement_forecast_live,
)
from src.data.raw_forecast_artifact_manifest import read_manifest, write_manifest_to_db  # noqa: E402


UTC = timezone.utc


@dataclass(frozen=True)
class TemperatureBin:
    bin_id: str
    lower_c: float | None
    upper_c: float | None
    center_c: float | None
    display_unit: str = "C"
    settlement_unit: str = "C"
    rounding_rule: str = "wmo_half_up"


def _dt(value: str, *, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_input_path(path_value: object, *, base_dir: Path) -> Path:
    path = Path(str(path_value))
    if path.is_absolute():
        return path
    candidates = [base_dir / path, ROOT / path, Path.cwd() / path]
    if len(path.parts) >= 2 and path.parts[0] == ".." and path.parts[1] == "raw_manifests":
        candidates.append(ROOT / "state" / "replacement_forecast_live" / Path(*path.parts[1:]))
    candidates.append(ROOT / "state" / "replacement_forecast_live" / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    base_candidate = candidates[0]
    return base_candidate


def _bins(payload: Mapping[str, Any]) -> tuple[TemperatureBin, ...]:
    rows = payload.get("bins")
    if not isinstance(rows, list) or not rows:
        raise ValueError("input JSON must contain non-empty bins[]")
    bins: list[TemperatureBin] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("bins[] entries must be objects")
        bins.append(
            TemperatureBin(
                bin_id=str(row["bin_id"]),
                lower_c=None if row.get("lower_c") is None else float(row["lower_c"]),
                upper_c=None if row.get("upper_c") is None else float(row["upper_c"]),
                center_c=None if row.get("center_c") is None else float(row["center_c"]),
                display_unit=str(row.get("display_unit") or "C").strip().upper(),  # type: ignore[arg-type]
                settlement_unit=str(row.get("settlement_unit") or "C").strip().upper(),  # type: ignore[arg-type]
                rounding_rule=str(row.get("rounding_rule") or "wmo_half_up").strip(),  # type: ignore[arg-type]
            )
        )
    return tuple(bins)


def _template() -> dict[str, object]:
    return {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "anchor_weight": 0.80,
        "anchor_sigma_c": 3.00,
        "bins": [
            {"bin_id": "cool", "lower_c": None, "upper_c": 20.0, "center_c": 19.0},
            {"bin_id": "warm", "lower_c": 21.0, "upper_c": 30.0, "center_c": 25.5},
            {"bin_id": "hot", "lower_c": 31.0, "upper_c": None, "center_c": 32.0},
        ],
        "openmeteo_payload_json": "openmeteo_payload.json",
        "precision_metadata_json": "openmeteo_precision_metadata.json",
        "latitude": 31.2304,
        "longitude": 121.4737,
    }


def _publish_materialization_wake(
    request: ReplacementForecastMaterializeRequest,
) -> bool:
    """Wake the reactor immediately after this family's durable commit."""
    try:
        from src.runtime.reactor_wake import publish_reactor_wake

        wake = publish_reactor_wake(
            source="replacement_forecast_materializer",
            reason="forecast_posterior_advanced",
            forecast_families=(
                (
                    request.city,
                    request.target_date.isoformat(),
                    request.temperature_metric,
                ),
            ),
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "forecast posterior committed but per-family reactor wake failed",
            exc_info=True,
        )
        return False
    logging.getLogger(__name__).info(
        "forecast posterior family wake published city=%s date=%s metric=%s id=%s",
        request.city,
        request.target_date,
        request.temperature_metric,
        wake.wake_id,
    )
    return True


def _materialize(
    input_json: Path,
    *,
    commit: bool,
    init_schema: bool,
    conn=None,
    publish_wake: bool = True,
) -> tuple[int, dict[str, object]]:
    payload = _load_json(input_json)
    if not isinstance(payload, Mapping):
        raise ValueError("input JSON must decode to an object")
    base_dir = input_json.parent
    metric = str(payload["temperature_metric"])
    target_date = date.fromisoformat(str(payload["target_date"]))
    source_cycle_time = _dt(str(payload["source_cycle_time"]), field_name="source_cycle_time")
    anchor_artifact_id = (
        None
        if payload.get("openmeteo_anchor_artifact_id") in (None, "")
        else int(payload["openmeteo_anchor_artifact_id"])
    )
    if "openmeteo_payload_json" in payload:
        openmeteo_payload = _load_json(
            _resolve_input_path(payload["openmeteo_payload_json"], base_dir=base_dir)
        )
        if not isinstance(openmeteo_payload, Mapping):
            raise ValueError("Open-Meteo payload JSON must decode to an object")
    else:
        if "latitude" not in payload or "longitude" not in payload:
            raise ValueError("Open-Meteo direct fetch requires latitude and longitude")
        openmeteo_payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(
            build_anchor_request(
                latitude=float(payload["latitude"]),
                longitude=float(payload["longitude"]),
                run=source_cycle_time,
                timezone_name=str(payload["city_timezone"]),
            )
        )
    openmeteo_anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
        openmeteo_payload,
        city_timezone=str(payload["city_timezone"]),
        target_local_date=target_date,
        source_cycle_time=source_cycle_time,
    )
    if "precision_metadata_json" not in payload:
        raise ValueError(
            "input JSON requires precision_metadata_json for Open-Meteo ECMWF IFS 9km anchor"
        )
    precision_payload = _load_json(
        _resolve_input_path(payload["precision_metadata_json"], base_dir=base_dir)
    )
    if not isinstance(precision_payload, Mapping):
        raise ValueError("precision_metadata_json must decode to an object")
    precision_guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(**dict(precision_payload))
    )
    request = ReplacementForecastMaterializeRequest(
        city=str(payload["city"]),
        city_id=str(payload.get("city_id") or payload["city"]),
        city_timezone=str(payload["city_timezone"]),
        target_date=target_date,
        temperature_metric=metric,
        baseline_source_run_id=str(payload["baseline_source_run_id"]),
        baseline_data_version=str(payload["baseline_data_version"]),
        baseline_source_available_at=_dt(
            str(payload["baseline_source_available_at"]),
            field_name="baseline_source_available_at",
        ),
        openmeteo_anchor=openmeteo_anchor,
        openmeteo_source_run_id=str(payload.get("openmeteo_source_run_id") or ""),
        openmeteo_source_available_at=_dt(
            str(payload["openmeteo_source_available_at"]),
            field_name="openmeteo_source_available_at",
        ),
        bins=_bins(payload),
        source_cycle_time=source_cycle_time,
        computed_at=_dt(str(payload["computed_at"]), field_name="computed_at"),
        expires_at=(
            None
            if payload.get("expires_at") is None
            else _dt(str(payload["expires_at"]), field_name="expires_at")
        ),
        openmeteo_precision_guard=precision_guard,
        anchor_weight=float(payload.get("anchor_weight", 0.80)),
        anchor_sigma_c=float(payload.get("anchor_sigma_c", 3.00)),
        settlement_step_c=float(payload.get("settlement_step_c", 1.0)),
        day0_observed_extreme_c=(
            None
            if payload.get("day0_observed_extreme_c") in (None, "")
            else float(payload["day0_observed_extreme_c"])
        ),
        day0_observed_extreme_source=(
            None
            if payload.get("day0_observed_extreme_source") in (None, "")
            else str(payload["day0_observed_extreme_source"])
        ),
        day0_observed_extreme_observation_time=(
            None
            if payload.get("day0_observed_extreme_observation_time") in (None, "")
            else str(payload["day0_observed_extreme_observation_time"])
        ),
        day0_observed_extreme_sample_count=(
            None
            if payload.get("day0_observed_extreme_sample_count") in (None, "")
            else int(payload["day0_observed_extreme_sample_count"])
        ),
        day0_observed_extreme_unit=(
            None
            if payload.get("day0_observed_extreme_unit") in (None, "")
            else str(payload["day0_observed_extreme_unit"])
        ),
        upgrade_trigger=(
            str(payload["upgrade_trigger"]) if payload.get("upgrade_trigger") else None
        ),
    )
    own_conn = conn is None
    wake_published = False
    if own_conn:
        from src.state.db import get_forecasts_connection

        conn = get_forecasts_connection(write_class="live")
    try:
        # BEGIN IMMEDIATE (not deferred): this is a WRITE transaction (manifests +
        # posteriors). zeus-forecasts.db runs in rollback-journal (delete) mode, so a
        # deferred BEGIN takes a SHARED lock on the first SELECT and then tries to
        # upgrade to EXCLUSIVE on the first INSERT. Taking the write lock up front
        # makes busy_timeout effective while readers remain unaffected.
        conn.execute("BEGIN IMMEDIATE")
        if init_schema:
            from src.state.db import _create_readiness_state
            from src.state.schema.v2_schema import (
                ensure_replacement_forecast_live_schema,
            )

            ensure_replacement_forecast_live_schema(conn)
            _create_readiness_state(conn)
        if "openmeteo_manifest_json" in payload:
            anchor_artifact_id = write_manifest_to_db(
                conn,
                read_manifest(
                    _resolve_input_path(
                        payload["openmeteo_manifest_json"],
                        base_dir=base_dir,
                    )
                ),
                root=ROOT,
            )
        if anchor_artifact_id is not None:
            request = replace(request, anchor_artifact_id=anchor_artifact_id)
        result = materialize_replacement_forecast_live(conn, request)
        if commit:
            conn.commit()
            if result.ok and publish_wake:
                wake_published = _publish_materialization_wake(request)
        else:
            conn.rollback()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()
    response = {
        "status": result.status,
        "reason_codes": list(result.reason_codes),
        "posterior_id": result.posterior_id,
        "anchor_id": result.anchor_id,
        "readiness_id": result.readiness_id,
        "openmeteo_anchor_artifact_id": anchor_artifact_id,
        "committed": commit,
        "reactor_wake_published": wake_published,
        "forecast_family": [
            request.city,
            request.target_date.isoformat(),
            request.temperature_metric,
        ],
    }
    return (0 if result.ok else 1), response


def _run_one(
    input_json: Path,
    *,
    commit: bool,
    init_schema: bool,
    conn=None,
    capture_logs: bool = False,
    publish_wake: bool = True,
) -> tuple[int, str, str]:
    log_output = StringIO()
    handler: logging.Handler | None = None
    if capture_logs:
        handler = logging.StreamHandler(log_output)
        handler.setLevel(logging.WARNING)
        logging.getLogger().addHandler(handler)
    try:
        returncode, response = _materialize(
            input_json,
            commit=commit,
            init_schema=init_schema,
            conn=conn,
            publish_wake=publish_wake,
        )
        return returncode, json.dumps(response, sort_keys=True) + "\n", log_output.getvalue()
    except Exception as exc:
        error = {
            "status": "ERROR",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }
        return 2, "", log_output.getvalue() + json.dumps(error, sort_keys=True) + "\n"
    finally:
        if handler is not None:
            logging.getLogger().removeHandler(handler)


def _print_batch_envelope(
    input_json: Path,
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    print(
        json.dumps(
            {
                "input_json": str(input_json),
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize replacement forecast live posterior"
    )
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--input-json", type=Path, help="Materialization request JSON")
    inputs.add_argument(
        "--batch-input-json",
        type=Path,
        nargs="+",
        help="Materialization requests processed in one process with per-request transactions",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit DB writes; default is dry-run rollback",
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="Idempotently initialize forecast/readiness tables before materializing",
    )
    parser.add_argument("--print-template", action="store_true")
    args = parser.parse_args(argv)
    if args.print_template:
        print(json.dumps(_template(), sort_keys=True, indent=2))
        return 0
    if args.input_json is None and not args.batch_input_json:
        parser.error(
            "--input-json or --batch-input-json is required unless --print-template is set"
        )
    if args.batch_input_json:
        from src.state.db import get_forecasts_connection

        conn = get_forecasts_connection(write_class="live")
        try:
            for index, input_json in enumerate(args.batch_input_json):
                returncode, stdout, stderr = _run_one(
                    input_json,
                    commit=args.commit,
                    init_schema=args.init_schema and index == 0,
                    conn=conn,
                    capture_logs=True,
                    publish_wake=True,
                )
                _print_batch_envelope(input_json, returncode, stdout, stderr)
        finally:
            conn.close()
        return 0
    returncode, stdout, stderr = _run_one(
        args.input_json,
        commit=args.commit,
        init_schema=args.init_schema,
    )
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
