# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: #90 calibration-coverage antibody / wiring verdict 2026-06-03
"""Loud calibration-coverage guard (antibody #90).

THE CATEGORY OF ERROR THIS MAKES LOUD
-------------------------------------
Zeus's live decision path resolves two calibration substrates per city by a
KEYED LOOKUP that, on a miss, falls THROUGH to a less-correct surface SILENTLY:

  * BIAS  — ``read_bias_model`` (src/calibration/ens_bias_repo.py:614) returns
    ``None`` when a city has no VERIFIED ``edli_per_city_v1`` row for the
    current (city, season, month, live_data_version).  The reactor's
    ``_maybe_apply_edli_bias_correction`` then leaves the members UNCORRECTED
    (raw), with no alarm.  A 9-agent deep-trace (2026-06-03) found 47/54 cities
    corrected but 7 silently on RAW (Auckland, Hong Kong, Jinan, Zhengzhou — no
    row; Dallas, Jakarta, Lagos — only month=5).

  * PLATT — ``get_calibrator`` (src/calibration/manager.py:756) tries the city's
    OWN (cluster, season) bucket first; on a miss it pools EVERY OTHER cluster's
    Platt (the season-only fallback loop, manager.py:968-1022) and serves a
    FOREIGN cluster's transform — e.g. a Buenos-Aires-WINTER Platt applied to a
    northern-summer city — or, if even that misses, falls to identity-by-
    starvation (``cal is None``).  Both are silent.

This guard converts that silent PARTIAL coverage into a LOUD, ENUMERABLE state.
It does NOT fix the gaps (those are repaired by data refits elsewhere); it makes
them VISIBLE so "PARTIAL calibration utilization" can never be a silent default.

ONE-UNITY NOTE (why this is a NEW module, not an extension)
-----------------------------------------------------------
``src/main.py::_assert_emos_ci_license_seasonal_coverage`` is a sibling
season-pin guard, but it covers a DIFFERENT layer (the EMOS-CI live-override's
``emos_calibration`` cells) with DIFFERENT severity semantics (it DROPS an
uncovered city from the in-process EMOS license, fail-closed to the MC lcb).
The bias + Platt substrates are a distinct concern with the inverted severity
contract this antibody requires — WARN in shadow, RAISE when armed — so folding
them into the EMOS-CI license guard would conflate two unrelated fail modes.
This module is the bias+Platt half of the #90 pattern; the EMOS-CI guard is the
EMOS-CI half.  Both share the season-pin idiom.

SEVERITY CONTRACT (critical — this is a DETECTOR, not a trading gate)
--------------------------------------------------------------------
  * SHADOW (real_order_submit_enabled == False): WARN ONLY.  Never blocks boot,
    never starves the reactor, never alters a trade decision.  Today's behaviour
    is byte-identical EXCEPT new warning log lines.  Calibration gaps must NOT
    become a no-trade (that violates the no-trade=fault mandate); they are fixed
    by refits, and this guard only makes them visible.
  * ARMED (real_order_submit_enabled == True): a CovrageGap escalates to a HARD
    fail-closed RuntimeError.  You may NOT arm with silent partial calibration.

The escalation is gated SOLELY on ``real_order_submit_enabled`` so the shadow
daemon (the only mode running today) is unaffected.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# The two live calibration substrates this antibody covers.
_LAYER_BIAS = "bias"
_LAYER_PLATT = "platt"

# Silent fall-through destinations a missing key resolves to.
_FALLBACK_RAW = "raw"            # bias miss -> uncorrected members
_FALLBACK_IDENTITY = "identity"  # platt miss -> identity-by-starvation (cal is None)
# borrowed:<foreign_cluster> is constructed dynamically.

# The metrics the live reactor prices.
_LIVE_METRICS: tuple[str, ...] = ("high", "low")

# The calibration bucket source axis the LIVE OpenData forecast routes through.
# ``ecmwf_open_data`` maps to ``tigge_mars`` (forecast_source_registry.py:256 —
# OpenData IS the TIGGE archive's live channel; Platt models are stored under
# source_id='tigge_mars').  Threading this into get_calibrator / load_platt_model
# makes the guard probe the EXACT Platt bucket the armed reactor reads, instead
# of the schema-default bucket a None source_id would hit.
_LIVE_CALIBRATION_SOURCE_ID = "tigge_mars"


class CalibrationCoverageError(RuntimeError):
    """Raised (armed mode only) when a live city would silently borrow / fall
    through its bias or Platt substrate for the current season."""


@dataclass(frozen=True)
class CoverageGap:
    """One enumerated silent fall-through for one (city, metric, layer)."""

    city: str
    metric: str
    layer: str            # _LAYER_BIAS | _LAYER_PLATT
    season: str
    fallback: str         # _FALLBACK_RAW | _FALLBACK_IDENTITY | "borrowed:<cluster>"

    def describe(self) -> str:
        return (
            f"{self.city}/{self.metric} season={self.season} "
            f"layer={self.layer} -> SILENT_FALLBACK={self.fallback}"
        )


@dataclass(frozen=True)
class CoverageReport:
    """Enumerated coverage outcome over all live cities × metrics."""

    armed: bool
    today: str
    cities_checked: int
    gaps: tuple[CoverageGap, ...]

    @property
    def ok(self) -> bool:
        return not self.gaps

    def summary(self) -> str:
        if self.ok:
            return (
                f"calibration coverage OK: {self.cities_checked} live cities × "
                f"{len(_LIVE_METRICS)} metrics fully covered for {self.today} "
                f"(armed={self.armed})"
            )
        return (
            f"calibration coverage PARTIAL: {len(self.gaps)} silent fall-through(s) "
            f"across {self.cities_checked} live cities for {self.today} "
            f"(armed={self.armed}): " + "; ".join(g.describe() for g in self.gaps)
        )


def _open_calibration_read_connection() -> sqlite3.Connection:
    """Read-only world connection with the forecasts DB ATTACHed.

    ``get_calibrator`` reads ``platt_models`` (world DB) AND, on its HIGH
    on-the-fly path, ``calibration_pairs`` (forecasts DB) — so the guard's
    connection must span BOTH, exactly as the production calibration connection
    does (cycle_runner.py:81-89 ATTACHes world + forecasts onto the trade conn).
    A world-only connection would raise ``no such table: calibration_pairs`` the
    moment a city's HIGH path tried an on-the-fly fit.  Read-only intent:
    write_class=None; the ATTACH adds no write capability.
    """
    from src.state.db import (
        ZEUS_FORECASTS_DB_PATH,
        get_world_connection_read_only,
    )

    conn = get_world_connection_read_only()
    try:
        attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in attached:
            conn.execute(
                "ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),)
            )
    except sqlite3.OperationalError as exc:  # pragma: no cover — ATTACH wiring
        logger.warning(
            "calibration-coverage guard: ATTACH forecasts failed (non-fatal): %r",
            exc,
        )
    return conn


def _today_iso(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(tz=timezone.utc)
    return now.date().isoformat()


def _live_data_version_for_metric(metric: str) -> str:
    """Canonical live OpenData data_version for a metric — the EXACT string the
    reactor threads into ``read_bias_model`` (event_reactor_adapter.py:3879)."""
    from src.contracts.ensemble_snapshot_provenance import (
        ECMWF_OPENDATA_HIGH_DATA_VERSION,
        ECMWF_OPENDATA_LOW_DATA_VERSION,
    )

    return (
        ECMWF_OPENDATA_HIGH_DATA_VERSION
        if metric == "high"
        else ECMWF_OPENDATA_LOW_DATA_VERSION
    )


def _bias_covered(
    conn: sqlite3.Connection,
    *,
    city_name: str,
    season: str,
    metric: str,
    month: int,
) -> bool:
    """True iff a VERIFIED edli_per_city_v1 bias row exists for THIS exact key.

    Reuses ``read_bias_model``'s EXACT key semantics as wired in the reactor
    (event_reactor_adapter.py:3889) — same family, same authority, same
    month/target_month, same live_data_version — so the guard's notion of
    "covered" is identical to what the live correction path requires.
    """
    from src.calibration.ens_bias_repo import read_bias_model

    # _EDLI_BIAS_FAMILY is the family literal the reactor keys on. Import-light:
    # the constant lives in the reactor adapter alongside the read site.
    from src.engine.event_reactor_adapter import _EDLI_BIAS_FAMILY

    conn.row_factory = sqlite3.Row
    row = read_bias_model(
        conn,
        city=city_name,
        season=season,
        metric=metric,
        live_data_version=_live_data_version_for_metric(metric),
        month=month,
        target_month=month,
        authority="VERIFIED",
        error_model_family=_EDLI_BIAS_FAMILY,
    )
    return row is not None


def _own_cluster_platt_present(
    conn: sqlite3.Connection,
    *,
    city: Any,
    season: str,
    metric: str,
) -> bool:
    """True iff the city's OWN (cluster, season) Platt bucket yields a model.

    Mirrors the OWN-cluster primary probe inside ``get_calibrator`` — the same
    pin resolution + candidate-data-version + ``load_platt_model`` call the
    production primary path makes (manager.py:822-868) — WITHOUT the cross-
    cluster fallback loop.  Used to distinguish an OWN-cluster resolution from a
    borrowed foreign-cluster one (``get_calibrator`` does not expose which
    cluster it served).
    """
    from src.calibration.manager import (
        _candidate_data_versions_for_metric_source,
        _resolve_pin_for_bucket,
    )
    from src.calibration.store import load_platt_model

    candidate_data_versions = _candidate_data_versions_for_metric_source(
        metric, _LIVE_CALIBRATION_SOURCE_ID
    )
    frozen_as_of, model_key = _resolve_pin_for_bucket(
        metric, city.cluster, season, None
    )
    for data_version in candidate_data_versions:
        model_data = load_platt_model(
            conn,
            temperature_metric=metric,
            cluster=city.cluster,
            season=season,
            data_version=data_version,
            frozen_as_of=frozen_as_of,
            model_key=model_key,
            source_id=_LIVE_CALIBRATION_SOURCE_ID,
        )
        if model_data is not None:
            return True
    return False


def _platt_resolution(
    conn: sqlite3.Connection,
    *,
    city: Any,
    today: str,
    season: str,
    metric: str,
) -> str:
    """Classify how the city resolves its Platt for (today, metric).

    Returns one of:
      * "own"                 — own (cluster, season) Platt or successful own
                                on-the-fly fit (acceptable: NOT a fall-through).
      * "identity"            — IdentityCalibrator (certified own route, OR
                                identity-by-starvation when cal is None).
      * "borrowed:<cluster>"  — a real Platt served from a FOREIGN cluster via
                                the season-only fallback pool (silent borrow).
    """
    from src.calibration.manager import get_calibrator
    from src.calibration.platt import IdentityCalibrator

    # Thread the live calibration source so get_calibrator hits the SAME Platt
    # bucket the armed reactor reads (the OpenData->tigge_mars routing). cycle /
    # horizon_profile are left at defaults: the boot guard has no in-flight
    # forecast, and the own-vs-borrowed disambiguation below is self-consistent
    # because _own_cluster_platt_present uses the same source axis.
    cal, _level = get_calibrator(
        conn, city, today, metric, source_id=_LIVE_CALIBRATION_SOURCE_ID
    )

    if cal is None:
        # No calibrator at all → p_raw is served verbatim == identity behaviour.
        return _FALLBACK_IDENTITY
    if isinstance(cal, IdentityCalibrator):
        # Certified identity route (identity_full_transport_v1) is an explicit
        # OWN resolution, not a starvation fall-through.
        return _FALLBACK_IDENTITY

    # A real Platt was served. It is a borrow iff the city's OWN-cluster primary
    # bucket has nothing (the only way get_calibrator returns a non-identity
    # Platt with no own bucket is the cross-cluster season-only fallback loop).
    if _own_cluster_platt_present(conn, city=city, season=season, metric=metric):
        return "own"
    return f"borrowed:{_foreign_cluster_for(city)}"


def _foreign_cluster_for(city: Any) -> str:
    """Best-effort label of the foreign cluster a borrow would draw from.

    ``get_calibrator`` iterates ``calibration_clusters()`` in order and returns
    the FIRST other cluster with a usable season Platt, so naming the precise
    donor would require re-walking that loop; for the LOUD signal it is enough
    to mark it a foreign borrow.  We return the generic marker
    ``foreign_cluster`` rather than re-deriving the exact donor to keep this
    detector decoupled from the fallback loop's internal ordering.
    """
    return "foreign_cluster"


def calibration_coverage_report(
    *,
    armed: bool,
    conn: sqlite3.Connection | None = None,
    cities: Iterable[Any] | None = None,
    now: datetime | None = None,
) -> CoverageReport:
    """Enumerate, for EVERY live runtime city × metric, whether its bias and
    Platt substrates are present for the CURRENT season — collecting a
    CoverageGap for any silent borrow / fall-through.

    Pure detector: SELECT-only, no writes, never mutates trade flow.  Connection
    and city list are injectable for tests; production passes neither and the
    function reads the live world DB + runtime cities.
    """
    from src.config import runtime_cities

    today = _today_iso(now)
    month = int(today[5:7])

    owns_conn = conn is None
    if conn is None:
        conn = _open_calibration_read_connection()

    if cities is None:
        cities = runtime_cities()
    city_list = list(cities)

    gaps: list[CoverageGap] = []
    try:
        for city in city_list:
            lat = getattr(city, "lat", 90.0)
            from src.contracts.season import season_from_date

            season = season_from_date(today, lat=lat)
            for metric in _LIVE_METRICS:
                # BIAS layer
                if not _bias_covered(
                    conn,
                    city_name=city.name,
                    season=season,
                    metric=metric,
                    month=month,
                ):
                    gaps.append(
                        CoverageGap(
                            city=city.name,
                            metric=metric,
                            layer=_LAYER_BIAS,
                            season=season,
                            fallback=_FALLBACK_RAW,
                        )
                    )
                # PLATT layer
                resolution = _platt_resolution(
                    conn, city=city, today=today, season=season, metric=metric
                )
                if resolution.startswith("borrowed:") or resolution == _FALLBACK_IDENTITY:
                    gaps.append(
                        CoverageGap(
                            city=city.name,
                            metric=metric,
                            layer=_LAYER_PLATT,
                            season=season,
                            fallback=resolution,
                        )
                    )
    finally:
        if owns_conn:
            conn.close()

    return CoverageReport(
        armed=armed,
        today=today,
        cities_checked=len(city_list),
        gaps=tuple(gaps),
    )


def assert_calibration_coverage(
    *,
    armed: bool,
    conn: sqlite3.Connection | None = None,
    cities: Iterable[Any] | None = None,
    now: datetime | None = None,
) -> CoverageReport:
    """Boot / pre-arm coverage check.

    SHADOW (armed=False): WARN per gap and continue — returns the report, never
    raises, never starves the reactor.

    ARMED (armed=True): if ANY gap exists, raise ``CalibrationCoverageError``
    (fail-closed — you may not arm with silent partial calibration).  When fully
    covered, logs an INFO and returns the (empty) report in both modes.

    Returns the CoverageReport so callers / tests can inspect the enumerated
    gaps regardless of severity.
    """
    report = calibration_coverage_report(
        armed=armed, conn=conn, cities=cities, now=now
    )

    if report.ok:
        logger.info("CALIBRATION_COVERAGE_OK: %s", report.summary())
        return report

    if armed:
        # Fail-closed: arming with silent partial calibration is unconstructable.
        raise CalibrationCoverageError(
            "CALIBRATION_COVERAGE_PARTIAL_ARMED: refusing to arm with silent "
            "bias/Platt fall-through. " + report.summary()
        )

    # Shadow: LOUD warn-and-continue. One line per gap (enumerable), plus a
    # roll-up so the operator sees the count at a glance.
    for gap in report.gaps:
        logger.warning("CALIBRATION_COVERAGE_GAP: %s", gap.describe())
    logger.warning("CALIBRATION_COVERAGE_PARTIAL (shadow, warn-only): %s", report.summary())
    return report
