# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: STRUCTURAL_FIX_PLAN_2026-06-03 §P0.4 (N2 — attribution driver
#   repoint). The live EDLI path writes edli_no_submit_receipts (60k+ rows);
#   decision_events has 0 rows. The old run_attribution joined decision_events
#   and silently attributed 0. Repoint the driver + guard nonempty input.
"""Relationship tests for the attribution driver repoint.

Cross-module property: the live reactor's OUTPUT table
(edli_no_submit_receipts) must be the SAME table the attribution job READS.
A producer/consumer table mismatch makes the learning loop silently dead.
These tests prove (a) input is non-empty when receipts+settlements exist, and
(b) the guard raises rather than silently succeeding on an empty input.
Written RED-first.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import contextlib

from src.state.db import init_schema, init_schema_forecasts
import src.cron.settlement_attribution as sa
from src.cron.settlement_attribution import (
    AttributionInputEmptyError,
    load_attribution_input_rows,
    run_receipt_attribution,
)


def _attach(world_conn: sqlite3.Connection, fcst_path: str) -> None:
    world_conn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))


@pytest.fixture()
def world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _insert_receipt(conn: sqlite3.Connection, *, city, target_date, metric,
                    direction, bin_label, price, size, receipt_id) -> None:
    rj = {
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "direction": direction,
        "bin_label": bin_label,
    }
    conn.execute(
        """
        INSERT INTO edli_no_submit_receipts
            (receipt_id, event_id, decision_time, direction, c_fee_adjusted,
             kelly_size_usd, token_id, side_effect_status, fdr_hypothesis_count,
             projection_hash, receipt_json, receipt_hash, created_at, schema_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'NO_SUBMIT', 1, ?, ?, ?, ?, ?)
        """,
        (receipt_id, f"evt_{receipt_id}", "2026-06-01T12:00:00+00:00", direction,
         price, size, f"tok_{receipt_id}", f"ph_{receipt_id}",
         json.dumps(rj), f"rh_{receipt_id}", "2026-06-01T12:00:00+00:00", 29),
    )


def _insert_settlement(conn: sqlite3.Connection, *, city, target_date, metric,
                       value, unit, settlement_id) -> None:
    # settlement_id is an INTEGER autoincrement PK — omit it. provenance_json
    # and recorded_at are NOT NULL.
    conn.execute(
        """
        INSERT INTO settlement_outcomes
            (city, target_date, temperature_metric,
             settlement_value, settlement_unit, authority,
             provenance_json, recorded_at)
        VALUES (?, ?, ?, ?, ?, 'VERIFIED', '{}', '2026-06-02T12:00:00+00:00')
        """,
        (city, target_date, metric, value, unit),
    )


# ---------------------------------------------------------------------------
# The repoint: input non-empty when receipts + settlements exist
# ---------------------------------------------------------------------------
def test_attribution_nonempty_when_receipts_exist(world_conn, tmp_path):
    fcst_path = str(tmp_path / "fcst.db")
    fconn = sqlite3.connect(fcst_path)
    init_schema_forecasts(fconn)
    _insert_settlement(fconn, city="Tokyo", target_date="2026-06-01",
                       metric="high", value=17.0, unit="C", settlement_id="s1")
    fconn.commit()
    fconn.close()

    _insert_receipt(world_conn, city="Tokyo", target_date="2026-06-01",
                    metric="high", direction="buy_yes", bin_label="17°C",
                    price=0.40, size=5.0, receipt_id="r1")
    world_conn.commit()
    _attach(world_conn, fcst_path)

    rows = load_attribution_input_rows(world_conn)
    assert len(rows) >= 1, "attribution input is empty despite a joinable receipt"
    # The graded row carries a Direction-Law win flag: buy_yes on 17°C with a
    # 17.0°C settlement WINS.
    matched = [r for r in rows if r["city"] == "Tokyo"]
    assert matched and matched[0]["won"] is True


# ---------------------------------------------------------------------------
# The guard: empty input is a FAILURE, not a silent success
# ---------------------------------------------------------------------------
def test_attribution_guard_raises_on_empty_input(world_conn, tmp_path):
    """No receipts at all → run_receipt_attribution must RAISE, never quietly
    report attributed=0 (which is how the dead decision_events join hid)."""
    fcst_path = str(tmp_path / "fcst.db")
    fconn = sqlite3.connect(fcst_path)
    init_schema_forecasts(fconn)
    fconn.commit()
    fconn.close()
    _attach(world_conn, fcst_path)

    with pytest.raises(AttributionInputEmptyError):
        run_receipt_attribution(world_conn=world_conn, now_utc=datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# H1 — the CLI (the ONLY live entry point) must drive the RECEIPT path.
# Before this fix, _cli() called run_attribution over decision_events (0 live
# rows) and silently reported attributed=0. These tests prove the wiring:
# (a) _cli reads receipts and reports a non-zero input_rows count;
# (b) _cli with no receipts RAISES the empty antibody (no silent 0).
# The dead decision_events driver would pass NEITHER: it returns an
# 'attributed'-keyed dict and never raises on empty receipts.
# ---------------------------------------------------------------------------
def _populated_conn(tmp_path) -> sqlite3.Connection:
    """A WORLD conn with one joinable receipt + an ATTACHed settlement DB."""
    fcst_path = str(tmp_path / "fcst.db")
    fconn = sqlite3.connect(fcst_path)
    init_schema_forecasts(fconn)
    _insert_settlement(fconn, city="Tokyo", target_date="2026-06-01",
                       metric="high", value=17.0, unit="C", settlement_id="s1")
    fconn.commit()
    fconn.close()

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _insert_receipt(conn, city="Tokyo", target_date="2026-06-01",
                    metric="high", direction="buy_yes", bin_label="17°C",
                    price=0.40, size=5.0, receipt_id="r1")
    conn.commit()
    _attach(conn, fcst_path)
    return conn


def test_cli_drives_receipt_path_and_reports_nonzero(tmp_path, monkeypatch, capsys):
    conn = _populated_conn(tmp_path)

    @contextlib.contextmanager
    def _fake_open(write_class="bulk"):
        yield conn

    monkeypatch.setattr(sa, "open_world_with_forecasts", _fake_open)

    # A bare invocation (no flags) is the cron form.
    sa._cli(argv=[])
    out = capsys.readouterr().out
    # The receipt path prints "Receipt-attribution stats: ..." with input_rows.
    assert "Receipt-attribution stats" in out
    assert "'input_rows': 1" in out, (
        f"CLI did not drive the receipt path (input_rows!=1). Output: {out!r}"
    )
    conn.close()


def test_cli_raises_empty_antibody_when_no_receipts(tmp_path, monkeypatch):
    """An empty join through the CLI must RAISE, not silently print 0 —
    the dead decision_events driver did the latter and that is the bug."""
    fcst_path = str(tmp_path / "fcst.db")
    fconn = sqlite3.connect(fcst_path)
    init_schema_forecasts(fconn)
    fconn.commit()
    fconn.close()
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _attach(conn, fcst_path)

    @contextlib.contextmanager
    def _fake_open(write_class="bulk"):
        yield conn

    monkeypatch.setattr(sa, "open_world_with_forecasts", _fake_open)

    with pytest.raises(AttributionInputEmptyError):
        sa._cli(argv=[])
    conn.close()
