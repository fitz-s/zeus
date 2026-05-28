# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=2026-05-24
# Authority basis: ENS full_transport_v1 REFIT task 2026-05-24
#   (docs/operations/ENS_REFIT_PLAN_2026-05-24.md). Builds the isolated staging
#   DB the rebuild + refit run against, sourcing READ-ONLY from the live
#   forecasts DB. Never writes a live DB.
# Purpose: Clone source tables from live zeus-forecasts.db (read-only) into a new
#   isolated staging DB; apply v2 write-target schema for offline rebuild+refit.
# Reuse: Run once per refit cycle before run_offline_calibration_rebuild.py.
"""Seed a lean isolated staging DB for an offline calibration rebuild/refit.

The rebuild (`scripts/rebuild_calibration_pairs_v2.py`) and refit
(`scripts/refit_platt.py`) read their SOURCE tables
(`ensemble_snapshots`, `observations`, `settlements_v2`) and the
predictive-error residual sources from the SAME connection they write
`calibration_pairs_v2` / `platt_models_v2` into. To keep the live DBs
read-only we copy just those source tables (optionally filtered to a city
subset) into an isolated staging DB and apply the v2 write-target schema.

Source DB is opened ``mode=ro``; only the new staging DB is written.

USAGE:
    python scripts/seed_isolated_calibration_db.py --out /tmp/ens_refit_sf.db \
        --city "San Francisco"
    python scripts/seed_isolated_calibration_db.py --out /tmp/ens_refit_all.db \
        --city "San Francisco" --city Chicago ...
    python scripts/seed_isolated_calibration_db.py --out /tmp/ens_refit_all.db   # all cities
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Source tables both scripts read from the write connection.
_SOURCE_TABLES = ("ensemble_snapshots", "observations", "settlements_v2")


def _source_db_path() -> Path:
    from src.state.db import ZEUS_FORECASTS_DB_PATH  # noqa: PLC0415
    p = Path(ZEUS_FORECASTS_DB_PATH).resolve()
    if not p.exists() or p.stat().st_size == 0:
        raise FileNotFoundError(
            f"live forecasts DB not found / empty at {p}. The source tables "
            "(ensemble_snapshots/observations/settlements_v2) live here."
        )
    return p


def seed(out_path: Path, cities: list[str]) -> dict[str, int]:
    from src.state.db import init_schema  # noqa: PLC0415
    from src.state.schema.v2_schema import apply_canonical_schema  # noqa: PLC0415

    src = _source_db_path()
    if out_path.exists():
        raise FileExistsError(f"refusing to overwrite existing {out_path}")

    conn = sqlite3.connect(str(out_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Build write-target + source schema first so the staging DB is self-contained.
    init_schema(conn)
    apply_canonical_schema(conn)

    src_uri = "file:" + str(src) + "?mode=ro"
    conn.execute("ATTACH DATABASE ? AS live", (src_uri,))

    counts: dict[str, int] = {}
    city_clause = ""
    params: tuple = ()
    if cities:
        placeholders = ", ".join("?" for _ in cities)
        city_clause = f" WHERE city IN ({placeholders})"
        params = tuple(cities)

    for table in _SOURCE_TABLES:
        # Drop the locally-created empty table and clone the live one verbatim so
        # column shape matches exactly (avoids INSERT column-count drift).
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        ddl = conn.execute(
            "SELECT sql FROM live.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if ddl is None or not ddl[0]:
            raise RuntimeError(f"source table {table!r} missing in live DB {src}")
        conn.execute(ddl[0])
        conn.execute(
            f"INSERT INTO {table} SELECT * FROM live.{table}{city_clause}", params
        )
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # Training-flag hygiene — NARROW. The rebuild's eligibility query selects all
    # training_allowed=1 rows for the metric (data_version is only filtered when
    # --data-version is passed), then _pre_compute_snapshot_v2 hard-RAISES
    # DataVersionQuarantinedError on any row whose data_version is OUTSIDE the
    # spec's allowed_data_versions set. The live DB carries a handful of
    # training_allowed=1 rows on a data_version that is off-spec for EVERY metric
    # (e.g. ecmwf_opendata_mx2t6_local_calendar_day_max_v1). We flip ONLY those
    # to training_allowed=0.
    #
    # CRITICAL: we do NOT touch the IN-SPEC OpenData rows (e.g.
    # ecmwf_opendata_mx2t3_local_calendar_day_max_v1). Those are the LIVE residual
    # source the predictive-error fit depends on (load_bucket_residuals
    # full_contributor_only requires training_allowed=1). They are in the spec's
    # allowed_data_versions so the cross-check passes, and the rebuild's
    # _fetch_eligible query + per-snapshot writer only build TIGGE-archive rows
    # into pairs — the mx2t3 rows feed residuals but are not the rebuild target.
    _neutralize_off_union_training_flags(conn)

    counts["snaps_training_allowed_after_hygiene"] = conn.execute(
        "SELECT COUNT(*) FROM ensemble_snapshots WHERE training_allowed=1"
    ).fetchone()[0]

    conn.commit()
    conn.execute("DETACH DATABASE live")
    conn.close()
    return counts


def _neutralize_off_union_training_flags(conn: sqlite3.Connection) -> int:
    """training_allowed=0 ONLY for rows whose data_version is off-spec for EVERY
    METRIC_SPEC (not in the union of allowed_data_versions). Leaves all in-spec
    rows — including the OpenData live-residual source — untouched. Returns the
    number of rows flipped.
    """
    from src.calibration.metric_specs import METRIC_SPECS  # noqa: PLC0415

    allowed: set[str] = set()
    for spec in METRIC_SPECS:
        allowed |= set(spec.allowed_data_versions)
    placeholders = ", ".join("?" for _ in allowed)
    cur = conn.execute(
        f"UPDATE ensemble_snapshots SET training_allowed=0 "
        f"WHERE COALESCE(training_allowed,0)=1 AND data_version NOT IN ({placeholders})",
        tuple(sorted(allowed)),
    )
    return cur.rowcount


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed an isolated calibration staging DB.")
    ap.add_argument("--out", required=True, help="Path to the new isolated staging DB (must not exist).")
    ap.add_argument("--city", action="append", default=None, help="Restrict to a city; repeatable. Omit for all cities.")
    args = ap.parse_args()

    out = Path(args.out).expanduser().resolve()
    cities = list(args.city) if args.city else []
    counts = seed(out, cities)
    print(f"Seeded isolated DB: {out}")
    print(f"Cities: {cities or 'ALL'}")
    for t, n in counts.items():
        print(f"  {t:28s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
