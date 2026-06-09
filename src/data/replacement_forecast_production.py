# Created: 2026-06-08
# Last reused/audited: 2026-06-08
# Authority basis: operator Point-1 directive 2026-06-08 — move U0R/replacement_0_1
#   forecast PRODUCTION (raw-input download + light shadow materialization) OFF the
#   live-trading daemon (src/main.py) INTO the forecast-live (data) daemon. The
#   ~365MB AIFS ensemble download (~11.5 min) monopolized disk I/O on the trading
#   process, starving the reactor + market_scanner and locking riskguard dependency
#   reads -> DATA_DEGRADED flap that blocked all trades. The weeks-stable baseline
#   ran forecast production in a SEPARATE daemon; this module restores that split.
"""Shared replacement-forecast PRODUCTION functions (raw-input download +
light shadow materialization).

These 6 functions were moved VERBATIM out of ``src/main.py`` so the heavy AIFS
ensemble download no longer runs inside the live-trading process. They are now
imported by BOTH ``src/main.py`` (for back-compat name resolution + the in-cycle
runtime-flags read) AND ``src/ingest/forecast_live_daemon.py`` (which actually
SCHEDULES the download + materialize jobs on the data daemon's lane).

Behavior, logging, gating, and fail-soft semantics are preserved exactly. The
download is a SEPARATE function/job from the materialize cycle; the materialize
cycle is LIGHT (seed_discovery -> seed -> materialize on already-downloaded
manifests only — it never downloads).
"""

from __future__ import annotations

import functools
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import settings

logger = logging.getLogger("zeus.replacement_forecast_production")


def _settings_section(name: str, default=None):
    source = settings._data if hasattr(settings, "_data") else settings
    if isinstance(source, dict):
        return source.get(name, default)
    try:
        return source[name]
    except KeyError:
        return default


def _scheduler_job(job_name: str):
    """Decorator: mirror of src/main.py's scheduler-health wrapper (B047).

    Wraps fn so success -> ``scheduler_jobs_health.json[job_name].status = OK``
    and exception -> logged with traceback + ``status = FAILED``. Never re-raises
    (fail-open per K2 design). Preserved here verbatim so the moved
    ``_replacement_forecast_download_cycle`` keeps its identical wrapping (and its
    ``.__wrapped__`` accessor) after the relocation.
    """

    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            from src.observability.scheduler_health import _write_scheduler_health

            try:
                result = fn(*args, **kwargs)
                _write_scheduler_health(job_name, failed=False)
                return result
            except Exception as exc:
                logger.error("%s failed: %s", job_name, exc, exc_info=True)
                _write_scheduler_health(job_name, failed=True, reason=str(exc))

        return _wrapper

    return _decorator


def _replacement_forecast_runtime_flags_from_settings() -> dict[str, bool]:
    from src.data.replacement_forecast_runtime_policy import REQUIRED_FLAGS

    try:
        flags = settings["feature_flags"]
    except Exception:
        flags = {}
    return {key: bool(flags.get(key, False)) for key in REQUIRED_FLAGS}


def _replacement_forecast_shadow_materialization_queue_config() -> dict[str, object]:
    from src.config import PROJECT_ROOT

    cfg = _settings_section("replacement_forecast_shadow", {}) or {}
    base_dir = PROJECT_ROOT / "state" / "replacement_forecast_shadow"
    raw_manifest_dir = cfg.get("raw_manifest_dir")
    forecast_db = cfg.get("forecast_db")

    def _rooted_path(value, fallback: Path | None = None) -> Path | None:
        raw = value if value not in (None, "") else fallback
        if raw in (None, ""):
            return None
        path = Path(str(raw))
        return path if path.is_absolute() else PROJECT_ROOT / path

    return {
        "seed_dir": _rooted_path(cfg.get("seed_dir"), base_dir / "seeds"),
        "seed_processed_dir": _rooted_path(cfg.get("seed_processed_dir"), base_dir / "seed_processed"),
        "seed_failed_dir": _rooted_path(cfg.get("seed_failed_dir"), base_dir / "seed_failed"),
        "forecast_db": _rooted_path(forecast_db),
        "raw_manifest_dir": _rooted_path(raw_manifest_dir),
        "seed_discovery_limit": int(cfg.get("seed_discovery_limit_per_cycle") or cfg.get("seed_limit_per_cycle") or cfg.get("materialization_limit_per_cycle") or 10),
        "request_dir": _rooted_path(cfg.get("request_dir"), base_dir / "requests"),
        "processed_dir": _rooted_path(cfg.get("processed_dir"), base_dir / "processed"),
        "failed_dir": _rooted_path(cfg.get("failed_dir"), base_dir / "failed"),
        "seed_limit": int(cfg.get("seed_limit_per_cycle") or cfg.get("materialization_limit_per_cycle") or 10),
        "limit": int(cfg.get("materialization_limit_per_cycle") or 10),
        "download_current_targets_enabled": bool(cfg.get("download_current_targets_enabled", False)),
        "download_output_dir": _rooted_path(cfg.get("download_output_dir"), _rooted_path(raw_manifest_dir, base_dir / "raw_manifests")),
        "download_limit": int(cfg.get("download_limit_per_cycle") or cfg.get("seed_discovery_limit_per_cycle") or cfg.get("materialization_limit_per_cycle") or 10),
        "download_release_lag_hours": float(cfg.get("download_release_lag_hours") or 14.0),
        "download_anchor_sigma_c": float(cfg.get("download_anchor_sigma_c") or 3.0),
        "download_aifs_retries": int(cfg.get("download_aifs_retries") or 4),
    }


# The two raw-artifact sources this downloader owns. The cycle high-water mark is the MIN over
# BOTH of MAX(source_cycle_time): a half-downloaded cycle (one source lagging) is NOT current.
_CURRENT_TARGET_ARTIFACT_SOURCE_IDS = ("ecmwf_aifs_ens", "openmeteo_ecmwf_ifs_9km")


def _max_downloaded_current_target_cycle(forecast_db: Path) -> datetime | None:
    """High-water mark of downloaded current-target raw-input cycles, or None when unknown.

    None (no rows for either source, or any read error) means "cannot prove currency" ->
    the caller treats the cycle as stale and fires the idempotent download. The currency
    check must FAIL OPEN toward downloading; it must never freeze freshness.
    """
    from src.state.db import _connect  # noqa: PLC0415

    try:
        conn = _connect(Path(forecast_db))
        try:
            maxes: list[datetime] = []
            for sid in _CURRENT_TARGET_ARTIFACT_SOURCE_IDS:
                row = conn.execute(
                    "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts"
                    " WHERE source_id = ?",
                    (sid,),
                ).fetchone()
                if row is None or row[0] is None:
                    return None
                maxes.append(
                    datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                )
            return min(maxes)
        finally:
            conn.close()
    except Exception:
        return None


def _download_replacement_forecast_current_targets_if_needed(cfg: dict[str, object]) -> dict[str, object] | None:
    if not bool(cfg.get("download_current_targets_enabled", False)):
        return None
    forecast_db = cfg.get("forecast_db")
    output_dir = cfg.get("download_output_dir") or cfg.get("raw_manifest_dir")
    if forecast_db is None or output_dir is None:
        raise ValueError("replacement current-target download requires forecast_db and raw_manifest_dir/download_output_dir")
    from scripts.download_replacement_forecast_current_targets import (
        _parse_cycle,
        download_current_target_raw_inputs,
    )
    from src.data.replacement_forecast_current_target_plan import (
        build_replacement_forecast_current_target_plan,
    )

    # CYCLE-CURRENCY ANTIBODY (2026-06-09): coverage ("a posterior exists for every target")
    # NEVER implies currency ("the currently-available IFS cycle's raw inputs exist"). The old
    # gates short-circuited on plan.ready alone, so once ANY cycle fully materialized the cron
    # could never advance the anchor again — deterministic_forecast_anchors froze at 06-08T18
    # for ~24h while Open-Meteo was serving 06-09T00 (it answered 200 OK to the U0R leg of the
    # SAME job run). Both early returns now additionally require the downloaded high-water mark
    # to have reached the currently-available cycle.
    release_lag_hours = float(cfg.get("download_release_lag_hours") or 14.0)
    available_cycle = _parse_cycle(
        None, now=datetime.now(timezone.utc), release_lag_hours=release_lag_hours
    )
    downloaded_cycle = _max_downloaded_current_target_cycle(Path(str(forecast_db)))
    cycle_is_current = downloaded_cycle is not None and downloaded_cycle >= available_cycle

    plan = build_replacement_forecast_current_target_plan(Path(str(forecast_db)))
    if plan.ready and cycle_is_current:
        return {
            "status": "CURRENT_TARGETS_ALREADY_COVERED",
            "coverage": plan.as_dict(),
            "available_cycle": available_cycle.isoformat(),
            "downloaded_cycle": downloaded_cycle.isoformat(),
        }
    if (
        plan.missing_aifs_manifest_count <= 0
        and plan.missing_openmeteo_manifest_count <= 0
        and cycle_is_current
    ):
        return {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "coverage": plan.as_dict(),
            "available_cycle": available_cycle.isoformat(),
            "downloaded_cycle": downloaded_cycle.isoformat(),
        }
    cycle = available_cycle
    return download_current_target_raw_inputs(
        forecast_db=Path(str(forecast_db)),
        output_dir=Path(str(output_dir)),
        cycle=cycle,
        limit=int(cfg.get("download_limit") or 10),
        write_db=True,
        skip_aifs=False,
        skip_openmeteo=False,
        release_lag_hours=release_lag_hours,
        anchor_sigma_c=float(cfg.get("download_anchor_sigma_c") or 3.0),
        aifs_retries=int(cfg.get("download_aifs_retries") or 4),
    )


def _download_u0r_extra_raw_inputs_if_needed(cfg: dict[str, object]) -> dict[str, object] | None:
    """THE_PATH U0R-Bayes multi-model SHADOW capture/accrual (CONTINUITY_AND_WIRING.md §4 step 2,
    U0R_BAYES_SPEC.md §6 F1). Gated by the NEW capture flag
    ``settings['edli_v1']['replacement_0_1_u0r_multimodel_capture_enabled']`` (default FALSE),
    SEPARATE from replacement_0_1_u0r_fusion_enabled: when ON it downloads + persists the 8 extra
    OM models (single_runs FORWARD + previous_runs fixed-lead) into raw_model_forecasts on
    zeus-forecasts.db. It writes NOTHING into forecast_posteriors and touches NO posterior/q/
    center/spread/order -> the money path is byte-identical whether or not this runs. Forward,
    daily, fail-soft (it NEVER raises into the shadow cycle). Returns None when the flag is OFF or
    there is no forecast_db / no targets."""
    try:
        if not bool(settings["edli_v1"].get("replacement_0_1_u0r_multimodel_capture_enabled", False)):
            return None
    except Exception:
        return None
    forecast_db = cfg.get("forecast_db")
    if forecast_db is None:
        return None
    try:
        from datetime import date, datetime as _dt, timezone as _tz  # noqa: PLC0415

        from scripts.download_replacement_forecast_current_targets import _parse_cycle  # noqa: PLC0415
        from src.config import cities_by_name  # noqa: PLC0415
        from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
            build_replacement_forecast_current_target_plan,
        )
        from src.data.u0r_multimodel_download import (  # noqa: PLC0415
            U0RDownloadTarget,
            download_u0r_extra_raw_inputs,
        )

        release_lag_hours = float(cfg.get("download_release_lag_hours") or 14.0)
        cycle = _parse_cycle(None, now=_dt.now(_tz.utc), release_lag_hours=release_lag_hours)

        plan = build_replacement_forecast_current_target_plan(Path(str(forecast_db)))
        targets: list[U0RDownloadTarget] = []
        for row in plan.rows:
            if row.covered:
                continue
            city_cfg = cities_by_name.get(row.city)
            if city_cfg is None:
                continue
            try:
                lead_days = max(0, (date.fromisoformat(row.target_date) - cycle.date()).days)
            except Exception:
                lead_days = 0
            targets.append(U0RDownloadTarget(
                city=row.city, metric=row.temperature_metric, target_date=row.target_date,
                lead_days=lead_days, latitude=float(city_cfg.lat), longitude=float(city_cfg.lon),
                timezone_name=str(city_cfg.timezone),
            ))
        if not targets:
            return {"status": "U0R_EXTRA_NO_TARGETS"}
        return download_u0r_extra_raw_inputs(
            forecast_db=Path(str(forecast_db)),
            cycle=cycle,
            targets=targets,
            release_lag_hours=release_lag_hours,
        )
    except Exception as exc:  # noqa: BLE001 - fail-soft: shadow accrual never breaks the cycle
        logger.warning("U0R extra-model shadow capture skipped (fail-soft): %s", exc)
        return {"status": "U0R_EXTRA_CAPTURE_FAILSOFT_SKIPPED", "error": str(exc)}


@_scheduler_job("replacement_forecast_shadow_materialize")
def _replacement_forecast_download_cycle() -> None:
    """Proactive raw-input PRE-FETCH for the U0R/replacement soft-anchor forecast.

    Operator directive 2026-06-08 (WIRING FIX): the 150-300MB AIFS-ensemble +
    OpenMeteo raw-input downloads MUST NOT run inside the 5-min seed->materialize
    cycle. At ~500kB/s a single AIFS-ensemble fetch takes 5-10 min, so when it ran
    inline the materialize job overran its 5-min interval and apscheduler SKIPPED
    every subsequent cycle ("maximum number of running instances reached") — seeds
    never got produced and readiness went permanently stale. Raw inputs are DATA
    and must be fetched ahead of need on a slower, independent lane; the trade-
    producing materialize cycle then only consumes already-downloaded manifests.

    Runs on the default executor (20-worker pool) on its own long interval, so it
    overlaps the fast materialize cycle on a separate thread without blocking it.
    Fail-soft and idempotent (skips already-downloaded manifests)."""
    flags = _replacement_forecast_runtime_flags_from_settings()
    if not bool(flags.get("openmeteo_ecmwf_ifs9_aifs_soft_anchor_shadow_enabled", False)):
        return
    cfg = _replacement_forecast_shadow_materialization_queue_config()
    download_report = _download_replacement_forecast_current_targets_if_needed(cfg)
    if download_report is not None:
        _dl_status = download_report.get("status")
        if _dl_status in {
            "CURRENT_TARGETS_ALREADY_COVERED",
            "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
        }:
            # ANTI-SILENT-SKIP (2026-06-09): the suppressed skip is what made the frozen-anchor
            # failure invisible for 24h. A skip must self-declare its cycle facts (compact, the
            # download job runs ~2x/day so this is cheap).
            logger.info(
                "replacement current-target download skipped (%s): available_cycle=%s "
                "downloaded_cycle=%s",
                _dl_status,
                download_report.get("available_cycle"),
                download_report.get("downloaded_cycle"),
            )
        else:
            logger.info(
                "replacement forecast current-target download report: %s", download_report
            )
    # THE_PATH U0R-Bayes multi-model SHADOW capture/accrual (forward + fixed-lead), gated by the
    # SEPARATE replacement_0_1_u0r_multimodel_capture_enabled flag. Pure side-effect on
    # raw_model_forecasts (zeus-forecasts.db); NO posterior/q/order effect. Fail-soft.
    u0r_capture_report = _download_u0r_extra_raw_inputs_if_needed(cfg)
    if u0r_capture_report is not None and u0r_capture_report.get("status") not in {
        "U0R_EXTRA_NO_TARGETS",
    }:
        logger.info("U0R extra-model shadow capture report: %s", u0r_capture_report)


def _replacement_forecast_shadow_materialize_cycle() -> None:
    flags = _replacement_forecast_runtime_flags_from_settings()
    if not bool(flags.get("openmeteo_ecmwf_ifs9_aifs_soft_anchor_shadow_enabled", False)):
        return
    from src.data.replacement_forecast_shadow_materialization_queue import (
        process_replacement_forecast_shadow_materialization_queue,
    )

    # Raw-input download is now a SEPARATE job (_replacement_forecast_download_cycle)
    # so it can never block this seed->materialize cycle (see that function's note).
    cfg = _replacement_forecast_shadow_materialization_queue_config()
    report = process_replacement_forecast_shadow_materialization_queue(
        request_dir=cfg["request_dir"],
        processed_dir=cfg["processed_dir"],
        failed_dir=cfg["failed_dir"],
        seed_dir=cfg["seed_dir"],
        seed_processed_dir=cfg["seed_processed_dir"],
        seed_failed_dir=cfg["seed_failed_dir"],
        forecast_db=cfg["forecast_db"],
        raw_manifest_dir=cfg["raw_manifest_dir"],
        seed_discovery_limit=int(cfg["seed_discovery_limit"]),
        seed_limit=int(cfg["seed_limit"]),
        limit=int(cfg["limit"]),
    )
    if report.failed_count:
        logger.warning("replacement forecast shadow materialization queue failures: %s", report.as_dict())
    elif report.processed_count:
        logger.info("replacement forecast shadow materialization queue processed: %s", report.as_dict())
