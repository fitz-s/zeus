# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: P0 2026-09-29 (SELECTED cuts with no ENTRY): every SELECTED
#   tier0_auction_cut row records what its winner became, so SELECTED with no
#   venue order is distinguishable from SELECTED-and-submitted.
"""A SELECTED cut persists its actuation outcome and reason.

Live case (2026-09-29 21:43-21:57Z): winners passed winner_preflight, the
executor refused them pre-venue (deployment_freshness_mismatch), and the cut
row stayed a bare SELECTED. The reason lived only in a WARNING in
zeus-live.err. These tests drive the real ``process_current_global_batch``
over a real SQLite trade DB and read the flushed ``tier0_auction_cut`` row.
"""

from __future__ import annotations

import ast
import datetime as _dt
import inspect
import json
import logging
import sqlite3
from types import SimpleNamespace

import pytest

import src.engine.global_batch_runtime as gbr
from src.engine import tier0_auction_corpus as corpus
from src.events.reactor import EventSubmissionReceipt
from src.state.schema.tier0_auction_corpus_schema import ensure_tables
from tests.integration.test_w3_solve_seam_g3 import (
    _WealthNamespace,
    _global_scope_event,
    _global_test_buy_candidate,
    _global_test_candidate_book,
    current_global_auction_scope_from_events,
)

AT = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
FRESHNESS_REJECT = (
    "EXECUTOR_PRE_VENUE_REJECTED:[gate_runtime] BLOCKED cap='live_venue_submit': "
    "condition 'deployment_freshness_mismatch' is active"
)


@pytest.fixture(autouse=True)
def _empty_queue():
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()
    yield
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()


def _run(tmp_path, monkeypatch, *, preflights, actuate):
    """One live-shaped cut: a single BUY winner, the preflighted lane."""

    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=AT)
    family_key = scope.family_keys[0]
    witness = SimpleNamespace(
        family_key=family_key,
        captured_at_utc=AT,
        posterior_identity_hash="run-a",
        witness_identity="q-run-a",
    )
    candidate = _global_test_buy_candidate(
        family_key=family_key,
        probability_witness_identity="q-run-a",
        book_identity="book-1",
        price="0.18",
        captured_at=AT,
        candidate_id="candidate-live",
    )
    book = _global_test_candidate_book(candidate, epoch_captured_at=AT)
    actuation = SimpleNamespace(
        actuation_identity="actuation-a",
        wealth_witness_identity="wealth-1",
        wealth_economic_identity="wealth-economics",
    )
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=candidate, no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=actuation,
    )
    venue = [0]
    row_ids = iter(range(100, 200))

    def store_receipt(conn, **kwargs):
        # The real writer queues the cut for this row; keep that contract
        # without building the full decision_log payload.
        row_id = next(row_ids)
        cut = gbr._tier0_cut_corpus(
            selection_epoch_identity=kwargs["selection_epoch_identity"],
            reason=None,
            decision_at_utc=kwargs["decision_at_utc"],
            scope_family_count=1,
            probability_witnesses={},
            ineligible_by_family={},
            excluded_by_family={},
            evaluations=(),
            winner_candidate_id=candidate.candidate_id,
            book_epoch=None,
            family_context_by_key={},
            fractional_kelly_multiplier=kwargs["fractional_kelly_multiplier"],
            buy_candidates_enabled=True,
        )
        corpus.queue_cut(
            gbr._decision_log_connection_key(conn),
            corpus.PendingCut(lambda: (cut, ()), row_id),
        )
        return row_id

    monkeypatch.setattr(gbr, "scan_current_global_auction_scope", lambda **_: scope)
    monkeypatch.setattr(
        gbr,
        "current_portfolio_wealth_witness",
        lambda *_, **__: _WealthNamespace(
            spendable_cash_usd="10",
            witness_identity="wealth-1",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(gbr, "select_prepared_global_auction", lambda *_, **__: selected)
    monkeypatch.setattr(gbr, "_store_global_auction_receipt", store_receipt)
    monkeypatch.setattr(gbr, "_bind_stored_global_auction_receipt", lambda _c, **kw: kw["selected"])
    monkeypatch.setattr(gbr, "_store_global_preflight_receipt", lambda *_, **__: 1)
    monkeypatch.setattr(
        gbr, "replace", lambda value, **changes: SimpleNamespace(**(vars(value) | changes))
    )
    preflight_iter = iter(preflights)

    def actuate_preflighted(winner, _actuation, _at, _token, _authority):
        receipt = actuate(winner)
        venue[0] += int(receipt.submitted)
        return receipt

    trade = sqlite3.connect(tmp_path / "trade.db")
    ensure_tables(trade)
    trade.commit()
    result = gbr.process_current_global_batch(
        (event,),
        decision_time=AT,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=trade,
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=SimpleNamespace(probability_witness=witness),
        ),
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=lambda *_: next(preflight_iter),
        actuate_preflighted_winner=gbr.GlobalOneShotActuator(actuate_preflighted),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue[0],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: AT,
        current_book_epoch_provider=lambda probabilities, _at: (probabilities, book),
    )
    trade.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in trade.execute(
            "SELECT status, winner_candidate_id, decision_log_id, cut_id, "
            "actuation_outcome, actuation_reason FROM tier0_auction_cut "
            "ORDER BY cut_seq"
        )
    ]
    return result, event, rows


def _stable():
    return gbr.GlobalWinnerPreflight(status="STABLE", binding_token="binding")


def test_selected_winner_refused_pre_venue_persists_its_reason(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=gbr._LOG.name)

    def refused(winner):
        return EventSubmissionReceipt(
            False,
            winner.event_id,
            winner.causal_snapshot_id,
            reason=FRESHNESS_REJECT,
            proof_accepted=True,
            side_effect_status="PRE_SUBMIT_ERROR",
        )

    result, event, rows = _run(
        tmp_path, monkeypatch, preflights=(_stable(),), actuate=refused
    )

    assert result.venue_submit_count == 0
    assert result.receipts[event.event_id].reason == FRESHNESS_REJECT
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "SELECTED"
    assert row["actuation_outcome"] == "NO_VENUE_ORDER"
    assert row["actuation_reason"] == f"PRE_SUBMIT_ERROR:{FRESHNESS_REJECT}"
    warning = next(
        r.getMessage() for r in caplog.records
        if "produced no venue order" in r.getMessage()
    )
    assert f"cut_id={row['cut_id']}" in warning


def test_selected_and_submitted_is_distinguishable_from_no_order(
    tmp_path, monkeypatch
):
    def submitted(winner):
        return EventSubmissionReceipt(
            True,
            winner.event_id,
            winner.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result, _event, rows = _run(
        tmp_path, monkeypatch, preflights=(_stable(),), actuate=submitted
    )

    assert result.venue_submit_count == 1
    assert [(r["status"], r["actuation_outcome"], r["actuation_reason"]) for r in rows] == [
        ("SELECTED", "SUBMITTED", None)
    ]


def test_preflight_rejected_winner_persists_status_and_reason(tmp_path, monkeypatch):
    rejected = gbr.GlobalWinnerPreflight(
        status="BATCH_BLOCKED", reason="GLOBAL_BUY_JIT_MAKER_WITNESS_SUPERSEDED"
    )

    result, event, rows = _run(
        tmp_path,
        monkeypatch,
        preflights=(rejected,),
        actuate=lambda _w: pytest.fail("a rejected winner never actuates"),
    )

    assert result.venue_submit_count == 0
    assert [(r["status"], r["actuation_outcome"], r["actuation_reason"]) for r in rows] == [
        (
            "SELECTED",
            "PREFLIGHT_REJECTED",
            "BATCH_BLOCKED:GLOBAL_BUY_JIT_MAKER_WITNESS_SUPERSEDED",
        )
    ]


def test_every_actuation_path_exit_settles_the_selected_cut():
    """Structural antibody: the outcome sites exist and the finally backstops."""

    source = inspect.getsource(gbr.process_current_global_batch)
    tree = ast.parse(source)
    settles = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "settle_selected_cut"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]
    assert set(settles) >= {
        "CUT_REJECTED",
        "RESELECTED",
        "PREFLIGHT_REJECTED",
        "SUBMITTED",
        "NO_VENUE_ORDER",
        "POST_SUBMIT_UNKNOWN",
        "UNRECORDED_EXIT",
    }
    reject = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "reject"
    )
    assert "settle_selected_cut" in ast.unparse(reject)
    outer = next(node for node in tree.body[0].body if isinstance(node, ast.Try))
    assert "settle_selected_cut('UNRECORDED_EXIT'" in ast.unparse(outer.finalbody)
    # Every direct GlobalBatchSubmitResult return outside reject() is preceded,
    # in the same block, by a settle call.
    for node in ast.walk(outer):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for index, stmt in enumerate(body):
            if (
                isinstance(stmt, ast.Return)
                and isinstance(stmt.value, ast.Call)
                and getattr(stmt.value.func, "id", None) == "GlobalBatchSubmitResult"
            ):
                preceding = ast.unparse(ast.Module(body=body[:index], type_ignores=[]))
                assert "settle_selected_cut" in preceding, ast.unparse(stmt)[:120]


def test_restart_schema_materialization_adds_the_actuation_columns(tmp_path):
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "deploy_live_selected_cut_outcome", root / "scripts" / "deploy_live.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    conn = sqlite3.connect(tmp_path / "trade.db", isolation_level=None)
    module._ensure_restart_trade_schemas(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(tier0_auction_cut)")}
    assert {"actuation_outcome", "actuation_reason"} <= columns
