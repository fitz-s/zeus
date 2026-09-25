# Created: 2026-09-25
# Authority basis: reversal_plan_tier0_2026-08-24 item 3b follow-up — the
#   market-anchored q correction (src/calibration/market_anchored_live_fit.py
#   _fit_selection, MIN_TRAIN_ROWS=20) trains only on settled confirmed
#   fills (79 rows/30d), never enough to clear any scope. This slice persists
#   each evaluated candidate's own raw q, served q, semantics revision and
#   witness identity onto tier0_candidate_set_provenance so the trainer can
#   use the ~125k evaluated candidates instead.
"""q-provenance threading (solver) + persistence (schema/writer) coverage.

Three surfaces, each covered without recomputing q anywhere in the test:
  1. ``_global_candidate_q_provenance`` — the pure (raw, served, revision)
     extractor off a candidate's own sealed correction.
  2. ``_global_candidate_evaluations`` — threads that extraction plus the
     candidate's own ``probability_witness_identity`` onto every produced
     ``GlobalSingleOrderCandidateEvaluation``, scored or bare-rejected alike.
  3. ``tier0_candidate_set_provenance_schema.ensure_table`` +
     ``global_batch_runtime._persist_tier0_candidate_set`` — the DB side:
     idempotent forward-only migration of an old-shape live table, and a
     fail-closed NULL write when an evaluation carries no correction.
"""

from __future__ import annotations

import datetime as _dt
import math
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.payoff_q_correction import (
    CalibrationFitScope,
    PayoffQCorrection,
    SourceIdentityBaseline,
)
from src.solve.solver import (
    ExpectedBuyTerminalWealthCertificate,
    ExpectedGrowthComparison,
    GlobalSingleOrderCandidate,
    GlobalSingleOrderCandidateEvaluation,
    _global_candidate_evaluations,
    _global_candidate_q_provenance,
    executable_curve_identity,
)
from src.state.schema.tier0_candidate_set_provenance_schema import ensure_table
from src.engine.global_batch_runtime import _persist_tier0_candidate_set


# --- old-shape CREATE (verbatim: the table's DDL immediately before this
# slice added q_raw/q_served/probability_semantics_revision/
# probability_witness_identity) -- used only to prove ensure_table upgrades a
# genuinely pre-existing live table in place.
_OLD_SHAPE_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tier0_candidate_set_provenance (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    selection_epoch_identity TEXT NOT NULL,
    decision_at_utc TEXT NOT NULL,
    city_date_group_id TEXT NOT NULL,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    family_key TEXT NOT NULL,
    bin_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    token_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    p0 REAL,
    p0_source TEXT,
    lead_bucket TEXT,
    eligible INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    rejection_reason TEXT,
    selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
    market_key TEXT NOT NULL,
    settled_y INTEGER CHECK (settled_y IN (0, 1) OR settled_y IS NULL),
    created_at TEXT NOT NULL,
    UNIQUE (selection_epoch_identity, candidate_id)
)
"""


def _new_column_names() -> set[str]:
    return {
        "q_raw", "q_served", "probability_semantics_revision",
        "probability_witness_identity",
    }


# ---------------------------------------------------------------------------
# 1. _global_candidate_q_provenance — pure extractor
# ---------------------------------------------------------------------------

def _source_identity_baseline(**overrides) -> SourceIdentityBaseline:
    fields = dict(
        family_key="family-a", bin_id="bin-a", side="YES", token_id="token-a",
        raw_q=0.42, p0=0.40, raw_probability_revision="day0_v3",
        q_version="q-v1", probability_witness_identity="witness-baseline",
        probability_content_identity="content-1", source_truth_identity="truth-1",
        sample_matrix_identity="matrix-1",
    )
    fields.update(overrides)
    return SourceIdentityBaseline(**fields)


def _payoff_q_correction(fit_scope=None, **overrides) -> PayoffQCorrection:
    fields = dict(
        family_key="family-a", bin_id="bin-a", side="YES", token_id="token-a",
        raw_q=0.33, corrected_q=0.30, p0=0.31, lead_bucket="L00_24",
        alpha_lead=1.0, beta=0.1, lambda_=0.05, training_cutoff="2026-09-01T00:00:00+00:00",
        n_train=25, param_hash="hash-1", fit_scope=fit_scope,
    )
    fields.update(overrides)
    return PayoffQCorrection(**fields)


def test_q_provenance_none_score_is_all_none():
    assert _global_candidate_q_provenance(None) == (None, None, None)


def test_q_provenance_none_correction_is_all_none():
    score = SimpleNamespace(payoff_q_correction=None)
    assert _global_candidate_q_provenance(score) == (None, None, None)


def test_q_provenance_source_identity_baseline_reads_raw_and_revision():
    baseline = _source_identity_baseline()
    score = SimpleNamespace(payoff_q_correction=baseline)
    raw, served, revision = _global_candidate_q_provenance(score)
    assert raw == pytest.approx(0.42)
    assert served == pytest.approx(0.42)  # baseline never corrects
    assert revision == "day0_v3"


def test_q_provenance_payoff_q_correction_without_fit_scope_has_no_revision():
    correction = _payoff_q_correction(fit_scope=None)
    score = SimpleNamespace(payoff_q_correction=correction)
    raw, served, revision = _global_candidate_q_provenance(score)
    assert raw == pytest.approx(0.33)
    assert served == pytest.approx(0.30)
    assert revision is None


def test_q_provenance_payoff_q_correction_with_fit_scope_reads_revision():
    scope = CalibrationFitScope(
        metric="high", execution_mode="TAKER_LIMIT",
        execution_contract="FOK_FULL_OR_ZERO", raw_probability_revision="day0_v3",
    )
    correction = _payoff_q_correction(fit_scope=scope)
    score = SimpleNamespace(payoff_q_correction=correction)
    raw, served, revision = _global_candidate_q_provenance(score)
    assert raw == pytest.approx(0.33)
    assert served == pytest.approx(0.30)
    assert revision == "day0_v3"


# ---------------------------------------------------------------------------
# 2. _global_candidate_evaluations — threading onto the evaluation object
# ---------------------------------------------------------------------------

def _minimal_candidate(candidate_id: str, *, probability_witness_identity: str) -> GlobalSingleOrderCandidate:
    at = _dt.datetime(2026, 9, 20, tzinfo=_dt.timezone.utc)
    curve = ExecutableCostCurve(
        token_id=f"token-{candidate_id}",
        side="YES",
        snapshot_id=f"book-{candidate_id}",
        book_hash=f"hash-{candidate_id}",
        levels=(BookLevel(price=Decimal("0.40"), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("5"),
        quote_ttl=_dt.timedelta(seconds=30),
        fee_details={
            "feeSchedule_taker_only": True,
            "fee_rate_bps": 0.0,
            "fee_rate_fraction": 0.0,
            "fee_rate_raw_unit": "fraction",
            "fee_rate_source_field": "fee_rate_fraction",
            "fee_type": "weather_fees",
            "source": "test",
            "token_id": f"token-{candidate_id}",
        },
    )
    return GlobalSingleOrderCandidate(
        candidate_id=candidate_id,
        family_key="family-a",
        bin_id="bin-a",
        condition_id=f"condition-{candidate_id}",
        side="YES",
        token_id=f"token-{candidate_id}",
        probability_witness_identity=probability_witness_identity,
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=at,
        execution_curve_identity=executable_curve_identity(curve),
        ledger_snapshot_id=f"ledger-{candidate_id}",
        executable_cost_curve=curve,
        resolution_identity=f"resolution-{candidate_id}",
        neg_risk=False,
    )


_DECISION_AT = _dt.datetime(2026, 9, 20, 12, 0, tzinfo=_dt.timezone.utc)


def _coherent_buy_score_fields() -> dict[str, object]:
    """A genuinely coherent scored-BUY shape (reused from the passing fixture
    in tests/integration/test_w3_solve_seam_g3.py::
    test_persist_tier0_candidate_set_writes_winner_and_rejected_rows_idempotently)
    -- GlobalSingleOrderCandidateEvaluation.__post_init__ enforces tight
    Kelly/expected-growth coherence on any non-rejected evaluation, so this
    fixture exists to satisfy that invariant rather than to test it.
    """

    win_q = 0.55
    expected_du = (1.0 - win_q) * math.log(0.9412) + win_q * math.log(1.0612)
    terminal = ExpectedBuyTerminalWealthCertificate(
        probability_basis="POSTERIOR_PREDICTIVE_MEAN",
        win_probability_mean=win_q,
        loss_probability_mean=1.0 - win_q,
        loss_payoff_usd=Decimal("-5.88"),
        win_payoff_usd=Decimal("6.12"),
        wealth_after_loss_usd=Decimal("94.12"),
        wealth_after_win_usd=Decimal("106.12"),
        expected_delta_log_wealth=expected_du,
        expected_ev_usd=0.72,
    )
    growth = ExpectedGrowthComparison(
        probability_basis="POSTERIOR_PREDICTIVE_MEAN",
        probability_witness_identity="q-a",
        expected_delta_log_wealth=expected_du,
        expected_ev_usd=0.72,
        capital_lock_hours=10.0,
        expected_log_growth_per_hour=expected_du / 10.0,
        expected_capital_efficiency=expected_du / 5.88,
    )
    return dict(
        shares=Decimal("12"),
        cost_usd=Decimal("5.88"),
        cash_proceeds_usd=Decimal("0"),
        robust_delta_log_wealth=0.0,
        ruin_probability_reduction=0.0,
        robust_ev_usd=0.0,
        capital_efficiency=0.0,
        capital_action_mode="SETTLEMENT_LOCKED_BUY",
        resolution_at_utc=_DECISION_AT + _dt.timedelta(hours=10),
        capital_lock_hours=10.0,
        robust_log_growth_per_hour=None,
        limit_price=Decimal("0.49"),
        expected_fill_price_before_fee=Decimal("0.49"),
        max_spend_usd=Decimal("5.88"),
        current_token_shares=Decimal("0"),
        full_kelly_target_shares=Decimal("40"),
        fractional_kelly_target_shares=Decimal("12"),
        terminal_wealth=None,
        expected_terminal_wealth=terminal,
        expected_growth=growth,
        buy_sizing_mode="FRACTIONAL_TARGET",
        buy_minimum_marketable_repair=None,
    )


def _stub_score(candidate: GlobalSingleOrderCandidate, *, payoff_q_correction) -> SimpleNamespace:
    """Duck-typed stand-in for GlobalSingleOrderDecision: carries exactly the
    attributes _global_candidate_evaluations reads off a scored candidate.
    Using a real GlobalSingleOrderDecision here would require satisfying its
    own (separate, heavier) construction invariants, which this slice does
    not touch; _global_candidate_evaluations only ever attribute-accesses
    ``score``, so a stub exercises the exact same production code path.
    """

    return SimpleNamespace(
        candidate=candidate,
        payoff_q_correction=payoff_q_correction,
        **_coherent_buy_score_fields(),
    )


def test_evaluations_thread_q_provenance_for_a_scored_winner():
    candidate = _minimal_candidate("cand-win", probability_witness_identity="witness-candidate")
    baseline = _source_identity_baseline(probability_witness_identity="witness-baseline-differs")
    score = _stub_score(candidate, payoff_q_correction=baseline)

    evaluations = _global_candidate_evaluations(
        (candidate,), rejections={}, scores=(score,), winner_id=candidate.candidate_id,
    )
    assert len(evaluations) == 1
    ev = evaluations[0]
    assert isinstance(ev, GlobalSingleOrderCandidateEvaluation)
    assert ev.status == "SELECTED"
    assert ev.q_raw == pytest.approx(baseline.raw_q)
    assert ev.q_served == pytest.approx(baseline.corrected_q)
    assert ev.probability_semantics_revision == baseline.raw_probability_revision
    # witness identity is the CANDIDATE's own, never the correction's copy —
    # it must be present on every candidate regardless of whether/what
    # correction was sealed.
    assert ev.probability_witness_identity == "witness-candidate"
    assert ev.probability_witness_identity != baseline.probability_witness_identity


def test_evaluations_bare_rejection_has_witness_identity_but_null_q():
    """A candidate rejected before scoring (score is None) still carries its
    own probability_witness_identity (always present on the candidate), but
    q_raw/q_served/revision are unavailable -- never guessed."""

    candidate = _minimal_candidate("cand-rej", probability_witness_identity="witness-rejected")
    evaluations = _global_candidate_evaluations(
        (candidate,),
        rejections={"cand-rej": "CAPITAL_CONSTRAINT_UNAVAILABLE"},
        default_rejection="CAPITAL_CONSTRAINT_UNAVAILABLE",
    )
    assert len(evaluations) == 1
    ev = evaluations[0]
    assert ev.status == "REJECTED"
    assert ev.q_raw is None
    assert ev.q_served is None
    assert ev.probability_semantics_revision is None
    assert ev.probability_witness_identity == "witness-rejected"


def test_evaluations_scored_candidate_without_correction_has_null_q_but_witness_identity():
    """Exact/settlement-locked payoffs never seal a correction
    (payoff_q_correction stays None); the evaluation must not fabricate q."""

    candidate = _minimal_candidate("cand-exact", probability_witness_identity="witness-exact")
    score = _stub_score(candidate, payoff_q_correction=None)
    evaluations = _global_candidate_evaluations(
        (candidate,), rejections={}, scores=(score,), winner_id=candidate.candidate_id,
    )
    ev = evaluations[0]
    assert ev.q_raw is None
    assert ev.q_served is None
    assert ev.probability_semantics_revision is None
    assert ev.probability_witness_identity == "witness-exact"


# ---------------------------------------------------------------------------
# 3a. _persist_tier0_candidate_set — DB write faithfully mirrors the
#     evaluation's own q-provenance fields (equal, not recomputed).
# ---------------------------------------------------------------------------

def _trade_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_persist_writes_q_provenance_equal_to_evaluation_values():
    conn = _trade_conn()
    decision_at = _DECISION_AT
    evaluations = (
        GlobalSingleOrderCandidateEvaluation(
            candidate_id="cand-a", family_key="family-a", bin_id="bin-a",
            condition_id="condition-a", side="YES", token_id="token-a",
            action="BUY", status="SELECTED",
            q_raw=0.4123456789, q_served=0.39, probability_semantics_revision="day0_v3",
            probability_witness_identity="witness-a",
            **_coherent_buy_score_fields(),
        ),
        GlobalSingleOrderCandidateEvaluation(
            candidate_id="cand-b", family_key="family-a", bin_id="bin-b",
            condition_id="condition-b", side="NO", token_id="token-b",
            action="BUY", status="REJECTED", rejection_reason="SOME_REASON",
            # q fields left at their None default -> the NULL path.
        ),
    )
    _persist_tier0_candidate_set(
        conn,
        evaluations=evaluations,
        selection_epoch_identity="epoch-1",
        decision_at_utc=decision_at,
        family_context_by_key={"family-a": {"city": "Denver", "target_date": "2026-09-20"}},
    )
    rows = {
        row["candidate_id"]: row
        for row in conn.execute(
            "SELECT * FROM tier0_candidate_set_provenance ORDER BY candidate_id"
        ).fetchall()
    }
    assert len(rows) == 2

    a = rows["cand-a"]
    assert a["q_raw"] == pytest.approx(0.4123456789)
    assert a["q_served"] == pytest.approx(0.39)
    assert a["probability_semantics_revision"] == "day0_v3"
    assert a["probability_witness_identity"] == "witness-a"

    b = rows["cand-b"]
    assert b["q_raw"] is None
    assert b["q_served"] is None
    assert b["probability_semantics_revision"] is None
    assert b["probability_witness_identity"] is None


# ---------------------------------------------------------------------------
# 3b. ensure_table — forward-only migration of an old-shape live table.
# ---------------------------------------------------------------------------

def test_ensure_table_creates_all_columns_on_a_fresh_db():
    conn = _trade_conn()
    ensure_table(conn)
    columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_xinfo(tier0_candidate_set_provenance)"
        ).fetchall()
    }
    assert _new_column_names() <= columns


def test_ensure_table_upgrades_old_shape_table_without_data_loss():
    conn = _trade_conn()
    conn.execute(_OLD_SHAPE_CREATE_TABLE_SQL)
    conn.execute(
        """
        INSERT INTO tier0_candidate_set_provenance (
            selection_epoch_identity, decision_at_utc, city_date_group_id,
            city, target_date, candidate_id, family_key, bin_id, side,
            token_id, action, p0, p0_source, lead_bucket, eligible,
            rejection_reason, selected, market_key, settled_y, created_at
        ) VALUES (
            'epoch-old', '2026-09-01T00:00:00+00:00', 'epoch-old:Denver:2026-09-01',
            'Denver', '2026-09-01', 'cand-old', 'family-old', 'bin-old', 'YES',
            'token-old', 'BUY', 0.5, 'snap', 'L00_24', 1,
            NULL, 1, 'condition-old', NULL, '2026-09-01T00:00:01+00:00'
        )
        """
    )
    conn.commit()
    pre_columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_xinfo(tier0_candidate_set_provenance)"
        ).fetchall()
    }
    assert not (_new_column_names() & pre_columns)

    ensure_table(conn)

    post_columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_xinfo(tier0_candidate_set_provenance)"
        ).fetchall()
    }
    assert _new_column_names() <= post_columns

    row = conn.execute(
        "SELECT * FROM tier0_candidate_set_provenance WHERE candidate_id = 'cand-old'"
    ).fetchone()
    assert row is not None
    # Pre-existing data intact.
    assert row["selection_epoch_identity"] == "epoch-old"
    assert row["city"] == "Denver"
    assert row["p0"] == pytest.approx(0.5)
    assert row["selected"] == 1
    # New columns default to NULL on a pre-existing row -- no fabricated backfill.
    assert row["q_raw"] is None
    assert row["q_served"] is None
    assert row["probability_semantics_revision"] is None
    assert row["probability_witness_identity"] is None

    # Idempotent: a second ensure_table on an already-upgraded table does not
    # error (ALTER ADD COLUMN would raise "duplicate column name" if the
    # presence check were broken) and the row count / data is unchanged.
    ensure_table(conn)
    rows = conn.execute(
        "SELECT COUNT(*) FROM tier0_candidate_set_provenance"
    ).fetchone()[0]
    assert rows == 1
    unchanged = conn.execute(
        "SELECT city, p0, selected FROM tier0_candidate_set_provenance "
        "WHERE candidate_id = 'cand-old'"
    ).fetchone()
    assert tuple(unchanged) == ("Denver", pytest.approx(0.5), 1)


def test_ensure_table_is_idempotent_on_a_fresh_table():
    conn = _trade_conn()
    ensure_table(conn)
    ensure_table(conn)  # must not raise (duplicate ALTER / duplicate CREATE)
    columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_xinfo(tier0_candidate_set_provenance)"
        ).fetchall()
    }
    assert _new_column_names() <= columns
