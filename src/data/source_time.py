# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §7
#   (Temporal Kernel); docs/operations/current/plans/data_temporal_kernel/PLAN.md;
#   config/source_release_calendar.yaml (temporal-fact authority);
#   ground-truth audit 2026-05-24 (mx2t3/mx2t6 cutover, freshness-ladder derivation).
"""Temporal control plane for data sources — PR1 of the Data Temporal Kernel program.

This module is the missing *center* the refactor spec names (§7 "Temporal Kernel"):
it formalises TIME as first-class source state. The catastrophic live-money failure it
guards is "data present but temporally wrong" — early, late, stale, blocked, backfilled,
from the wrong cycle, or from the wrong local day. A row can carry the right VALUE on the
wrong CLOCK.

Three type layers:

  * ``TimePlane``           — *which clock* a timestamp lives on. Twelve distinct planes;
                              the whole point is that write-time (COLLECTION/IMPORT) is NOT
                              event-time (EVENT) is NOT issue/release time. Conflating them
                              is how a backfill written today masquerades as fresh live data.
  * ``PartialPolicy``       — what an INCOMPLETE partition may authorize (calendar axis:
                              BLOCK_LIVE / BLOCK_LIVE / ALLOW).
  * ``LateArrivalPolicy``   — what to DO with a row that arrives later than expected
                              (replace / append-revision / hold / ignore-if-closed /
                              backfill-only). Orthogonal to PartialPolicy — the draft this
                              replaces conflated the two.

``TemporalPolicy`` is a typed *view over* ``config/source_release_calendar.yaml`` — NOT a
re-declaration of its constants and NOT a fourth source registry (that would duplicate
``architecture/data_sources_registry_2026_05_08.yaml`` + ``src/data/forecast_source_registry.py``;
forbidden by the anti-duplication law). ``load_temporal_policy(calendar_id)`` reads exactly one
calendar entry. Every *temporal fact* (safe-fetch lag, partial policy, live authorization,
backfill flag, freshness windows) comes from that entry — ZERO hardcoded temporal facts here.

Fields the calendar does not (yet) carry — the per-source time-FIELD-NAME mappings
(``event_time_field``, ``source_issue_time_field``, …) and ``partition_grain`` refinements —
are left at their structural defaults / ``None`` by the calendar loader rather than invented
per-source. They are populated by the SourceContract layer (later PR) or by a calendar
schema extension; the TYPE carries them now for forward-compatibility and spec fidelity.

Structural policy-margin defaults (``safe_fetch_jitter`` 5min, ``max_clock_skew`` 5s,
``max_backfill_live_masking_age`` 0) are the spec's declared defaults, NOT calendar-derived
facts. They are documented as such and overridable; ``safe_fetch_not_before`` deliberately
does NOT fold jitter into the core gate (the gate is ``issue + lag``; jitter is an advisory
margin the frontier/report layer applies in PR2).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

import yaml


# ---------------------------------------------------------------------------
# Freshness ladder derivation ratios (applied to calendar max_source_lag_seconds).
# NOT invented absolute windows — ratios of the calendar's own max-lag field:
#   degraded_after = _DEGRADED_RATIO * max_source_lag_seconds   (source entering staleness)
#   expired_after  = _EXPIRED_RATIO  * max_source_lag_seconds   (source no longer usable)
# The spec's invented 18h/24h/30h ladder is explicitly rejected by the 2026-05-24 audit;
# the calendar already carries the per-source ceiling (ECMWF 108000s=30h, OpenMeteo
# 172800s=48h, TIGGE 604800s=7d) so freshness must be a FUNCTION of that field.
# ---------------------------------------------------------------------------
_DEGRADED_RATIO: float = 0.8
_EXPIRED_RATIO: float = 1.0

_CALENDAR_PATH = Path(__file__).resolve().parents[2] / "config" / "source_release_calendar.yaml"


class TimePlane(str, Enum):
    """Which clock a timestamp lives on.

    Distinguishing these is the reason this module exists: a value is only meaningful
    paired with the plane its timestamp belongs to. Write-time planes (COLLECTION, IMPORT,
    ARTIFACT) must never be mistaken for the planes that establish live authority
    (SOURCE_ISSUE, SOURCE_RELEASE, EVENT, LOCAL_DAY).
    """

    SCHEDULER = "scheduler"            # when our cron/job intended to run
    SOURCE_ISSUE = "source_issue"      # model cycle / data issue time (e.g. 00Z)
    SOURCE_RELEASE = "source_release"  # when the source publishes/releases the cycle
    SOURCE_PUBLISH = "source_publish"  # provider-stamped publish time of the row
    EVENT = "event"                    # physical valid time of the datum (forecast step / obs time)
    LOCAL_DAY = "local_day"            # city-local civil calendar day the datum belongs to
    COLLECTION = "collection"          # when WE fetched it (captured_at) — a WRITE-time plane
    IMPORT = "import"                  # when WE wrote it to the DB (imported_at) — a WRITE-time plane
    READINESS = "readiness"            # when readiness was evaluated / expires
    MARKET = "market"                  # venue quote / market-update time
    BLOCKCHAIN = "blockchain"          # on-chain block time / finality
    ARTIFACT = "artifact"              # file mtime / generated-at — never authority


class PartialPolicy(str, Enum):
    """What an INCOMPLETE partition (missing steps/members/hours) may authorize.

    This is the calendar's ``partial_policy`` axis. Distinct from ``LateArrivalPolicy``.
    """

    BLOCK_LIVE = "BLOCK_LIVE"      # incomplete partition must not authorize live trading
    ALLOW = "ALLOW"               # partial is acceptable for live (rare)


class LateArrivalPolicy(str, Enum):
    """What to DO with a row that arrives later than its expected release window.

    Orthogonal to ``PartialPolicy``: a partition can be complete-but-late, or
    incomplete-but-on-time. This axis governs the WRITE disposition of a late row.
    """

    REPLACE_SAME_IDEMPOTENCY_KEY = "replace_same_idempotency_key"  # overwrite the keyed row
    APPEND_REVISION = "append_revision"                            # keep history, add a revision
    HOLD = "hold"                                                   # hold out of the live chain
    IGNORE_IF_LIVE_CLOSED = "ignore_if_live_closed"                # drop if the market already closed
    BACKFILL_ONLY = "backfill_only"                                # only ever a backfill write


def default_late_arrival_for(
    partial_policy: PartialPolicy, backfill_only: bool
) -> LateArrivalPolicy:
    """Suggest a *safe* late-arrival disposition for a source — an EXPLICIT helper,
    NOT a calendar fact baked into ``TemporalPolicy``.

    The release calendar does not carry a late-arrival axis. Binding it inside the
    loader would smuggle a hardcoded axis-coupling into a module whose contract is
    "zero hardcoded temporal facts" (and would silently violate the orthogonality the
    two axes are supposed to have). So this is a free function consumers call by choice;
    ``load_temporal_policy`` leaves ``TemporalPolicy.late_arrival_policy`` as ``None``.
    The SourceContract layer / a calendar extension owns the real per-source value later.

    Fail-safe: backfill-only sources never replace a live row.
    """
    if backfill_only:
        return LateArrivalPolicy.BACKFILL_ONLY
    return LateArrivalPolicy.REPLACE_SAME_IDEMPOTENCY_KEY


@dataclass(frozen=True)
class TemporalPolicy:
    """Typed temporal semantics for one calendar entry — a view over the calendar yaml.

    Temporal FACTS (``safe_fetch_lag``, ``partial_policy``, ``live_authorization``,
    ``backfill_only``, freshness windows, ``max_source_lag_seconds``) are calendar-sourced.
    Time-FIELD-NAME mappings and ``partition_grain`` default to ``None`` / structural
    defaults when the calendar is silent (the calendar loader does not invent them).
    Policy-margin defaults (jitter, clock-skew, masking-age) are the spec's declared
    defaults, not calendar facts.
    """

    # --- Identity (calendar) ---
    calendar_id: str
    source_id: str

    # --- Authority axes (calendar) ---
    live_authorization: bool
    backfill_only: bool
    partial_policy: PartialPolicy

    # --- Safe-fetch gate (calendar fact) ---
    safe_fetch_lag: timedelta                       # = minutes(entry.safe_fetch.default_lag_minutes)

    # --- Freshness ladder (derived from calendar max_source_lag_seconds) ---
    max_source_lag_seconds: int
    freshness_sla: timedelta                        # = seconds(max_source_lag_seconds)
    degraded_after: timedelta                       # = _DEGRADED_RATIO * max lag
    expired_after: timedelta                        # = _EXPIRED_RATIO  * max lag

    # --- Partitioning ---
    partition_grain: Literal["cycle", "date", "hour", "block", "market", "artifact"]

    # --- Late-arrival disposition (calendar-silent → None; see default_late_arrival_for) ---
    # NOT derived in the loader: the calendar carries no late-arrival axis, so binding it
    # here would be a hardcoded fact. Consumers use default_late_arrival_for() explicitly.
    late_arrival_policy: Optional[LateArrivalPolicy] = None

    # --- Time-field-name mappings (calendar-silent → None until SourceContract/calendar ext) ---
    event_time_field: Optional[str] = None
    source_issue_time_field: Optional[str] = None
    source_release_time_field: Optional[str] = None
    source_publish_time_field: Optional[str] = None
    target_local_date_field: Optional[str] = None
    timezone_field: Optional[str] = None
    collection_time_field: str = "captured_at"      # spec default (write-time convention)
    import_time_field: str = "imported_at"          # spec default (write-time convention)

    # --- Policy-margin defaults (spec-declared, NOT calendar facts) ---
    safe_fetch_jitter: timedelta = timedelta(minutes=5)
    allow_early_partial: bool = False
    max_clock_skew: timedelta = timedelta(seconds=5)
    max_backfill_live_masking_age: timedelta = timedelta(0)

    # ---- Test/back-compat scalar views (preserve PR1 relationship-test API) ----
    @property
    def safe_fetch_lag_minutes(self) -> int:
        return int(self.safe_fetch_lag.total_seconds() // 60)

    @property
    def degraded_after_seconds(self) -> float:
        return self.degraded_after.total_seconds()

    @property
    def expired_after_seconds(self) -> float:
        return self.expired_after.total_seconds()

    def safe_fetch_not_before(self, issue: datetime) -> datetime:
        """Earliest wall-clock time it is safe to fetch this source's ``issue`` cycle.

        Core gate = ``issue + safe_fetch_lag`` (calendar fact only). ``safe_fetch_jitter``
        is deliberately NOT folded in here: the gate is the contractual minimum; jitter is
        an advisory margin the frontier/report layer (PR2) applies on top. This keeps the
        gate exactly reproducible from the calendar (and matches the relationship test).

        NOTE (PR1 scope): ``safe_fetch_lag`` reflects only the calendar entry's TOP-LEVEL
        ``safe_fetch.default_lag_minutes``. Cycle-pair overrides (``cycle_profiles[].safe_fetch``
        — e.g. 285min for 06/18 short cycles vs 485min default) are NOT surfaced here. A
        consumer needing cycle-aware safe-fetch must read ``calendar.entries[].cycle_profiles``
        directly until PR2 adds cycle-aware resolution. For a 06Z/18Z issue this method
        reports a conservative (later) gate, never an early one — fail-late, not fail-open.
        """
        return issue + self.safe_fetch_lag

    def freshness_state(self, age_seconds: float) -> Literal["CURRENT", "DEGRADED", "EXPIRED"]:
        """Freshness band for data of the given age (seconds since its EVENT/SOURCE time).

        Age MUST be measured on a source/event-time plane, never on a write-time plane —
        a backfill's fresh ``captured_at`` would otherwise report CURRENT for stale data.
        """
        if age_seconds < self.degraded_after.total_seconds():
            return "CURRENT"
        if age_seconds < self.expired_after.total_seconds():
            return "DEGRADED"
        return "EXPIRED"


def _coerce_partial_policy(raw: object) -> PartialPolicy:
    try:
        return PartialPolicy(str(raw))
    except ValueError:
        # Unknown/absent partial policy is treated as the safest: block live.
        return PartialPolicy.BLOCK_LIVE


def _derive_partition_grain(
    entry: dict[str, Any],
) -> Literal["cycle", "date", "hour", "block", "market", "artifact"]:
    """Conservative partition-grain inference from calendar shape (no per-source table).

    Cycle-bearing forecast entries partition by cycle; everything else defaults to date.
    Finer grains (hour/block/market/artifact) are set by the SourceContract layer later.
    """
    if entry.get("cycle_hours_utc"):
        return "cycle"
    return "date"


@lru_cache(maxsize=4)
def _load_calendar_index(mtime_ns: int) -> dict[str, dict[str, Any]]:
    """Parse the release calendar ONCE and index entries by calendar_id.

    Keyed by the file's mtime so repeated calls (e.g. the frontier loop iterating every
    entry) reuse a single parse, while an edited calendar still triggers a fresh read.
    The ``mtime_ns`` arg is the cache key — callers pass it via ``_calendar_index()``.
    """
    with _CALENDAR_PATH.open() as f:
        data = yaml.safe_load(f)
    return {
        e["calendar_id"]: e
        for e in data.get("entries", [])
        if isinstance(e, dict) and e.get("calendar_id")
    }


def _calendar_index() -> dict[str, dict[str, Any]]:
    """Current calendar index (cached by mtime)."""
    return _load_calendar_index(_CALENDAR_PATH.stat().st_mtime_ns)


def load_temporal_policy(calendar_id: str) -> TemporalPolicy:
    """Build a :class:`TemporalPolicy` for ``calendar_id`` from the release calendar.

    Reads exactly ONE entry (from an mtime-cached parse — no re-parse per call). Raises
    ``KeyError`` if not found. All temporal facts come from the entry; calendar-silent
    fields keep their structural defaults / ``None``.
    """
    index = _calendar_index()
    entry = index.get(calendar_id)
    if entry is None:
        raise KeyError(
            f"calendar_id {calendar_id!r} not found in source_release_calendar.yaml. "
            f"Available: {sorted(index)}"
        )

    partial_policy = _coerce_partial_policy(entry.get("partial_policy"))
    # late_arrival_policy is NOT derived here — the calendar carries no late-arrival axis.
    # It stays None; consumers call default_late_arrival_for() explicitly when needed.

    safe_fetch_block = entry.get("safe_fetch", {}) or {}
    safe_fetch_lag = timedelta(minutes=int(safe_fetch_block.get("default_lag_minutes", 0)))

    max_source_lag_seconds = int(entry["max_source_lag_seconds"])
    freshness_sla = timedelta(seconds=max_source_lag_seconds)
    degraded_after = timedelta(seconds=_DEGRADED_RATIO * max_source_lag_seconds)
    expired_after = timedelta(seconds=_EXPIRED_RATIO * max_source_lag_seconds)

    return TemporalPolicy(
        calendar_id=calendar_id,
        source_id=entry["source_id"],
        live_authorization=bool(entry.get("live_authorization", False)),
        backfill_only=bool(entry.get("backfill_only", False)),
        partial_policy=partial_policy,
        safe_fetch_lag=safe_fetch_lag,
        max_source_lag_seconds=max_source_lag_seconds,
        freshness_sla=freshness_sla,
        degraded_after=degraded_after,
        expired_after=expired_after,
        partition_grain=_derive_partition_grain(entry),
    )
