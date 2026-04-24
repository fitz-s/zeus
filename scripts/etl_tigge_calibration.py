"""Deprecated fail-closed TIGGE calibration ETL entrypoint.

This legacy script wrote `ensemble_snapshots`, `calibration_pairs`, and Platt
models through an unaudited direct path.  The canonical flow is:

1. audited GRIB-to-snapshot ingestion (task #53),
2. `scripts/rebuild_calibration_pairs_canonical.py`,
3. `scripts/refit_platt.py`.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    _ = argv
    print(
        "scripts/etl_tigge_calibration.py is retired and fails closed.\n"
        "Use the audited GRIB ingest plus rebuild_calibration_pairs_canonical.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
