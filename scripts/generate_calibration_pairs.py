"""Deprecated fail-closed calibration pair generator.

This legacy entrypoint depended on market_events and an older P_raw/bin
alignment path.  The canonical calibration-pair producer is now
`scripts/rebuild_calibration_pairs_canonical.py`, which uses verified
observations, verified ensemble snapshots, canonical bins, WMO semantics, and
explicit decision_group_id production.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    _ = argv
    print(
        "scripts/generate_calibration_pairs.py is retired and fails closed.\n"
        "Use scripts/rebuild_calibration_pairs_canonical.py --dry-run instead.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
