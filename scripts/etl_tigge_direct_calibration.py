"""Deprecated fail-closed TIGGE direct-calibration entrypoint."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    _ = argv
    print(
        "scripts/etl_tigge_direct_calibration.py is retired and fails closed.\n"
        "Use the audited GRIB ingest plus rebuild_calibration_pairs_canonical.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
