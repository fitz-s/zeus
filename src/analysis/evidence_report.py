# Created: 2026-05-21
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T4
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/07_PHASE_6_EVIDENCE_LADDER.md §Object model
"""EvidenceReport — per-strategy evidence aggregator.

Aggregates decision_events, no_trade_events, regret_decompositions, and
shadow_experiments data for a given strategy into a structured report consumed
by the LiveReadinessTribunal.

Bayesian Beta(2,2) credible interval
--------------------------------------
For a strategy with n observed decisions and k wins (edge-positive outcomes),
the posterior under a Beta(2,2) prior is Beta(2+k, 2+n-k). The 95% credible
interval lower bound is the 2.5th percentile of this posterior.

Beta(2,2) is a "weak" prior centered on 0.5 — appropriate when we have limited
prior information and want to avoid extreme extrapolation from small samples.

Promotion gate fires only when CI_lower > breakeven + cost_of_capital.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scipy.stats import beta as scipy_beta

from src.contracts.evidence_tier import EvidenceTier


# ---------------------------------------------------------------------------
# Domain object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceReport:
    """Per-strategy evidence summary for tribunal input.

    Fields
    ------
    strategy_id:
        Strategy key.
    tier_observed:
        Current EvidenceTier from registry (what we have evidence for).
    n_decisions:
        Total shadow/paper decisions logged for this strategy.
    n_wins:
        Decisions with positive realized edge (win-rate numerator).
    n_no_trades:
        Structured no-trade events logged for this strategy. Current schema
        stores strategy_key directly; older compatibility rows are counted only
        when they carry the candidate_strategy_key marker in reason_detail.
    n_settled:
        Settled decisions (subset of n_decisions with known outcome).
    mean_regret_usd:
        Mean total_regret_usd across regret_decompositions rows.
    ci_lower:
        Lower bound of 95% Beta(2,2) credible interval on win-rate.
        None if n_settled == 0.
    ci_upper:
        Upper bound of 95% Beta(2,2) credible interval on win-rate.
        None if n_settled == 0.
    breakeven_win_rate:
        Strategy-specific breakeven win-rate (from profile metadata or caller).
    promotion_blockers:
        List of operator-recorded promotion blockers from registry.
    """
    strategy_id: str
    tier_observed: EvidenceTier
    n_decisions: int
    n_wins: int
    n_no_trades: int
    n_settled: int
    mean_regret_usd: float
    ci_lower: Optional[float]
    ci_upper: Optional[float]
    breakeven_win_rate: float
    promotion_blockers: tuple[str, ...] = ()
    # review5.23 P1-2: n_decisions is strategy+source scoped; when experiment_id or
    # cohort_tag is supplied, only regret analytics are narrowed — the denominator
    # is NOT.  "REGRET_ONLY_SCOPE" signals the mismatch so promotion logic can block.
    # "FULL_SCOPE" means no experiment/cohort filter was applied (all three metrics
    # describe the same population).
    cohort_scope_status: str = "FULL_SCOPE"
    # review5.23 P1-3: no_trade_events has no experiment_id / cohort_tag FK, so
    # n_no_trades can only be scoped by strategy_key + source.  Always
    # "strategy_source_only"; callers must not treat it as cohort evidence.
    n_no_trades_scope_status: str = "strategy_source_only"


def _bayesian_ci(
    n_wins: int,
    n_trials: int,
    alpha_prior: float = 2.0,
    beta_prior: float = 2.0,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Beta(alpha_prior, beta_prior) posterior credible interval.

    Posterior: Beta(alpha_prior + n_wins, beta_prior + n_trials - n_wins).
    Returns (lower, upper) bounds for the given confidence level.
    """
    lower_p = (1.0 - confidence) / 2.0
    upper_p = 1.0 - lower_p
    a = alpha_prior + n_wins
    b = beta_prior + (n_trials - n_wins)
    lower = float(scipy_beta.ppf(lower_p, a, b))
    upper = float(scipy_beta.ppf(upper_p, a, b))
    return lower, upper


def build_evidence_report(
    strategy_id: str,
    tier_observed: EvidenceTier,
    *,
    conn: sqlite3.Connection,
    breakeven_win_rate: float | None = None,
    promotion_blockers: tuple[str, ...] = (),
    experiment_id: str | None = None,
    cohort_tag: str | None = None,
    source: str | None = None,
) -> EvidenceReport:
    """Build an EvidenceReport by querying the world DB.

    Queries:
      - decision_events: authoritative n_decisions denominator (strategy_key filter)
      - no_trade_events: structured strategy_key count, excluding degraded rows
      - regret_decompositions: n_settled, n_wins (total_regret_usd > 0 = WIN),
        mean_regret, joined through decision_events to verify strategy_key+source match

    Optional scoping:
      - experiment_id: restrict regret analytics to a single experiment;
                       n_decisions denominator is NOT narrowed by experiment because
                       decision_events has no experiment FK — unsettled decisions must
                       remain in the denominator.
      - cohort_tag:    restrict regret analytics to a cohort (same caveat)
      - source:        restrict denominator, no_trade_events, AND regret analytics
                       (via de.source JOIN on decision_events) to a specific source
                       ('phase0_backfill', 'live_decision', 'shadow_decision')

    breakeven_win_rate must be supplied by the caller (strategy-specific value from
    the profile registry). No default — a hardcoded 0.5 silently miscalibrates the
    promotion gate for every strategy with a different fee structure.

    Sign convention: total_regret_usd > 0 means realized > counterfactual (WIN).
    Consistent with regret_decomposer.py SEV2-1 canonical convention.

    INV-37: caller supplies conn; never auto-opens.
    """
    if breakeven_win_rate is None:
        raise ValueError(
            "breakeven_win_rate must be supplied by caller; "
            "no generic default — use strategy profile registry value."
        )

    # n_decisions: authoritative denominator from decision_events.
    # Filtered by source when provided. NOT narrowed by experiment_id/cohort_tag
    # because decision_events has no experiment FK — scoping via regret_decompositions
    # would exclude unsettled decisions, corrupting the denominator (P1-7 fix).
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    # Try to ATTACH the trade DB so we can read decision_integrity_quarantine
    # (which lives in zeus_trades.db) from this world-DB connection.
    # Safe to skip if the trade DB path is unavailable (tests, non-production envs).
    #
    # Priority order (ghost-table-safe):
    #   1. trade already ATTACHed by caller → use trade-qualified ref
    #   2. trade DB path exists on disk → ATTACH it, use trade-qualified ref
    #   3. table co-located in main (test-only; no trade path) → use unqualified ref
    #
    # The unqualified fallback (3) is intentionally LAST so that a ghost
    # decision_integrity_quarantine table in the world DB (e.g. from a mis-applied
    # ensure_table call) never shadows the real trade table when the trade path is
    # resolvable. Only a pure in-memory test DB with no trade path reaches branch 3.
    _quarantine_ref: str | None = None
    _trade_attached_here = False
    try:
        from src.state.db import _zeus_trade_db_path  # local import; avoid circular at module load
        _trade_db_path = _zeus_trade_db_path()
        _attached_schemas = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "trade" in _attached_schemas:
            # Branch 1: already attached by caller.
            _quarantine_ref = "trade.decision_integrity_quarantine"
        elif Path(_trade_db_path).exists():
            # Branch 2: ATTACH trade DB (preferred over unqualified ghost fallback).
            conn.execute("ATTACH DATABASE ? AS trade", (str(_trade_db_path),))
            _trade_attached_here = True
            _quarantine_ref = "trade.decision_integrity_quarantine"
        elif "decision_integrity_quarantine" in tables:
            # Branch 3: co-located (pure in-memory test DB; trade path not resolvable).
            _quarantine_ref = "decision_integrity_quarantine"
    except Exception:  # noqa: BLE001
        # Non-fatal: quarantine exclusion is best-effort on learning paths.
        pass

    if "decision_events" in tables:
        _de_params: list = [strategy_id]
        _de_filters = "WHERE strategy_key = ?"
        if source is not None:
            _de_filters += " AND source = ?"
            _de_params.append(source)
        # Exclude decision_events rows whose opportunity_fact entry is quarantined
        # (non-contributing forecast extrema). Uses the opportunity_fact quarantine
        # row_id (= opportunity_fact.decision_id = decision_events.decision_event_id)
        # rather than the decision_events hash row_id, since the link is 1-to-1 and
        # decision_event_id IS unique on opportunity_fact.
        if _quarantine_ref is not None:
            _de_filters += (
                f" AND NOT EXISTS ("
                f"SELECT 1 FROM {_quarantine_ref} q"
                f" WHERE q.table_name = 'opportunity_fact'"
                f" AND q.row_id = de.decision_event_id)"
            )
        _de_decision_sql = f"SELECT COUNT(*) FROM decision_events de {_de_filters}"
        n_decisions_row = conn.execute(_de_decision_sql, _de_params).fetchone()
        n_decisions = int(n_decisions_row[0] or 0)
    else:
        n_decisions = 0

    # Win/regret analytics from regret_decompositions joined through decision_events
    # to verify strategy_key AND source match the experiment's strategy.
    # Finding 3 fix: join through decision_events.decision_event_id so cross-strategy
    # contamination (a regret row sharing experiment_id with a different strategy_key)
    # is excluded.
    # Guarded on table presence: pre-Phase-6 DBs and partial fixtures lack these
    # tables; absence produces zeros rather than OperationalError.
    n_wins = 0
    mean_regret_usd = 0.0
    n_settled = 0
    if "regret_decompositions" in tables and "shadow_experiments" in tables:
        _rg_params: list = [strategy_id]
        _rg_join = ""
        _rg_filter = "WHERE se.strategy_id = ?"
        if "decision_events" in tables:
            _rg_join = """
            JOIN decision_events de
              ON de.decision_event_id = rd.decision_event_id
             AND de.strategy_key = se.strategy_id"""
            if source is not None:
                _rg_filter += " AND de.source = ?"
                _rg_params.append(source)
        if experiment_id is not None:
            _rg_filter += " AND se.experiment_id = ?"
            _rg_params.append(experiment_id)
        if cohort_tag is not None:
            _rg_filter += " AND se.cohort_tag = ?"
            _rg_params.append(cohort_tag)
        # Exclude regret rows backed by quarantined decision_events
        # (non-contributing forecast extrema) from learning aggregates.
        # Uses opportunity_fact quarantine (row_id = decision_id = decision_event_id)
        # since that's the 1-to-1 forecast-linkage anchor.
        if _quarantine_ref is not None:
            _rg_filter += (
                f" AND NOT EXISTS ("
                f"SELECT 1 FROM {_quarantine_ref} q"
                f" WHERE q.table_name = 'opportunity_fact'"
                f" AND q.row_id = rd.decision_event_id)"
            )
        regret_row = conn.execute(
            f"""
            SELECT
                COUNT(*) as n_regret,
                SUM(CASE WHEN rd.total_regret_usd > 0 THEN 1 ELSE 0 END) as n_wins,
                AVG(rd.total_regret_usd) as mean_regret
            FROM regret_decompositions rd
            JOIN shadow_experiments se ON rd.experiment_id = se.experiment_id
            {_rg_join}
            {_rg_filter}
            """,
            _rg_params,
        ).fetchone()
        n_wins = int(regret_row[1] or 0)
        mean_regret_usd = float(regret_row[2] or 0.0)
        n_settled = int(regret_row[0] or 0)  # rows with settled regret outcomes

    if "no_trade_events" in tables:
        no_trade_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(no_trade_events)").fetchall()
        }
        if "strategy_key" in no_trade_columns:
            _nt_params: list = [strategy_id]
            if "schema_compatibility" in no_trade_columns:
                _nt_where = "WHERE strategy_key = ? AND schema_compatibility = 'current'"
            else:
                _nt_where = "WHERE strategy_key = ?"
            # P1-8 fix: scope no_trade_events by source (via event_source column) when provided.
            # no_trade_events has no experiment_id FK so experiment/cohort cannot be applied.
            if source is not None and "event_source" in no_trade_columns:
                _nt_where += " AND event_source = ?"
                _nt_params.append(source)
            n_no_trade_row = conn.execute(
                f"SELECT COUNT(*) FROM no_trade_events {_nt_where}",
                _nt_params,
            ).fetchone()
            n_no_trades = int(n_no_trade_row[0] or 0)
        else:
            n_no_trade_row = conn.execute(
                """
                SELECT COUNT(*) FROM no_trade_events
                WHERE reason_detail LIKE ?
                """,
                (f"%candidate_strategy_key={strategy_id};%",),
            ).fetchone()
            n_no_trades = int(n_no_trade_row[0] or 0)
    else:
        n_no_trades = 0

    # Bayesian CI
    ci_lower: Optional[float] = None
    ci_upper: Optional[float] = None
    if n_settled > 0:
        ci_lower, ci_upper = _bayesian_ci(n_wins, n_settled)

    # Detach trade DB if we attached it in this call.
    if _trade_attached_here:
        try:
            conn.execute("DETACH DATABASE trade")
        except Exception:  # noqa: BLE001
            pass

    # P1-2: n_decisions is NOT narrowed by experiment_id/cohort_tag (no FK on
    # decision_events). Surface the mismatch so promotion gates can reject.
    cohort_scope_status = (
        "REGRET_ONLY_SCOPE" if (experiment_id is not None or cohort_tag is not None) else "FULL_SCOPE"
    )
    return EvidenceReport(
        strategy_id=strategy_id,
        tier_observed=tier_observed,
        n_decisions=n_decisions,
        n_wins=n_wins,
        n_no_trades=n_no_trades,
        n_settled=n_settled,
        mean_regret_usd=mean_regret_usd,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        breakeven_win_rate=breakeven_win_rate,
        promotion_blockers=promotion_blockers,
        cohort_scope_status=cohort_scope_status,
        n_no_trades_scope_status="strategy_source_only",
    )
