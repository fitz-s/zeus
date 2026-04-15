"""Ensemble-snapshot provenance quarantine contract.

Single source of truth for which ``data_version`` values are forbidden
on the ``ensemble_snapshots`` table. Any writer (live ingest, backfill,
or rebuild) MUST consult this contract before inserting rows. Any
reader that fetches snapshots for calibration MUST call
``assert_data_version_allowed`` on each row before forwarding it to
the Platt training path.

2026-04-14 quarantine rationale
-------------------------------
The TIGGE ``param_167`` (``2t`` / ``stepType=instant``) archive was
downloaded with the wrong physical quantity. Each stored
``members_json`` vector is a **per-member point temperature at
``issue_time + step`` hours (UTC)**, not the per-member local-day
maximum that the live path consumes via
``src/signal/ensemble_signal.py::member_maxes_for_target_date``.

Training Platt on point forecasts but querying it with daily-max
inputs at inference time produces a systematic train/infer geometry
mismatch that neither density normalization nor Platt's ``C*lead_days``
term can repair. The replacement archive is ``param=121.128`` / ``mx2t6``
(``stepType=max``, 6-hour sliding windows) composited into per-member
**local peak-window max**, tagged in DB as
``tigge_mx2t6_local_peak_window_max_v1``.

Until that replacement lands, the old ``param_167`` variants must be
kept out of every live calibration path — not just filtered at read
time, but refused at write time too, so nothing downstream can rely on
"the table is clean because I cleared it yesterday". This contract is
the refusal point.

Semantics
---------
- ``QUARANTINED_DATA_VERSIONS``: exact-match set — versions that are
  known to exist in the archive and MUST never be touched.
- ``QUARANTINED_DATA_VERSION_PREFIXES``: prefix-match tuple — catches
  near-future variants (e.g. ``tigge_step024_v2_*`` that may be produced
  by an experimental ingest path). Prefix match is deliberately
  conservative: a legitimate ``tigge_mx2t6_*`` version does NOT match
  any quarantine prefix.
- Both sets are additive; extend them if new failure modes surface.

Failure mode on write
---------------------
Writers call ``assert_data_version_allowed`` and let
``DataVersionQuarantinedError`` propagate. That bubbles up the caller
(live ingest, backfill, test fixture) and fails loudly, which is what
we want — silent drops hide bugs.

Failure mode on read
--------------------
Callers can use ``is_quarantined`` to partition rows (drop silently,
count, log). ``rebuild_calibration_pairs_canonical.py`` does this so
dry-run reports always surface the refusal count even when
``--allow-unaudited-ensemble`` is passed.
"""

from __future__ import annotations

from typing import Iterable


class DataVersionQuarantinedError(RuntimeError):
    """Raised when a writer tries to persist a quarantined data_version."""


# Known-bad exact matches. These are the two ``param_167`` variants
# that exist in the 2026-04-14 TIGGE partial archive. Adding them by
# name makes the refusal precise — a future legitimate ingest can use
# any data_version not in this set without tripping the guard.
QUARANTINED_DATA_VERSIONS: frozenset[str] = frozenset({
    "tigge_step024_v1_near_peak",
    "tigge_step024_v1_overnight_snapshot",
    "tigge_partial_legacy",
})


# Prefix matches for conservative blanket refusal. Covers the case where
# someone re-runs the old ingest with a bumped version suffix
# (``tigge_step024_v2_``, ``tigge_param167_v3``, etc.). These are all
# point-in-time ``2t instant`` forecasts — structurally wrong physical
# quantity — so they never belong in a Platt training set. The
# replacement ``tigge_mx2t6_*`` family is intentionally NOT in the
# quarantine list.
QUARANTINED_DATA_VERSION_PREFIXES: tuple[str, ...] = (
    "tigge_step",       # any tigge_step*_v*
    "tigge_param167",   # any tigge_param167*
    "tigge_2t_instant", # any hand-tagged point-forecast variant
)


def is_quarantined(data_version: str | None) -> bool:
    """True if ``data_version`` is forbidden for ensemble_snapshots writes."""
    if not data_version:
        return False
    if data_version in QUARANTINED_DATA_VERSIONS:
        return True
    for prefix in QUARANTINED_DATA_VERSION_PREFIXES:
        if data_version.startswith(prefix):
            return True
    return False


def assert_data_version_allowed(data_version: str | None, *, context: str = "") -> None:
    """Raise ``DataVersionQuarantinedError`` if ``data_version`` is quarantined.

    Call this from every writer of ``ensemble_snapshots`` — live ingest,
    backfill, test fixtures, rebuild. The ``context`` parameter is
    appended to the error message so operators can see which caller
    tripped the guard.
    """
    if is_quarantined(data_version):
        ctx = f" (context={context})" if context else ""
        raise DataVersionQuarantinedError(
            f"ensemble_snapshots write refused: data_version={data_version!r} "
            f"is quarantined per src/contracts/ensemble_snapshot_provenance.py. "
            f"The quarantine exists because param_167 point forecasts do not "
            f"match the local-day maximum physical quantity the live path "
            f"consumes. Use tigge_mx2t6_local_peak_window_max_v1 (or the "
            f"current replacement tag) instead.{ctx}"
        )


def filter_allowed(
    rows: Iterable[dict],
    *,
    data_version_key: str = "data_version",
) -> tuple[list[dict], list[dict]]:
    """Split an iterable of row-dicts into (allowed, quarantined).

    Reader-side helper. Lets ``rebuild_calibration_pairs_canonical.py``
    report quarantine counts in its dry-run plan without crashing on
    legacy rows.
    """
    allowed: list[dict] = []
    quarantined: list[dict] = []
    for row in rows:
        dv = row.get(data_version_key) if isinstance(row, dict) else None
        if is_quarantined(dv):
            quarantined.append(row)
        else:
            allowed.append(row)
    return allowed, quarantined
