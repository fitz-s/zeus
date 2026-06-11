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


def _probe_resolved_available_cycle() -> datetime | None:
    """SINGLE run-selection authority for every production download lane (K4.0b(a)).

    The fetchable cycle is whatever the providers' probes CONFIRM is published for BOTH
    legs (AIFS open-data index + the anchor transport ladder incl. the S3 bucket) — never
    a wall-clock − release-lag guess. The guessed clock asked for unpublished 12Z/18Z
    runs every night; the rung-2 meta guard refused them (correctly) and the refusal
    aborted the whole download→materialize cycle (2026-06-11 incident,
    logs/zeus-forecast-live.err: "provider declares run 06:00 but caller wants 18:00").
    None = no pair-complete cycle provable right now → callers SKIP the tick with a
    receipt and retry next tick; they must never fall back to a guessed run.
    """
    from src.data.replacement_cycle_availability import (  # noqa: PLC0415
        newest_complete_cycle,
        probe_aifs_cycle_available,
        probe_anchor_available_any,
        resolve_cycle_leg_availability,
    )

    availability = resolve_cycle_leg_availability(
        datetime.now(timezone.utc),
        probe_aifs=probe_aifs_cycle_available,
        probe_anchor=probe_anchor_available_any,
    )
    return newest_complete_cycle(availability)


def _download_replacement_forecast_current_targets_if_needed(cfg: dict[str, object]) -> dict[str, object] | None:
    if not bool(cfg.get("download_current_targets_enabled", False)):
        return None
    forecast_db = cfg.get("forecast_db")
    output_dir = cfg.get("download_output_dir") or cfg.get("raw_manifest_dir")
    if forecast_db is None or output_dir is None:
        raise ValueError("replacement current-target download requires forecast_db and raw_manifest_dir/download_output_dir")
    from scripts.download_replacement_forecast_current_targets import (
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
    #
    # RUN-SELECTION AUTHORITY (2026-06-11, twin-authority kill): the available cycle is
    # probe-resolved, NEVER now − release_lag (that guess requested unpublished runs and the
    # rung-2 refusal aborted the whole cycle). release_lag_hours survives ONLY as the
    # source_available_at metadata model passed to the downloader — it takes no part in
    # deciding WHICH run to fetch.
    release_lag_hours = float(cfg.get("download_release_lag_hours") or 14.0)
    available_cycle = _probe_resolved_available_cycle()
    if available_cycle is None:
        return {
            "status": "CYCLE_PROBE_UNRESOLVED_SKIP",
            "detail": "no pair-complete cycle provable by provider probes this tick; "
            "retrying next tick — a guessed run is never requested",
        }
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
        # CYCLE-CURRENCY (K-root instance #3): when this call fires because the available
        # cycle is AHEAD of the downloaded high-water mark, the NEW cycle's raw inputs are
        # needed for ALL current targets — coverage ("a posterior exists") must not filter
        # the target list, or covered targets can never re-materialize on the fresh cycle.
        include_covered=not cycle_is_current,
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
        from datetime import date  # noqa: PLC0415

        from src.config import cities_by_name  # noqa: PLC0415
        from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
            build_replacement_forecast_current_target_plan,
        )
        from src.data.u0r_multimodel_download import (  # noqa: PLC0415
            U0RDownloadTarget,
            download_u0r_extra_raw_inputs,
        )

        release_lag_hours = float(cfg.get("download_release_lag_hours") or 14.0)
        # RUN-SELECTION AUTHORITY (2026-06-11): the capture cycle is the SAME probe-resolved
        # pair-complete cycle the anchor/AIFS lanes fetch — fusion binds same-cycle rows, so
        # capture at a guessed (now − lag) cycle either targets an unpublished run (every
        # extras fetch 400s, high-water froze at 06-10T06Z, q_lcb stayed NULL on every fresh
        # posterior) or a stale one. Per-model publication gaps inside the cycle stay
        # fail-soft in the downloader (per-row skip).
        cycle = _probe_resolved_available_cycle()
        if cycle is None:
            return {"status": "U0R_EXTRA_CYCLE_PROBE_UNRESOLVED_SKIP"}

        # CYCLE-CURRENCY (2026-06-09, K-root instance #5 — same structural decision as the
        # anchor downloader's include_covered): plan 'covered' has NO cycle-awareness, so
        # skipping covered rows meant a covered target NEVER received the new cycle's extras
        # (observed live: Madrid 06-10 fused with icon_global because its icon_eu row only
        # existed at the stale 06-08T12 cycle — the 00z extras run had skipped Madrid 06-10 as
        # covered). The coverage filter is REMOVED: the extras job now feeds ALL current
        # targets, and the downloader itself skips per-ROW (model, city, target, metric,
        # cycle, endpoint) combos that are already persisted, so the steady-state cost is
        # only-missing fetches (self-healing per cycle, no covered/freshness conflation).
        plan = build_replacement_forecast_current_target_plan(Path(str(forecast_db)))
        targets: list[U0RDownloadTarget] = []
        for row in plan.rows:
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


def _per_leg_downloaded_cycle(forecast_db: Path, source_id: str) -> datetime | None:
    """Per-leg high-water mark of downloaded raw-input cycles (None = unknown → fetch).

    Same fail-open contract as _max_downloaded_current_target_cycle, but for ONE leg, so
    the availability poll can complete a cycle leg-by-leg as the provider publishes
    (2026-06-10 incident: AIFS 12Z published hours before the open-meteo 12Z anchor;
    the one-shot whole-cycle download could only fail the pair together)."""
    from src.state.db import _connect  # noqa: PLC0415

    try:
        conn = _connect(Path(forecast_db))
        try:
            row = conn.execute(
                "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts"
                " WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        finally:
            conn.close()
    except Exception:
        return None


def _replacement_cycle_availability_poll_if_needed(cfg: dict[str, object]) -> dict[str, object] | None:
    """PROBE-RESOLVED raw-input fetch (operator directive 2026-06-11: automatic, ahead of
    need, no guessed numbers — K4.0b(a) availability-poll organ).

    Every poll tick:
      1. Resolve per-leg published state of the recent cycles by PROBING the providers
         (src/data/replacement_cycle_availability.py). The release-lag constant takes NO
         part in this decision; it remains only the legacy cron's backstop schedule.
      2. Fetch any published leg the journal does not yet hold, newest cycle first —
         per leg, so one provider lagging (the 12Z-anchor case) never delays the other.
    Idempotent: per-leg high-water marks short-circuit; the underlying downloader also
    skips already-present manifests. Fail-soft per leg: a failed leg is retried on the
    next tick. Returns a compact report dict (None when the feature flag is off)."""
    if not bool(cfg.get("download_current_targets_enabled", False)):
        return None
    forecast_db = cfg.get("forecast_db")
    output_dir = cfg.get("download_output_dir") or cfg.get("raw_manifest_dir")
    if forecast_db is None or output_dir is None:
        return None
    from scripts.download_replacement_forecast_current_targets import (  # noqa: PLC0415
        download_current_target_raw_inputs,
    )
    from src.data.replacement_cycle_availability import (  # noqa: PLC0415
        newest_complete_cycle,
        probe_aifs_cycle_available,
        probe_anchor_available_any,
        resolve_cycle_leg_availability,
    )

    now = datetime.now(timezone.utc)
    availability = resolve_cycle_leg_availability(
        now,
        probe_aifs=probe_aifs_cycle_available,
        probe_anchor=probe_anchor_available_any,
    )
    aifs_have = _per_leg_downloaded_cycle(Path(str(forecast_db)), "ecmwf_aifs_ens")
    anchor_have = _per_leg_downloaded_cycle(Path(str(forecast_db)), "openmeteo_ecmwf_ifs_9km")
    newest_aifs_published = next((a.cycle for a in availability if a.aifs_available), None)
    newest_anchor_published = next((a.cycle for a in availability if a.anchor_available), None)

    fetch_aifs_cycle = (
        newest_aifs_published
        if newest_aifs_published is not None
        and (aifs_have is None or newest_aifs_published > aifs_have)
        else None
    )
    fetch_anchor_cycle = (
        newest_anchor_published
        if newest_anchor_published is not None
        and (anchor_have is None or newest_anchor_published > anchor_have)
        else None
    )
    report: dict[str, object] = {
        "status": "AVAILABILITY_POLL",
        "now": now.isoformat(),
        "newest_aifs_published": newest_aifs_published.isoformat() if newest_aifs_published else None,
        "newest_anchor_published": newest_anchor_published.isoformat() if newest_anchor_published else None,
        "newest_complete_published": (
            newest_complete_cycle(availability).isoformat()
            if newest_complete_cycle(availability)
            else None
        ),
        "aifs_downloaded_cycle": aifs_have.isoformat() if aifs_have else None,
        "anchor_downloaded_cycle": anchor_have.isoformat() if anchor_have else None,
        "legs_fetched": [],
    }
    if fetch_aifs_cycle is None and fetch_anchor_cycle is None:
        report["status"] = "AVAILABILITY_POLL_CURRENT"
        return report
    for leg, cycle, skip_aifs, skip_openmeteo in (
        ("aifs", fetch_aifs_cycle, False, True),
        ("anchor", fetch_anchor_cycle, True, False),
    ):
        if cycle is None:
            continue
        try:
            download_current_target_raw_inputs(
                forecast_db=Path(str(forecast_db)),
                output_dir=Path(str(output_dir)),
                cycle=cycle,
                limit=int(cfg.get("download_limit") or 10),
                write_db=True,
                skip_aifs=skip_aifs,
                skip_openmeteo=skip_openmeteo,
                release_lag_hours=float(cfg.get("download_release_lag_hours") or 14.0),
                anchor_sigma_c=float(cfg.get("download_anchor_sigma_c") or 3.0),
                aifs_retries=int(cfg.get("download_aifs_retries") or 4),
                include_covered=True,
            )
            report["legs_fetched"].append({"leg": leg, "cycle": cycle.isoformat()})  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — per-leg fail-soft; next tick retries
            logger.warning(
                "availability-poll %s leg fetch failed for cycle %s (retry next tick): %s",
                leg,
                cycle.isoformat(),
                exc,
            )
            report.setdefault("legs_failed", []).append(  # type: ignore[union-attr]
                {"leg": leg, "cycle": cycle.isoformat(), "error": str(exc)[:200]}
            )
    return report


@_scheduler_job("anchor_meta_stamp_cross_check")
def _anchor_meta_stamp_cross_check() -> None:
    """Hourly: re-verify meta-stamped anchor artifacts against single-runs once the same
    run is served there (K4.0b(f) belt-and-suspenders; MISMATCH ⇒ ERROR + receipt)."""
    flags = _replacement_forecast_runtime_flags_from_settings()
    if not bool(flags.get("openmeteo_ecmwf_ifs9_aifs_soft_anchor_shadow_enabled", False)):
        return
    cfg = _replacement_forecast_shadow_materialization_queue_config()
    forecast_db = cfg.get("forecast_db")
    if forecast_db is None:
        return
    from src.data.anchor_cross_check import (  # noqa: PLC0415
        run_anchor_cross_check_cycle,
        run_bucket_anchor_cross_check_cycle,
    )

    report = run_anchor_cross_check_cycle(Path(str(forecast_db)))
    if report.get("checked") or report.get("errors"):
        logger.info("anchor meta-stamp cross-check report: %s", report)

    # Rung-3 bucket transport antibody: re-verify bucket artifacts against single-runs once
    # the run is served there. VERIFIED receipts grow the city whitelist that gates future
    # bucket serves; MISMATCH ⇒ ERROR + receipt (coastal/terrain city stays off the whitelist).
    bucket_report = run_bucket_anchor_cross_check_cycle(Path(str(forecast_db)))
    if bucket_report.get("checked") or bucket_report.get("errors"):
        logger.info("anchor bucket-transport cross-check report: %s", bucket_report)


@_scheduler_job("replacement_cycle_availability_poll")
def _replacement_cycle_availability_poll() -> None:
    """Interval job: probe provider publication state and fetch fresh raw-input legs the
    moment they exist — BEFORE the engine needs them (operator directive 2026-06-11).
    Runs on the download lane; never blocks the 5-min materialize cycle."""
    flags = _replacement_forecast_runtime_flags_from_settings()
    if not bool(flags.get("openmeteo_ecmwf_ifs9_aifs_soft_anchor_shadow_enabled", False)):
        return
    cfg = _replacement_forecast_shadow_materialization_queue_config()
    report = _replacement_cycle_availability_poll_if_needed(cfg)
    if report is None:
        return
    if report.get("status") == "AVAILABILITY_POLL_CURRENT":
        logger.debug("cycle availability poll current: %s", report)
    else:
        logger.info("cycle availability poll report: %s", report)


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
    # SILENT-DEATH SURFACING (2026-06-09): if the extras sub-step fails or is
    # fail-soft skipped, the parent download job still shows OK in scheduler
    # health (only AIFS/IFS9 success is tracked by the @_scheduler_job wrapper).
    # Write a distinct component entry so logs/scheduler_jobs_health.json shows
    # the degradation and an operator/alert can detect multi-day extras outages.
    if u0r_capture_report is not None:
        _u0r_status = u0r_capture_report.get("status", "")
        _u0r_failed = _u0r_status == "U0R_EXTRA_CAPTURE_FAILSOFT_SKIPPED"
        if _u0r_failed or u0r_capture_report.get("global_models_unavailable"):
            from src.observability.scheduler_health import _write_scheduler_health as _wsh  # noqa: PLC0415
            _failure_reason = u0r_capture_report.get("error") or str(
                u0r_capture_report.get("global_models_unavailable", "")
            )
            _wsh("u0r_multimodel_capture", failed=True, reason=_failure_reason)
        elif _u0r_status not in {"U0R_EXTRA_NO_TARGETS", ""}:
            from src.observability.scheduler_health import _write_scheduler_health as _wsh  # noqa: PLC0415, F811
            _wsh("u0r_multimodel_capture", failed=False)


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
