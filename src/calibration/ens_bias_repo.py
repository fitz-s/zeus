# Created: 2026-05-24
# Last reused/audited: 2026-05-26
# Authority basis: operator hierarchical-bias adjudication 2026-05-24 §9 + PR #334 pre-check
#   blockers (unit->degC normalization, authority/contributor/causality/boundary filters,
#   training-cutoff leakage guard, lineage schema, read-safety).
# 2026-05-26 FT-ship F2: init_ens_bias_schema now also applies canonical-extension
#   ALTERs idempotently so init_schema (world.db boot) yields a runtime-ready table
#   without requiring a separate one-off migration call.
#   Authority: docs/operations/FT_SHIP_EXECUTION_LEDGER_2026-05-25.md F2.
# 2026-05-26 FT-ship F4: read_bias_model now requires error_model_family and filters
#   AND authority = 'VERIFIED' so STAGING/LEGACY rows can never leak into the
#   live FT path (no `is_active` column exists; authority + family are the
#   discriminators per .schema). Authority: FT_SHIP_EXECUTION_LEDGER F4.
"""DB I/O for hierarchical ENS bias correction.

- ``load_bucket_residuals``: per-bucket (forecast - actual) residuals for a forecast
  product, NORMALIZED TO CANONICAL degC (members + settlement share the city's native
  unit, read from members_unit) so cross-city/cluster estimation is unit-consistent and
  degF cities are not mis-scaled. Metric-aware snapshot selected per (city, target_date):
  HIGH prefers the 0Z cycle (daytime/afternoon coverage); LOW prefers 12Z (nighttime).
  Falls back to freshest-by-available_at when issue_time is NULL. Filtered by
  data_version, metric, lead, optional season months, authority, contributor policy, and
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

# Canonical domain-identity extension columns added by migration
# scripts/migrate_model_bias_ens_v2_canonical_fields.py (Zeus #64 / #68 / #69).
# These are NOT in the PRIMARY KEY (SQLite prohibits ALTER TABLE to add PK columns).
# The producer (fit_full_transport_error_models.py) inserts them; legacy onboard_cities
# rows carry NULL in these columns and are identifiable by error_model_family IS NULL.
_CANONICAL_EXTENSION_COLUMNS: list[tuple[str, str]] = [
    # identity / lineage
    ("error_model_family",   "TEXT"),          # e.g. 'full_transport_v1', 'none'
    ("error_model_key",      "TEXT"),          # composite natural key string
    ("transport_delta_policy", "TEXT"),        # serialised kappa/delta-source descriptor
    # predictive-error model fields (canonical names parallel PredictiveErrorModel)
    ("bias_c",               "REAL"),          # posterior mean bias (forecast - actual), degC
    ("bias_sd_c",            "REAL"),          # posterior bias SD, degC
    ("residual_sd_c",        "REAL"),          # forecast/station residual scale, degC
    ("heterogeneity_var_c2", "REAL"),          # prior<->live excess variance, degC^2
    ("correction_strength",  "REAL"),          # lambda in [0,1]
    ("effective_bias_c",     "REAL"),          # lambda * bias_c
    ("total_residual_sd_c",  "REAL"),          # sqrt(residual_sd^2 + heterogeneity_var)
    # provenance
    ("code_commit",          "TEXT"),          # git HEAD SHA at fit time
    ("fit_signature_hash",   "TEXT"),          # sha256 of sorted inputs+params (16-char prefix)
    ("authority",            "TEXT"),          # 'STAGING' | 'VERIFIED' | 'LEGACY'
    # gate-set + coverage identity (domain-canonicality antibody, 2026-05-28).
    # gate_set_hash pins the active math-gate set (MIN_PAIRED_N, min_live_n,
    # min_prior_n, residual_floor) at fit time; the reader rejects any served
    # row whose gate_set_hash != current so a future gate change auto-quarantines
    # stale rows (replaces version-suffix family renames — no full_transport_v2).
    # coverage_months records the months actually present in the fit window so a
    # season-labelled row cannot be misapplied to a month it never trained on.
    ("gate_set_hash",        "TEXT"),          # sha256(active gate names+thresholds)[:16]
    ("coverage_months",      "TEXT"),          # CSV of months covered, e.g. '3,4,5'
]

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
    """Create model_bias_ens_v2 base table (idempotent) and apply all canonical
    extension columns (PRAGMA-guarded ALTER TABLE, also idempotent).

    Zeus #64 FT-ship F2 (2026-05-26): unified init so both init_schema (daemon
    boot) and the standalone migration script reach a fully-extended schema via
    a single call.  Re-running on an already-extended DB is a safe no-op.
    """
    conn.execute(MODEL_BIAS_ENS_V2_SCHEMA)
    conn.commit()

    # Apply canonical extension columns. SQLite has no ADD COLUMN IF NOT EXISTS,
    # so we use PRAGMA table_info to check before each ALTER.
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(model_bias_ens_v2)").fetchall()}
    for col, sql_type in _CANONICAL_EXTENSION_COLUMNS:
        if col in existing_cols:
            continue
        try:
            conn.execute(f"ALTER TABLE model_bias_ens_v2 ADD COLUMN {col} {sql_type}")
            conn.commit()
        except sqlite3.OperationalError as exc:
            # Race-safe: another writer added the column between our PRAGMA check and ALTER.
            if "duplicate column name" in str(exc).lower():
                conn.rollback()
            else:
                raise


# Columns a canonical producer row MUST be able to stamp. If any is absent the
# producer would 'succeed' while silently dropping gate_set_hash / coverage / scale —
# the row then fails closed at read time or, worse, serves without its domain identity.
_REQUIRED_PRODUCER_COLUMNS: tuple[str, ...] = (
    "error_model_family", "authority", "bias_c", "residual_sd_c",
    "heterogeneity_var_c2", "correction_strength", "effective_bias_c",
    "total_residual_sd_c", "fit_signature_hash", "gate_set_hash", "coverage_months",
)


def assert_model_bias_schema_ready(conn: sqlite3.Connection) -> None:
    """Fail CLOSED if model_bias_ens_v2 lacks any canonical producer column (SD5 / Blocker G).

    write_bias_model only stamps gate_set_hash / coverage_months / scale fields WHEN the
    columns exist (PRAGMA-guarded, for backward compat). That is correct for reads but
    DANGEROUS for a production fit: on an unmigrated schema the producer would write rows
    with NULL domain identity and 'succeed'. This preflight makes the producer refuse to run
    until init_ens_bias_schema / the canonical migration has been applied.
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(model_bias_ens_v2)").fetchall()}
    if not existing:
        raise RuntimeError(
            "assert_model_bias_schema_ready: model_bias_ens_v2 does not exist — "
            "run init_ens_bias_schema(conn) before fitting."
        )
    missing = [c for c in _REQUIRED_PRODUCER_COLUMNS if c not in existing]
    if missing:
        raise RuntimeError(
            "assert_model_bias_schema_ready: model_bias_ens_v2 is missing required "
            f"canonical columns {missing}; producer refuses to run (would write rows "
            "without domain identity). Apply init_ens_bias_schema / the canonical migration."
        )


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

    Metric-aware snapshot selection per (city, target_date): HIGH prefers the 0Z cycle
    (daytime coverage, captures afternoon high); LOW keeps the 12Z cycle (nighttime
    coverage). When ``issue_time`` is unavailable, falls back to freshest-by-available_at.
    Rationale: the TIGGE mx2t6 12Z snapshot covers 12Z→12Z (nighttime) and systematically
    misses the afternoon HIGH extremum, producing a -3 to -4 °C cold bias in the prior.
    Filters: data_version, metric, ``lead_hours <= lead_max``, optional ``season_months``
    (month of target_date), ``settled_before`` (target_date strictly before, anti-leakage),
    ``require_verified`` (authority='VERIFIED' on snapshot + settlement), and
    ``contributor_policy``:
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
               e.available_at AS av, e.issue_time AS it, s.settlement_value AS sv
        FROM ensemble_snapshots_v2 e
        JOIN settlements_v2 s
          ON s.city = e.city AND s.target_date = e.target_date
         AND s.temperature_metric = e.temperature_metric
        WHERE {" AND ".join(where)}
        ORDER BY e.available_at
        """,
        params,
    ).fetchall()

    # HIGH → prefer 0Z cycle (covers daytime/afternoon peak); LOW → prefer 12Z (nighttime).
    # Preference is applied per target_date: if a preferred-cycle snapshot exists for a date,
    # it wins over any other cycle regardless of available_at order.
    # When issue_time is NULL or unparseable, falls back to freshest-by-available_at.
    _preferred_hour: int = 0 if metric == "high" else 12

    def _issue_hour(it: object) -> int | None:
        try:
            return int(str(it)[11:13]) if it is not None else None
        except (ValueError, IndexError):
            return None

    # dict value: (av, mj, mu, sv, it) — 5-tuple with issue_time at index 4
    freshest: dict[str, tuple[str, object, object, float, object]] = {}
    for r in rows:
        td = r["td"]
        if season_months is not None and int(str(td)[5:7]) not in season_months:
            continue
        if r["sv"] is None or r["mj"] is None:
            continue
        if to_celsius and r["mu"] is None:
            continue  # cannot safely unit-normalize without members_unit
        av = str(r["av"])
        cur_hour = _issue_hour(r["it"])
        prev = freshest.get(td)
        if prev is None:
            freshest[td] = (av, r["mj"], r["mu"], float(r["sv"]), r["it"])
        else:
            prev_hour = _issue_hour(prev[4])
            if cur_hour == _preferred_hour and prev_hour != _preferred_hour:
                freshest[td] = (av, r["mj"], r["mu"], float(r["sv"]), r["it"])
            elif cur_hour != _preferred_hour and prev_hour == _preferred_hour:
                pass  # keep existing preferred-cycle snapshot
            elif av > prev[0]:
                freshest[td] = (av, r["mj"], r["mu"], float(r["sv"]), r["it"])

    residuals: list[float] = []
    for _td, (_av, mj, mu, sv, _it) in freshest.items():
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
    # --- canonical extension fields (Zeus #64 / #68 / #69) ---
    # These are written only when the canonical migration has been applied;
    # callers that don't supply them leave the columns at NULL (legacy rows).
    error_model_family: str | None = None,
    error_model_key: str | None = None,
    transport_delta_policy: str | None = None,
    bias_c: float | None = None,
    bias_sd_c: float | None = None,
    residual_sd_c: float | None = None,
    heterogeneity_var_c2: float | None = None,
    correction_strength: float | None = None,
    effective_bias_c: float | None = None,
    total_residual_sd_c: float | None = None,
    code_commit: str | None = None,
    fit_signature_hash: str | None = None,
    authority: str | None = None,
    gate_set_hash: str | None = None,
    coverage_months: str | None = None,
) -> None:
    """Persist one bias-model row to model_bias_ens_v2.

    The canonical extension fields (bias_c, residual_sd_c, …) are optional;
    supplying them requires that the canonical-fields migration has been applied
    to the target DB.  When they are NOT supplied the INSERT still works because
    SQLite silently ignores columns not listed in the INSERT column list.

    The writer does NOT call conn.commit() — callers control transaction scope.
    """
    # Determine which columns are available in this DB (idempotent extension support).
    _existing = {r[1] for r in conn.execute("PRAGMA table_info(model_bias_ens_v2)").fetchall()}
    _has_canonical = "error_model_family" in _existing

    base_cols = (
        "city, season, month, metric, live_source_id, live_data_version, "
        "prior_source_id, prior_data_version, contributor_policy, bias_unit, "
        "posterior_bias_c, posterior_sd_c, n_live, n_prior, n_paired, weight_live, "
        "paired_delta_c, v0_c2, vo_c2, estimator, training_cutoff, recorded_at"
    )
    base_vals: list[object] = [
        city, season, (0 if month is None else int(month)), metric, live_source_id, live_data_version,
        prior_source_id, prior_data_version, contributor_policy, bias_unit,
        float(posterior_bias_c), float(posterior_sd_c), int(n_live), int(n_prior),
        (int(n_paired) if n_paired is not None else None), float(weight_live),
        (float(paired_delta_c) if paired_delta_c is not None else None),
        (float(v0_c2) if v0_c2 is not None else None),
        (float(vo_c2) if vo_c2 is not None else None),
        estimator, training_cutoff,
        recorded_at or datetime.now(timezone.utc).isoformat(),
    ]

    if _has_canonical:
        ext_cols = (
            ", error_model_family, error_model_key, transport_delta_policy"
            ", bias_c, bias_sd_c, residual_sd_c, heterogeneity_var_c2"
            ", correction_strength, effective_bias_c, total_residual_sd_c"
            ", code_commit, fit_signature_hash, authority"
        )
        ext_vals: list[object] = [
            error_model_family,
            error_model_key,
            transport_delta_policy,
            (float(bias_c) if bias_c is not None else None),
            (float(bias_sd_c) if bias_sd_c is not None else None),
            (float(residual_sd_c) if residual_sd_c is not None else None),
            (float(heterogeneity_var_c2) if heterogeneity_var_c2 is not None else None),
            (float(correction_strength) if correction_strength is not None else None),
            (float(effective_bias_c) if effective_bias_c is not None else None),
            (float(total_residual_sd_c) if total_residual_sd_c is not None else None),
            code_commit,
            fit_signature_hash,
            authority,
        ]
        # gate_set_hash + coverage_months are themselves canonical-extension columns
        # but were added after the original F2 migration; guard each independently so
        # a DB migrated to F2 but not yet to this column-set still writes the rest.
        if "gate_set_hash" in _existing:
            ext_cols += ", gate_set_hash"
            ext_vals.append(gate_set_hash)
        if "coverage_months" in _existing:
            ext_cols += ", coverage_months"
            ext_vals.append(coverage_months)
        placeholders = ",".join(["?"] * (len(base_vals) + len(ext_vals)))
        conn.execute(
            f"INSERT OR REPLACE INTO model_bias_ens_v2 ({base_cols}{ext_cols}) "
            f"VALUES ({placeholders})",
            base_vals + ext_vals,
        )
    else:
        placeholders = ",".join(["?"] * len(base_vals))
        conn.execute(
            f"INSERT OR REPLACE INTO model_bias_ens_v2 ({base_cols}) "
            f"VALUES ({placeholders})",
            base_vals,
        )


def read_bias_model(
    conn: sqlite3.Connection,
    *,
    city: str,
    season: str,
    metric: str,
    live_data_version: str | None = None,
    month: int | None = None,
    error_model_family: str | None = None,
    require_gate_set_hash: str | None = None,
    target_month: int | None = None,
    require_coverage_months: bool = False,
    authority: str = "VERIFIED",
) -> sqlite3.Row | None:
    """Read a bias row. ``live_data_version`` is REQUIRED — there is no
    'latest across data versions' fallback (that would serve the wrong product).

    Zeus #64 FT-ship F4 (2026-05-26): when ``error_model_family`` is supplied
    the query also filters ``error_model_family = ?`` AND ``authority = 'VERIFIED'``
    so STAGING / LEGACY rows can never leak into the live FT path.  The filter is
    applied only when the canonical-extension columns are present (PRAGMA guard);
    on a schema that predates F2 migration the call degrades to the base filter.
    No ``is_active`` column exists — authority + family are the discriminators.
    Authority: docs/operations/FT_SHIP_EXECUTION_LEDGER_2026-05-25.md F4.

    Domain-canonicality antibody (2026-05-28):
      * ``require_gate_set_hash``: when supplied, the row is REJECTED (returns None)
        unless its ``gate_set_hash`` equals this value. A stale row fit under a
        superseded gate set (e.g. pre-MIN_PAIRED_N) can never be served. This is the
        structural fix for the pre-gate-transport-delta contamination — it replaces
        version-suffix family renames; the family name stays stable and the gate-set
        hash carries the probability-domain identity.
      * ``target_month`` / ``require_coverage_months``: month-scope guard
        (COVERAGE_MISLABELED + Blocker E). ``require_coverage_months`` is auto-forced ON
        whenever ``require_gate_set_hash`` is supplied (a canonical read).
          - CANONICAL read (hash required, or require_coverage_months=True): the row MUST
            declare non-empty parseable coverage. Missing column / NULL / empty / malformed
            coverage → REJECT (fail closed). If target_month is given, also REJECT unless
            target_month ∈ covered set. A canonical row with no declared scope is untrusted.
          - LEGACY read (no hash required): malformed coverage → REJECT; empty/NULL coverage
            → 'no declared scope' → served (no-op); non-empty → REJECT unless target_month ∈
            covered. Keeps pre-antibody season rows servable.
        A season-labelled row whose fit window only covered one month cannot be applied to a
        month it never trained on; a canonical row that declares no scope at all cannot be
        applied anywhere.
    The gate_set_hash guard fails CLOSED (missing/NULL/old column → None). For canonical
    rows the coverage guard ALSO fails closed on empty/missing coverage; only legacy
    (no-hash) reads keep the lenient no-op-on-empty behaviour.
    """
    if not live_data_version:
        raise ValueError(
            "read_bias_model requires an exact live_data_version (no latest-row fallback "
            "— serving the wrong product's bias is a correctness hazard)"
        )

    base_sql = (
        "SELECT * FROM model_bias_ens_v2 WHERE city=? AND season=? AND metric=? "
        "AND live_data_version=? AND month=?"
    )
    base_params: tuple = (city, season, metric, live_data_version, (0 if month is None else int(month)))

    if error_model_family is not None:
        # Check canonical columns exist before adding the filter (defensive for DBs
        # that haven't been migrated yet — treats them as having no matching VERIFIED row).
        existing = {r[1] for r in conn.execute("PRAGMA table_info(model_bias_ens_v2)").fetchall()}
        if "error_model_family" not in existing or "authority" not in existing:
            # Schema predates F2 migration — no VERIFIED rows possible.
            return None
        # authority defaults to VERIFIED (live-serving safety). The MC rebuild path
        # passes authority='STAGING' to read the just-fit canonical rows BEFORE they
        # are promoted — so MC p_raw is generated from the SAME persisted row the
        # producer wrote (not an on-the-fly re-fit with divergent gates). The
        # gate_set_hash + month-scope guards below still apply regardless of authority.
        row = conn.execute(
            base_sql + " AND error_model_family=? AND authority=?",
            base_params + (error_model_family, authority),
        ).fetchone()
        if row is None:
            return None
        # Antibody 1: gate-set-hash must match current. Fail closed on mismatch OR
        # on a row that predates the column (NULL gate_set_hash = pre-antibody = stale).
        if require_gate_set_hash is not None:
            if "gate_set_hash" not in existing:
                return None
            if (row["gate_set_hash"] or "") != require_gate_set_hash:
                return None
        # Antibody 2: month-scope guard + Blocker E (mandatory coverage for canonical rows).
        # require_coverage_months is auto-forced ON whenever a gate_set_hash is required:
        # a CANONICAL row (new gate set) that declares no month scope cannot be trusted to
        # apply to any month, so empty/NULL/missing coverage fails CLOSED — it can no longer
        # be silently served as 'no declared scope'. Legacy reads (no hash required) keep the
        # lenient behaviour so pre-antibody season rows without coverage remain servable.
        _require_cov = require_coverage_months or (require_gate_set_hash is not None)
        if _require_cov:
            if "coverage_months" not in existing:
                return None  # canonical read on a schema without the column → fail closed
            covered = _parse_coverage_months(row["coverage_months"])
            if not covered:  # None (malformed) OR empty set → canonical row must declare scope
                return None
            if target_month is not None and int(target_month) not in covered:
                return None
        elif target_month is not None and "coverage_months" in existing:
            #  * malformed coverage (None) → fail CLOSED (reject).
            #  * empty coverage (set()) → no declared scope → guard is a no-op (served).
            #  * non-empty coverage → reject unless target_month is in the covered set.
            covered = _parse_coverage_months(row["coverage_months"])
            if covered is None:
                return None  # malformed non-empty coverage → fail closed
            if covered and int(target_month) not in covered:
                return None
        return row

    return conn.execute(base_sql, base_params).fetchone()


def _parse_coverage_months(raw: object) -> set[int] | None:
    """Parse a 'coverage_months' CSV cell into a set of ints.

    Returns:
      * empty set         when raw is empty/NULL — 'no declared scope', month guard is a no-op.
      * set[int]          the covered months when every token parses.
      * None              when raw is NON-empty but ANY token is malformed — the caller
                          MUST fail CLOSED (reject the row). A malformed coverage string
                          must never silently collapse to 'no scope' and re-enable a row
                          for months it can't vouch for.
    """
    if not raw:
        return set()
    out: set[int] = set()
    for tok in str(raw).split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
            return None  # malformed non-empty coverage → caller fails closed
    return out


_LEGACY_NULL_CONTRIBUTOR_SQL = (
    "(e.contributes_to_target_extrema IS NULL OR e.contributes_to_target_extrema = 1) "
    "AND COALESCE(e.boundary_ambiguous, 0) = 0"
)


def _forecast_means(
    conn, city, data_version, metric, lead_max, season_months, settled_before,
    contributor_sql, require_verified,
):
    """Metric-aware-per-date ensemble-mean forecast (degC), no settlement join.

    Uses the same cycle preference as load_bucket_residuals: HIGH → 0Z cycle,
    LOW → 12Z cycle. Falls back to freshest-by-available_at when issue_time is NULL.
    """
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
        f"e.available_at AS av, e.issue_time AS it "
        f"FROM ensemble_snapshots_v2 e WHERE {' AND '.join(where)} "
        f"ORDER BY e.available_at",
        params,
    ).fetchall()
    _preferred_hour: int = 0 if metric == "high" else 12

    def _issue_hour(it: object) -> int | None:
        try:
            return int(str(it)[11:13]) if it is not None else None
        except (ValueError, IndexError):
            return None

    # dict value: (av, mj, mu, it) — 4-tuple with issue_time at index 3
    fresh: dict[str, tuple[str, object, object, object]] = {}
    for r in rows:
        td = r["td"]
        if season_months is not None and int(str(td)[5:7]) not in season_months:
            continue
        if r["mj"] is None or r["mu"] is None:
            continue
        av = str(r["av"])
        cur_hour = _issue_hour(r["it"])
        prev = fresh.get(td)
        if prev is None:
            fresh[td] = (av, r["mj"], r["mu"], r["it"])
        else:
            prev_hour = _issue_hour(prev[3])
            if cur_hour == _preferred_hour and prev_hour != _preferred_hour:
                fresh[td] = (av, r["mj"], r["mu"], r["it"])
            elif cur_hour != _preferred_hour and prev_hour == _preferred_hour:
                pass  # keep existing preferred-cycle snapshot
            elif av > prev[0]:
                fresh[td] = (av, r["mj"], r["mu"], r["it"])
    out: dict[str, float] = {}
    for td, (_av, mj, mu, _it) in fresh.items():
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


def paired_delta_coverage(
    conn,
    *,
    city: str,
    live_data_version: str,
    prior_data_version: str,
    metric: str = "high",
    lead_max: float = 48.0,
    season_months: tuple[int, ...] | None = None,
    settled_before: str | None = None,
) -> tuple[int, set[int]]:
    """(n_paired_dates, {months}) for the EXACT dates load_paired_delta pairs on.

    Reuses the same _forecast_means primitives as load_paired_delta (set(f25) & set(f50))
    so paired coverage cannot drift from the transport delta the fit actually used. The
    count drives transport-active (n >= MIN_PAIRED_N); the months feed the effective-coverage
    intersection (SD1 / Blocker D): if transport is active, a row must not be applied to a
    target month its paired evidence never covered.
    """
    f25 = _forecast_means(conn, city, live_data_version, metric, lead_max, season_months,
                          settled_before, _POSITIVE_CONTRIBUTOR_SQL, require_verified=True)
    f50 = _forecast_means(conn, city, prior_data_version, metric, lead_max, season_months,
                          settled_before, _LEGACY_NULL_CONTRIBUTOR_SQL, require_verified=False)
    paired_dates = set(f25) & set(f50)
    return len(paired_dates), {int(str(d)[5:7]) for d in paired_dates}
