"""Pending audited GRIB-to-ensemble_snapshots ingestor.

Task #53 owns the real implementation. This placeholder exists so topology
can point at the intended audited producer without routing agents/operators
to retired direct TIGGE calibration writers.

MANDATORY CONTRACT for the future implementation
------------------------------------------------
When this script is written, every INSERT into ``ensemble_snapshots`` MUST
call::

    from src.contracts.ensemble_snapshot_provenance import (
        assert_data_version_allowed,
    )
    assert_data_version_allowed(data_version, context="ingest_grib_to_snapshots")

Allowed data_version values (2026-04-14):

- ``tigge_mx2t6_local_peak_window_max_v1`` (per the mx2t6 redownload plan at
  ``/Users/leofitz/.openclaw/workspace-venus/51 source data/TIGGE_MX2T6_LOCALMAX_REDOWNLOAD_PLAN.md``)
- Any new tag that is NOT caught by
  ``src/contracts/ensemble_snapshot_provenance.QUARANTINED_DATA_VERSION_PREFIXES``

Forbidden families (caught by the guard):

- ``tigge_step024_v1_near_peak`` / ``tigge_step024_v1_overnight_snapshot``
  (old partial ingest, wrong physical quantity)
- ``tigge_step*`` / ``tigge_param167*`` / ``tigge_2t_instant*`` (blanket
  prefix refusal of any 2t-instant point-forecast variant)

The guard raises ``DataVersionQuarantinedError`` — do not catch it. The
error message tells operators to use the mx2t6 replacement tag.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    _ = argv
    print(
        "scripts/ingest_grib_to_snapshots.py is not implemented yet.\n"
        "Complete task #53 before running the TIGGE raw->DB ingest.\n"
        "\n"
        "When implementing, import assert_data_version_allowed from\n"
        "src/contracts/ensemble_snapshot_provenance.py and call it before\n"
        "every ensemble_snapshots INSERT.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
