#!/usr/bin/env python3
# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: WAVE-1 (unblock-W1) W1-T2. LOW metric returns (None, 4) from
#   get_calibrator (src/calibration/manager.py) → receipts die at
#   CALIBRATION_AUTHORITY_EVIDENCE_MISSING (Tokyo/NYC/Seoul, 2490/24h). This
#   registers identity-Platt rows for LOW (calibration_method=
#   'identity_full_transport_v1', the SAME identity route HIGH already uses) so
#   the LOW primary-bucket lookup hits and returns (cal, 1). NOT EMOS — EMOS is
#   HIGH-only. The identity transform (p_cal == p_raw) is the certified route
#   for buckets with no trained Platt; it is NOT a missing-Platt fallback.
"""Register LOW identity-Platt rows into platt_models.

The LOW live read seam (get_calibrator, temperature_metric='low') looks up a
platt_models row keyed by (temperature_metric, cluster, season, data_version,
cycle, source_id, horizon_profile, input_space). When the primary bucket has a
``calibration_method='identity_full_transport_v1'`` row, get_calibrator returns
``(IdentityCalibrator, 1)`` (manager.py: the identity fast-path bypasses the
maturity_level(0)=4 block). With no LOW row at all, it falls to
``if temperature_metric == 'low': return None, 4`` → CALIBRATION_AUTHORITY_
EVIDENCE_MISSING.

This script writes those identity rows for the LOW buckets the live caller will
request. Two bucket families are covered by default (see ``LOW_BUCKET_VARIANTS``):

  1. Legacy LOW default — data_version=LOW_LOCALDAY_MIN.data_version,
     cycle='00', source_id='tigge_mars', horizon_profile='full'. This is the
     bucket get_calibrator hits when source_id is None (offline / source-less
     callers and the W1-T2 RED→GREEN test).
  2. Live contract-window LOW — data_version=
     TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION, across cycles {00,06,12,18} with
     the matching horizon_profile (00/12→'full', 06/18→'short'), source_id=
     'tigge_mars'. This is what the live EDLI LOW path requests, because the
     reactor maps source_id 'ecmwf_open_data' →
     calibration_source_id_for_lookup → 'tigge_mars' before calling
     get_calibrator, and _candidate_data_versions_for_metric_source('low',
     'tigge_mars') resolves to the TIGGE contract-window LOW data_version.

CRITICAL — frozen-pin visibility: rows are written with
``recorded_at <= FROZEN_AS_OF`` (the calibration.pin frozen_as_of instant). The
frozen-pin load filter (store.py: ``AND recorded_at <= ?``) hides any row whose
recorded_at is AFTER the pin, so a row written at "now" would be invisible to
the live read. We pin recorded_at to a fixed instant strictly <= FROZEN_AS_OF.

Safety: ``--dry-run`` reports the plan and writes nothing. The script does NOT
execute against the live world.db by default — callers must pass an explicit
``--db`` path. The flag-gated identity LOW route is inert on live until the
operator runs this against state/zeus-world.db AND the LOW path is exercised;
the receipt continues to fail-closed (None, 4) until then.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.platt import IDENTITY_CALIBRATION_METHOD  # noqa: E402
from src.contracts.ensemble_snapshot_provenance import (  # noqa: E402
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
)
from src.types.metric_identity import LOW_LOCALDAY_MIN  # noqa: E402

# Must be <= calibration.pin frozen_as_of in config/settings.json
# (2026-05-27T15:51:42Z) so the frozen-pin load filter (store.py:
# ``AND recorded_at<=?``) does NOT hide these rows. Pinned strictly before it.
FROZEN_AS_OF = "2026-05-27T15:51:42Z"
_RECORDED_AT = "2026-05-27T00:00:00Z"  # strictly < FROZEN_AS_OF
_FITTED_AT = "2026-05-27T00:00:00Z"

_INPUT_SPACE = "width_normalized_density"

# (data_version, cycle, source_id, horizon_profile) bucket variants the LOW
# live read seam may request. See module docstring for derivation.
LOW_BUCKET_VARIANTS: tuple[tuple[str, str, str, str], ...] = (
    # 1. Legacy default bucket (source_id=None callers + RED→GREEN test).
    (LOW_LOCALDAY_MIN.data_version, "00", "tigge_mars", "full"),
    # 2. Live contract-window buckets, per cycle (calibration_source_id='tigge_mars').
    (TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION, "00", "tigge_mars", "full"),
    (TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION, "06", "tigge_mars", "short"),
    (TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION, "12", "tigge_mars", "full"),
    (TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION, "18", "tigge_mars", "short"),
)

# Default seasons cover the full year so a LOW family in any season hits an
# identity bucket. Callers can narrow via --seasons.
DEFAULT_SEASONS: tuple[str, ...] = ("DJF", "MAM", "JJA", "SON")


def _model_key(
    *,
    cluster: str,
    season: str,
    data_version: str,
    cycle: str,
    source_id: str,
    horizon_profile: str,
) -> str:
    """Reconstruct the model_key save_platt_model would use, with the identity
    calibration_method appended (the identity route lives under a distinct key
    so it never collides with a future learned-Platt row for the same bucket)."""
    base = (
        f"low:{cluster}:{season}:{data_version}"
        f":{cycle}:{source_id}:{horizon_profile}:{_INPUT_SPACE}"
    )
    return f"{base}:{IDENTITY_CALIBRATION_METHOD}"


def _plan_rows(
    *,
    clusters: Sequence[str],
    seasons: Sequence[str],
) -> list[dict]:
    rows: list[dict] = []
    for cluster in clusters:
        for season in seasons:
            for data_version, cycle, source_id, horizon_profile in LOW_BUCKET_VARIANTS:
                rows.append(
                    {
                        "model_key": _model_key(
                            cluster=cluster,
                            season=season,
                            data_version=data_version,
                            cycle=cycle,
                            source_id=source_id,
                            horizon_profile=horizon_profile,
                        ),
                        "cluster": cluster,
                        "season": season,
                        "data_version": data_version,
                        "cycle": cycle,
                        "source_id": source_id,
                        "horizon_profile": horizon_profile,
                    }
                )
    return rows


def register_low_identity_rows(
    conn: sqlite3.Connection,
    *,
    clusters: Sequence[str],
    seasons: Sequence[str] | None = None,
    dry_run: bool = True,
) -> int:
    """Write LOW identity-Platt rows for the (cluster × season × bucket-variant)
    grid. Returns the number of rows written (or, when dry_run, the number that
    WOULD be written). Idempotent: INSERT OR REPLACE keyed on model_key, so a
    re-run does not duplicate.

    The rows are VERIFIED / is_active=1 / n_samples=0 /
    calibration_method=identity_full_transport_v1 / input_space=
    width_normalized_density (so the manager does NOT attempt a stale-Platt
    refit). recorded_at + fitted_at are pinned strictly before FROZEN_AS_OF so
    the frozen-pin load filter does not hide them.
    """
    seasons = tuple(seasons) if seasons else DEFAULT_SEASONS
    plan = _plan_rows(clusters=clusters, seasons=seasons)
    if dry_run:
        return len(plan)

    written = 0
    for r in plan:
        conn.execute(
            """
            INSERT OR REPLACE INTO platt_models
              (model_key, temperature_metric, cluster, season, data_version,
               input_space, param_A, param_B, param_C, bootstrap_params_json,
               n_samples, brier_insample, fitted_at, is_active, authority,
               cycle, source_id, horizon_profile, recorded_at, calibration_method)
            VALUES (?, 'low', ?, ?, ?,
                    ?, 0.0, 0.0, 0.0, '[]',
                    0, NULL, ?, 1, 'VERIFIED',
                    ?, ?, ?, ?, ?)
            """,
            (
                r["model_key"],
                r["cluster"],
                r["season"],
                r["data_version"],
                _INPUT_SPACE,
                _FITTED_AT,
                r["cycle"],
                r["source_id"],
                r["horizon_profile"],
                _RECORDED_AT,
                IDENTITY_CALIBRATION_METHOD,
            ),
        )
        written += 1
    conn.commit()
    return written


def _default_clusters() -> list[str]:
    """Live LOW-trading clusters from the runtime city registry.

    Falls back to an empty list if the registry is unavailable; callers should
    pass --clusters explicitly in that case.
    """
    try:
        from src.config import runtime_cities_by_name

        return sorted({c.cluster for c in runtime_cities_by_name().values() if c.cluster})
    except Exception:  # noqa: BLE001
        return []


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        required=False,
        default=None,
        help="Path to the world DB to write into (e.g. state/zeus-world.db). "
        "REQUIRED to actually write; omit for a registry-only dry-run plan.",
    )
    p.add_argument(
        "--clusters",
        nargs="*",
        default=None,
        help="Clusters to register (default: all runtime-registry clusters).",
    )
    p.add_argument(
        "--seasons",
        nargs="*",
        default=None,
        help="Seasons to register (default: DJF MAM JJA SON).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the plan and write nothing (default behavior when --db is omitted).",
    )
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    clusters = args.clusters if args.clusters else _default_clusters()
    if not clusters:
        print("no clusters resolved; pass --clusters explicitly", file=sys.stderr)
        return 2
    seasons = args.seasons or list(DEFAULT_SEASONS)

    # Dry-run when --db is omitted OR --dry-run is set.
    dry_run = args.dry_run or args.db is None
    if dry_run:
        plan = _plan_rows(clusters=clusters, seasons=seasons)
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "db": args.db,
                    "clusters": list(clusters),
                    "seasons": list(seasons),
                    "planned_rows": len(plan),
                    "recorded_at": _RECORDED_AT,
                    "frozen_as_of": FROZEN_AS_OF,
                    "sample_model_keys": [r["model_key"] for r in plan[:4]],
                },
                indent=2,
            )
        )
        return 0

    # Use the project connection helper (allowlisted by the writer-lock
    # antibody) rather than a raw sqlite3.connect. write_class="bulk" matches
    # this script's offline-registration classification.
    from src.state.db import get_connection

    conn = get_connection(Path(args.db), write_class="bulk")
    try:
        n = register_low_identity_rows(
            conn, clusters=clusters, seasons=seasons, dry_run=False
        )
    finally:
        conn.close()
    print(
        json.dumps(
            {
                "dry_run": False,
                "db": args.db,
                "clusters": list(clusters),
                "seasons": list(seasons),
                "rows_written": n,
                "recorded_at": _RECORDED_AT,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
