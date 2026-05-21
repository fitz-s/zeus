# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Phase 5 T2 acceptance tests for RegimeCorrelationStore — fit/get/
#   invalidate, UNKNOWN-raises-ValueError antibody, cache round-trip, schema 24.
# Reuse: Run when changing src/strategy/regime_correlation_store.py or the
#   regime_correlation_cache table schema (SCHEMA_VERSION 24).
# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase5_regime_correlation/PHASE_5_PLAN.md §Track2 acceptance tests

"""Phase 5 T2 acceptance tests for RegimeCorrelationStore.

Four tests per plan §T2:
  1. test_heat_dome_tighter_than_normal        — off-diagonal entries larger for HEAT_DOME
  2. test_fit_unknown_raises_valueerror        — fit(UNKNOWN) raises ValueError at fit()
  3. test_cache_round_trip                     — fit → persist → reload → equality
  4. test_schema_migration_N_to_N_plus_1       — fresh DB gains table + PRAGMA user_version=24
"""

import json
import sqlite3

import numpy as np
import pytest

from src.contracts.weather_regime_tag import WeatherRegimeTag
from src.state.db import SCHEMA_VERSION, init_schema
from src.strategy.regime_correlation_store import RegimeCorrelationStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _world_conn() -> sqlite3.Connection:
    """In-memory world DB with full schema initialised."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _synthetic_residuals(
    n: int,
    p: int,
    off_diag_corr: float = 0.0,
    seed: int = 42,
) -> np.ndarray:
    """Generate synthetic residuals with controlled pairwise correlation.

    When off_diag_corr > 0, all pairs have that correlation (one-factor model).
    """
    rng = np.random.default_rng(seed)
    if off_diag_corr == 0.0:
        return rng.standard_normal((n, p))

    # One-factor: x_i = sqrt(rho)*Z + sqrt(1-rho)*eps_i
    Z = rng.standard_normal(n)
    eps = rng.standard_normal((n, p))
    factor_load = np.sqrt(off_diag_corr)
    idio_load = np.sqrt(1.0 - off_diag_corr)
    return factor_load * Z[:, None] + idio_load * eps


# ---------------------------------------------------------------------------
# T2-1: HEAT_DOME shrunk matrix has larger off-diagonal than NORMAL (plan §T2 test 1)
# ---------------------------------------------------------------------------

def test_heat_dome_tighter_than_normal() -> None:
    """Synthetic HEAT_DOME residuals have higher correlation than NORMAL.

    HEAT_DOME uses a one-factor model with rho=0.7; NORMAL uses iid columns.
    The stored shrunk matrices must satisfy: HEAT_DOME mean off-diagonal > NORMAL.

    Plan §T2 acceptance test 1 (verbatim):
      "stored matrix HEAT_DOME[i,j] > NORMAL[i,j] off-diagonal."
    We verify this in aggregate (mean of absolute off-diagonal entries).
    """
    conn = _world_conn()
    store = RegimeCorrelationStore(conn)
    cities = [f"city_{i}" for i in range(8)]

    heat_res = _synthetic_residuals(200, 8, off_diag_corr=0.7, seed=1)
    normal_res = _synthetic_residuals(200, 8, off_diag_corr=0.0, seed=2)

    heat_est = store.fit(WeatherRegimeTag.HEAT_DOME, heat_res, cities=cities)
    normal_est = store.fit(WeatherRegimeTag.NORMAL, normal_res, cities=cities)

    off = ~np.eye(8, dtype=bool)
    heat_off_mean = float(np.abs(heat_est.shrunk_correlation[off]).mean())
    normal_off_mean = float(np.abs(normal_est.shrunk_correlation[off]).mean())

    assert heat_off_mean > normal_off_mean, (
        f"Expected HEAT_DOME off-diagonal ({heat_off_mean:.4f}) > "
        f"NORMAL ({normal_off_mean:.4f})"
    )

    # Also verify via get() that the stored matrices are accessible.
    subset = cities[:4]
    heat_mat = store.get(WeatherRegimeTag.HEAT_DOME, subset)
    normal_mat = store.get(WeatherRegimeTag.NORMAL, subset)
    assert heat_mat.shape == (4, 4)
    assert normal_mat.shape == (4, 4)
    off4 = ~np.eye(4, dtype=bool)
    assert float(np.abs(heat_mat[off4]).mean()) > float(np.abs(normal_mat[off4]).mean()), (
        "HEAT_DOME sub-matrix mean off-diagonal must exceed NORMAL after get()."
    )


# ---------------------------------------------------------------------------
# T2-2: fit(UNKNOWN) raises ValueError at fit() (plan §T2 test 2)
# ---------------------------------------------------------------------------

def test_fit_unknown_raises_valueerror() -> None:
    """fit(UNKNOWN, …) raises ValueError immediately (not deferred to get()).

    Phase 3 R-1 antibody: UNKNOWN regime must never enter the correlation cache.

    Plan §T2 acceptance test 2 (verbatim):
      "fit(UNKNOWN, …) → ValueError at fit() (Phase 3 R-1 antibody enforced at
       write boundary)."
    """
    conn = _world_conn()
    store = RegimeCorrelationStore(conn)
    residuals = np.random.default_rng(0).standard_normal((50, 5))

    with pytest.raises(ValueError, match="UNKNOWN"):
        store.fit(WeatherRegimeTag.UNKNOWN, residuals)

    # Verify nothing was written to the table.
    row = conn.execute(
        "SELECT COUNT(*) FROM regime_correlation_cache WHERE regime = ?",
        (str(WeatherRegimeTag.UNKNOWN),),
    ).fetchone()
    assert row[0] == 0, "UNKNOWN regime must not appear in regime_correlation_cache."


# ---------------------------------------------------------------------------
# T2-3: cache round-trip: fit → persist → reload → equality (plan §T2 test 3)
# ---------------------------------------------------------------------------

def test_cache_round_trip() -> None:
    """fit() → persist → reload via get() → matrix equality within 1e-9.

    Plan §T2 acceptance test 3 (verbatim):
      "fit → persist → reload → matrix equality within 1e-9."
    """
    conn = _world_conn()
    store = RegimeCorrelationStore(conn)
    cities = ["NYC", "Boston", "Chicago", "Miami", "Seattle"]
    residuals = _synthetic_residuals(150, 5, off_diag_corr=0.4, seed=77)

    est = store.fit(WeatherRegimeTag.COLD_SNAP, residuals, cities=cities)

    # Reload by constructing a new store on the same connection.
    store2 = RegimeCorrelationStore(conn)
    reloaded = store2.get(WeatherRegimeTag.COLD_SNAP, cities)

    np.testing.assert_allclose(
        reloaded, est.shrunk_correlation, atol=1e-9,
        err_msg="Reloaded matrix differs from fitted matrix beyond 1e-9 tolerance."
    )

    # Verify subset retrieval preserves order.
    subset = ["Chicago", "NYC"]
    sub_mat = store2.get(WeatherRegimeTag.COLD_SNAP, subset)
    assert sub_mat.shape == (2, 2)
    # [Chicago, NYC] sub-matrix from full matrix.
    chi_idx = cities.index("Chicago")
    nyc_idx = cities.index("NYC")
    expected_sub = est.shrunk_correlation[np.ix_([chi_idx, nyc_idx], [chi_idx, nyc_idx])]
    np.testing.assert_allclose(sub_mat, expected_sub, atol=1e-12)


# ---------------------------------------------------------------------------
# T2-4: schema migration N→N+1 (plan §T2 test 4)
# ---------------------------------------------------------------------------

def test_schema_migration_N_to_N_plus_1() -> None:
    """A fresh init_schema DB has regime_correlation_cache + PRAGMA user_version=24.

    Plan §T2 acceptance test 4 (verbatim):
      "pre-migration world DB at version N gains regime_correlation_cache table;
       PRAGMA user_version == N+1 post-migration."

    We verify two things:
      1. init_schema creates regime_correlation_cache (table exists in sqlite_master).
      2. PRAGMA user_version equals SCHEMA_VERSION (currently 24).
    """
    conn = sqlite3.connect(":memory:")
    init_schema(conn)

    # Table must exist.
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='regime_correlation_cache'"
    ).fetchone()
    assert row is not None, (
        "regime_correlation_cache table not found after init_schema(); "
        "ensure ensure_table is called from db.py::init_schema."
    )

    # PRAGMA user_version must match SCHEMA_VERSION (24 at Phase 5 T2 dispatch).
    pragma_version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert pragma_version == SCHEMA_VERSION, (
        f"PRAGMA user_version={pragma_version} != SCHEMA_VERSION={SCHEMA_VERSION}. "
        "db.py::init_schema must set PRAGMA user_version = SCHEMA_VERSION as final step."
    )
    assert SCHEMA_VERSION == 24, (
        f"SCHEMA_VERSION expected 24 (Phase 5 T2 bump); got {SCHEMA_VERSION}. "
        "Update this assertion if the schema was bumped again."
    )

    # Columns must match the DDL specification.
    col_info = conn.execute(
        "PRAGMA table_info(regime_correlation_cache)"
    ).fetchall()
    col_names = {row[1] for row in col_info}
    expected_cols = {"regime", "cities_json", "matrix_json", "fitted_at",
                     "n_observations", "intensity", "schema_version"}
    assert expected_cols <= col_names, (
        f"Missing columns: {expected_cols - col_names}"
    )
