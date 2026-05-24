# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: operator hierarchical-bias adjudication 2026-05-24 §9 + PR #334 pre-check
#   blockers (unit->degC normalization, authority/contributor/causality/boundary filters,
#   training-cutoff leakage guard, lineage schema, read-safety).
"""DB I/O for hierarchical ENS bias correction.

- ``load_bucket_residuals``: per-bucket (forecast - actual) residuals for a forecast
  product, NORMALIZED TO CANONICAL degC (members + settlement share the city's native
  unit, read from members_unit) so cross-city/cluster estimation is unit-consistent and
  degF cities are not mis-scaled. Freshest snapshot per (city, target_date) wins; filtered
  by data_version, metric, lead, optional season months, authority, contributor policy, and
  a training-cutoff (``settled_before``) to prevent leakage.
- ``model_bias_ens_v2`` store: the ENS-product posterior-bias table with full lineage
  (live/prior source + data_version, month, unit, variances, paired delta, training cutoff).
  Distinct from the legacy deterministic ``model_bias`` (trained on the wrong product).
  Real-DB table ownership (world vs forecasts) is a review item; tests use a fixture.
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
    month INTEGER NOT NULL DEFAULT 0,
    metric TEXT NOT NULL,
    live_source_id TEXT,
    live_data_version TEXT NOT NULL,
    prior_source_id TEXT,
    prior_data_version TEXT,
    contributor_policy TEXT,
    bias_unit TEXT NOT NULL,
    posterior_bias_c REAL NOT NULL,
    posterior_sd_c REAL NOT NULL,
    n_live INTEGER NOT NULL,
    n_prior INTEGER NOT NULL,
    n_paired INTEGER,
    weight_live REAL NOT NULL,
    paired_delta_c REAL,
    v0_c2 REAL,
    vo_c2 REAL,
    estimator TEXT NOT NULL,
    training_cutoff TEXT,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (city, season, month, metric, live_data_version)
)
"""

_POSITIVE_CONTRIBUTOR_SQL = (
    "e.contributes_to_target_extrema = 1 "
    "AND COALESCE(e.boundary_ambiguous, 0) = 0 "
    "AND COALESCE(e.training_allowed, 1) = 1 "
    "AND COALESCE(e.causality_status, 'OK') = 'OK'"
)


def _to_c(value: float, unit: str | None) -> float:
    u = (unit or "").strip().lower()
    if u in {"f", "degf", "fahrenheit"} or (u and u.endswith("f")):
        return (value - 32.0) / 1.8
    if u in {"c", "degc", "celsius"} or (u and u.endswith("c")):
        return value
    raise ValueError(f"unknown temperature unit: {unit!r}")


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
    settled_before: str | None = None,
    require_verified: bool = True,
    contributor_policy: str = "full_contributor_only",
    to_celsius: bool = True,
) -> list[float]:
    """Return (ensemble_mean - settlement) residuals for the bucket, normalized to degC.

    Freshest snapshot per (city, target_date) wins. Filters: data_version, metric,
    ``lead_hours <= lead_max``, optional ``season_months`` (month of target_date),
    ``settled_before`` (target_date strictly before, anti-leakage), ``require_verified``
    (authority='VERIFIED' on snapshot + settlement), and ``contributor_policy``:
      - "full_contributor_only" (default): contributes=1, not boundary-ambiguous,
        training_allowed, causality OK;
      - "legacy_tigge_null_passthrough": contributes NULL-or-1, not boundary-ambiguous —
        use ONLY for enumerated legacy TIGGE data_versions (pre-extractor NULL rows);
      - "all_for_diagnostic": no contributor filter.
    NOTE: ``settled_before`` is a TARGET-DATE cutoff (target_date < cutoff), a first
    anti-leakage seam — NOT a settlement-known-time cutoff. For rigorous historical
    rebuilds, prefer a settled_at/fact-known-time cutoff once that column is available.
    """
    where = ["e.city = ?", "e.data_version = ?", "e.temperature_metric = ?", "e.lead_hours <= ?"]
    params: list[object] = [city, data_version, metric, lead_max]
    if require_verified:
        where.append("e.authority = 'VERIFIED'")
        where.append("s.authority = 'VERIFIED'")
    if contributor_policy == "full_contributor_only":
        where.append(_POSITIVE_CONTRIBUTOR_SQL)
    elif contributor_policy == "legacy_tigge_null_passthrough":
        # Legacy TIGGE rows predate the extrema extractor and carry
        # contributes_to_target_extrema=NULL. Allow NULL-or-1, still reject
        # boundary-ambiguous. Use ONLY for enumerated legacy TIGGE data_versions.
        where.append(
            "(e.contributes_to_target_extrema IS NULL OR e.contributes_to_target_extrema = 1) "
            "AND COALESCE(e.boundary_ambiguous, 0) = 0"
        )
    elif contributor_policy != "all_for_diagnostic":
        raise ValueError(f"unknown contributor_policy: {contributor_policy!r}")
    if settled_before is not None:
        where.append("e.target_date < ?")
        params.append(settled_before)

    rows = conn.execute(
        f"""
        SELECT e.target_date AS td, e.members_json AS mj, e.members_unit AS mu,
               e.available_at AS av, s.settlement_value AS sv
        FROM ensemble_snapshots_v2 e
        JOIN settlements_v2 s
          ON s.city = e.city AND s.target_date = e.target_date
         AND s.temperature_metric = e.temperature_metric
        WHERE {" AND ".join(where)}
        ORDER BY e.available_at
        """,
        params,
    ).fetchall()

    freshest: dict[str, tuple[str, str, object, float]] = {}
    for r in rows:
        td = r["td"]
        if season_months is not None and int(str(td)[5:7]) not in season_months:
            continue
        if r["sv"] is None or r["mj"] is None:
            continue
        if to_celsius and r["mu"] is None:
            continue  # cannot safely unit-normalize without members_unit
        prev = freshest.get(td)
        if prev is None or str(r["av"]) > prev[0]:
            freshest[td] = (str(r["av"]), r["mj"], r["mu"], float(r["sv"]))

    residuals: list[float] = []
    for _td, (_av, mj, mu, sv) in freshest.items():
        parsed = json.loads(mj)
        raw = [float(x) for x in (parsed.values() if isinstance(parsed, dict) else parsed) if x is not None]
        if not raw:
            continue
        ens_mean = statistics.fmean(raw)
        if to_celsius:
            residuals.append(_to_c(ens_mean, mu) - _to_c(sv, mu))
        else:
            residuals.append(ens_mean - sv)
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
    month: int | None = None,
    live_source_id: str | None = None,
    prior_source_id: str | None = None,
    contributor_policy: str | None = None,
    bias_unit: str = "C",
    n_paired: int | None = None,
    paired_delta_c: float | None = None,
    v0_c2: float | None = None,
    vo_c2: float | None = None,
    training_cutoff: str | None = None,
    recorded_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO model_bias_ens_v2
        (city, season, month, metric, live_source_id, live_data_version,
         prior_source_id, prior_data_version, contributor_policy, bias_unit,
         posterior_bias_c, posterior_sd_c, n_live, n_prior, n_paired, weight_live,
         paired_delta_c, v0_c2, vo_c2, estimator, training_cutoff, recorded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            city, season, (0 if month is None else int(month)), metric, live_source_id, live_data_version,
            prior_source_id, prior_data_version, contributor_policy, bias_unit,
            float(posterior_bias_c), float(posterior_sd_c), int(n_live), int(n_prior),
            (int(n_paired) if n_paired is not None else None), float(weight_live),
            (float(paired_delta_c) if paired_delta_c is not None else None),
            (float(v0_c2) if v0_c2 is not None else None),
            (float(vo_c2) if vo_c2 is not None else None),
            estimator, training_cutoff,
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
    month: int | None = None,
) -> sqlite3.Row | None:
    """Read a bias row. ``live_data_version`` is REQUIRED — there is no
    'latest across data versions' fallback (that would serve the wrong product)."""
    if not live_data_version:
        raise ValueError(
            "read_bias_model requires an exact live_data_version (no latest-row fallback "
            "— serving the wrong product's bias is a correctness hazard)"
        )
    return conn.execute(
        "SELECT * FROM model_bias_ens_v2 WHERE city=? AND season=? AND metric=? "
        "AND live_data_version=? AND month=?",
        (city, season, metric, live_data_version, (0 if month is None else int(month))),
    ).fetchone()


_LEGACY_NULL_CONTRIBUTOR_SQL = (
    "(e.contributes_to_target_extrema IS NULL OR e.contributes_to_target_extrema = 1) "
    "AND COALESCE(e.boundary_ambiguous, 0) = 0"
)


def _forecast_means(
    conn, city, data_version, metric, lead_max, season_months, settled_before,
    contributor_sql, require_verified,
):
    """Freshest-per-date ensemble-mean forecast (degC), no settlement join."""
    where = ["e.city = ?", "e.data_version = ?", "e.temperature_metric = ?", "e.lead_hours <= ?"]
    params: list[object] = [city, data_version, metric, lead_max]
    if require_verified:
        where.append("e.authority = 'VERIFIED'")
    if contributor_sql:
        where.append(contributor_sql)
    if settled_before is not None:
        where.append("e.target_date < ?")
        params.append(settled_before)
    rows = conn.execute(
        f"SELECT e.target_date AS td, e.members_json AS mj, e.members_unit AS mu, "
        f"e.available_at AS av FROM ensemble_snapshots_v2 e WHERE {' AND '.join(where)} "
        f"ORDER BY e.available_at",
        params,
    ).fetchall()
    fresh: dict[str, tuple[str, str, object]] = {}
    for r in rows:
        td = r["td"]
        if season_months is not None and int(str(td)[5:7]) not in season_months:
            continue
        if r["mj"] is None or r["mu"] is None:
            continue
        if td not in fresh or str(r["av"]) > fresh[td][0]:
            fresh[td] = (str(r["av"]), r["mj"], r["mu"])
    out: dict[str, float] = {}
    for td, (_av, mj, mu) in fresh.items():
        parsed = json.loads(mj)
        vals = [_to_c(float(x), mu) for x in (parsed.values() if isinstance(parsed, dict) else parsed) if x is not None]
        if vals:
            out[td] = statistics.fmean(vals)
    return out


def load_paired_delta(
    conn,
    *,
    city: str,
    live_data_version: str,
    prior_data_version: str,
    metric: str = "high",
    lead_max: float = 48.0,
    season_months: tuple[int, ...] | None = None,
    settled_before: str | None = None,
) -> list[float]:
    """Δ = F25_mean - F50_mean (degC) for dates where BOTH products have a freshest
    snapshot. Live (F25) uses the full-contributor population; prior (F50/legacy TIGGE)
    uses the NULL-passthrough population. No settlement needed — Δ is a paired-lineage
    product difference, transported into the bias prior."""
    f25 = _forecast_means(conn, city, live_data_version, metric, lead_max, season_months,
                          settled_before, _POSITIVE_CONTRIBUTOR_SQL, require_verified=True)
    f50 = _forecast_means(conn, city, prior_data_version, metric, lead_max, season_months,
                          settled_before, _LEGACY_NULL_CONTRIBUTOR_SQL, require_verified=False)
    return [f25[d] - f50[d] for d in (set(f25) & set(f50))]
