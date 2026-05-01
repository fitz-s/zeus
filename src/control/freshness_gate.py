# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.1
"""Trading freshness gate — three-branch decision tree (FRESH / STALE / ABSENT).

Reads state/source_health.json (written by ingest daemon's source_health_probe_tick
every 10 minutes). Returns a FreshnessVerdict dataclass that callers act on.

Called from:
- src/main.py boot path (with ABSENT-causes-FATAL-after-retry semantics)
- src/main.py _run_mode() each cycle (with ABSENT-causes-DEGRADE-only semantics)

Operator override: state/control_plane.json field
  "force_ignore_freshness": ["ecmwf_open_data", ...]
  lets an operator bypass per-source staleness for a named source.

Wallet override is NEVER allowed — see _startup_wallet_check in src/main.py.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Freshness budgets per source family (seconds)
FRESHNESS_BUDGETS: dict[str, int] = {
    "open_meteo_archive": 6 * 3600,   # hourly_obs: 6h
    "wu_pws": 6 * 3600,               # hourly_obs: 6h
    "hko": 36 * 3600,                 # daily_obs: 36h
    "ogimet": 36 * 3600,              # daily_obs: 36h
    "ecmwf_open_data": 24 * 3600,     # TIGGE proxy: 24h
    "noaa": 36 * 3600,                # daily_obs: 36h
    "tigge_mars": 24 * 3600,          # TIGGE direct: 24h
}

# Sources whose staleness disables DAY0_CAPTURE
DAY0_CAPTURE_GATED_SOURCES = frozenset({"open_meteo_archive", "wu_pws", "hko", "ogimet", "noaa"})
# Sources whose staleness disables ensemble-only nowcasts
ENSEMBLE_GATED_SOURCES = frozenset({"ecmwf_open_data", "tigge_mars"})

# Mid-run ABSENT threshold: if file disappears or written_at ages past this, degrade
ABSENT_MID_RUN_THRESHOLD_SECONDS = 5 * 60  # 5 minutes

# Boot retry: total 5 minutes, 10s interval = 30 attempts
BOOT_RETRY_INTERVAL_SECONDS = 10
BOOT_RETRY_MAX_ATTEMPTS = 30  # 30 × 10s = 5 min


@dataclass
class SourceStatus:
    source: str
    fresh: bool
    stale: bool
    last_success_at: Optional[str]
    budget_seconds: int
    age_seconds: Optional[float]
    degradation_flags: list[str] = field(default_factory=list)


@dataclass
class FreshnessVerdict:
    """Result of freshness gate evaluation.

    branch: "FRESH" | "STALE" | "ABSENT"
    stale_sources: which sources exceeded their budget
    day0_capture_disabled: bool — DAY0_CAPTURE mode must be suppressed
    ensemble_disabled: bool — ensemble-only nowcasts must be suppressed
    degraded_data: bool — any degradation flag set (goes into decision_log)
    operator_overrides: list of source names the operator explicitly ignored
    """
    branch: str
    stale_sources: list[str] = field(default_factory=list)
    day0_capture_disabled: bool = False
    ensemble_disabled: bool = False
    degraded_data: bool = False
    operator_overrides: list[str] = field(default_factory=list)
    source_statuses: list[SourceStatus] = field(default_factory=list)
    written_at: Optional[str] = None


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _age_seconds(ts_str: Optional[str], now: datetime) -> Optional[float]:
    if not ts_str:
        return None
    parsed = _parse_iso(ts_str)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds()


def _load_source_health(state_path: Path) -> Optional[dict]:
    """Load source_health.json. Returns None if absent or unreadable."""
    try:
        with open(state_path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def _operator_overrides_from_control_plane(state_dir: Path) -> list[str]:
    """Read force_ignore_freshness list from control_plane.json (if present)."""
    cp_path = state_dir / "control_plane.json"
    try:
        with open(cp_path) as f:
            data = json.load(f)
        overrides = data.get("force_ignore_freshness") or []
        return [str(s) for s in overrides if isinstance(s, str)]
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []


def evaluate_freshness(
    *,
    state_dir: Path,
    now: Optional[datetime] = None,
    _is_boot: bool = False,
) -> FreshnessVerdict:
    """Core freshness evaluation — single call, no retry logic.

    Returns a FreshnessVerdict with branch FRESH | STALE | ABSENT.
    Callers implement retry for ABSENT at boot; mid-run callers degrade.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    health_path = state_dir / "source_health.json"
    data = _load_source_health(health_path)

    if data is None:
        logger.warning("source_health.json absent or unreadable — cannot evaluate freshness")
        return FreshnessVerdict(branch="ABSENT")

    written_at_str = data.get("written_at")
    written_at_age = _age_seconds(written_at_str, now)

    # Mid-run: if written_at aged past 5 minutes, treat as ABSENT
    if not _is_boot and written_at_age is not None and written_at_age > ABSENT_MID_RUN_THRESHOLD_SECONDS:
        logger.warning(
            "source_health.json written_at=%s is %.0f seconds old (threshold=%d) — treating as ABSENT",
            written_at_str, written_at_age, ABSENT_MID_RUN_THRESHOLD_SECONDS,
        )
        return FreshnessVerdict(branch="ABSENT", written_at=written_at_str)

    # Boot: written_at check is softer — file just needs to exist and be parseable
    if _is_boot and written_at_age is not None and written_at_age > 90:
        # > 90 seconds at boot: could be stale from prior run — continue but note it
        logger.info(
            "source_health.json written_at age=%.0fs at boot (>90s) — evaluating contents anyway",
            written_at_age,
        )

    operator_overrides = _operator_overrides_from_control_plane(state_dir)
    sources = data.get("sources") or {}

    stale_sources: list[str] = []
    source_statuses: list[SourceStatus] = []
    day0_disabled = False
    ensemble_disabled = False

    for source, budget_seconds in FRESHNESS_BUDGETS.items():
        src_data = sources.get(source) or {}
        last_success = src_data.get("last_success_at")
        age = _age_seconds(last_success, now)

        is_overridden = source in operator_overrides
        is_fresh = (age is not None) and (age <= budget_seconds)
        is_stale = not is_fresh and not is_overridden

        degradation_flags: list[str] = []
        if is_stale:
            stale_sources.append(source)
            if source in DAY0_CAPTURE_GATED_SOURCES:
                day0_disabled = True
                degradation_flags.append("day0_capture_disabled")
            if source in ENSEMBLE_GATED_SOURCES:
                ensemble_disabled = True
                degradation_flags.append("ensemble_disabled")

        status = SourceStatus(
            source=source,
            fresh=is_fresh,
            stale=is_stale,
            last_success_at=last_success,
            budget_seconds=budget_seconds,
            age_seconds=age,
            degradation_flags=degradation_flags,
        )
        source_statuses.append(status)

    if stale_sources:
        logger.warning(
            "Freshness gate STALE: sources=%s day0_capture_disabled=%s ensemble_disabled=%s",
            stale_sources, day0_disabled, ensemble_disabled,
        )
        return FreshnessVerdict(
            branch="STALE",
            stale_sources=stale_sources,
            day0_capture_disabled=day0_disabled,
            ensemble_disabled=ensemble_disabled,
            degraded_data=True,
            operator_overrides=operator_overrides,
            source_statuses=source_statuses,
            written_at=written_at_str,
        )

    return FreshnessVerdict(
        branch="FRESH",
        stale_sources=[],
        day0_capture_disabled=False,
        ensemble_disabled=False,
        degraded_data=False,
        operator_overrides=operator_overrides,
        source_statuses=source_statuses,
        written_at=written_at_str,
    )


def evaluate_freshness_at_boot(state_dir: Path) -> FreshnessVerdict:
    """Boot-time freshness evaluation with 5-minute retry-with-backoff.

    If source_health.json is absent:
    - Polls every BOOT_RETRY_INTERVAL_SECONDS for up to BOOT_RETRY_MAX_ATTEMPTS.
    - On exhaustion: raises SystemExit with operator-actionable message.

    If STALE: returns the verdict immediately (caller handles degradation).
    If FRESH: returns immediately.
    """
    health_path = state_dir / "source_health.json"

    for attempt in range(1, BOOT_RETRY_MAX_ATTEMPTS + 1):
        verdict = evaluate_freshness(state_dir=state_dir, _is_boot=True)
        if verdict.branch != "ABSENT":
            return verdict
        if attempt < BOOT_RETRY_MAX_ATTEMPTS:
            logger.info(
                "source_health.json absent — retry %d/%d in %ds "
                "(is com.zeus.data-ingest running?)",
                attempt, BOOT_RETRY_MAX_ATTEMPTS, BOOT_RETRY_INTERVAL_SECONDS,
            )
            time.sleep(BOOT_RETRY_INTERVAL_SECONDS)
        else:
            logger.critical(
                "source_health.json absent after %d attempts (%.0f minutes) — FATAL. "
                "Is data-ingest daemon running? Check: launchctl list com.zeus.data-ingest",
                BOOT_RETRY_MAX_ATTEMPTS,
                BOOT_RETRY_MAX_ATTEMPTS * BOOT_RETRY_INTERVAL_SECONDS / 60,
            )
            raise SystemExit(
                "FATAL: source_health.json absent — is data-ingest daemon running? "
                "Check: launchctl list com.zeus.data-ingest"
            )

    # Unreachable but satisfies type checker
    return FreshnessVerdict(branch="ABSENT")


def evaluate_freshness_mid_run(state_dir: Path) -> FreshnessVerdict:
    """Mid-run freshness evaluation — degrade only, never exit.

    ABSENT → treated as all sources STALE (no exit, no retry).
    """
    verdict = evaluate_freshness(state_dir=state_dir, _is_boot=False)
    if verdict.branch == "ABSENT":
        logger.warning(
            "source_health.json absent mid-run — treating all sources as STALE (source_health_absent alert)"
        )
        # Synthesize full-STALE verdict
        stale_all = list(FRESHNESS_BUDGETS.keys())
        return FreshnessVerdict(
            branch="STALE",
            stale_sources=stale_all,
            day0_capture_disabled=True,
            ensemble_disabled=True,
            degraded_data=True,
            operator_overrides=[],
            source_statuses=[],
            written_at=None,
        )
    return verdict
