# Created: 2026-05-20
# Last reused or audited: 2026-06-18
# Authority basis: operator P0-1 live-money spec 2026-05-20/21 (mutually-exclusive weather
#                  family sizing), Fitz §1 (structural decision > patch).

"""Mutually-exclusive weather-family portfolio selection.

A weather market for one ``(city, target_date, temperature_metric)`` is a
PARTITION: exactly one temperature bin resolves YES. The bins are NOT
independent assets — payoff covariance is singular/negative (only one YES
pays). The legacy pipeline ran family-wise FDR, marked EVERY bin passing the
BH cutoff as ``should_trade=True``, and the cycle runtime submitted each as an
INDEPENDENT scalar-Kelly live order → ~Nx over-allocation on one underlying
event.

The live path uses ``optimize_exclusive_outcome_portfolio`` through
``preselect_single_family_edge_before_kelly`` / ``build_weather_family_decision``.
That optimizer compares buy-YES, native buy-NO, and multi-leg portfolios by the
same payoff-vector objective, so dominated sibling-NO baskets lose to a
capital-efficient center YES when the payoff is equivalent. Explicit
``ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS=1`` remains an emergency rollback only,
not the live default.

``dedup_mutually_exclusive_families`` remains a second-line runtime safety net
for legacy/mixed callers and existing-exposure conflicts. It prevents scalar
independent orders from leaking to the executor when no first-class family
portfolio intent exists.

Fail-safe: this gate can only REMOVE entries (set should_trade False). It never
adds, resizes, or re-enables a decision, so it can never increase exposure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
import logging
import math
import os
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.engine.evaluator import EdgeDecision

logger = logging.getLogger(__name__)

ENV_FLAG = "ZEUS_LIVE_MAX_ONE_ENTRY_PER_WEATHER_FAMILY"
_DEFAULT = "1"  # ON by default (live-money fail-safe).
BUY_NO_NATIVE_QUOTE_EVIDENCE_FLAG = "BUY_NO_NATIVE_QUOTE_EVIDENCE_ENABLED"
BUY_NO_NATIVE_QUOTE_EVIDENCE_SUBMIT_FLAG = "BUY_NO_NATIVE_QUOTE_EVIDENCE_SUBMIT_ENABLED"

# Wave 4 (2026-05-27), live repair 2026-06-19: family-portfolio optimizer
# activation. Default 3 lets the live optimizer compare common
# dominated baskets (for example two sibling NO legs) against a capital-efficient
# center YES. Set the env var to 1 only for an explicit emergency single-leg
# rollback.
ENV_FAMILY_PORTFOLIO_MAX_LEGS_LIVE = "ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS"
DEFAULT_FAMILY_PORTFOLIO_MAX_LEGS_LIVE = 3
# Hard cap on a family portfolio's worst-case loss (USD). When set, the
# optimizer rejects portfolios with ``max_loss_usd > cap`` (returns None) so
# the caller falls back to the single-leg safety selector. Default None = no cap (relies
# on per-leg Kelly + portfolio_heat to bound exposure).
ENV_FAMILY_PORTFOLIO_MAX_LOSS_USD = "ZEUS_FAMILY_PORTFOLIO_MAX_LOSS_USD"


def _family_portfolio_max_legs() -> int:
    """Live max_legs for the Stage B family portfolio optimizer.

    Unknown or invalid values fall back to the live default.
    """
    try:
        raw = os.environ.get(
            ENV_FAMILY_PORTFOLIO_MAX_LEGS_LIVE,
            str(DEFAULT_FAMILY_PORTFOLIO_MAX_LEGS_LIVE),
        )
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_FAMILY_PORTFOLIO_MAX_LEGS_LIVE


def _family_portfolio_max_loss_usd() -> float | None:
    """Optional hard-cap on Stage B portfolio worst-case loss (USD).

    Returns None when unset, disabled, or invalid (fail-open: no cap).
    """
    raw = os.environ.get(ENV_FAMILY_PORTFOLIO_MAX_LOSS_USD, "").strip()
    if not raw:
        return None
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return None
    return cap if cap > 0.0 else None

from src.contracts.no_trade_reason import NoTradeReason
from src.config import get_mode, settings

# Audit reason string for dropped bins.
MUTUALLY_EXCLUSIVE_FAMILY_DEDUP = "mutually_exclusive_family_dedup"
# X2 fix (Copilot review of PR #348): audit-trail string for the Wave 4
# loss-cap rejection path. NoTradeReason enum bump is deferred until the next
# DB-migration PR (SV15 CHECK constraint requires schema_version bump + re-pin).
# The string constant matches the MUTUALLY_EXCLUSIVE_FAMILY_DEDUP pattern so
# operators can grep / aggregate optimizer loss-cap fallbacks from logs.
# See architecture/market_cost_seam_executable_uncertainty_2026_05_27.md
# section Wave 4.
FAMILY_PORTFOLIO_LOSS_CAP_EXCEEDED = "family_portfolio_loss_cap_exceeded"
FAMILY_REJECTION_STAGE = "ANTI_CHURN"
_SAME_FAMILY_MONITOR_OWNED_REASON_BASE = "OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED"


@dataclass(frozen=True)
class WeatherFamilyKey:
    """Identity for one mutually-exclusive weather outcome family."""

    city: str
    target_date: str
    temperature_metric: str
    market_family_id: str = ""


@dataclass(frozen=True)
class WeatherFamilyExposure:
    """Minimal read model for existing open/pending/active family exposure."""

    key: WeatherFamilyKey
    bin_label: str = ""
    phase: str = "active"
    position_id: str = ""


@dataclass(frozen=True)
class FamilyPreselectionDrop:
    """One FDR-selected edge removed before scalar Kelly sizing."""

    edge: Any
    dropped_bin: str
    kept_bin: str
    family_selection_score: float
    kept_family_selection_score: float
    rejection_reason: str = MUTUALLY_EXCLUSIVE_FAMILY_DEDUP


@dataclass(frozen=True)
class FamilyPortfolioLeg:
    """One leg inside an exclusive-outcome payoff-vector portfolio."""

    edge: Any
    bin_label: str
    support_index: int
    cost: float
    outcome_probability: float
    direction: str


@dataclass(frozen=True)
class FamilyOutcome:
    """One settlement outcome in the complete family partition."""

    support_index: int
    probability: float


@dataclass(frozen=True)
class ExclusiveOutcomePortfolio:
    """Single-family payoff object for mutually-exclusive weather bins.

    Stage B can extend this to multi-leg payoff vectors. The current live path
    intentionally emits one explicit family-approved leg so scalar Kelly never
    sees sibling hypotheses as independent positions.
    """

    family_key: WeatherFamilyKey
    selected_leg: Any
    candidate_legs: tuple[Any, ...]
    selection_score: float
    expected_net_profit_usd: float
    expected_fill_probability: float
    objective: str = "single_leg_expected_net_profit"
    selected_legs: tuple[Any, ...] = ()
    candidate_leg_descriptors: tuple[FamilyPortfolioLeg, ...] = ()
    payoff_matrix: tuple[tuple[float, ...], ...] = ()
    posterior_vector: tuple[float, ...] = ()
    cost_vector: tuple[float, ...] = ()
    leg_weights: tuple[float, ...] = ()
    outcome_support_indices: tuple[int, ...] = ()
    expected_log_growth: float = 0.0
    capital_cost_usd: float = 0.0
    capital_efficiency: float = 0.0
    max_loss_usd: float = 0.0
    fallback_candidate_legs: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not self.selected_legs:
            object.__setattr__(self, "selected_legs", (self.selected_leg,))
        if not self.fallback_candidate_legs:
            object.__setattr__(self, "fallback_candidate_legs", self.selected_legs)


@dataclass(frozen=True)
class WeatherFamilyDecision:
    """Decision authority for one weather outcome family."""

    portfolio: ExclusiveOutcomePortfolio
    dropped: tuple[FamilyPreselectionDrop, ...]
    family_portfolio_intent: bool = True


_BLOCKING_EXPOSURE_PHASES = frozenset(
    {
        "",
        "open",
        "pending",
        "active",
        "pending_entry",
        "pending_tracked",
        "entered",
        "holding",
        "day0_window",
        "pending_exit",
        "acked",
        "live",
        "partial",
        "partially_filled",
        "filled",
        "submit_unknown_side_effect",
        "unknown",
        "review_required",
        "submitted",
    }
)


def _family_key(
    city: str,
    target_date: str,
    temperature_metric: str,
    market_family_id: str = "",
) -> WeatherFamilyKey:
    return WeatherFamilyKey(
        str(city),
        str(target_date),
        str(temperature_metric),
        str(market_family_id or ""),
    )


def _field(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _exposure_key(exposure: Any) -> WeatherFamilyKey:
    key = _field(exposure, "key", None)
    if isinstance(key, WeatherFamilyKey):
        return key
    if isinstance(key, dict):
        return WeatherFamilyKey(
            str(key.get("city", "")),
            str(key.get("target_date", "")),
            str(key.get("temperature_metric", "")),
            str(
                key.get("market_family_id")
                or key.get("event_slug")
                or key.get("market_slug")
                or key.get("condition_id")
                or ""
            ),
        )
    return WeatherFamilyKey(
        str(_field(exposure, "city", "")),
        str(_field(exposure, "target_date", "")),
        str(_field(exposure, "temperature_metric", "")),
        str(
            _field(
                exposure,
                "market_family_id",
                _field(
                    exposure,
                    "event_slug",
                    _field(exposure, "market_slug", _field(exposure, "condition_id", "")),
                ),
            )
            or ""
        ),
    )


def _family_keys_conflict(left: WeatherFamilyKey, right: WeatherFamilyKey) -> bool:
    """Return whether two keys should share one exposure budget.

    Weather temperature bins for one city/date/metric are one settlement
    partition. Venue ids and market slugs are not allowed to narrow this live
    mutex because they can be per-bin/per-condition identifiers rather than the
    physical underlying event.
    """
    if (
        left.city,
        left.target_date,
        left.temperature_metric,
    ) != (
        right.city,
        right.target_date,
        right.temperature_metric,
    ):
        return False
    return True


def _exposure_bin_label(exposure: Any) -> str:
    return str(
        _field(
            exposure,
            "bin_label",
            _field(exposure, "range_label", _field(exposure, "outcome_label", "")),
        )
        or ""
    )


def _decision_bin_label(decision: "EdgeDecision") -> str:
    edge = getattr(decision, "edge", None)
    if edge is None or getattr(edge, "bin", None) is None:
        return ""
    return str(getattr(edge.bin, "label", "") or "")


def _blocking_exposures_for_key(
    exposures: Iterable[Any] | None,
    key: WeatherFamilyKey,
) -> list[Any]:
    if exposures is None:
        return []
    blocking: list[Any] = []
    for exposure in exposures:
        if not _family_keys_conflict(_exposure_key(exposure), key):
            continue
        phase = str(_field(exposure, "phase", _field(exposure, "state", "")) or "").lower()
        if phase in _BLOCKING_EXPOSURE_PHASES:
            blocking.append(exposure)
    return blocking


def _weather_family_exposures_from_portfolio_impl(portfolio: Any) -> list[WeatherFamilyExposure]:
    """Project portfolio positions into the family-gate exposure read model."""
    exposures: list[WeatherFamilyExposure] = []
    for pos in getattr(portfolio, "positions", None) or ():
        city = str(_field(pos, "city", "") or "")
        target_date = str(_field(pos, "target_date", "") or "")
        temperature_metric = str(_field(pos, "temperature_metric", "") or "")
        market_family_id = str(
            _field(
                pos,
                "market_family_id",
                _field(pos, "event_slug", _field(pos, "market_slug", "")),
            )
            or ""
        )
        if not (city and target_date and temperature_metric):
            continue
        phase = str(_field(pos, "phase", _field(pos, "state", "")) or "")
        if phase.lower() not in _BLOCKING_EXPOSURE_PHASES:
            continue
        exposures.append(
            WeatherFamilyExposure(
                key=WeatherFamilyKey(
                    city,
                    target_date,
                    temperature_metric,
                    market_family_id,
                ),
                bin_label=_exposure_bin_label(pos),
                phase=phase,
                position_id=str(_field(pos, "position_id", _field(pos, "trade_id", "")) or ""),
            )
        )
    return exposures


_TRADE_COMMAND_BLOCKING_STATES = frozenset(
    {
        "ACKED",
        "LIVE",
        "PARTIAL",
        "PARTIALLY_FILLED",
        "FILLED",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "UNKNOWN",
        "REVIEW_REQUIRED",
        "PENDING",
        "SUBMITTED",
    }
)
_TRADE_ORDER_BLOCKING_STATES = frozenset(
    {
        "LIVE",
        "RESTING",
        "PARTIALLY_MATCHED",
        "MATCHED",
        "ACKED",
        "UNKNOWN",
        "REVIEW_REQUIRED",
    }
)
_TRADE_FACT_BLOCKING_STATES = frozenset({"MATCHED", "MINED", "CONFIRMED", "PARTIAL"})
_TRADE_POSITION_BLOCKING_PHASES = frozenset(
    {"pending_entry", "active", "day0_window", "pending_exit"}
)


def _table_exists(conn: Any, table_name: str, *, schema: str = "main") -> bool:
    expected_schema = schema
    try:
        rows = conn.execute("PRAGMA table_list").fetchall()
    except Exception:
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
        except Exception:
            return False
        return expected_schema == "main" and row is not None
    for row in rows:
        row_schema = str(row[0]) if len(row) > 0 else ""
        row_name = str(row[1]) if len(row) > 1 else ""
        row_type = str(row[2]) if len(row) > 2 else ""
        if row_schema == expected_schema and row_name == table_name and row_type == "table":
            return True
    return False


def _attached_schemas(conn: Any) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    except Exception:
        return {"main"}


def _first_table_schema(
    conn: Any,
    table_name: str,
    *,
    preferred: tuple[str, ...] = ("main", "world", "forecasts"),
) -> str | None:
    schemas = _attached_schemas(conn)
    for schema in preferred:
        if schema in schemas and _table_exists(conn, table_name, schema=schema):
            return schema
    for schema in sorted(schemas):
        if _table_exists(conn, table_name, schema=schema):
            return schema
    return None


def _qualified_table(schema: str, table_name: str) -> str:
    if schema == "main":
        return table_name
    return f"{schema}.{table_name}"


def _table_columns(conn: Any, table_name: str, *, schema: str = "main") -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA {schema}.table_info({table_name})").fetchall()
    except Exception:
        try:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        except Exception:
            return set()
    return {str(row[1]) for row in rows if len(row) > 1}


def _column_expr(
    columns: set[str],
    alias: str,
    column_name: str,
    *,
    default: str = "NULL",
) -> str:
    return f"{alias}.{column_name}" if column_name in columns else default


def _weather_family_exposures_from_trade_db_impl(conn: Any) -> list[WeatherFamilyExposure]:
    """Read family exposure from command/order/trade truth.

    The trade DB owns venue command/order/trade facts. Family metadata still
    prefers the canonical position projection when it exists, but projection
    lag must not erase durable command truth, and command-row absence must not
    erase a held chain/bridge position. A blocking exposure is admitted directly
    from open ``position_current`` rows and from command/order/trade truth that
    says an ENTRY is live, partially matched, filled, unknown-side-effect, or
    under review.
    """

    if conn is None:
        return []
    schemas = _attached_schemas(conn)
    position_schema = "world" if "world" in schemas and _table_exists(conn, "position_current", schema="world") else "main"
    has_position_current = _table_exists(conn, "position_current", schema=position_schema)
    has_venue_commands = _table_exists(conn, "venue_commands")
    if not has_position_current and not has_venue_commands:
        return []
    has_order_facts = _table_exists(conn, "venue_order_facts")
    has_trade_facts = _table_exists(conn, "venue_trade_facts")
    order_state_sql = (
        "EXISTS (SELECT 1 FROM venue_order_facts vof "
        "WHERE vof.command_id = vc.command_id "
        f"AND UPPER(COALESCE(vof.state, '')) IN ({','.join('?' for _ in _TRADE_ORDER_BLOCKING_STATES)}))"
        if has_order_facts
        else "0"
    )
    trade_state_sql = (
        "EXISTS (SELECT 1 FROM venue_trade_facts vtf "
        "WHERE vtf.command_id = vc.command_id "
        f"AND UPPER(COALESCE(vtf.state, '')) IN ({','.join('?' for _ in _TRADE_FACT_BLOCKING_STATES)}))"
        if has_trade_facts
        else "0"
    )
    command_state_placeholders = ",".join("?" for _ in _TRADE_COMMAND_BLOCKING_STATES)
    params: list[str] = [
        *_TRADE_COMMAND_BLOCKING_STATES,
        *(_TRADE_ORDER_BLOCKING_STATES if has_order_facts else ()),
        *(_TRADE_FACT_BLOCKING_STATES if has_trade_facts else ()),
    ]
    exposures: list[WeatherFamilyExposure] = []
    seen: set[tuple[WeatherFamilyKey, str, str, str]] = set()

    def _append_exposure(
        *,
        city: Any,
        target_date: Any,
        metric: Any,
        market_family_id: Any = "",
        bin_label: Any,
        phase: Any,
        position_id: Any,
    ) -> None:
        if not (city and target_date and metric):
            return
        exposure = WeatherFamilyExposure(
            key=WeatherFamilyKey(
                str(city),
                str(target_date),
                str(metric),
                str(market_family_id or ""),
            ),
            bin_label=str(bin_label or ""),
            phase=str(phase or "pending_entry"),
            position_id=str(position_id or ""),
        )
        dedupe_key = (
            exposure.key,
            exposure.bin_label,
            exposure.phase,
            exposure.position_id,
        )
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        exposures.append(exposure)

    if has_position_current:
        pc_cols = _table_columns(conn, "position_current", schema=position_schema)
        pc_family_id = _column_expr(
            pc_cols,
            "pc",
            "market_family_id",
            default=_column_expr(
                pc_cols,
                "pc",
                "event_slug",
                default=_column_expr(pc_cols, "pc", "market_slug"),
            ),
        )
        pc_city = _column_expr(pc_cols, "pc", "city")
        pc_target_date = _column_expr(pc_cols, "pc", "target_date")
        pc_metric = _column_expr(
            pc_cols,
            "pc",
            "temperature_metric",
            default=_column_expr(pc_cols, "pc", "metric"),
        )
        pc_bin_label = _column_expr(pc_cols, "pc", "bin_label")
        pc_phase = _column_expr(pc_cols, "pc", "phase", default="'active'")
        pc_position_id = _column_expr(pc_cols, "pc", "position_id")
        positive_terms = [
            f"COALESCE(pc.{name}, 0) > 0"
            for name in ("chain_shares", "shares", "chain_cost_basis_usd", "cost_basis_usd", "size_usd")
            if name in pc_cols
        ]
        direct_positive_sql = " AND (" + " OR ".join(positive_terms) + ")" if positive_terms else " AND 0"
        direct_phase_sql = (
            "LOWER(COALESCE(pc.phase, '')) IN ({})".format(
                ",".join("?" for _ in _TRADE_POSITION_BLOCKING_PHASES)
            )
            if "phase" in pc_cols
            else "1=1"
        )
        direct_position_sql = f"""
        SELECT
            {pc_city} AS city,
            {pc_target_date} AS target_date,
            {pc_metric} AS temperature_metric,
            {pc_family_id} AS market_family_id,
            {pc_bin_label} AS bin_label,
            {pc_phase} AS phase,
            {pc_position_id} AS position_id
        FROM {position_schema}.position_current pc
        WHERE {direct_phase_sql}
          {direct_positive_sql}
        """
        try:
            rows = conn.execute(
                direct_position_sql,
                tuple(sorted(_TRADE_POSITION_BLOCKING_PHASES)) if "phase" in pc_cols else (),
            ).fetchall()
        except Exception:
            logger.warning("[WEATHER_FAMILY_EXPOSURE_POSITION_DB_READ_FAILED]", exc_info=True)
        else:
            for row in rows:
                city, target_date, metric, market_family_id, bin_label, phase, position_id = tuple(row)
                _append_exposure(
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    market_family_id=market_family_id,
                    bin_label=bin_label,
                    phase=phase,
                    position_id=position_id,
                )

        if has_venue_commands:
            projection_sql = f"""
        SELECT
            {pc_city} AS city,
            {pc_target_date} AS target_date,
            {pc_metric} AS temperature_metric,
            {pc_family_id} AS market_family_id,
            {pc_bin_label} AS bin_label,
            {pc_phase} AS phase,
            {pc_position_id} AS position_id,
            vc.command_id
        FROM venue_commands vc
        JOIN {position_schema}.position_current pc
          ON pc.position_id = vc.position_id
        WHERE vc.intent_kind = 'ENTRY'
          AND (
              UPPER(COALESCE(vc.state, '')) IN ({command_state_placeholders})
              OR {order_state_sql}
              OR {trade_state_sql}
          )
        """
            try:
                rows = conn.execute(projection_sql, params).fetchall()
            except Exception:
                logger.warning("[WEATHER_FAMILY_EXPOSURE_PROJECTION_DB_READ_FAILED]", exc_info=True)
            else:
                for row in rows:
                    (
                        city,
                        target_date,
                        metric,
                        market_family_id,
                        bin_label,
                        phase,
                        position_id,
                        command_id,
                    ) = tuple(row)
                    _append_exposure(
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        market_family_id=market_family_id,
                        bin_label=bin_label,
                        phase=phase,
                        position_id=position_id or command_id,
                    )

    if not has_venue_commands:
        return exposures

    envelope_table = "venue_submission_envelopes" if _table_exists(conn, "venue_submission_envelopes") else None
    snapshot_table = "executable_market_snapshots" if _table_exists(conn, "executable_market_snapshots") else None
    market_schema = _first_table_schema(
        conn,
        "market_events",
        preferred=("forecasts", "main", "world"),
    )
    if market_schema is None:
        return exposures
    vc_cols = _table_columns(conn, "venue_commands")
    env_cols = _table_columns(conn, "venue_submission_envelopes") if envelope_table else set()
    snap_cols = _table_columns(conn, "executable_market_snapshots") if snapshot_table else set()
    me_cols = _table_columns(conn, "market_events", schema=market_schema)

    envelope_join = (
        "LEFT JOIN venue_submission_envelopes env ON env.envelope_id = vc.envelope_id"
        if envelope_table
        else ""
    )
    snapshot_join = (
        "LEFT JOIN executable_market_snapshots snap ON snap.snapshot_id = vc.snapshot_id"
        if snapshot_table
        else ""
    )
    env_condition = _column_expr(env_cols, "env", "condition_id")
    env_token = _column_expr(env_cols, "env", "selected_outcome_token_id")
    env_label = _column_expr(env_cols, "env", "outcome_label")
    snap_condition = _column_expr(snap_cols, "snap", "condition_id")
    snap_token = _column_expr(snap_cols, "snap", "selected_outcome_token_id")
    snap_label = _column_expr(snap_cols, "snap", "outcome_label")
    snap_slug = _column_expr(snap_cols, "snap", "event_slug")
    vc_market_id = _column_expr(vc_cols, "vc", "market_id")
    vc_token_id = _column_expr(vc_cols, "vc", "token_id")
    me_condition = _column_expr(me_cols, "me", "condition_id")
    me_token = _column_expr(me_cols, "me", "token_id")
    me_slug = _column_expr(me_cols, "me", "market_slug")
    me_range_label = _column_expr(me_cols, "me", "range_label")
    me_outcome = _column_expr(me_cols, "me", "outcome")
    market_table = _qualified_table(market_schema, "market_events")
    command_identity_sql = f"""
        SELECT DISTINCT
            me.city,
            me.target_date,
            me.temperature_metric,
            COALESCE({me_slug}, {snap_slug}, {vc_market_id}, {env_condition}, {snap_condition}) AS market_family_id,
            COALESCE({me_range_label}, {me_outcome}, {env_label}, {snap_label}) AS bin_label,
            vc.state AS phase,
            COALESCE(NULLIF(vc.position_id, ''), vc.command_id) AS position_id,
            vc.command_id
        FROM venue_commands vc
        {envelope_join}
        {snapshot_join}
        JOIN {market_table} me
          ON (
              ({env_condition} IS NOT NULL AND {me_condition} = {env_condition})
              OR ({snap_condition} IS NOT NULL AND {me_condition} = {snap_condition})
              OR ({vc_market_id} IS NOT NULL AND {me_condition} = {vc_market_id})
              OR ({env_token} IS NOT NULL AND {me_token} = {env_token})
              OR ({snap_token} IS NOT NULL AND {me_token} = {snap_token})
              OR ({vc_token_id} IS NOT NULL AND {me_token} = {vc_token_id})
              OR ({snap_slug} IS NOT NULL AND {me_slug} = {snap_slug})
              OR ({vc_market_id} IS NOT NULL AND {me_slug} = {vc_market_id})
          )
        WHERE vc.intent_kind = 'ENTRY'
          AND (
              UPPER(COALESCE(vc.state, '')) IN ({command_state_placeholders})
              OR {order_state_sql}
              OR {trade_state_sql}
          )
    """
    try:
        rows = conn.execute(command_identity_sql, params).fetchall()
    except Exception:
        logger.warning("[WEATHER_FAMILY_EXPOSURE_COMMAND_DB_READ_FAILED]", exc_info=True)
        return exposures
    for row in rows:
        city, target_date, metric, market_family_id, bin_label, phase, position_id, command_id = tuple(row)
        _append_exposure(
            city=city,
            target_date=target_date,
            metric=metric,
            market_family_id=market_family_id,
            bin_label=bin_label,
            phase=phase,
            position_id=position_id or command_id,
        )
    return exposures


class WeatherFamilyExposureReducer:
    """Canonical family-exposure reducer for evaluator/runtime/no-trade gates.

    P1-4: family exposure cannot be inferred separately by evaluator,
    cycle-runtime dedup, and telemetry. All public exposure readers route through
    this reducer so command/order/fill truth and portfolio projection share one
    blocking-phase contract and one dedupe shape.
    """

    @staticmethod
    def from_portfolio(portfolio: Any) -> list[WeatherFamilyExposure]:
        return _weather_family_exposures_from_portfolio_impl(portfolio)

    @staticmethod
    def from_trade_db(conn: Any) -> list[WeatherFamilyExposure]:
        return _weather_family_exposures_from_trade_db_impl(conn)

    @staticmethod
    def merge(*exposure_groups: Iterable[Any] | None) -> list[WeatherFamilyExposure]:
        merged: list[WeatherFamilyExposure] = []
        seen: set[tuple[WeatherFamilyKey, str, str, str]] = set()
        for group in exposure_groups:
            for raw in group or ():
                key = _exposure_key(raw)
                phase = str(_field(raw, "phase", _field(raw, "state", "")) or "")
                exposure = WeatherFamilyExposure(
                    key=key,
                    bin_label=_exposure_bin_label(raw),
                    phase=phase,
                    position_id=str(_field(raw, "position_id", _field(raw, "trade_id", "")) or ""),
                )
                dedupe_key = (
                    exposure.key,
                    exposure.bin_label,
                    exposure.phase,
                    exposure.position_id,
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                merged.append(exposure)
        return merged

    @classmethod
    def resolve(cls, *, trade_conn: Any | None = None, portfolio: Any | None = None) -> list[WeatherFamilyExposure]:
        trade_exposures = cls.from_trade_db(trade_conn) if trade_conn is not None else []
        portfolio_exposures = cls.from_portfolio(portfolio) if portfolio is not None else []
        return cls.merge(trade_exposures, portfolio_exposures)


def weather_family_exposures_from_portfolio(portfolio: Any) -> list[WeatherFamilyExposure]:
    """Compatibility wrapper; new callers should use WeatherFamilyExposureReducer."""

    return WeatherFamilyExposureReducer.from_portfolio(portfolio)


def weather_family_exposures_from_trade_db(conn: Any) -> list[WeatherFamilyExposure]:
    """Compatibility wrapper; new callers should use WeatherFamilyExposureReducer."""

    return WeatherFamilyExposureReducer.from_trade_db(conn)


def resolve_weather_family_exposures(
    *,
    trade_conn: Any | None = None,
    portfolio: Any | None = None,
) -> list[WeatherFamilyExposure]:
    """Canonical public exposure resolver for live family gates."""

    return WeatherFamilyExposureReducer.resolve(trade_conn=trade_conn, portfolio=portfolio)


def _has_conflicting_existing_exposure(
    decision: "EdgeDecision",
    exposures: list[Any],
) -> tuple[bool, Any | None]:
    new_label = _decision_bin_label(decision)
    for exposure in exposures:
        existing_label = _exposure_bin_label(exposure)
        if not existing_label or not new_label or existing_label != new_label:
            return True, exposure
    return False, None


def family_gate_enabled() -> bool:
    """True when the scalar family safety gate is ON.

    Default ON ("1"). Disabled only by an explicit ``"0"`` / ``"false"`` /
    ``"no"`` / ``"off"`` (case-insensitive). Any other value (including the
    unset default) keeps the live-money fail-safe ON.
    """
    raw = os.environ.get(ENV_FLAG, _DEFAULT).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _decision_family_selection_score(decision: "EdgeDecision") -> float:
    """Expected net-profit proxy for a sized family leg.

    This intentionally does not rank by size. It uses the candidate's forward
    edge at the submitted notional, with non-positive or malformed costs
    falling back to zero.
    """

    edge = getattr(decision, "edge", None)
    if edge is None:
        return 0.0
    try:
        forward_edge = float(getattr(edge, "forward_edge", 0.0) or 0.0)
    except (TypeError, ValueError):
        forward_edge = 0.0
    try:
        cost = float(getattr(edge, "entry_price", 0.0) or getattr(edge, "p_market", 0.0) or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    try:
        notional = float(getattr(decision, "size_usd", 0.0) or 0.0)
    except (TypeError, ValueError):
        notional = 0.0
    if cost <= 0.0 or notional <= 0.0:
        return max(0.0, forward_edge)
    return max(0.0, forward_edge) * (notional / cost)


def _edge_family_selection_score(edge: Any) -> float:
    """Stage-A pre-Kelly utility proxy for one mutually-exclusive family leg.

    This intentionally does not use ``EdgeDecision.size_usd`` because that is a
    downstream scalar-Kelly output. Until Stage B lands a payoff-vector
    optimizer, the least-wrong pre-sizing score is the edge's executable
    forward edge, with legacy ``edge`` as fallback.
    """

    try:
        score = float(getattr(edge, "forward_edge", 0.0) or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score == 0.0:
        try:
            score = float(getattr(edge, "edge", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
    return score


def _edge_cost(edge: Any) -> float:
    try:
        cost = float(getattr(edge, "entry_price", 0.0) or getattr(edge, "p_market", 0.0) or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return cost if 0.0 < cost < 1.0 else 0.0


def _edge_posterior(edge: Any) -> float:
    try:
        q_lcb = getattr(edge, "q_lcb_5pct", None)
        posterior = float(
            q_lcb
            if q_lcb is not None
            else getattr(edge, "p_posterior", 0.0) or getattr(edge, "p_model", 0.0) or 0.0
        )
    except (TypeError, ValueError):
        posterior = 0.0
    return max(0.0, min(1.0, posterior))


def _edge_outcome_probability(edge: Any) -> float:
    """Return settlement probability for this leg's own support outcome.

    ``BinEdge.p_posterior`` is held-side probability. For ``buy_yes`` that is
    already P(this bin settles YES); for ``buy_no`` it is P(this bin does NOT
    settle YES). The family payoff matrix is indexed by settlement outcomes, so
    NO legs must be inverted before building the outcome probability vector.
    """

    held_side_probability = _edge_posterior(edge)
    direction = str(getattr(edge, "direction", "") or "").lower()
    if direction == "buy_no":
        return max(0.0, min(1.0, 1.0 - held_side_probability))
    return held_side_probability


def _edge_bin_label(edge: Any) -> str:
    bin_obj = getattr(edge, "bin", None)
    if bin_obj is None:
        return ""
    return str(getattr(bin_obj, "label", "") or "")


def _edge_support_index(edge: Any, fallback: int) -> int:
    try:
        support_index = getattr(edge, "support_index", None)
        if support_index is not None:
            return int(support_index)
    except (TypeError, ValueError):
        pass
    return fallback


def _portfolio_payoff_for_leg(
    *,
    outcome_support_index: int,
    leg: FamilyPortfolioLeg,
) -> float:
    """Return dollar profit for one share of one leg in one family outcome."""

    cost = leg.cost
    if cost <= 0.0 or cost >= 1.0:
        return -cost
    direction = leg.direction.lower()
    leg_wins = outcome_support_index == leg.support_index
    if direction == "buy_yes":
        return (1.0 - cost) if leg_wins else -cost
    if direction == "buy_no":
        return -cost if leg_wins else (1.0 - cost)
    return _edge_family_selection_score(leg.edge)


def _normalize_probability_values(values: Sequence[float]) -> tuple[float, ...]:
    raw = [max(0.0, float(value)) for value in values]
    total = sum(raw)
    if total <= 0.0:
        return tuple(1.0 / len(raw) for _ in raw) if raw else ()
    return tuple(value / total for value in raw)


def _outcome_support_from_probabilities(
    outcome_probabilities: Mapping[int, float] | Sequence[float] | None,
    legs: list[FamilyPortfolioLeg],
) -> tuple[FamilyOutcome, ...]:
    """Return the complete settlement support used by the family optimizer.

    Candidate legs are executable instruments. They are not the outcome space. Live callers
    pass the full calibrated family probability vector so non-executable / non-candidate bins
    keep their loss states in the payoff matrix. Legacy unit callers that omit the vector fall
    back to the old leg-derived support, which is acceptable only outside the live evaluator
    seam.
    """

    support_probability: dict[int, float] = {}
    if outcome_probabilities is not None:
        if isinstance(outcome_probabilities, Mapping):
            iterator = outcome_probabilities.items()
        else:
            iterator = enumerate(outcome_probabilities)
        for raw_index, raw_probability in iterator:
            try:
                support_index = int(raw_index)
                probability = float(raw_probability)
            except (TypeError, ValueError):
                continue
            if support_index < 0 or not math.isfinite(probability):
                continue
            support_probability[support_index] = max(0.0, probability)

    if not support_probability:
        for leg in legs:
            support_probability[leg.support_index] = max(
                support_probability.get(leg.support_index, 0.0),
                leg.outcome_probability,
            )
    else:
        for leg in legs:
            support_probability.setdefault(leg.support_index, 0.0)

    if not support_probability:
        return ()

    ordered_indices = tuple(sorted(support_probability))
    normalized = _normalize_probability_values(
        [support_probability[index] for index in ordered_indices]
    )
    return tuple(
        FamilyOutcome(support_index=index, probability=probability)
        for index, probability in zip(ordered_indices, normalized)
    )


def _score_portfolio_combo(
    legs: list[FamilyPortfolioLeg],
    outcomes: tuple[FamilyOutcome, ...],
    selected_indexes: tuple[int, ...],
    *,
    log_growth_fraction: float = 0.01,
) -> tuple[float, float, float, float, float, tuple[tuple[float, ...], ...], tuple[float, ...], tuple[float, ...]]:
    """Score a candidate portfolio by capital-aware family payoff."""

    selected = [legs[i] for i in selected_indexes]
    posterior_vector = tuple(outcome.probability for outcome in outcomes)
    if not selected or not posterior_vector:
        return (-math.inf, 0.0, 0.0, 0.0, 0.0, (), (), ())

    leg_weights = tuple(1.0 / len(selected) for _ in selected)
    capital_cost = sum(max(0.0, leg.cost) for leg in selected)
    payoff_rows: list[tuple[float, ...]] = []
    outcome_returns: list[float] = []
    for outcome in outcomes:
        row = tuple(
            _portfolio_payoff_for_leg(
                outcome_support_index=outcome.support_index,
                leg=selected_leg,
            )
            for selected_leg in selected
        )
        payoff_rows.append(row)
        outcome_returns.append(sum(row))

    expected_net_profit = sum(
        probability * outcome_return
        for probability, outcome_return in zip(posterior_vector, outcome_returns)
    )
    capital_efficiency = expected_net_profit / capital_cost if capital_cost > 0.0 else -math.inf
    expected_log_growth = 0.0
    for probability, outcome_return in zip(posterior_vector, outcome_returns):
        normalized_return = outcome_return / capital_cost if capital_cost > 0.0 else outcome_return
        growth = 1.0 + log_growth_fraction * normalized_return
        if growth <= 0.0:
            expected_log_growth = -math.inf
            break
        expected_log_growth += probability * math.log(growth)
    return (
        expected_log_growth,
        expected_net_profit,
        capital_cost,
        capital_efficiency,
        min(outcome_returns),
        tuple(payoff_rows),
        posterior_vector,
        leg_weights,
    )


def _portfolio_dominated(
    candidate: tuple[float, tuple[float, ...]],
    challenger: tuple[float, tuple[float, ...]],
) -> bool:
    """Return True when challenger is no-more-costly and pays at least as well in every outcome."""

    candidate_capital, candidate_returns = candidate
    challenger_capital, challenger_returns = challenger
    if len(candidate_returns) != len(challenger_returns):
        return False
    if challenger_capital > candidate_capital + 1e-9:
        return False
    payoff_not_worse = all(
        challenger_return >= candidate_return - 1e-9
        for challenger_return, candidate_return in zip(challenger_returns, candidate_returns)
    )
    if not payoff_not_worse:
        return False
    return challenger_capital < candidate_capital - 1e-9 or any(
        challenger_return > candidate_return + 1e-9
        for challenger_return, candidate_return in zip(challenger_returns, candidate_returns)
    )


def optimize_exclusive_outcome_portfolio(
    edges: list[Any],
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    market_family_id: str = "",
    outcome_probabilities: Mapping[int, float] | Sequence[float] | None = None,
    min_legs: int = 1,
    max_legs: int = 1,
    allow_same_family_monitor_owned: bool = False,
) -> ExclusiveOutcomePortfolio | None:
    """Build a payoff-vector family portfolio before scalar order sizing."""

    if not edges:
        return None
    executable_edges = [
        edge
        for edge in edges
        if _edge_family_candidate_rejection_reason(
            edge,
            allow_same_family_monitor_owned=allow_same_family_monitor_owned,
        ) is None
    ]
    if not executable_edges:
        return None
    legs = sorted(
        [
            FamilyPortfolioLeg(
                edge=edge,
                bin_label=_edge_bin_label(edge),
                support_index=_edge_support_index(edge, idx),
                cost=_edge_cost(edge),
                outcome_probability=_edge_outcome_probability(edge),
                direction=str(getattr(edge, "direction", "") or ""),
            )
            for idx, edge in enumerate(executable_edges)
        ],
        key=lambda leg: (leg.support_index, leg.bin_label),
    )
    outcomes = _outcome_support_from_probabilities(outcome_probabilities, legs)
    if not outcomes:
        return None
    max_legs = max(1, min(int(max_legs or 1), len(legs)))
    min_legs = max(1, min(int(min_legs or 1), max_legs))
    best_key: tuple[float, float, float, float, float, float, tuple[int, ...]] | None = None
    best_payload: tuple[
        tuple[int, ...],
        float,
        float,
        float,
        float,
        float,
        float,
        tuple[tuple[float, ...], ...],
        tuple[float, ...],
        tuple[float, ...],
    ] | None = None
    payloads: list[
        tuple[
            tuple[int, ...],
            float,
            float,
            float,
            float,
            float,
            float,
            tuple[float, ...],
            tuple[tuple[float, ...], ...],
            tuple[float, ...],
            tuple[float, ...],
        ]
    ] = []
    for width in range(min_legs, max_legs + 1):
        for selected_indexes in combinations(range(len(legs)), width):
            (
                expected_log_growth,
                expected_net_profit,
                capital_cost,
                capital_efficiency,
                max_loss,
                payoff_matrix,
                posterior_vector,
                weights,
            ) = (
                _score_portfolio_combo(legs, outcomes, selected_indexes)
            )
            if not payoff_matrix:
                continue
            edge_selection_utility = sum(
                _edge_family_selection_score(legs[idx].edge) for idx in selected_indexes
            ) / float(width)
            outcome_returns = tuple(sum(row) for row in payoff_matrix)
            payloads.append(
                (
                    selected_indexes,
                    edge_selection_utility,
                    expected_log_growth,
                    expected_net_profit,
                    capital_cost,
                    capital_efficiency,
                    max_loss,
                    outcome_returns,
                    payoff_matrix,
                    posterior_vector,
                    weights,
                )
            )
    for payload in payloads:
        (
            selected_indexes,
            edge_selection_utility,
            expected_log_growth,
            expected_net_profit,
            capital_cost,
            capital_efficiency,
            max_loss,
            outcome_returns,
            payoff_matrix,
            posterior_vector,
            weights,
        ) = payload
        if any(
            other is not payload
            and _portfolio_dominated(
                (capital_cost, outcome_returns),
                (other[4], other[7]),
            )
            for other in payloads
        ):
            continue
        width = len(selected_indexes)
        key = (
            expected_log_growth,
            expected_net_profit,
            capital_efficiency,
            -capital_cost,
            edge_selection_utility,
            -float(width),
            tuple(-_edge_support_index(legs[idx].edge, idx) for idx in selected_indexes),
        )
        if best_key is None or key > best_key:
            best_key = key
            best_payload = (
                selected_indexes,
                edge_selection_utility,
                expected_log_growth,
                expected_net_profit,
                capital_cost,
                capital_efficiency,
                max_loss,
                payoff_matrix,
                posterior_vector,
                weights,
            )

    if best_payload is None:
        return None
    (
        selected_indexes,
        edge_selection_utility,
        expected_log_growth,
        expected_net_profit,
        capital_cost,
        capital_efficiency,
        max_loss,
        payoff_matrix,
        posterior_vector,
        weights,
    ) = best_payload
    selected_legs = tuple(legs[idx].edge for idx in selected_indexes)
    selected_leg = selected_legs[0]
    selected_cost_vector = tuple(legs[idx].cost for idx in selected_indexes)
    return ExclusiveOutcomePortfolio(
        family_key=_family_key(city, target_date, temperature_metric, market_family_id),
        selected_leg=selected_leg,
        selected_legs=selected_legs,
        candidate_legs=tuple(executable_edges),
        candidate_leg_descriptors=tuple(legs),
        selection_score=edge_selection_utility,
        expected_net_profit_usd=expected_net_profit,
        expected_fill_probability=1.0,
        objective="expected_log_growth_payoff_vector",
        payoff_matrix=payoff_matrix,
        posterior_vector=posterior_vector,
        cost_vector=selected_cost_vector,
        leg_weights=weights,
        outcome_support_indices=tuple(outcome.support_index for outcome in outcomes),
        expected_log_growth=expected_log_growth,
        capital_cost_usd=capital_cost,
        capital_efficiency=capital_efficiency,
        max_loss_usd=abs(min(0.0, max_loss)),
    )


def _edge_preselection_key(edge: Any) -> tuple[float, float, float, tuple[int, ...]]:
    score = _edge_family_selection_score(edge)
    try:
        posterior = float(getattr(edge, "p_posterior", 0.0) or 0.0)
    except (TypeError, ValueError):
        posterior = 0.0
    try:
        entry_price = float(getattr(edge, "entry_price", 0.0) or 0.0)
    except (TypeError, ValueError):
        entry_price = 0.0
    label = ""
    bin_obj = getattr(edge, "bin", None)
    if bin_obj is not None:
        label = str(getattr(bin_obj, "label", "") or "")
    return (score, posterior, entry_price, tuple(-ord(c) for c in label))


def _strict_feature_flag(name: str, *, default: bool = False) -> bool:
    flags = settings["feature_flags"]
    value = flags.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"feature flag {name} must be boolean, got {type(value).__name__}")
    return bool(value)


def _native_buy_no_live_rejection_reason() -> str | None:
    if not buy_no_native_quote_evidence_submit_enabled():
        return "BUY_NO_NATIVE_QUOTE_EVIDENCE_SUBMIT_DISABLED"
    return None


def buy_no_native_quote_evidence_enabled() -> bool:
    return _strict_feature_flag(BUY_NO_NATIVE_QUOTE_EVIDENCE_FLAG)


def buy_no_native_quote_evidence_submit_enabled() -> bool:
    evidence_enabled = buy_no_native_quote_evidence_enabled()
    submit_enabled = _strict_feature_flag(BUY_NO_NATIVE_QUOTE_EVIDENCE_SUBMIT_FLAG)
    if submit_enabled and not evidence_enabled:
        raise ValueError(
            f"{BUY_NO_NATIVE_QUOTE_EVIDENCE_SUBMIT_FLAG}=true requires "
            f"{BUY_NO_NATIVE_QUOTE_EVIDENCE_FLAG}=true"
        )
    return submit_enabled


def _edge_live_family_executable_rejection_reason(edge: Any) -> str | None:
    """Return structural live-execution reason before a leg consumes fallback rank."""

    structural = _edge_family_candidate_rejection_reason(edge)
    if structural:
        return structural
    if get_mode() != "live":
        return None
    if str(getattr(edge, "direction", "") or "") != "buy_no":
        return None
    try:
        return _native_buy_no_live_rejection_reason()
    except ValueError as exc:
        return f"BUY_NO_NATIVE_QUOTE_EVIDENCE_FLAG_INVALID:{exc}"


def _same_family_monitor_owned_reason(reason: object) -> bool:
    text = str(reason or "").strip()
    return text.startswith(_SAME_FAMILY_MONITOR_OWNED_REASON_BASE)


def _edge_family_candidate_rejection_reason(
    edge: Any,
    *,
    allow_same_family_monitor_owned: bool = False,
) -> str | None:
    """Return the upstream candidate blocker the family optimizer must honor."""

    missing_reason = str(getattr(edge, "missing_reason", "") or "").strip()
    if missing_reason:
        if allow_same_family_monitor_owned and _same_family_monitor_owned_reason(missing_reason):
            return None
        return missing_reason
    if getattr(edge, "admitted", None) is False:
        return "FAMILY_CANDIDATE_NOT_ADMITTED"
    return None


def preselect_single_family_edge_before_kelly(
    edges: list[Any],
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    outcome_probabilities: Mapping[int, float] | Sequence[float] | None = None,
    enabled: bool | None = None,
) -> tuple[list[Any], list[FamilyPreselectionDrop]]:
    """Collapse one mutually-exclusive weather family before scalar Kelly.

    ``evaluate_candidate`` calls this after full-family FDR and before the
    Kelly/risk loop. That prevents dropped sibling bins from mutating projected
    exposure, heat, min-order, or risk throttles.
    """

    if enabled is None:
        enabled = family_gate_enabled()
    if not enabled or len(edges) < 2:
        return edges, []

    # The family portfolio optimizer is the live selector, including when max_legs is explicitly set
    # to 1 for emergency single-leg rollback. This keeps pre-Kelly selection
    # and build_weather_family_decision on the same payoff-vector objective.
    max_legs = _family_portfolio_max_legs()
    portfolio = optimize_exclusive_outcome_portfolio(
        edges,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
        outcome_probabilities=outcome_probabilities,
        max_legs=max_legs,
    )
    loss_cap = _family_portfolio_max_loss_usd()
    if portfolio is not None and (
        loss_cap is None or float(portfolio.max_loss_usd) <= loss_cap
    ):
        selected_ids = {id(edge) for edge in portfolio.selected_legs}
        kept: list[Any] = [edge for edge in edges if id(edge) in selected_ids]
        kept_labels = ",".join(_edge_bin_label(edge) for edge in kept)
        kept_score = float(portfolio.selection_score)
        drops_b: list[FamilyPreselectionDrop] = []
        for edge in edges:
            if id(edge) in selected_ids:
                continue
            drops_b.append(
                FamilyPreselectionDrop(
                    edge=edge,
                    dropped_bin=_edge_bin_label(edge),
                    kept_bin=kept_labels,
                    family_selection_score=_edge_family_selection_score(edge),
                    kept_family_selection_score=kept_score,
                )
            )
            logger.info(
                "[FAMILY_PORTFOLIO_STAGE_B_PRE_KELLY] family=%s|%s|%s "
                "max_legs=%d kept=%s dropped_bin=%r",
                city, target_date, temperature_metric, max_legs,
                kept_labels, _edge_bin_label(edge),
            )
        return kept, drops_b
    # Optimizer returned None OR loss cap exceeded -> fall through to the
    # deterministic single-leg safety selector.

    best = max(edges, key=_edge_preselection_key)
    best_bin = ""
    best_edge_bin = getattr(best, "bin", None)
    if best_edge_bin is not None:
        best_bin = str(getattr(best_edge_bin, "label", "") or "")
    best_score = _edge_family_selection_score(best)
    kept: list[Any] = [best]
    drops: list[FamilyPreselectionDrop] = []
    for edge in edges:
        if edge is best:
            continue
        dropped_bin = ""
        edge_bin = getattr(edge, "bin", None)
        if edge_bin is not None:
            dropped_bin = str(getattr(edge_bin, "label", "") or "")
        score = _edge_family_selection_score(edge)
        drops.append(
            FamilyPreselectionDrop(
                edge=edge,
                dropped_bin=dropped_bin,
                kept_bin=best_bin,
                family_selection_score=score,
                kept_family_selection_score=best_score,
            )
        )
        logger.info(
            "[MUTUALLY_EXCLUSIVE_FAMILY_PRE_KELLY] family=%s|%s|%s dropped_bin=%r "
            "kept_bin=%r dropped_score=%.6f kept_score=%.6f",
            city,
            target_date,
            temperature_metric,
            dropped_bin,
            best_bin,
            score,
            best_score,
        )
    return kept, drops


def build_weather_family_decision(
    edges: list[Any],
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    market_family_id: str = "",
    outcome_probabilities: Mapping[int, float] | Sequence[float] | None = None,
    enabled: bool | None = None,
) -> WeatherFamilyDecision | None:
    """Build the family portfolio decision consumed before scalar Kelly."""

    gate_enabled = family_gate_enabled() if enabled is None else enabled
    if not gate_enabled:
        return None
    candidate_edges = list(edges)
    blocked_edges: list[tuple[Any, str]] = []
    excluded_blocked_edges: list[tuple[Any, str]] = []
    executable_candidate_edges: list[Any] = []
    for edge in candidate_edges:
        rejection_reason = _edge_live_family_executable_rejection_reason(edge)
        if rejection_reason:
            blocked_edges.append((edge, rejection_reason))
        else:
            executable_candidate_edges.append(edge)
    if executable_candidate_edges:
        candidate_edges = executable_candidate_edges
        excluded_blocked_edges = blocked_edges

    # Wave 4 (2026-05-27): max_legs controls the live family portfolio optimizer.
    max_legs = _family_portfolio_max_legs()
    try:
        fallback_candidate_count = int(
            os.environ.get("ZEUS_LIVE_FAMILY_EXECUTABLE_FALLBACK_CANDIDATES", "3")
        )
    except ValueError:
        fallback_candidate_count = 3
    portfolio = optimize_exclusive_outcome_portfolio(
        candidate_edges,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
        market_family_id=market_family_id,
        outcome_probabilities=outcome_probabilities,
        max_legs=max_legs,
    )
    if portfolio is None:
        return None
    # Wave 4: hard-cap on worst-case family loss. When the cap is set and the
    # optimizer's portfolio exceeds it, return None so the caller can use the
    # deterministic single-leg safety selector. Fail-open (no cap) when env unset.
    loss_cap = _family_portfolio_max_loss_usd()
    if loss_cap is not None and float(portfolio.max_loss_usd) > loss_cap:
        logger.warning(
            "[%s] city=%s target=%s metric=%s "
            "max_loss_usd=%.4f > cap=%.4f - falling back to single-leg safety selector",
            FAMILY_PORTFOLIO_LOSS_CAP_EXCEEDED.upper(),
            city, target_date, temperature_metric,
            float(portfolio.max_loss_usd), loss_cap,
        )
        return None
    ranked_edges = sorted(candidate_edges, key=_edge_preselection_key, reverse=True)
    selected_legs = list(portfolio.selected_legs)
    if len(selected_legs) > 1:
        # A multi-leg family portfolio is a coherent payoff vector. Ranked
        # scalar siblings are not interchangeable fallback legs for it; replacing
        # one selected leg requires re-optimizing the portfolio, not appending the
        # old scalar fallback queue.
        fallback_candidates = selected_legs
    else:
        # A scalar entry has no first-class ordered-alternative intent today.
        # Emitting ranked sibling fallbacks as separate should_trade decisions lets
        # the submit loop race a second same-family order after an unknown first
        # attempt. Keep only the optimized leg until ordered alternatives are a
        # typed execution-intent primitive.
        fallback_candidates = [portfolio.selected_leg]
    portfolio = ExclusiveOutcomePortfolio(
        family_key=portfolio.family_key,
        selected_leg=portfolio.selected_leg,
        selected_legs=portfolio.selected_legs,
        fallback_candidate_legs=tuple(fallback_candidates),
        candidate_legs=portfolio.candidate_legs,
        candidate_leg_descriptors=portfolio.candidate_leg_descriptors,
        selection_score=portfolio.selection_score,
        expected_net_profit_usd=portfolio.expected_net_profit_usd,
        expected_fill_probability=portfolio.expected_fill_probability,
        objective=(
            f"{portfolio.objective}:ranked_executable_fallback_top_"
            f"{len(fallback_candidates)}"
        ),
        payoff_matrix=portfolio.payoff_matrix,
        posterior_vector=portfolio.posterior_vector,
        cost_vector=portfolio.cost_vector,
        leg_weights=portfolio.leg_weights,
        outcome_support_indices=portfolio.outcome_support_indices,
        expected_log_growth=portfolio.expected_log_growth,
        capital_cost_usd=portfolio.capital_cost_usd,
        capital_efficiency=portfolio.capital_efficiency,
        max_loss_usd=portfolio.max_loss_usd,
    )
    selected_set = set(id(edge) for edge in portfolio.fallback_candidate_legs)
    kept_bin = ",".join(_edge_bin_label(edge) for edge in portfolio.fallback_candidate_legs)
    dropped: list[FamilyPreselectionDrop] = []
    for edge, rejection_reason in excluded_blocked_edges:
        dropped.append(
            FamilyPreselectionDrop(
                edge=edge,
                dropped_bin=_edge_bin_label(edge),
                kept_bin=kept_bin,
                family_selection_score=_edge_family_selection_score(edge),
                kept_family_selection_score=portfolio.selection_score,
                rejection_reason=rejection_reason,
            )
        )
    for edge in candidate_edges:
        if id(edge) in selected_set:
            continue
        dropped.append(
            FamilyPreselectionDrop(
                edge=edge,
                dropped_bin=_edge_bin_label(edge),
                kept_bin=kept_bin,
                family_selection_score=_edge_family_selection_score(edge),
                kept_family_selection_score=portfolio.selection_score,
            )
        )
    return WeatherFamilyDecision(portfolio=portfolio, dropped=tuple(dropped))


def _pick_best_index(decisions: list["EdgeDecision"], idxs: list[int]) -> int:
    """Return the index of the best emergency single-leg family member.

    Best = highest ``(size_usd, forward_edge)``; on a full economic tie the
    lexicographically smallest ``decision_id`` wins (stable, deterministic).
    """
    def _composite(i: int) -> tuple[float, float, tuple[int, ...]]:
        score = _decision_family_selection_score(decisions[i])
        edge = getattr(decisions[i], "edge", None)
        try:
            fwd = float(getattr(edge, "forward_edge", 0.0) or 0.0) if edge is not None else 0.0
        except (TypeError, ValueError):
            fwd = 0.0
        did = getattr(decisions[i], "decision_id", "") or ""
        # Negate the id codepoints so that `max` selects the SMALLEST id on a
        # (size, forward_edge) tie — deterministic and run-stable.
        neg_id = tuple(-ord(c) for c in did)
        return (score, fwd, neg_id)

    return max(idxs, key=_composite)


def dedup_mutually_exclusive_families(
    decisions: list["EdgeDecision"],
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    market_family_id: str = "",
    enabled: bool | None = None,
    existing_exposures: Iterable[Any] | None = None,
    family_portfolio_intent: bool = False,
    family_portfolio_allowed_exposure_ids: Iterable[str] | None = None,
) -> list["EdgeDecision"]:
    """Second-line safety gate for scalar entries in an exclusive family.

    Mutates the passed ``EdgeDecision`` objects in place (sets
    ``should_trade=False`` + ``rejection_stage`` + ``rejection_reasons`` string
    + ``rejection_reason_detail`` on dropped bins)
    and returns the same list for caller convenience.

    Args:
        decisions: the per-candidate decision list from ``evaluate_candidate``.
            All entries belong to ONE ``(city, target_date, metric)`` market
            family (one candidate == one family; see
            ``src/strategy/selection_family.py`` family-scope docstring). They
            are nonetheless grouped defensively by
            ``(city, target_date, temperature_metric)`` so the contract holds
            even if a future caller passes a mixed list.
        city / target_date / temperature_metric: the family key. EdgeDecision
            does not itself carry the family identity (city/date/metric live on
            the candidate, not the per-bin decision), so the caller supplies it.
        enabled: override for the env gate; ``None`` reads
            ``family_gate_enabled()``.
        existing_exposures: optional current-cycle read model of already
            open/pending/active exposure keyed by ``WeatherFamilyKey``. When a
            different bin already has exposure, new independent entries for the
            same family are blocked unless a typed rebalance intent explicitly
            names the existing exposure id.
        family_portfolio_intent: true only when a first-class family portfolio
            optimizer emitted the executable portfolio intent. FDR-selected
            hypotheses alone are not a portfolio intent. This boolean is NOT
            authority to ignore existing live exposure.
        family_portfolio_allowed_exposure_ids: position/command ids that a
            typed monitor/rebalance intent is explicitly allowed to touch. New
            entry intents pass none, so existing same-family exposure blocks.

    Returns:
        The same ``decisions`` list (mutated in place when the gate fires).
    """
    if enabled is None:
        enabled = family_gate_enabled()
    if not enabled:
        return decisions

    key = _family_key(city, target_date, temperature_metric, market_family_id)
    allowed_exposure_ids = {
        str(value).strip()
        for value in (family_portfolio_allowed_exposure_ids or ())
        if str(value).strip()
    }
    blocking_exposures = [
        exposure
        for exposure in _blocking_exposures_for_key(existing_exposures, key)
        if str(_field(exposure, "position_id", "") or "").strip() not in allowed_exposure_ids
    ]
    if blocking_exposures:
        for d in decisions:
            if not getattr(d, "should_trade", False):
                continue
            conflicts, exposure = _has_conflicting_existing_exposure(d, blocking_exposures)
            if not conflicts:
                continue
            existing_label = _exposure_bin_label(exposure) if exposure is not None else ""
            existing_position = str(_field(exposure, "position_id", "") or "")
            d.should_trade = False
            d.rejection_stage = FAMILY_REJECTION_STAGE
            d.rejection_reasons = [MUTUALLY_EXCLUSIVE_FAMILY_DEDUP]
            d.rejection_reason_enum = NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP
            d.rejection_reason_detail = (
                f"family={city}|{target_date}|{temperature_metric} "
                f"dropped_bin={_decision_bin_label(d)!r} "
                f"existing_exposure_bin={existing_label!r} "
                f"existing_position_id={existing_position!r} "
                f"({ENV_FLAG}=1; existing family exposure; no scoped rebalance intent)"
            )
            logger.info(
                "[MUTUALLY_EXCLUSIVE_FAMILY_DEDUP] family=%s|%s|%s dropped_bin=%r "
                "existing_exposure_bin=%r existing_position_id=%r decision_id=%s",
                city,
                target_date,
                temperature_metric,
                _decision_bin_label(d),
                existing_label,
                existing_position,
                getattr(d, "decision_id", "") or "",
            )

    # Group the should_trade=True decisions by the family key. With one
    # candidate per call this is a single group, but the dict keeps the
    # contract robust under a mixed-list caller.
    groups: dict[tuple[str, str, str], list[int]] = {}
    for i, d in enumerate(decisions):
        if not getattr(d, "should_trade", False):
            continue
        key = (str(city), str(target_date), str(temperature_metric))
        groups.setdefault(key, []).append(i)

    for key, idxs in groups.items():
        if len(idxs) < 2:
            # Single-bin (or single-entry) family: untouched — byte-identical
            # to the legacy per-edge path. No regression.
            continue
        if all(
            int(getattr(decisions[i], "family_fallback_candidate_count", 0) or 0) > 1
            and str(getattr(decisions[i], "family_portfolio_leg_role", "") or "")
            == "portfolio_selected"
            for i in idxs
        ):
            for i in idxs:
                validations = getattr(decisions[i], "applied_validations", None)
                if isinstance(validations, list) and "family_ranked_executable_fallback" not in validations:
                    validations.append("family_ranked_executable_fallback")
            logger.info(
                "[MUTUALLY_EXCLUSIVE_FAMILY_FALLBACK_CANDIDATES] family=%s candidate_count=%d",
                "|".join(key),
                len(idxs),
            )
            continue
        best_i = _pick_best_index(decisions, idxs)
        best = decisions[best_i]
        best_label = ""
        best_edge = getattr(best, "edge", None)
        if best_edge is not None and getattr(best_edge, "bin", None) is not None:
            best_label = str(getattr(best_edge.bin, "label", "") or "")
        kept_size = float(getattr(best, "size_usd", 0.0) or 0.0)
        for i in idxs:
            if i == best_i:
                continue
            d = decisions[i]
            d.should_trade = False
            d.rejection_stage = FAMILY_REJECTION_STAGE
            d.rejection_reasons = [MUTUALLY_EXCLUSIVE_FAMILY_DEDUP]
            d.rejection_reason_enum = NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP
            dropped_label = ""
            d_edge = getattr(d, "edge", None)
            if d_edge is not None and getattr(d_edge, "bin", None) is not None:
                dropped_label = str(getattr(d_edge.bin, "label", "") or "")
            d.rejection_reason_detail = (
                f"family={city}|{target_date}|{temperature_metric} "
                f"dropped_bin={dropped_label!r} kept_bin={best_label!r} "
                f"kept_size_usd={kept_size:.2f} "
                f"kept_expected_net_profit_usd={_decision_family_selection_score(best):.6f} "
                f"({ENV_FLAG}=1; scalar family safety gate; no portfolio intent)"
            )
            logger.info(
                "[MUTUALLY_EXCLUSIVE_FAMILY_DEDUP] family=%s|%s|%s dropped_bin=%r "
                "kept_bin=%r kept_size_usd=%.2f decision_id=%s",
                city,
                target_date,
                temperature_metric,
                dropped_label,
                best_label,
                kept_size,
                getattr(d, "decision_id", "") or "",
            )

    return decisions
