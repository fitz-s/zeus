# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2
"""World-view accessor: calibration (Platt models).

Provides get_active_platt_model(world_conn, city, season, metric_identity) -> PlattModelView | None.

Wraps load_platt_model_v2 with a friendlier typed API that:
- Takes an explicit world_conn (no ATTACH, no singleton)
- Returns a typed PlattModelView instead of a raw dict
- Preserves full backward compat with load_platt_model_v2 semantics

Uses world_conn opened by the caller — no ATTACH, no module-level singleton.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PlattModelView:
    """Read-only typed view of an active Platt model from world DB.

    Field naming convention: ``param_A``/``param_B``/``param_C`` (typed-view
    canonical shape). This deliberately differs from the raw dict returned
    by ``src.calibration.store.load_platt_model_v2``, which uses bare
    ``"A"``/``"B"``/``"C"`` keys. The typed-view layer prefixes them to
    make ``A`` (matrix-style) vs ``param_A`` (named coefficient) explicit
    at call sites.

    Callers must not write back through this object.
    """
    param_A: float
    param_B: float
    param_C: float
    n_samples: int
    brier_insample: Optional[float]
    fitted_at: str
    input_space: str
    bootstrap_params_json: Optional[str]
    temperature_metric: str
    cluster: str
    season: str
    data_version: Optional[str]

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical PlattModelView dict shape (param_A/B/C keys).

        NOT the same as ``load_platt_model_v2``'s raw dict shape (A/B/C keys).
        PR #65 Copilot follow-up 2026-05-06: docstring previously claimed
        compatibility with load_platt_model_v2 callers — incorrect; the keys
        differ deliberately. Callers that need the raw dict shape should
        call ``load_platt_model_v2`` directly; callers that have a typed
        ``PlattModelView`` and want a serialisable dict should use this
        method and consume ``param_A/B/C``.
        """
        return {
            "param_A": self.param_A,
            "param_B": self.param_B,
            "param_C": self.param_C,
            "n_samples": self.n_samples,
            "brier_insample": self.brier_insample,
            "fitted_at": self.fitted_at,
            "input_space": self.input_space,
            "bootstrap_params_json": self.bootstrap_params_json,
        }


def get_active_platt_model(
    world_conn: sqlite3.Connection,
    city: str,
    season: str,
    metric_identity: Any,
    *,
    cycle: Optional[str] = None,
    source_id: Optional[str] = None,
    horizon_profile: Optional[str] = None,
) -> Optional[PlattModelView]:
    """Return the active Platt model for (city, season, metric_identity) from world DB.

    metric_identity should be a MetricIdentity object (or duck-typed equivalent) with:
      - temperature_metric: "high" | "low"
      - data_version: str
      - input_space: str (optional, defaults to "width_normalized_density")

    Fix B (golden-knitting-wand.md Phase 1): added cycle/source_id/horizon_profile
    keyword params so callers can pass phase-2 stratification keys. Without these,
    load_platt_model_v2 silently defaults to (cycle=None, source_id=None,
    horizon_profile=None) which resolves to schema defaults (00z TIGGE full) —
    a 12z OpenData call would receive the 00z TIGGE Platt instead of the
    cycle-matched bucket. Same bug pattern sonnet fixed at manager.py:391-394.

    world_conn must already be open — caller manages lifecycle.
    Returns None if no matching active VERIFIED model exists.
    """
    # Extract fields from metric_identity (duck-typed)
    temperature_metric = getattr(metric_identity, "temperature_metric", "high")
    data_version = getattr(metric_identity, "data_version", None)
    input_space = getattr(metric_identity, "input_space", "width_normalized_density")

    # Delegate to load_platt_model_v2 — it handles all the SQL and version logic
    from src.calibration.store import load_platt_model_v2

    raw = load_platt_model_v2(
        world_conn,
        temperature_metric=temperature_metric,
        cluster=city,
        season=season,
        data_version=data_version,
        input_space=input_space,
        cycle=cycle,
        source_id=source_id,
        horizon_profile=horizon_profile,
    )
    if raw is None:
        return None

    # load_platt_model_v2 returns keys "A", "B", "C" (not "param_A"/"param_B"/"param_C").
    # Fixed here to match the actual dict shape from store.py.
    return PlattModelView(
        param_A=raw["A"],
        param_B=raw["B"],
        param_C=raw["C"],
        n_samples=raw.get("n_samples", 0),
        brier_insample=raw.get("brier_insample"),
        fitted_at=raw.get("fitted_at", ""),
        input_space=raw.get("input_space", input_space),
        bootstrap_params_json=raw.get("bootstrap_params_json"),
        temperature_metric=temperature_metric,
        cluster=city,
        season=season,
        data_version=data_version,
    )
