# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-03_ddd_implementation_plan/
#                  RERUN_PLAN_v2.md §5 D-E (live wiring) + F2 (null-floor fail-CLOSED)
"""Live wiring helper for DDD v2.

Computes the inputs that the DDD module needs (current_cov, window_elapsed,
N_platt_samples) at decision time, then calls ``evaluate_ddd_from_files``.

Failure semantics (F2 forward-fix from RERUN_PLAN_v2.md §5):
- Floors config missing on disk           → DDD_CONFIG_MISSING (fail-CLOSED)
- City not present in floors config       → DDD_CITY_UNCONFIGURED (fail-CLOSED)
- City has status='NO_TRAIN_DATA'         → DDD_NO_TRAIN_DATA (fail-CLOSED;
                                              HK / Istanbul / Moscow / Tel Aviv)
- City has status='EXCLUDED_WORKSTREAM_A' → DDD_EXCLUDED (fail-CLOSED;
                                              Paris until workstream A lands)
- Rail 1 fires (cov<0.35, window>0.5)     → HALT  (caller rejects decision)
- Rail 2 fires                             → DISCOUNT (caller composes with Kelly)

These are NOT exceptions to swallow — the caller treats them as decision-level
rejections. Unexpected exceptions propagate (bug, not a routine state).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from src.oracle.data_density_discount import (
    DDDResult,
    evaluate_ddd,
    load_city_floors,
    load_nstar_config,
)

logger = logging.getLogger(__name__)

# ── module-level config cache ────────────────────────────────────────────────
# Hot-path optimization: load configs once per process. Tests / tooling can
# invalidate by setting these to None.
_FLOORS_CACHE: dict | None = None
_NSTAR_CACHE: dict | None = None


def _floors() -> dict:
    global _FLOORS_CACHE
    if _FLOORS_CACHE is None:
        _FLOORS_CACHE = load_city_floors()
    return _FLOORS_CACHE


def _nstar() -> dict:
    global _NSTAR_CACHE
    if _NSTAR_CACHE is None:
        _NSTAR_CACHE = load_nstar_config()
    return _NSTAR_CACHE


def reset_caches() -> None:
    """Test hook: clear cached configs so the next call re-reads from disk."""
    global _FLOORS_CACHE, _NSTAR_CACHE
    _FLOORS_CACHE = None
    _NSTAR_CACHE = None


# ── exceptions for caller pattern-match ──────────────────────────────────────


@dataclass
class DDDFailClosed(Exception):
    """Raised when DDD cannot evaluate and policy is fail-CLOSED.

    The caller (evaluator) catches this and rejects the decision with an
    appropriate rejection_stage. The reason string is suitable as a
    human-readable rejection reason.
    """

    code: str          # e.g. "DDD_CONFIG_MISSING", "DDD_NO_TRAIN_DATA"
    reason: str        # human-readable
    city: str = ""
    track: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.reason}"


# ── coverage + window helpers ────────────────────────────────────────────────

WINDOW_RADIUS = 3  # ±3 hours, per RERUN_PLAN_v2 directional window contract


def directional_window(peak_hour: float, radius: int = WINDOW_RADIUS) -> list[int]:
    """Return ``radius*2 + 1`` integer hours bracketing ``peak_hour``."""
    p = round(peak_hour)
    return [(p + d) % 24 for d in range(-radius, radius + 1)]


def fetch_directional_coverage(
    conn: sqlite3.Connection,
    city: str,
    target_hours: list[int],
    target_date: str,
    *,
    source: str = "wu_icao_history",
    data_version: str = "v1.wu-native",
) -> float:
    """Return cov ∈ [0, 1] = (distinct hours observed) / (target hours).

    Reads ``observation_instants_v2``. H1-fix semantics: zero rows → cov=0.0.
    Source defaults to the canonical settlement source. The ``data_version``
    filter ensures we only count rows under the active training contract.
    """
    n = len(target_hours)
    if n <= 0:
        return 0.0
    in_clause = ",".join(str(h) for h in target_hours)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT CAST(local_hour AS INTEGER)) AS hrs
        FROM observation_instants_v2
        WHERE city = ?
          AND source = ?
          AND data_version = ?
          AND target_date = ?
          AND CAST(local_hour AS INTEGER) IN ({in_clause})
        """,
        (city, source, data_version, target_date),
    ).fetchone()
    if row is None:
        return 0.0
    hrs = row[0] or 0
    return hrs / n


def compute_window_elapsed(
    target_date: str,
    peak_hour: float,
    *,
    decision_time: Optional[datetime] = None,
    radius: int = WINDOW_RADIUS,
    timezone_name: str | None = None,
) -> float:
    """Fraction of the directional observation window elapsed at decision_time.

    The window for a (city, target_date, metric) is the local-time span
    ``[peak_hour - radius, peak_hour + radius]``. When ``timezone_name`` is
    provided, ``target_date`` and ``peak_hour`` are interpreted in that city's
    local timezone, then converted to UTC for comparison with decision time.
    Window elapsed = (decision_time_utc - window_start_utc) / window_length,
    clipped to [0, 1].

    ``decision_time`` defaults to ``datetime.now(timezone.utc)``. The function
    defaults to ``datetime.now(timezone.utc)``. If ``timezone_name`` is absent,
    the helper preserves the legacy UTC approximation for compatibility with
    old tests and tooling.
    """
    if decision_time is None:
        decision_time = datetime.now(timezone.utc)
    if decision_time.tzinfo is None:
        decision_time = decision_time.replace(tzinfo=timezone.utc)
    decision_time = decision_time.astimezone(timezone.utc)

    if timezone_name:
        tz = ZoneInfo(timezone_name)
        target_dt = datetime.fromisoformat(target_date).replace(tzinfo=tz)
    else:
        target_dt = datetime.fromisoformat(target_date).replace(tzinfo=timezone.utc)
    window_start = target_dt + timedelta(hours=(peak_hour - radius))
    window_start = window_start.astimezone(timezone.utc)
    window_length_hours = 2 * radius + 1
    elapsed_hours = (decision_time - window_start).total_seconds() / 3600.0
    if elapsed_hours <= 0.0:
        return 0.0
    if elapsed_hours >= window_length_hours:
        return 1.0
    return elapsed_hours / window_length_hours


def _timezone_name_for_city(city: str) -> str | None:
    try:
        from src.config import runtime_cities_by_name
    except Exception as exc:  # noqa: BLE001 — DDD can still use legacy fallback.
        logger.warning("DDD timezone lookup unavailable: %s", exc)
        return None
    city_cfg = runtime_cities_by_name().get(city)
    return getattr(city_cfg, "timezone", None) if city_cfg is not None else None


def fetch_n_platt_samples(
    conn: sqlite3.Connection,
    city: str,
    metric: Literal["high", "low"],
    season: str,
    *,
    data_version: Optional[str] = None,
) -> int:
    """Read n_samples from the active platt_models_v2 row for this bucket.

    Returns 0 when no eligible row exists — caller treats as small-sample.
    Mirrors the loader's filter semantics (is_active=1, authority='VERIFIED'),
    but does NOT thread frozen_as_of / model_key — DDD's small-sample threshold
    works against whatever model is actually live, not pinned generations.
    """
    if data_version is not None:
        row = conn.execute(
            """
            SELECT n_samples FROM platt_models_v2
            WHERE temperature_metric = ?
              AND cluster = ?
              AND season = ?
              AND data_version = ?
              AND is_active = 1
              AND authority = 'VERIFIED'
            ORDER BY fitted_at DESC LIMIT 1
            """,
            (metric, city, season, data_version),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT n_samples FROM platt_models_v2
            WHERE temperature_metric = ?
              AND cluster = ?
              AND season = ?
              AND is_active = 1
              AND authority = 'VERIFIED'
            ORDER BY fitted_at DESC LIMIT 1
            """,
            (metric, city, season),
        ).fetchone()
    if row is None:
        return 0
    return int(row[0] or 0)


# ── public API ───────────────────────────────────────────────────────────────


def evaluate_ddd_for_decision(
    *,
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    metric: Literal["high", "low"],
    peak_hour: float,
    season: str,
    mismatch_rate: float,
    data_version: Optional[str] = None,
    decision_time: Optional[datetime] = None,
    cycle: Optional[str] = None,
    source_id: Optional[str] = None,
    horizon_profile: Optional[str] = None,
) -> DDDResult:
    """Compute live DDD for one (city, target_date, metric) decision.

    On Rail 1 fire: returns DDDResult(action="HALT"). Caller rejects the
    decision with rejection_stage="DDD_HALT".

    On Rail 2: returns DDDResult(action="DISCOUNT"). Caller multiplies its
    Kelly multiplier by ``(1 - result.discount)``.

    Raises DDDFailClosed when the city is unconfigured / NO_TRAIN_DATA /
    EXCLUDED — caller rejects with the corresponding rejection_stage. This
    is the F2 forward-fix: do NOT silently skip DDD for these cities.

    Raises FileNotFoundError if the floors config is missing entirely
    (caller may treat this as a hard config gate).
    """
    try:
        floors_cfg = _floors()
    except FileNotFoundError as exc:
        raise DDDFailClosed(
            code="DDD_CONFIG_MISSING",
            reason=str(exc),
            city=city,
            track=metric,
        ) from exc

    nstar_cfg = _nstar()  # let FileNotFoundError propagate same as floors

    # F2 fail-CLOSED: city must exist with a non-status entry.
    per_city = floors_cfg.get("per_city", floors_cfg)
    entry = per_city.get(city)
    if entry is None:
        raise DDDFailClosed(
            code="DDD_CITY_UNCONFIGURED",
            reason=f"city '{city}' missing from DDD floors config",
            city=city,
            track=metric,
        )
    if isinstance(entry, dict) and "status" in entry:
        status = entry["status"]
        if status == "NO_TRAIN_DATA":
            raise DDDFailClosed(
                code="DDD_NO_TRAIN_DATA",
                reason=f"city '{city}' has NO_TRAIN_DATA — DDD cannot evaluate",
                city=city,
                track=metric,
            )
        if status == "EXCLUDED_WORKSTREAM_A":
            raise DDDFailClosed(
                code="DDD_EXCLUDED_WORKSTREAM_A",
                reason=f"city '{city}' EXCLUDED until workstream A LFPB resync completes",
                city=city,
                track=metric,
            )
        # Unknown status — fail-CLOSED to be safe
        raise DDDFailClosed(
            code="DDD_UNKNOWN_STATUS",
            reason=f"city '{city}' has unrecognized status '{status}'",
            city=city,
            track=metric,
        )

    # Inputs from DB
    hours = directional_window(peak_hour)
    cov = fetch_directional_coverage(conn, city, hours, target_date)
    n_platt = fetch_n_platt_samples(conn, city, metric, season, data_version=data_version)
    window_elapsed = compute_window_elapsed(
        target_date,
        peak_hour,
        decision_time=decision_time,
        timezone_name=_timezone_name_for_city(city),
    )

    # Sigma is monitoring-only; not required for the call. The DDD module
    # accepts None and emits the structured log without the σ field.
    return evaluate_ddd(
        city=city,
        track=metric,
        current_cov=cov,
        window_elapsed=window_elapsed,
        N_platt_samples=n_platt,
        mismatch_rate=mismatch_rate,
        city_floors_config=floors_cfg,
        n_star_config=nstar_cfg,
        sigma_diagnostic=None,
        cycle=cycle,
        source_id=source_id,
        horizon_profile=horizon_profile,
    )
