"""Deprecated fail-closed TIGGE ENS entrypoint plus TIGGE identity helper."""

from __future__ import annotations

import sys


def tigge_issue_time_from_members(members: list[dict]) -> str:
    """Return the UTC TIGGE cycle issue time from normalized member metadata."""
    data_dates = {str(m.get("data_date", "")) for m in members}
    data_times = {str(m.get("data_time", "")) for m in members}
    if len(data_dates) != 1 or len(data_times) != 1:
        raise ValueError(
            f"TIGGE members disagree on data_date/data_time: {data_dates}, {data_times}"
        )
    data_date = next(iter(data_dates))
    data_time_raw = next(iter(data_times))
    if len(data_date) != 8 or not data_date.isdigit():
        raise ValueError(f"Invalid TIGGE data_date {data_date!r}")
    try:
        time_value = int(data_time_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid TIGGE data_time {data_time_raw!r}") from exc
    time_str = f"{time_value:04d}"
    hour = int(time_str[:2])
    minute = int(time_str[2:])
    if hour > 23 or minute > 59:
        raise ValueError(f"Invalid TIGGE data_time {data_time_raw!r}")
    return f"{data_date[:4]}-{data_date[4:6]}-{data_date[6:8]}T{hour:02d}:{minute:02d}:00Z"


def main(argv: list[str] | None = None) -> int:
    _ = argv
    print(
        "scripts/etl_tigge_ens.py is retired and fails closed.\n"
        "Use the audited GRIB ingest plus rebuild_calibration_pairs_canonical.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
