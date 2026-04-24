"""Canonical decision_group_id producer.

The **independent sample unit** for calibration is the decision group:

    g = (city, target_date, issue_time, source_model_version)

All pair rows from a single ensemble snapshot at a single issue_time share
the same underlying forecast and are NOT independent. Every statistical
operation that assumes independence must count / resample / split by
decision_group, never by pair row (see `zeus_math_spec.md §12.1`).

This module defines `compute_id()` — the ONLY permitted producer of
`calibration_pairs.decision_group_id` in the codebase. A `semantic_linter`
rule (landed separately) forbids `sha1`/`md5`/`hashlib` imports on any
identifier-generation path outside this file.

The hash template uses **explicit strftime** rather than `.isoformat()`
because Python's `.isoformat()` output differs across `date`, naive
`datetime`, and timezone-aware `datetime` for the same logical point in
time. That fragility is fatal: the same physical ensemble snapshot
reaching `compute_id()` via direct Python construction, SQLite TEXT
round-trip, or JSON round-trip would get different hashes — silently
duplicating decision_groups and inflating `n_eff` for every downstream
statistical operation.

Authority: `docs/operations/data_rebuild_plan.md` v2.2 §3.3, Change G.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Union

DateLike = Union[date, datetime, str]
DatetimeLike = Union[datetime, str]


def compute_id(
    city: str,
    target_date: DateLike,
    issue_time: DatetimeLike,
    source_model_version: str,
) -> str:
    """Canonical decision_group_id producer.

    This is the ONLY function in the codebase permitted to compute this hash.
    All callers must import this.

    Args:
        city: City name (e.g., "NYC"). Non-empty string.
        target_date: The market's settlement date. Accepted as `datetime.date`,
            `datetime.datetime` (the date component is extracted), or an
            ISO-formatted `YYYY-MM-DD` string.
        issue_time: The ensemble forecast's issue time. Accepted as a
            timezone-aware `datetime.datetime` or an ISO-formatted string
            parseable by `datetime.fromisoformat`. Naive datetimes are
            rejected with `TypeError` because they are ambiguous — there
            is no correct hash for an unknown timezone.
        source_model_version: TIGGE cycle / model version identifier. Non-empty.

    Returns:
        SHA-1 hex digest as a string. Stable across Python versions and
        across reads from SQLite TEXT columns.

    Raises:
        TypeError: `target_date` or `issue_time` is not an accepted type,
            or `issue_time` is a naive datetime.
        ValueError: `city` or `source_model_version` is empty, or an
            ISO-formatted date / datetime string fails to parse.

    Normalization (before hashing):
      - `target_date` is coerced to `datetime.date` and formatted as
        `'%Y-%m-%d'` — strips any time/tz component from a datetime input
        and produces a single canonical byte sequence.
      - `issue_time` is coerced to a UTC-aware `datetime.datetime` via
        `astimezone(timezone.utc)` and formatted as
        `'%Y-%m-%dT%H:00:00Z'` — hour resolution, explicit UTC, explicit
        `Z` suffix. Sub-hour components are truncated to zero so that
        forecasts released within the same hour (Zeus's cycle cadence is
        ~30 minutes) hash together only if they come from the same
        forecast cycle identified by `source_model_version`.

    Regression test (relationship test R3, TRAP A): the same logical
    snapshot reaching this function via 3 distinct code paths — direct
    Python construction, SQLite TEXT round-trip via
    `datetime.fromisoformat`, SQLite TEXT round-trip via
    `datetime.strptime` — must produce identical hashes.
    """
    # --- sanity checks on the string inputs ---
    if not isinstance(city, str) or not city:
        raise ValueError(f"city must be a non-empty string, got {city!r}")
    if not isinstance(source_model_version, str) or not source_model_version:
        raise ValueError(
            f"source_model_version must be a non-empty string, got "
            f"{source_model_version!r}"
        )

    # --- target_date normalization ---
    normalized_date: date
    if isinstance(target_date, str):
        try:
            normalized_date = date.fromisoformat(target_date)
        except ValueError as exc:
            raise ValueError(
                f"target_date string must be ISO 'YYYY-MM-DD', got "
                f"{target_date!r}: {exc}"
            ) from exc
    elif isinstance(target_date, datetime):
        # `datetime` is a subclass of `date`, check datetime FIRST.
        normalized_date = target_date.date()
    elif isinstance(target_date, date):
        normalized_date = target_date
    else:
        raise TypeError(
            f"target_date must be date / datetime / str, got "
            f"{type(target_date).__name__}"
        )
    date_str = normalized_date.strftime("%Y-%m-%d")

    # --- issue_time normalization ---
    normalized_dt: datetime
    if isinstance(issue_time, str):
        # Python 3.11+ accepts trailing "Z" in fromisoformat, but be safe.
        iso = issue_time.replace("Z", "+00:00") if issue_time.endswith("Z") else issue_time
        try:
            normalized_dt = datetime.fromisoformat(iso)
        except ValueError as exc:
            raise ValueError(
                f"issue_time string must be ISO datetime, got {issue_time!r}: {exc}"
            ) from exc
    elif isinstance(issue_time, datetime):
        normalized_dt = issue_time
    else:
        raise TypeError(
            f"issue_time must be datetime / str, got {type(issue_time).__name__}"
        )

    if normalized_dt.tzinfo is None or normalized_dt.utcoffset() is None:
        raise TypeError(
            "issue_time must be timezone-aware; naive datetime is ambiguous "
            "and would silently produce wrong hashes across code paths. "
            "Use datetime(..., tzinfo=timezone.utc) or an ISO string with "
            "a UTC offset / 'Z' suffix."
        )

    utc_dt = normalized_dt.astimezone(timezone.utc)
    # Hour resolution — truncate minute/second/microsecond to zero before
    # formatting so sub-hour jitter does not fragment decision groups.
    utc_dt_hour = utc_dt.replace(minute=0, second=0, microsecond=0)
    time_str = utc_dt_hour.strftime("%Y-%m-%dT%H:00:00Z")

    # --- hash ---
    payload = f"{city}|{date_str}|{time_str}|{source_model_version}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
