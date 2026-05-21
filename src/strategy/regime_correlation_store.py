# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase5_regime_correlation/PHASE_5_PLAN.md §Track2 + 06_PHASE_5_WEATHER_REGIME_CORRELATION.md §T2

"""Regime-conditional correlation matrix store for Phase 5 cluster cap.

Persists one Ledoit-Wolf shrunk correlation matrix per WeatherRegimeTag value
in the world DB (zeus-world.db) table `regime_correlation_cache`.

Design contract (plan §Track2):
  - fit(regime, residuals) → ShrinkageEstimate:
      Fits the shrunk correlation matrix and persists it.
      fit(UNKNOWN, …) raises ValueError at call time (Phase 3 R-1 antibody —
      UNKNOWN regime never enters the cache).
  - get(regime, cities) → np.ndarray:
      Returns the stored sub-matrix for the given city subset.
      City order in the returned matrix matches the supplied cities list.
      Raises KeyError if regime not yet fit.
      Raises KeyError if any city in the subset was not in the fitted matrix.
  - invalidate(regime):
      Removes the cache row for the given regime.

Persistence schema: regime_correlation_cache (world DB, SCHEMA_VERSION 24).
INV-37: caller supplies conn; never auto-opens inside this class.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterator

import numpy as np

from src.contracts.weather_regime_tag import WeatherRegimeTag
from src.strategy.correlation_shrinkage import (
    ShrinkageEstimate,
    ledoit_wolf_shrunk_correlation,
)
from src.state.schema.regime_correlation_cache_schema import (
    SCHEMA_VERSION,
)

if TYPE_CHECKING:
    pass


def _validate_correlation_matrix(cities: list[str], matrix: np.ndarray) -> None:
    if len(cities) != len(set(cities)):
        raise ValueError("correlation matrix cities must be unique")
    arr = np.array(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
        raise ValueError(f"correlation matrix must be square, got shape={arr.shape}")
    if arr.shape[0] != len(cities):
        raise ValueError(
            f"correlation matrix dimension {arr.shape[0]} does not match cities length {len(cities)}"
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError("correlation matrix contains NaN or infinite values")
    if not np.allclose(np.diag(arr), 1.0, atol=1e-8):
        raise ValueError("correlation matrix diagonal must be 1")
    if not np.allclose(arr, arr.T, atol=1e-8):
        raise ValueError("correlation matrix must be symmetric")
    if np.any(arr < -1.0 - 1e-8) or np.any(arr > 1.0 + 1e-8):
        raise ValueError("correlation matrix values must be in [-1, 1]")
    eigenvalues = np.linalg.eigvalsh(arr)
    if float(np.min(eigenvalues)) < -1e-8:
        raise ValueError("correlation matrix must be positive semidefinite")


@contextmanager
def _savepoint_atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """SAVEPOINT-based atomic region that nests inside outer transactions.

    Unlike `with conn:` (which BEGINs/COMMITs and silently RELEASEs any outer
    SAVEPOINT — see project memory feedback_with_conn_nested_savepoint_audit),
    SAVEPOINT/RELEASE/ROLLBACK TO compose correctly. Callers can wrap calls
    inside their own outer SAVEPOINT without losing rollback granularity.
    """
    sp = "regime_correlation_store_sp"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        yield
        conn.execute(f"RELEASE {sp}")
    except BaseException:
        conn.execute(f"ROLLBACK TO {sp}")
        conn.execute(f"RELEASE {sp}")
        raise


class RegimeCorrelationStore:
    """Per-regime shrunk correlation matrix cache backed by world DB.

    Args:
        conn: sqlite3.Connection to the world DB (or :memory: for tests).
              The regime_correlation_cache table must already exist (created by
              db.py:init_schema at daemon boot, or in tests via init_schema).

    Example:
        store = RegimeCorrelationStore(world_conn)
        est = store.fit(WeatherRegimeTag.HEAT_DOME, residuals)
        matrix = store.get(WeatherRegimeTag.HEAT_DOME, ["NYC", "Boston"])
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(
        self,
        regime: WeatherRegimeTag,
        residuals: np.ndarray,
        cities: list[str] | None = None,
    ) -> ShrinkageEstimate:
        """Fit and persist a Ledoit-Wolf shrunk correlation matrix for regime.

        Args:
            regime:    WeatherRegimeTag value to associate with this matrix.
                       UNKNOWN raises ValueError immediately (Phase 3 R-1 antibody).
            residuals: Shape (n, p) array of ensemble residuals or daily anomalies.
                       Column order must correspond to the cities list.
            cities:    Optional list of p city names matching residuals columns.
                       If None, columns are named "city_0" … "city_{p-1}".

        Returns:
            ShrinkageEstimate from ledoit_wolf_shrunk_correlation.

        Raises:
            ValueError: If regime is WeatherRegimeTag.UNKNOWN.
        """
        if regime is WeatherRegimeTag.UNKNOWN:
            raise ValueError(
                "fit() called with WeatherRegimeTag.UNKNOWN. "
                "UNKNOWN regime must never enter the correlation cache "
                "(Phase 3 R-1 antibody — UNKNOWN does not aggregate into any cluster)."
            )

        est = ledoit_wolf_shrunk_correlation(residuals, target="diagonal")

        n, p = residuals.shape if hasattr(residuals, "shape") else (len(residuals), 0)
        if cities is None:
            cities = [f"city_{i}" for i in range(est.p_dimensions)]
        if len(cities) != est.p_dimensions:
            raise ValueError(
                f"cities length ({len(cities)}) must match residuals columns "
                f"({est.p_dimensions})."
            )
        _validate_correlation_matrix(cities, est.shrunk_correlation)

        fitted_at = datetime.now(timezone.utc).isoformat()
        cities_json = json.dumps(cities)
        matrix_json = json.dumps(est.shrunk_correlation.tolist())

        with _savepoint_atomic(self._conn):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO regime_correlation_cache
                    (regime, cities_json, matrix_json, fitted_at, n_observations,
                     intensity, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(regime),
                    cities_json,
                    matrix_json,
                    fitted_at,
                    est.n_observations,
                    est.intensity,
                    SCHEMA_VERSION,
                ),
            )
        return est

    def get(self, regime: WeatherRegimeTag, cities: list[str]) -> np.ndarray:
        """Return the stored shrunk correlation sub-matrix for a city subset.

        The returned matrix has shape (len(cities), len(cities)) with rows/
        columns ordered to match the supplied cities list.

        Args:
            regime: WeatherRegimeTag for the desired matrix.
            cities: List of city names; must be a subset of the stored city
                    list (in any order). KeyError raised on unknown city.

        Returns:
            np.ndarray of shape (len(cities), len(cities)).

        Raises:
            KeyError: If regime has not been fit yet, or if any city was not
                      present in the fitted matrix.
        """
        row = self._conn.execute(
            "SELECT cities_json, matrix_json FROM regime_correlation_cache WHERE regime = ?",
            (str(regime),),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Regime {regime!r} has no fitted matrix in regime_correlation_cache. "
                "Call fit() first."
            )

        stored_cities: list[str] = json.loads(row[0])
        matrix: np.ndarray = np.array(json.loads(row[1]))
        _validate_correlation_matrix(stored_cities, matrix)

        city_index = {c: i for i, c in enumerate(stored_cities)}
        try:
            indices = [city_index[c] for c in cities]
        except KeyError as exc:
            raise KeyError(
                f"City {exc} not found in stored matrix for regime {regime!r}. "
                f"Stored cities: {stored_cities}"
            ) from exc

        # Slice the sub-matrix in the requested city order.
        idx = np.array(indices)
        return matrix[np.ix_(idx, idx)]

    def invalidate(self, regime: WeatherRegimeTag) -> None:
        """Remove the cache row for the given regime.

        No-op if the regime was never fit.
        """
        with _savepoint_atomic(self._conn):
            self._conn.execute(
                "DELETE FROM regime_correlation_cache WHERE regime = ?",
                (str(regime),),
            )
