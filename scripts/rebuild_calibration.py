"""Deprecated fail-closed calibration rebuild entrypoint.

The historical implementation in this file computed a simplified local P_raw
and could generate calibration pairs with a bin taxonomy that diverged from the
live/canonical Monte Carlo path.  It is intentionally retained only as a
fail-closed tombstone so old operator commands cannot mutate calibration tables.

Use `scripts/rebuild_calibration_pairs_canonical.py` for the active canonical
calibration-pair rebuild path.
"""

from __future__ import annotations

import sys


REPLACEMENT = "python scripts/rebuild_calibration_pairs_canonical.py --dry-run"


def main(argv: list[str] | None = None) -> int:
    _ = argv
    print(
        "scripts/rebuild_calibration.py is retired and fails closed.\n"
        "Use the canonical WMO/Monte-Carlo path instead:\n"
        f"  {REPLACEMENT}\n"
        "For writes, review its --force and --allow-unaudited-ensemble gates "
        "explicitly; this legacy command does not redirect automatically.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
