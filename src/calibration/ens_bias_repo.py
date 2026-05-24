# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: operator hierarchical-bias adjudication 2026-05-24 §9
#   (model_bias_ens_v2; ENS-product residuals from ensemble_snapshots_v2 × settlements_v2).
"""DB I/O for hierarchical ENS bias correction.

- ``load_bucket_residuals``: per-bucket (forecast - actual) residuals for a given
  forecast product, computed in the city's NATIVE unit (members and settlement
  share it). Freshest snapshot per (city, target_date) wins; filtered by
  data_version, lead, and optional season months.
- ``model_bias_ens_v2`` store: the ENS-product posterior bias table. This is a
  NEW table, distinct from the legacy deterministic ``model_bias`` (which is
  trained on ``forecasts.ecmwf_previous_runs``, the wrong product). Real-DB table
  ownership (world vs forecasts) is a review item; tests use an in-memory fixture.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone

MODEL_BIAS_ENS_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_bias_ens_v2(
    city TEXT NOT NULL,
    season TEXT NOT NULL,
    metric TEXT NOT NULL,
    live_data_version TEXT NOT NULL,
    prior_data_version TEXT,
    posterior_bias_c REAL NOT NULL,
    posterior_sd_c REAL NOT NULL,
    n_live INTEGER NOT NULL,
    n_prior INTEGER NOT NULL,
    weight_live REAL NOT NULL,
    estimator TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (city, season, metric, live_data_version)
)
"""


def init_ens_bias_schema(conn: sqlite3.Connection) -> None:
    conn.execute(MODEL_BIAS_ENS_V2_SCHEMA)
    conn.commit()


def load_bucket_residuals(
    conn: sqlite3.Connection,
    *,
    city: str,
    data_version: str,
    metric: str = "high",
    lead_max: float = 48.0,
    season_months: tuple[int, ...] | None = None,
) -> list[float]:
    """Return (ensemble_mean - settlement) residuals (native unit) for the bucket.

    Freshest snapshot per (city, target_date) wins; rows are filtered by
    ``data_version``, ``metric``, ``lead_hours <= lead_max`` and, if given,
    ``month(target_date) in season_months``.
    """
    rows = conn.execute(
        """
        SELECT e.target_date AS td, e.members_json AS mj, e.available_at AS av,
               s.settlement_value AS sv
        FROM ensemble_snapshots_v2 e
        JOIN settlements_v2 s
          ON s.city = e.city
         AND s.target_date = e.target_date
         AND s.temperature_metric = e.temperature_metric
        WHERE e.city = ? AND e.data_version = ? AND e.temperature_metric = ?
          AND e.lead_hours <= ?
        ORDER BY e.available_at
        """,
        (city, data_version, metric, lead_max),
    ).fetchall()

    freshest: dict[str, tuple[str, str, float]] = {}
    for r in rows:
        td = r["td"]
        if season_months is not None and int(str(td)[5:7]) not in season_months:
            continue
        if r["sv"] is None or r["mj"] is None:
            continue
        prev = freshest.get(td)
        if prev is None or str(r["av"]) > prev[0]:
            freshest[td] = (str(r["av"]), r["mj"], float(r["sv"]))

    residuals: list[float] = []
    for _td, (_av, mj, sv) in freshest.items():
        parsed = json.loads(mj)
        vals = [float(x) for x in (parsed.values() if isinstance(parsed, dict) else parsed) if x is not None]
        if vals:
            residuals.append(statistics.fmean(vals) - sv)
    return residuals


def write_bias_model(
    conn: sqlite3.Connection,
    *,
    city: str,
    season: str,
    metric: str,
    live_data_version: str,
    prior_data_version: str | None,
    posterior_bias_c: float,
    posterior_sd_c: float,
    n_live: int,
    n_prior: int,
    weight_live: float,
    estimator: str,
    recorded_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO model_bias_ens_v2
        (city, season, metric, live_data_version, prior_data_version,
         posterior_bias_c, posterior_sd_c, n_live, n_prior, weight_live,
         estimator, recorded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            city, season, metric, live_data_version, prior_data_version,
            float(posterior_bias_c), float(posterior_sd_c), int(n_live), int(n_prior),
            float(weight_live), estimator,
            recorded_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def read_bias_model(
    conn: sqlite3.Connection,
    *,
    city: str,
    season: str,
    metric: str,
    live_data_version: str | None = None,
) -> sqlite3.Row | None:
    if live_data_version is not None:
        return conn.execute(
            "SELECT * FROM model_bias_ens_v2 WHERE city=? AND season=? AND metric=? "
            "AND live_data_version=?",
            (city, season, metric, live_data_version),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM model_bias_ens_v2 WHERE city=? AND season=? AND metric=? "
        "ORDER BY recorded_at DESC LIMIT 1",
        (city, season, metric),
    ).fetchone()
