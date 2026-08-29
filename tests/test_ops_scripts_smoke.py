# Lifecycle: created=2026-06-12; last_reviewed=2026-08-29; last_reused=2026-08-29
# Purpose: light smoke coverage for the three new ops scripts (zeus_status,
#   deploy_live, generate_schema_cheatsheet).
# Reuse: asserts the FAIL-SOFT contract (a locked/empty/missing DB degrades one
#   section to ERR, the rest still render) and that each script runs read-only
#   against temp DBs. No live DB is touched.
# Last reused/audited: 2026-08-29
# Authority basis: operator big-direction 2026-06-12 ("大方向现在也只是添加几个文件现在做")
"""Smoke tests for scripts/zeus_status.py, deploy_live.py, generate_schema_cheatsheet.py."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import plistlib
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"


def test_log_rotation_launchd_plist_contract(tmp_path):
    """The independent launchd backstop stays portable and non-recursive."""
    template = _REPO / "deploy" / "launchd" / "com.zeus.log-rotation.plist"
    raw = template.read_bytes()
    plist = plistlib.loads(raw)
    env = plist["EnvironmentVariables"]

    assert plist["Label"] == "com.zeus.log-rotation"
    assert plist["ProgramArguments"] == [
        "/bin/bash",
        "ZEUS_REPO_PLACEHOLDER/scripts/ops/rotate_zeus_logs.sh",
        "--apply",
    ]
    assert plist["StartInterval"] == 900
    assert plist["RunAtLoad"] is True
    assert plist["ThrottleInterval"] >= 60
    assert env["ZEUS_LOG_ROTATE_MB"] == "50"
    assert env["ZEUS_LOG_ROTATE_KEEP"] == "5"
    assert env["ZEUS_PRIMARY_ROOT"] == "ZEUS_REPO_PLACEHOLDER"
    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert plist["WorkingDirectory"] == "ZEUS_REPO_PLACEHOLDER"

    for key in ("StandardOutPath", "StandardErrorPath"):
        output_path = plist[key]
        assert output_path == "/dev/null"
        assert "ZEUS_REPO_PLACEHOLDER/logs/" not in output_path
    assert not any(
        secret_marker in raw
        for secret_marker in (
            b"POLYMARKET_API_KEY",
            b"POLYMARKET_API_SECRET",
            b"POLYMARKET_API_PASSPHRASE",
            b"secrets.env",
        )
    )

    install = _load("install_launchd_plist_log_rotation", "install_launchd_plist.py")
    repo_root = tmp_path / "portable repo"
    home = tmp_path / "portable home"
    rendered = install.render(template, repo_root, home)
    assert b"PLACEHOLDER" not in rendered
    rendered_plist = plistlib.loads(rendered)
    assert rendered_plist["WorkingDirectory"] == str(repo_root)
    assert rendered_plist["EnvironmentVariables"]["ZEUS_PRIMARY_ROOT"] == str(repo_root)
    assert rendered_plist["ProgramArguments"][1] == str(
        repo_root / "scripts" / "ops" / "rotate_zeus_logs.sh"
    )


def _load(modname: str, filename: str):
    """Import a scripts/*.py module by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(modname, _SCRIPTS / filename)
    assert spec and spec.loader, f"cannot load {filename}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# zeus_status
# --------------------------------------------------------------------------
def _empty_db(path: Path) -> None:
    """Create a syntactically-valid but schema-less SQLite file."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE _placeholder (x INTEGER)")
    conn.commit()
    conn.close()


def test_zeus_status_failsoft_on_empty_dbs(tmp_path, capsys):
    """Empty DBs (no expected tables) -> sections degrade to ERR, no crash, JSON valid."""
    zs = _load("zeus_status_smoke", "zeus_status.py")
    # Point all three DB paths at empty temp DBs.
    w = tmp_path / "zeus-world.db"
    t = tmp_path / "zeus_trades.db"
    f = tmp_path / "zeus-forecasts.db"
    for p in (w, t, f):
        _empty_db(p)
    zs.WORLD_DB = str(w)
    zs.TRADES_DB = str(t)
    zs.FORECASTS_DB = str(f)

    rc = zs.main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)  # must be valid JSON
    # All sections present even though the queries failed.
    for sect in ("daemons", "events", "blocks", "surface", "positions", "orders", "price_holes"):
        assert sect in data
    # Sections that query missing tables must carry an error key (fail-soft),
    # not have raised.
    assert "error" in data["events"]
    assert "error" in data["blocks"]
    assert "error" in data["orders"]


@pytest.mark.parametrize("phase", ("settled", "economically_closed"))
def test_restart_preflight_catches_terminal_fak_ctf_reservation_debt(
    monkeypatch,
    tmp_path,
    phase,
):
    """The blocker reports one exact terminal/no-live-remainder debt sample."""
    preflight = _load(
        f"preflight_terminal_fak_debt_{phase}",
        "check_live_restart_preflight.py",
    )
    db = tmp_path / f"terminal-fak-debt-{phase}.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY, position_id TEXT, token_id TEXT,
            side TEXT, intent_kind TEXT, state TEXT, venue_order_id TEXT,
            size TEXT, envelope_id TEXT
        );
        CREATE TABLE venue_command_events (
            command_id TEXT, event_type TEXT, sequence_no INTEGER,
            payload_json TEXT
        );
        CREATE TABLE venue_submission_envelopes (
            envelope_id TEXT PRIMARY KEY, order_type TEXT
        );
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id TEXT, venue_order_id TEXT, state TEXT,
            matched_size TEXT, remaining_size TEXT
        );
        CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT, command_id TEXT, venue_order_id TEXT, state TEXT,
            filled_size TEXT, fill_price TEXT, tx_hash TEXT,
            observed_at TEXT, venue_timestamp TEXT, local_sequence INTEGER,
            raw_payload_json TEXT
        );
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY, phase TEXT, direction TEXT,
            token_id TEXT, no_token_id TEXT
        );
        CREATE TABLE collateral_reservations (
            command_id TEXT, reservation_type TEXT, token_id TEXT,
            amount INTEGER, released_at TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands(
            command_id, position_id, token_id, side, intent_kind, state,
            venue_order_id, size, envelope_id
        ) VALUES ('cmd-terminal-debt', 'pos-terminal-debt', 'tok-terminal-debt',
                  'SELL', 'EXIT', 'REVIEW_REQUIRED', 'ord-terminal-debt',
                  '6', 'env-terminal-debt')
        """
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES ('env-terminal-debt', 'FAK')"
    )
    conn.execute(
        "INSERT INTO position_current VALUES (?, ?, 'buy_yes', ?, ?)" ,
        ("pos-terminal-debt", phase, "tok-terminal-debt", "tok-terminal-debt-no"),
    )
    conn.execute(
        """INSERT INTO collateral_reservations VALUES (
            'cmd-terminal-debt', 'CTF_SELL', 'tok-terminal-debt', 6000000, NULL
        )"""
    )
    review_payload = {
        "reason": "partial_remainder_point_order_filled_without_full_trade_fact",
        "point_order": {
            "orderID": "ord-terminal-debt",
            "status": "MATCHED",
            "order_type": "FAK",
            "side": "SELL",
            "asset_id": "tok-terminal-debt",
            "original_size": "6",
            "size_matched": "2",
            "remaining_size": "0",
        },
    }
    conn.execute(
        "INSERT INTO venue_command_events VALUES (?, 'REVIEW_REQUIRED', 1, ?)",
        ("cmd-terminal-debt", json.dumps(review_payload)),
    )
    conn.execute(
        """INSERT INTO venue_order_facts(
            command_id, venue_order_id, state, matched_size, remaining_size
        ) VALUES (
            'cmd-terminal-debt', 'ord-terminal-debt', 'PARTIALLY_MATCHED', '2', '4'
        )"""
    )
    # A later revision of the same trade is one economic fill, not a second
    # fill.  The preflight must use the canonical reducer shared with recovery.
    conn.execute(
        """INSERT INTO venue_trade_facts(
            trade_id, command_id, venue_order_id, state, filled_size,
            fill_price, tx_hash, observed_at, local_sequence, raw_payload_json
        ) VALUES (
            'trade-terminal-debt', 'cmd-terminal-debt', 'ord-terminal-debt',
            'CONFIRMED', '2', '0.46', 'tx-terminal-debt',
            '2026-08-02T00:01:00+00:00', 2, '{}'
        )"""
    )
    conn.execute(
        """INSERT INTO venue_trade_facts(
            trade_id, command_id, venue_order_id, state, filled_size,
            fill_price, tx_hash, observed_at, local_sequence, raw_payload_json
        ) VALUES (
            'trade-terminal-debt', 'cmd-terminal-debt', 'ord-terminal-debt',
            'CONFIRMED', '2', '0.46', 'tx-terminal-debt',
            '2026-08-02T00:00:00+00:00', 1, '{}'
        )"""
    )
    conn.commit()

    @contextlib.contextmanager
    def _temp_trade_db():
        yield conn

    monkeypatch.setattr(preflight, "_connect_live_ro", _temp_trade_db)
    result = preflight._terminal_fak_collateral_reservation_debt_check()

    assert result.ok is False
    assert result.restart_blocking is True
    assert result.name == "terminal_fak_collateral_reservation_debt"
    assert result.evidence["debt_samples"] == [
        {
            "command_id": "cmd-terminal-debt",
            "position_id": "pos-terminal-debt",
            "venue_order_id": "ord-terminal-debt",
            "token_id": "tok-terminal-debt",
            "position_phase": phase,
            "requested_size": "6",
            "matched_size": "2",
            "active_ctf_reservation_amount": 6000000,
            "resolution": "command_recovery.terminal_fak_partial_exit_review",
        }
    ]

    conn.execute(
        "UPDATE collateral_reservations SET amount = 1000000 "
        "WHERE command_id = 'cmd-terminal-debt'"
    )
    amount_mismatch = preflight._terminal_fak_collateral_reservation_debt_check()
    assert amount_mismatch.ok is False
    assert amount_mismatch.evidence["debt_count"] == 0
    assert "reservation_exact" in amount_mismatch.evidence["unknown_samples"][0][
        "reason"
    ]
    conn.execute(
        "UPDATE collateral_reservations SET amount = 6000000 "
        "WHERE command_id = 'cmd-terminal-debt'"
    )

    review_payload["point_order"]["remaining_size"] = "4"
    conn.execute(
        "UPDATE venue_command_events SET payload_json = ?",
        (json.dumps(review_payload),),
    )
    nonzero = preflight._terminal_fak_collateral_reservation_debt_check()
    assert nonzero.ok is False
    assert nonzero.evidence["debt_count"] == 0
    assert nonzero.evidence["unknown_samples"][0]["reason"] == (
        "point_order_explicit_remainder_nonzero"
    )

    review_payload["point_order"]["remaining_size"] = "invalid"
    conn.execute(
        "UPDATE venue_command_events SET payload_json = ?",
        (json.dumps(review_payload),),
    )
    invalid_remainder = preflight._terminal_fak_collateral_reservation_debt_check()
    assert invalid_remainder.ok is False
    assert invalid_remainder.evidence["unknown_samples"][0]["reason"] == (
        "point_order_explicit_remainder_invalid"
    )

    conn.execute("UPDATE venue_command_events SET payload_json = '{bad-json'")
    malformed = preflight._terminal_fak_collateral_reservation_debt_check()
    assert malformed.ok is False
    assert malformed.evidence["unknown_samples"][0]["reason"] == (
        "review_payload_invalid_json"
    )

    review_payload["point_order"]["remaining_size"] = "0"
    conn.execute(
        "UPDATE venue_command_events SET payload_json = ?",
        (json.dumps(review_payload),),
    )
    conn.execute(
        "DELETE FROM position_current WHERE position_id = 'pos-terminal-debt'"
    )
    missing_position = preflight._terminal_fak_collateral_reservation_debt_check()
    assert missing_position.ok is False
    assert missing_position.evidence["unknown_samples"][0]["reason"] == (
        "position_current_missing"
    )
    conn.execute(
        "INSERT INTO position_current VALUES (?, ?, 'buy_yes', ?, ?)",
        (
            "pos-terminal-debt",
            phase,
            "tok-terminal-debt",
            "tok-terminal-debt-no",
        ),
    )
    conn.execute("DELETE FROM venue_command_events")
    missing_review = preflight._terminal_fak_collateral_reservation_debt_check()
    assert missing_review.ok is False
    assert missing_review.evidence["unknown_samples"][0]["reason"] == (
        "latest_review_reason_or_shape_mismatch"
    )
    conn.close()


def test_restart_preflight_terminal_fak_debt_missing_surface_fails_closed(
    monkeypatch,
):
    preflight = _load(
        "preflight_terminal_fak_debt_missing_surface",
        "check_live_restart_preflight.py",
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    @contextlib.contextmanager
    def _missing_surface():
        yield conn

    monkeypatch.setattr(preflight, "_connect_live_ro", _missing_surface)
    result = preflight._terminal_fak_collateral_reservation_debt_check()

    assert result.ok is False
    assert result.restart_blocking is True
    assert "venue_commands" in result.evidence["missing_tables"]
    conn.close()


def test_zeus_status_failsoft_on_missing_db_file(tmp_path, capsys):
    """A nonexistent DB path -> ERR for that section, others still render."""
    zs = _load("zeus_status_smoke2", "zeus_status.py")
    zs.WORLD_DB = str(tmp_path / "does-not-exist-world.db")
    zs.TRADES_DB = str(tmp_path / "does-not-exist-trades.db")
    zs.FORECASTS_DB = str(tmp_path / "does-not-exist-forecasts.db")
    rc = zs.main([])  # text mode
    assert rc == 0
    out = capsys.readouterr().out
    assert "ZEUS FUNNEL" in out
    assert "ERR" in out  # at least one section degraded


def test_zeus_status_classifier():
    """The substrate-vs-economic block classifier keys on the REASON, not the stage."""
    zs = _load("zeus_status_smoke3", "zeus_status.py")
    # Substrate causes (missing input / blocked snapshot / shadow scope) = transient,
    # even under an economic-sounding stage name.
    assert zs.classify_block("TRADE_SCORE", "LIVE_INFERENCE_INPUTS_MISSING:q_ucb") == "transient"
    assert zs.classify_block("EXECUTABLE_QUOTE", "EXECUTABLE_SNAPSHOT_BLOCKED") == "transient"
    # Honest no-edge = economic.
    assert zs.classify_block("TRADE_SCORE", "TRADE_SCORE_NON_POSITIVE") == "economic"


def test_zeus_status_positions_include_day0_and_pending_exit(tmp_path):
    """Operator funnel must not hide non-active open lifecycle phases."""
    zs = _load("zeus_status_positions_day0", "zeus_status.py")
    tdb = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(tdb))
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            shares REAL,
            entry_price REAL,
            last_monitor_prob REAL,
            last_monitor_market_price REAL,
            last_monitor_prob_is_fresh INTEGER,
            last_monitor_market_price_is_fresh INTEGER,
            chain_state TEXT,
            updated_at TEXT,
            settled_at TEXT,
            exit_reason TEXT
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, bin_label, direction,
            shares, entry_price, last_monitor_prob, last_monitor_market_price,
            last_monitor_prob_is_fresh, last_monitor_market_price_is_fresh,
            chain_state, updated_at, settled_at, exit_reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                "pos-active",
                "active",
                "Tokyo",
                "2026-07-04",
                "32C",
                "buy_yes",
                10.0,
                0.4,
                0.6,
                0.5,
                1,
                1,
                "synced",
                now,
                None,
                None,
            ),
            (
                "pos-day0",
                "day0_window",
                "Manila",
                "2026-07-04",
                "33C",
                "buy_yes",
                11.0,
                0.3,
                0.0,
                None,
                1,
                0,
                "synced",
                now,
                None,
                None,
            ),
            (
                "pos-pending-exit",
                "pending_exit",
                "Paris",
                "2026-07-04",
                "22C",
                "buy_yes",
                12.0,
                0.2,
                0.1,
                0.15,
                1,
                1,
                "synced",
                now,
                None,
                None,
            ),
            (
                "pos-settled",
                "settled",
                "London",
                "2026-07-04",
                "21C",
                "buy_yes",
                13.0,
                0.2,
                None,
                None,
                None,
                None,
                "synced",
                now,
                now,
                "settled",
            ),
        ],
    )
    conn.commit()
    conn.close()
    zs.TRADES_DB = str(tdb)

    result = zs.section_positions()

    assert result["n_open"] == 3
    assert result["n_open_by_phase"] == {
        "active": 1,
        "day0_window": 1,
        "pending_exit": 1,
    }
    assert {row["phase"] for row in result["open"]} == {
        "active",
        "day0_window",
        "pending_exit",
    }
    rendered = zs.render_text(
        {
            "generated_at": now,
            "daemons": {"rows": []},
            "events": {"pending": 0, "proc_1h": {}, "proc_24h": {}},
            "blocks": {"w2h": {"class": {}, "top": []}, "w24h": {"class": {}, "top": []}},
            "surface": {},
            "obs_holes": {"holes": [], "cities_total": 0, "stale_hours": 2.0},
            "price_holes": {"holes": [], "cities_total": 0, "fresh_count": 0, "stale_hours": 2.0},
            "positions": result,
            "orders": {"state_24h": {}, "last5": []},
            "selection": {},
        }
    )
    assert "POSITIONS open=3" in rendered
    assert "day0_window=1" in rendered
    assert "pending_exit=1" in rendered


def test_zeus_status_screen_edges_filters_temperature_metric(tmp_path):
    """HIGH posterior must never join LOW market condition_ids (external review
    2026-06-12): same city/date carries both metrics; an unfiltered join counts
    edge against the wrong market family."""
    zs = _load("zeus_status_smoke_metric", "zeus_status.py")
    fdb = tmp_path / "f.db"
    tdb = tmp_path / "t.db"
    fc = sqlite3.connect(str(fdb))
    fc.execute(
        "CREATE TABLE forecast_posteriors (city TEXT, target_date TEXT, "
        "temperature_metric TEXT, q_lcb_json TEXT, computed_at TEXT)"
    )
    fc.execute(
        "CREATE TABLE market_events (city TEXT, target_date TEXT, "
        "temperature_metric TEXT, range_label TEXT, condition_id TEXT)"
    )
    # HIGH posterior says label '30-31' has q_lcb 0.90.
    fc.execute(
        "INSERT INTO forecast_posteriors VALUES "
        "('seoul', '2026-06-12', 'high', '{\"30-31\": 0.90}', '2026-06-12T00:00:00')"
    )
    # Same label exists in BOTH metric families with different condition ids.
    fc.execute(
        "INSERT INTO market_events VALUES "
        "('seoul', '2026-06-12', 'high', '30-31', 'cond-high')"
    )
    fc.execute(
        "INSERT INTO market_events VALUES "
        "('seoul', '2026-06-12', 'low', '30-31', 'cond-low')"
    )
    fc.commit()
    tr = sqlite3.connect(str(tdb))
    tr.execute(
        "CREATE TABLE executable_market_snapshot_latest (condition_id TEXT, "
        "outcome_label TEXT, orderbook_top_ask REAL, captured_at TEXT)"
    )
    # Only the LOW market has a cheap ask — a metric-blind join would count
    # phantom edge here. The HIGH market's ask leaves no edge.
    tr.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES "
        "('cond-low', 'YES', 0.10, '2026-06-12T00:00:00')"
    )
    tr.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES "
        "('cond-high', 'YES', 0.95, '2026-06-12T00:00:00')"
    )
    tr.commit()
    tr.row_factory = sqlite3.Row
    fc.row_factory = sqlite3.Row
    e3, e5 = zs._screen_edges(fc, tr, "2026-06-12")
    assert (e3, e5) == (0, 0)  # cond-low's phantom 0.80 edge must NOT count
    fc.close()
    tr.close()


def test_zeus_status_age_str():
    zs = _load("zeus_status_smoke4", "zeus_status.py")
    assert zs.age_str(None) == "-"
    assert zs.age_str("not-a-timestamp") == "?"
    # A recent timestamp renders as seconds/minutes, never crashes.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    out = zs.age_str(now_iso)
    assert out.endswith(("s", "m", "h", "d"))


def _price_truth_dbs(tmp_path, *, markets, feasibility=(), snapshots=()):
    """Build only the two read-only status surfaces used by price coverage."""
    fdb = tmp_path / "forecasts.db"
    tdb = tmp_path / "trades.db"
    fc = sqlite3.connect(str(fdb))
    fc.execute(
        "CREATE TABLE market_events "
        "(city TEXT, target_date TEXT, condition_id TEXT, token_id TEXT, "
        "temperature_metric TEXT, range_label TEXT)"
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fc.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, 'high', '30-31')",
        [(city, today, condition, token) for city, condition, token in markets],
    )
    fc.commit()
    fc.close()

    tr = sqlite3.connect(str(tdb))
    tr.execute(
        "CREATE TABLE execution_feasibility_latest "
        "(token_id TEXT, direction TEXT, quote_seen_at TEXT, "
        "best_bid_before REAL, best_ask_before REAL, depth_before_json TEXT)"
    )
    tr.executemany(
        "INSERT INTO execution_feasibility_latest VALUES (?, ?, ?, ?, ?, ?)",
        feasibility,
    )
    tr.execute(
        "CREATE TABLE executable_market_snapshot_latest "
        "(condition_id TEXT, outcome_label TEXT, "
        "orderbook_top_ask REAL, captured_at TEXT)"
    )
    tr.executemany(
        "INSERT INTO executable_market_snapshot_latest VALUES (?, 'YES', 0.55, ?)",
        snapshots,
    )
    tr.commit()
    tr.close()
    return fdb, tdb


def test_zeus_status_price_truth_uses_fresh_feasibility_not_stale_snapshot(tmp_path):
    """Snapshot staleness is topology-only; fresh feasibility BBA is green."""
    zs = _load("zeus_status_smoke_price1", "zeus_status.py")
    now = datetime.now(timezone.utc)
    fdb, tdb = _price_truth_dbs(
        tmp_path,
        markets=[("Tokyo", "cond-tok", "tok-yes")],
        feasibility=[
            ("tok-yes", "buy_yes", now.isoformat(), 0.45, 0.55, '{"bids":[[0.45,1]],"asks":[[0.55,1]]}'),
            # Same token, different direction: deduplicate token coverage.
            ("tok-yes", "sell_yes", now.isoformat(), 0.45, 0.55, '{"bids":[[0.45,1]],"asks":[[0.55,1]]}'),
        ],
        snapshots=[("cond-tok", (now - timedelta(hours=4)).isoformat())],
    )
    zs.FORECASTS_DB, zs.TRADES_DB = str(fdb), str(tdb)

    result = zs.section_price_holes()

    assert result.get("error") is None, result.get("error")
    assert result["bba_token_coverage"]["fresh_tokens"] == 1
    assert result["bba_token_coverage"]["cities"] == [
        {"city": "Tokyo", "tokens_total": 1, "fresh_tokens": 1, "bba_fresh_tokens": 1, "status": "green"}
    ]
    assert result["topology_metadata_staleness"]["stale_or_missing_conditions"][0]["condition_id"] == "cond-tok"
    assert result["holes"] == []


def test_zeus_status_price_truth_requires_every_city_token_fresh(tmp_path):
    """A fresh sibling cannot mask a stale token in the same city."""
    zs = _load("zeus_status_smoke_price2", "zeus_status.py")
    now = datetime.now(timezone.utc)
    fdb, tdb = _price_truth_dbs(
        tmp_path,
        markets=[("Seoul", "cond-one", "tok-one"), ("Seoul", "cond-two", "tok-two")],
        feasibility=[
            ("tok-one", "buy_yes", now.isoformat(), 0.45, 0.55, '{"bids":[[0.45,1]],"asks":[[0.55,1]]}'),
            ("tok-two", "buy_yes", (now - timedelta(hours=4)).isoformat(), 0.45, 0.55, '{"bids":[[0.45,1]],"asks":[[0.55,1]]}'),
        ],
    )
    zs.FORECASTS_DB, zs.TRADES_DB = str(fdb), str(tdb)

    result = zs.section_price_holes()

    assert result["bba_token_coverage"]["fresh_tokens"] == 1
    assert result["bba_token_coverage"]["cities"][0]["status"] == "partial"
    assert len(result["holes"]) == 1
    hole = result["holes"][0]
    assert (hole["city"], hole["condition_id"], hole["token_id"]) == ("Seoul", "cond-two", "tok-two")
    assert hole["age"].endswith("h")
    assert hole["reason"] == "stale_or_missing_evidence"


def test_zeus_status_price_truth_distinguishes_bba_from_full_depth(tmp_path):
    """A fresh BBA-only row is green for BBA but partial for full depth."""
    zs = _load("zeus_status_smoke_price3", "zeus_status.py")
    now = datetime.now(timezone.utc)
    fdb, tdb = _price_truth_dbs(
        tmp_path,
        markets=[("Manila", "cond-man", "tok-man")],
        feasibility=[
            ("tok-man", "buy_yes", now.isoformat(), 0.45, 0.55, '{"bids":[],"asks":[[0.55,1]]}'),
        ],
    )
    zs.FORECASTS_DB, zs.TRADES_DB = str(fdb), str(tdb)

    result = zs.section_price_holes()

    assert result["bba_token_coverage"]["cities"][0]["status"] == "green"
    assert result["full_depth_token_coverage"]["fresh_tokens"] == 0
    assert result["full_depth_token_coverage"]["cities"][0]["status"] == "partial"


def test_zeus_status_price_truth_no_evidence_is_missing(tmp_path):
    """No feasibility row is missing BBA/depth evidence even with no snapshot."""
    zs = _load("zeus_status_smoke_price4", "zeus_status.py")
    fdb, tdb = _price_truth_dbs(
        tmp_path,
        markets=[("Mumbai", "cond-mum", "tok-mum")],
    )
    zs.FORECASTS_DB, zs.TRADES_DB = str(fdb), str(tdb)

    result = zs.section_price_holes()

    assert result["bba_token_coverage"]["cities"][0]["status"] == "missing"
    assert result["full_depth_token_coverage"]["cities"][0]["status"] == "missing"
    assert result["holes"][0]["age"] == "NONE"
    assert result["holes"][0]["reason"] == "stale_or_missing_evidence"


def test_zeus_status_price_truth_excludes_proven_ended_local_today_market(tmp_path):
    """A past venue end boundary cannot make the still-open surface look stale."""
    zs = _load("zeus_status_smoke_price5", "zeus_status.py")
    now = datetime.now(timezone.utc)
    fdb = tmp_path / "forecasts.db"
    tdb = tmp_path / "trades.db"
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    fc = sqlite3.connect(str(fdb))
    fc.execute(
        "CREATE TABLE market_events "
        "(city TEXT, target_date TEXT, condition_id TEXT, token_id TEXT)"
    )
    fc.executemany(
        "INSERT INTO market_events VALUES ('Tokyo', ?, ?, ?)",
        [
            (today, "cond-ended", "tok-ended"),
            (tomorrow, "cond-open", "tok-open"),
        ],
    )
    fc.commit()
    fc.close()

    tr = sqlite3.connect(str(tdb))
    tr.execute(
        "CREATE TABLE execution_feasibility_latest "
        "(token_id TEXT, quote_seen_at TEXT, best_bid_before REAL, "
        "best_ask_before REAL, depth_before_json TEXT)"
    )
    tr.executemany(
        "INSERT INTO execution_feasibility_latest VALUES (?, ?, 0.45, 0.55, ?)",
        [
            ("tok-ended", (now - timedelta(hours=8)).isoformat(), '{"bids":[[0.45,1]],"asks":[[0.55,1]]}'),
            ("tok-open", now.isoformat(), '{"bids":[[0.45,1]],"asks":[[0.55,1]]}'),
        ],
    )
    tr.execute(
        "CREATE TABLE executable_market_snapshot_latest "
        "(condition_id TEXT, snapshot_id TEXT, outcome_label TEXT, "
        "orderbook_top_ask REAL, captured_at TEXT)"
    )
    tr.execute(
        "CREATE TABLE executable_market_snapshots "
        "(snapshot_id TEXT, market_end_at TEXT)"
    )
    tr.executemany(
        "INSERT INTO executable_market_snapshot_latest VALUES (?, ?, 'YES', 0.55, ?)",
        [
            ("cond-ended", "snap-ended", (now - timedelta(hours=8)).isoformat()),
            ("cond-open", "snap-open", now.isoformat()),
        ],
    )
    tr.executemany(
        "INSERT INTO executable_market_snapshots VALUES (?, ?)",
        [
            ("snap-ended", (now - timedelta(hours=1)).isoformat()),
            ("snap-open", (now + timedelta(hours=12)).isoformat()),
        ],
    )
    tr.commit()
    tr.close()

    zs.FORECASTS_DB, zs.TRADES_DB = str(fdb), str(tdb)
    result = zs.section_price_holes()

    assert result.get("error") is None, result.get("error")
    assert result["proven_ended_tokens_excluded"] == 1
    assert result["bba_token_coverage"]["tokens_total"] == 1
    assert result["bba_token_coverage"]["fresh_tokens"] == 1
    assert result["bba_token_coverage"]["cities"][0]["status"] == "green"
    assert result["holes"] == []


# --------------------------------------------------------------------------
# deploy_live
# --------------------------------------------------------------------------
def test_deploy_live_head_sha_reads_single_revision(monkeypatch):
    import subprocess

    dl = _load("deploy_live_head_sha_single_revision", "deploy_live.py")
    calls: list[tuple[str, ...]] = []

    def _fake_git(*args, repo=None):  # noqa: ANN001, ARG001
        calls.append(tuple(args))
        if args == ("rev-parse", "--short", "HEAD"):
            return subprocess.CompletedProcess(["git"], 0, "abc1234\n", "")
        if args == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(["git"], 0, "a" * 40 + "\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(dl, "_git", _fake_git)

    assert dl.head_sha(short=True) == "abc1234"
    assert dl.head_sha(short=False) == "a" * 40
    assert calls == [
        ("rev-parse", "--short", "HEAD"),
        ("rev-parse", "HEAD"),
    ]


def test_deploy_live_fetch_timeout_is_an_unpushed_blocker(monkeypatch):
    import subprocess

    dl = _load("deploy_live_fetch_timeout", "deploy_live.py")

    def _fake_git(*args, repo=None):  # noqa: ANN001, ARG001
        if args == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(["git"], 0, "a" * 40 + "\n", "")
        if args[:1] == ("fetch",):
            raise subprocess.TimeoutExpired(["git", *args], timeout=20.0)
        raise AssertionError(args)

    monkeypatch.setattr(dl, "_git", _fake_git)

    unpushed, detail = dl.unpushed_state("main")

    assert unpushed is True
    assert detail == "fetch origin/main timed out (fail-closed)"


def test_deploy_live_status_runs(capsys):
    """status runs against this checkout and prints structured output.

    LIVE_REPO is repointed at the test's own repo root so the test is
    meaningful on CI (the hardcoded operator path does not exist there).
    """
    dl = _load("deploy_live_smoke", "deploy_live.py")
    dl.LIVE_REPO = str(_REPO)
    rc = dl.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deploy_live status" in out
    assert "branch" in out and "HEAD" in out and "daemons" in out
    assert "substrate-observer" in out
    assert "price-channel-ingest" in out
    assert "post-trade-capital" in out


def test_deploy_live_status_json_reports_restart_gate(capsys):
    dl = _load("deploy_live_status_json", "deploy_live.py")
    dl.LIVE_REPO = str(_REPO)

    rc = dl.main(["status", "--json"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["live_repo"] == str(_REPO)
    assert "branch" in data
    assert "head" in data
    assert "push_state" in data
    assert "dirty_runtime_files" in data
    assert "restart_gate" in data
    assert "ok" in data["restart_gate"]
    assert "blockers" in data["restart_gate"]
    assert "runtime_status" in data
    assert data["daemons"]["live-trading"]["label"] == "com.zeus.live-trading"
    assert data["daemons"]["forecast-live"]["label"] == "com.zeus.forecast-live"


def test_deploy_live_status_json_reports_runtime_boot_blocker(monkeypatch, tmp_path, capsys):
    dl = _load("deploy_live_status_runtime", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    (state / "status_summary.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-09T16:50:18+00:00",
                "status": "BOOT_BLOCKED",
                "mode": "live",
                "live_action_authorized": False,
                "failure_reason": "LIVE_SIDECAR_BOOT_BLOCKED: forecast-live:git_head_mismatch",
                "live_boot": {
                    "ok": False,
                    "issue": "LIVE_SIDECAR_BOOT_BLOCKED",
                },
                "execution_capability": {
                    "live_action_authorized": False,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl, "current_branch", lambda: "main")
    monkeypatch.setattr(dl, "unpushed_state", lambda _branch: (False, "clean"))
    monkeypatch.setattr(dl, "dirty_runtime_files", lambda: [])
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "abc1234")
    monkeypatch.setattr(dl, "daemon_pid_uptime", lambda _label: ("-", "-"))

    rc = dl.main(["status", "--json"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    runtime = data["runtime_status"]
    assert runtime["present"] is True
    assert runtime["status"] == "BOOT_BLOCKED"
    assert runtime["live_action_authorized"] is False
    assert runtime["live_boot"]["issue"] == "LIVE_SIDECAR_BOOT_BLOCKED"


def test_deploy_live_status_text_reports_runtime_boot_blocker(monkeypatch, tmp_path, capsys):
    dl = _load("deploy_live_status_text_runtime", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    (state / "status_summary.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-09T16:50:18+00:00",
                "status": "BOOT_BLOCKED",
                "mode": "live",
                "live_action_authorized": False,
                "failure_reason": "LIVE_SIDECAR_BOOT_BLOCKED: forecast-live:git_head_mismatch",
                "live_boot": {
                    "ok": False,
                    "issue": "LIVE_SIDECAR_BOOT_BLOCKED",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl, "current_branch", lambda: "main")
    monkeypatch.setattr(dl, "unpushed_state", lambda _branch: (False, "clean"))
    monkeypatch.setattr(dl, "dirty_runtime_files", lambda: [])
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "abc1234")
    monkeypatch.setattr(dl, "daemon_pid_uptime", lambda _label: ("-", "-"))

    rc = dl.main(["status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "dirty runtime surface : (clean)" in out
    assert "runtime status: BOOT_BLOCKED mode=live live_action_authorized=False" in out
    assert "runtime boot : LIVE_SIDECAR_BOOT_BLOCKED" in out
    assert "runtime failure: LIVE_SIDECAR_BOOT_BLOCKED: forecast-live:git_head_mismatch" in out


def test_deploy_live_dirty_runtime_files_ignores_readonly_audit_scripts(monkeypatch):
    dl = _load("deploy_live_dirty_runtime_filter", "deploy_live.py")

    def _fake_git(*args, repo=None):
        assert args[:3] == ("status", "--porcelain", "--")
        return dl.subprocess.CompletedProcess(
            ["git", *args],
            0,
            stdout=(
                "?? scripts/audit_live_probability_reality.py\n"
                "?? scripts/audit_yes_no_selection_skew.py\n"
                " M src/main.py\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(dl, "_git", _fake_git)

    assert dl.dirty_runtime_files() == [" M src/main.py"]


def _write_yes_no_selection_event(
    conn: sqlite3.Connection,
    *,
    direction: str,
    yes_optimal_delta_u: float,
    yes_score: float,
    no_optimal_delta_u: float,
    no_score: float,
    yes_condition_id: str | None = None,
    yes_q_lcb: float = 0.62,
    yes_q_point: float | None = None,
    yes_cost: float = 0.44,
) -> None:
    yes_qkernel = {
        "optimal_delta_u": yes_optimal_delta_u,
        "delta_u_at_min": yes_optimal_delta_u / 10.0,
        "robust_trade_score": yes_score,
        "edge_lcb": yes_optimal_delta_u,
        "payoff_q_lcb": yes_q_lcb,
        "cost": yes_cost,
    }
    if yes_q_point is not None:
        yes_qkernel["payoff_q_point"] = yes_q_point
    yes_candidate = {
        "direction": "buy_yes",
        "bin_label": "Will the highest temperature in Paris be 26C?",
        "qkernel_execution_economics": yes_qkernel,
    }
    if yes_condition_id is not None:
        yes_candidate["condition_id"] = yes_condition_id
    payload = {
        "decision_audit": {
            "city": "Paris",
            "target_date": "2026-07-09",
            "direction": direction,
            "opportunity_book": {
                "candidates": [
                    yes_candidate,
                    {
                        "direction": "buy_no",
                        "bin_label": "Will the highest temperature in Paris be 34C?",
                        "qkernel_execution_economics": {
                            "optimal_delta_u": no_optimal_delta_u,
                            "delta_u_at_min": no_optimal_delta_u / 10.0,
                            "robust_trade_score": no_score,
                            "edge_lcb": no_optimal_delta_u,
                            "q_lcb_5pct": 0.71,
                            "cost": 0.51,
                        },
                    },
                ],
            },
        },
    }
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            event_type, created_at, payload_json
        ) VALUES ('DecisionProofAccepted', ?, ?)
        """,
        (datetime.now(timezone.utc).isoformat(), json.dumps(payload)),
    )


def _write_yes_no_execution_chain(
    conn: sqlite3.Connection,
    *,
    final_intent_id: str,
    direction: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            event_type, created_at, payload_json
        ) VALUES ('SubmitPlanBuilt', ?, ?)
        """,
        (
            now,
            json.dumps(
                {
                    "final_intent_id": final_intent_id,
                    "direction": direction,
                    "size": 1.0,
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            event_type, created_at, payload_json
        ) VALUES ('UserTradeObserved', ?, ?)
        """,
        (
            now,
            json.dumps(
                {
                    "final_intent_id": final_intent_id,
                    "filled_size": 1.0,
                    "avg_fill_price": 0.64,
                }
            ),
        ),
    )


def _init_yes_no_selection_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE edli_live_order_events (
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    return conn


def _init_yes_no_selection_db_with_aggregate_id(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    return conn


def _init_yes_no_forecast_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE market_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_unit TEXT,
            settled_at TEXT,
            authority TEXT
        )
        """
    )
    return conn


def test_audit_yes_no_selection_skew_does_not_flag_score_only_yes(tmp_path):
    audit = _load("audit_yes_no_selection_score_only", "audit_yes_no_selection_skew.py")
    trade_db = tmp_path / "zeus_trades.db"
    conn = _init_yes_no_selection_db(trade_db)
    _write_yes_no_selection_event(
        conn,
        direction="buy_no",
        yes_optimal_delta_u=0.001,
        yes_score=0.50,
        no_optimal_delta_u=0.02,
        no_score=0.10,
    )
    conn.commit()
    conn.close()

    report = audit.audit_selection_skew(trade_db=trade_db, days=1.0)

    assert report["verdict"] == "NO_SELECTED_YES_BUT_NO_OBJECTIVE_SELECTOR_ANOMALY"
    summary = report["summary"]
    assert summary["selected_buy_no"] == 1
    assert summary["selected_buy_yes"] == 0
    assert summary["selected_no_top_yes_objective_better"] == 0
    assert summary["selected_no_top_yes_score_better_only"] == 1


def test_audit_settlement_payload_grades_buy_no_win_from_held_side_outcome():
    audit = _load("audit_yes_no_settlement_side", "audit_yes_no_selection_skew.py")

    assert audit._position_won_from_settlement_payload(
        {"won": False, "outcome": 1, "pnl": 19.26}
    ) is True
    assert audit._position_won_from_settlement_payload(
        {"won": True, "outcome": 0, "pnl": -80.74}
    ) is False


def test_audit_settlement_payload_fails_closed_on_position_outcome_conflict():
    audit = _load("audit_yes_no_settlement_conflict", "audit_yes_no_selection_skew.py")

    assert audit._position_won_from_settlement_payload(
        {"position_won": False, "outcome": 1, "won": False}
    ) is None


def test_audit_yes_no_selection_skew_flags_objective_metric_false_positive_when_roi_not_useful(tmp_path):
    audit = _load("audit_yes_no_selection_objective", "audit_yes_no_selection_skew.py")
    trade_db = tmp_path / "zeus_trades.db"
    conn = _init_yes_no_selection_db(trade_db)
    _write_yes_no_selection_event(
        conn,
        direction="buy_no",
        yes_optimal_delta_u=0.04,
        yes_score=0.20,
        no_optimal_delta_u=0.01,
        no_score=0.10,
    )
    conn.commit()
    conn.close()

    report = audit.audit_selection_skew(trade_db=trade_db, days=1.0)

    assert report["verdict"] == "OBJECTIVE_METRIC_FALSE_POSITIVE_NO_ROI_SELECTOR_ANOMALY"
    summary = report["summary"]
    assert summary["selected_buy_no"] == 1
    assert summary["selected_no_top_yes_objective_better"] == 1
    assert report["objective_better_samples"][0]["top_yes"]["optimal_delta_u"] == 0.04
    assert report["objective_better_samples"][0]["top_yes"]["roi_frontier"]["roi_frontier_useful"] is False


def test_audit_yes_no_selection_skew_attributes_user_trade_direction(tmp_path):
    audit = _load("audit_yes_no_selection_execution_chain", "audit_yes_no_selection_skew.py")
    trade_db = tmp_path / "zeus_trades.db"
    conn = _init_yes_no_selection_db(trade_db)
    _write_yes_no_selection_event(
        conn,
        direction="buy_no",
        yes_optimal_delta_u=0.001,
        yes_score=0.10,
        no_optimal_delta_u=0.02,
        no_score=0.20,
    )
    _write_yes_no_execution_chain(
        conn,
        final_intent_id="intent-no-1",
        direction="buy_no",
    )
    conn.commit()
    conn.close()

    report = audit.audit_selection_skew(trade_db=trade_db, days=1.0)

    assert report["execution_chain"]["SubmitPlanBuilt"]["buy_no"] == 1
    assert report["execution_chain"]["UserTradeObserved"]["buy_no"] == 1
    assert report["execution_chain"]["UserTradeObserved"]["buy_yes"] == 0
    assert report["execution_chain"]["UserTradeObserved"]["unknown"] == 0
    day_counts = next(iter(report["by_day"].values()))
    assert day_counts["selected_buy_no"] == 1
    assert day_counts["yes_candidates"] == 1


def test_audit_yes_no_selection_skew_reports_confirmed_yes_trade_quality(tmp_path):
    audit = _load("audit_yes_no_selection_confirmed_yes", "audit_yes_no_selection_skew.py")
    trade_db = tmp_path / "zeus_trades.db"
    conn = _init_yes_no_selection_db_with_aggregate_id(trade_db)
    now = datetime.now(timezone.utc).isoformat()
    aggregate_id = "agg-yes-1"
    final_intent_id = "intent-yes-1"
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_type, created_at, payload_json
        ) VALUES (?, 'SubmitPlanBuilt', ?, ?)
        """,
        (
            aggregate_id,
            now,
            json.dumps(
                {
                    "final_intent_id": final_intent_id,
                    "direction": "buy_yes",
                    "size": 2.0,
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_type, created_at, payload_json
        ) VALUES (?, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            aggregate_id,
            now,
            json.dumps(
                {
                    "final_intent_id": final_intent_id,
                    "direction": "buy_yes",
                    "condition_id": "0xyes",
                    "strategy_key": "center_buy",
                    "q_live": 0.42,
                    "q_lcb_5pct": 0.31,
                    "limit_price": 0.22,
                    "qkernel_execution_economics": {
                        "selection_guard_basis": "SELECTION_BETA_95",
                        "q_lcb_guard_basis": "OOF_WILSON_95",
                    },
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_type, created_at, payload_json
        ) VALUES (?, 'UserTradeObserved', ?, ?)
        """,
        (
            aggregate_id,
            now,
            json.dumps(
                {
                    "final_intent_id": final_intent_id,
                    "trade_status": "CONFIRMED",
                    "fill_authority_state": "FILL_CONFIRMED",
                    "trade_id": "trade-1",
                    "fill_price": 0.21,
                    "filled_size": 2.0,
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    report = audit.audit_selection_skew(trade_db=trade_db, days=1.0)

    assert report["execution_chain"]["UserTradeObserved"]["buy_yes"] == 1
    assert report["confirmed_user_trade_chain"]["buy_yes"] == 1
    assert report["confirmed_yes_trade_quality"]["count"] == 1
    assert report["confirmed_yes_trade_quality"]["q_lcb_ge_025"] == 1
    assert report["confirmed_yes_trade_quality"]["samples"][0]["q_lcb"] == 0.31
    assert report["confirmed_yes_trade_quality"]["samples"][0]["fill_price"] == 0.21


def test_audit_yes_no_selection_skew_flags_day0_boundary_high_q_yes_fill_loss(tmp_path):
    audit = _load("audit_yes_no_selection_day0_boundary_fill_loss", "audit_yes_no_selection_skew.py")
    trade_db = tmp_path / "zeus_trades.db"
    conn = _init_yes_no_selection_db_with_aggregate_id(trade_db)
    conn.executescript(
        """
        CREATE TABLE venue_order_facts (
            venue_order_id TEXT,
            state TEXT,
            matched_size TEXT,
            remaining_size TEXT,
            observed_at TEXT,
            ingested_at TEXT
        );
        CREATE TABLE venue_commands (
            venue_order_id TEXT,
            state TEXT,
            updated_at TEXT,
            created_at TEXT
        );
        CREATE TABLE position_events (
            position_id TEXT,
            event_type TEXT,
            payload_json TEXT,
            sequence_no INTEGER,
            order_id TEXT
        );
        CREATE TABLE position_current (
            position_id TEXT,
            phase TEXT,
            realized_pnl_usd REAL
        );
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    aggregate_id = "agg-day0-boundary-yes"
    final_intent_id = "intent-day0-boundary-yes"
    venue_order_id = "0xday0boundary"
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_type, created_at, payload_json
        ) VALUES (?, 'DecisionProofAccepted', ?, ?)
        """,
        (
            aggregate_id,
            now,
            json.dumps(
                {
                    "decision_audit": {
                        "city": "Wellington",
                        "target_date": "2026-07-02",
                        "direction": "buy_yes",
                        "opportunity_book": {"candidates": []},
                    }
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_type, created_at, payload_json
        ) VALUES (?, 'SubmitPlanBuilt', ?, ?)
        """,
        (
            aggregate_id,
            now,
            json.dumps(
                {
                    "final_intent_id": final_intent_id,
                    "direction": "buy_yes",
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_type, created_at, payload_json
        ) VALUES (?, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            aggregate_id,
            now,
            json.dumps(
                {
                    "final_intent_id": final_intent_id,
                    "direction": "buy_yes",
                    "city": "Wellington",
                    "target_date": "2026-07-02",
                    "bin_label": "Will the highest temperature in Wellington be 12C on July 2?",
                    "strategy_key": "day0_nowcast_entry",
                    "q_live": 0.9602,
                    "q_lcb_5pct": 0.9602,
                    "q_lcb_calibration_source": "FORECAST_BOOTSTRAP",
                    "limit_price": 0.50,
                    "size": 15.0,
                    "qkernel_execution_economics": {
                        "q_lcb_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                        "selection_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                        "selection_guard_n": 1,
                    },
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_type, created_at, payload_json
        ) VALUES (?, 'VenueSubmitAcknowledged', ?, ?)
        """,
        (
            aggregate_id,
            now,
            json.dumps({"venue_order_id": venue_order_id}),
        ),
    )
    conn.execute(
        "INSERT INTO venue_order_facts VALUES (?, 'MATCHED', '15', '0', ?, ?)",
        (venue_order_id, now, now),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, 'FILLED', ?, ?)",
        (venue_order_id, now, now),
    )
    conn.execute(
        "INSERT INTO position_current VALUES ('pos-yes-loss', 'settled', -7.50)"
    )
    conn.execute(
        """
        INSERT INTO position_events VALUES (
            'pos-yes-loss', 'ENTRY_ORDER_FILLED', '{}', 1, ?
        )
        """,
        (venue_order_id,),
    )
    conn.execute(
        """
        INSERT INTO position_events VALUES (
            'pos-yes-loss', 'SETTLED', ?, 2, ?
        )
        """,
        (json.dumps({"won": False, "pnl": -7.50}), venue_order_id),
    )
    conn.commit()
    conn.close()

    report = audit.audit_selection_skew(trade_db=trade_db, days=1.0)

    assert report["verdict"] == "HIGH_Q_YES_DAY0_OBSERVED_BOUNDARY_FILLED_SETTLED_LOSS"
    chain = report["high_quality_yes_chain"]
    assert chain["pre_submit_q_lcb_ge_025"] == 1
    assert chain["venue_matched_or_filled"] == 1
    assert chain["settled_losses"] == 1
    assert chain["user_trade_observed_confirmed"] == 0
    assert chain["day0_observed_boundary_pre_submit"] == 1
    assert chain["day0_observed_boundary_venue_filled"] == 1
    assert chain["day0_observed_boundary_settled_losses"] == 1
    assert chain["q_lcb_guard_basis_counts"] == {"DAY0_OBSERVED_BOUNDARY": 1}
    assert chain["selection_guard_n_buckets"] == {"<=1": 1}
    sample = chain["samples"][0]
    assert sample["day0_observed_boundary_guard"] is True
    assert sample["position_entry_filled"] is True
    assert sample["settled_won"] is False


def test_audit_yes_no_selection_skew_prefers_qkernel_payoff_lcb():
    audit = _load("audit_yes_no_selection_qkernel_lcb", "audit_yes_no_selection_skew.py")

    value = audit._metric(
        {
            "q_lcb_5pct": 0.40,
            "qkernel_execution_economics": {
                "payoff_q_lcb": 0.17,
            },
        },
        "q_lcb",
    )

    assert value == 0.17


def test_audit_yes_no_selection_skew_joins_yes_candidate_to_settlement(tmp_path):
    audit = _load("audit_yes_no_selection_settlement", "audit_yes_no_selection_skew.py")
    trade_db = tmp_path / "zeus_trades.db"
    forecast_db = tmp_path / "zeus-forecasts.db"
    condition_id = "0xparis26"
    trade = _init_yes_no_selection_db(trade_db)
    _write_yes_no_selection_event(
        trade,
        direction="buy_no",
        yes_optimal_delta_u=0.02,
        yes_score=0.20,
        no_optimal_delta_u=0.03,
        no_score=0.30,
        yes_condition_id=condition_id,
        yes_q_lcb=0.22,
        yes_q_point=0.80,
        yes_cost=0.21,
    )
    trade.commit()
    trade.close()
    forecasts = _init_yes_no_forecast_db(forecast_db)
    forecasts.execute(
        """
        INSERT INTO market_events (
            city, target_date, temperature_metric, condition_id, token_id,
            range_label, range_low, range_high
        ) VALUES (
            'Paris', '2026-07-09', 'high', ?, 'yes-token-26',
            'Will the highest temperature in Paris be 26C?', 26, 26
        )
        """,
        (condition_id,),
    )
    forecasts.execute(
        """
        INSERT INTO settlement_outcomes (
            city, target_date, temperature_metric, winning_bin,
            settlement_value, settlement_unit, settled_at, authority
        ) VALUES (
            'Paris', '2026-07-09', 'high', '26C',
            26, 'C', '2026-07-10T10:00:00+00:00', 'VERIFIED'
        )
        """
    )
    forecasts.commit()
    forecasts.close()

    report = audit.audit_selection_skew(
        trade_db=trade_db,
        forecast_db=forecast_db,
        days=1.0,
    )

    outcome = report["yes_settlement_outcome"]
    assert outcome["with_bin_outcome"] == 1
    assert outcome["actual_yes_wins"] == 1
    assert outcome["actual_yes_win_rate"] == 1.0
    assert outcome["by_q_lcb_bucket"]["[0.20,0.25)"] == {
        "n": 1,
        "wins": 1,
        "win_rate": 1.0,
    }
    assert outcome["unique_conditions"]["by_q_lcb_bucket"]["[0.20,0.25)"] == {
        "n": 1,
        "wins": 1,
        "win_rate": 1.0,
    }
    sample = outcome["actual_win_point_ev_positive_samples"][0]
    assert sample["label"] == "Will the highest temperature in Paris be 26C?"
    assert sample["settlement_value"] == 26


def _init_live_probability_reality_trade_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            strategy_key TEXT,
            direction TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            bin_label TEXT,
            p_posterior REAL,
            entry_price REAL,
            cost_basis_usd REAL,
            shares REAL,
            chain_shares REAL,
            chain_state TEXT,
            order_status TEXT,
            realized_pnl_usd REAL,
            settled_at TEXT,
            last_monitor_prob REAL,
            last_monitor_market_price REAL,
            last_monitor_prob_is_fresh INTEGER,
            last_monitor_market_price_is_fresh INTEGER,
            updated_at TEXT,
            exit_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE outcome_fact (
            position_id TEXT PRIMARY KEY,
            strategy_key TEXT,
            entered_at TEXT,
            exited_at TEXT,
            settled_at TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            decision_snapshot_id TEXT,
            pnl REAL,
            outcome INTEGER,
            hold_duration_hours REAL,
            monitor_count INTEGER,
            chain_corrections_count INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            event_version INTEGER NOT NULL DEFAULT 1,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            phase_before TEXT,
            phase_after TEXT,
            strategy_key TEXT NOT NULL,
            decision_id TEXT,
            snapshot_id TEXT,
            order_id TEXT,
            command_id TEXT,
            caused_by TEXT,
            idempotency_key TEXT,
            venue_status TEXT,
            source_module TEXT NOT NULL,
            env TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    return conn


def _init_live_probability_reality_world_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE settlement_attribution (
            direction TEXT,
            category TEXT,
            won INTEGER,
            q_live REAL,
            q_lcb_5pct REAL,
            fresh_q_supports_position INTEGER
        )
        """
    )
    return conn


def test_audit_live_probability_reality_flags_miss_and_zero_monitor(tmp_path):
    audit = _load("audit_live_probability_reality_smoke", "audit_live_probability_reality.py")
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    trade = _init_live_probability_reality_trade_db(trade_db)
    now = datetime.now(timezone.utc).isoformat()
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, strategy_key, direction, city, target_date,
            temperature_metric, bin_label, p_posterior, entry_price,
            cost_basis_usd, shares, chain_shares, chain_state, order_status,
            realized_pnl_usd, settled_at, last_monitor_prob,
            last_monitor_market_price, last_monitor_prob_is_fresh,
            last_monitor_market_price_is_fresh, updated_at, exit_reason
        ) VALUES (
            'pos-miss', 'settled', 'forecast_qkernel_entry', 'buy_no',
            'Wuhan', '2026-07-09', 'high', '35C', 0.86, 0.64,
            6.4, 10.0, 10.0, 'synced', 'filled', -6.4, ?,
            NULL, NULL, NULL, NULL, ?, NULL
        )
        """,
        (now, now),
    )
    trade.execute(
        """
        INSERT INTO outcome_fact (
            position_id, strategy_key, entered_at, exited_at, settled_at,
            exit_reason, admin_exit_reason, decision_snapshot_id, pnl, outcome,
            hold_duration_hours, monitor_count, chain_corrections_count
        ) VALUES (
            'pos-miss', 'forecast_qkernel_entry', ?, NULL, ?, 'SETTLEMENT',
            NULL, 'snap-1', -6.4, 0, 10.0, 0, 0
        )
        """,
        (now, now),
    )
    trade.commit()
    trade.close()
    world = _init_live_probability_reality_world_db(world_db)
    world.execute(
        """
        INSERT INTO settlement_attribution (
            direction, category, won, q_live, q_lcb_5pct, fresh_q_supports_position
        ) VALUES ('buy_no', 'MISCALIBRATED', 0, 0.86, 0.81, 0)
        """
    )
    world.commit()
    world.close()

    report = audit.audit_live_probability_reality(
        trade_db=trade_db,
        world_db=world_db,
        days=1.0,
    )

    assert report["verdict"] == "PROBABILITY_REALITY_AND_ACTUAL_MONITOR_ABSENCE_EVIDENCE"
    assert report["settled_summary"]["with_outcome_fact"] == 1
    assert report["settled_summary"]["wins"] == 0
    assert report["settled_summary"]["actual_monitor_zero"] == 1
    assert report["settled_summary"]["outcome_monitor_zero"] == 1
    assert report["by_declared_probability_bin"]["[0.85,0.90)"]["win_rate"] == 0.0
    assert report["settlement_attribution"]["rows"] == 1


def test_audit_live_probability_reality_distinguishes_monitor_projection_gap(tmp_path):
    audit = _load("audit_live_probability_reality_projection_gap", "audit_live_probability_reality.py")
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    trade = _init_live_probability_reality_trade_db(trade_db)
    now = datetime.now(timezone.utc).isoformat()
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, strategy_key, direction, city, target_date,
            temperature_metric, bin_label, p_posterior, entry_price,
            cost_basis_usd, shares, chain_shares, chain_state, order_status,
            realized_pnl_usd, settled_at, last_monitor_prob,
            last_monitor_market_price, last_monitor_prob_is_fresh,
            last_monitor_market_price_is_fresh, updated_at, exit_reason
        ) VALUES (
            'pos-gap', 'settled', 'forecast_qkernel_entry', 'buy_no',
            'Wuhan', '2026-07-09', 'high', '35C', 0.86, 0.64,
            6.4, 10.0, 10.0, 'synced', 'filled', -6.4, ?,
            NULL, NULL, NULL, NULL, ?, NULL
        )
        """,
        (now, now),
    )
    trade.execute(
        """
        INSERT INTO outcome_fact (
            position_id, strategy_key, entered_at, exited_at, settled_at,
            exit_reason, admin_exit_reason, decision_snapshot_id, pnl, outcome,
            hold_duration_hours, monitor_count, chain_corrections_count
        ) VALUES (
            'pos-gap', 'forecast_qkernel_entry', ?, NULL, ?, 'SETTLEMENT',
            NULL, 'snap-1', -6.4, 0, 10.0, 0, 0
        )
        """,
        (now, now),
    )
    trade.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, source_module,
            env, payload_json
        ) VALUES (
            'evt-monitor-gap', 'pos-gap', 1, 1, 'MONITOR_REFRESHED',
            ?, 'active', 'active', 'forecast_qkernel_entry',
            'test.monitor', 'test', '{}'
        )
        """,
        (now,),
    )
    trade.commit()
    trade.close()
    world = _init_live_probability_reality_world_db(world_db)
    world.commit()
    world.close()

    report = audit.audit_live_probability_reality(
        trade_db=trade_db,
        world_db=world_db,
        days=1.0,
    )

    assert report["verdict"] == "PROBABILITY_REALITY_AND_MONITOR_PROJECTION_GAP_EVIDENCE"
    assert report["settled_summary"]["actual_monitor_zero"] == 0
    assert report["settled_summary"]["outcome_monitor_zero"] == 1
    assert report["settled_summary"]["monitor_projection_gap"] == 1
    assert report["settled_summary"]["avg_actual_monitor_events"] == 1.0


def test_audit_live_probability_reality_reports_open_monitor_probability_jumps(tmp_path):
    audit = _load("audit_live_probability_reality_jumps", "audit_live_probability_reality.py")
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    trade = _init_live_probability_reality_trade_db(trade_db)
    now = datetime.now(timezone.utc)
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, strategy_key, direction, city, target_date,
            temperature_metric, bin_label, p_posterior, entry_price,
            cost_basis_usd, shares, chain_shares, chain_state, order_status,
            realized_pnl_usd, settled_at, last_monitor_prob,
            last_monitor_market_price, last_monitor_prob_is_fresh,
            last_monitor_market_price_is_fresh, updated_at, exit_reason
        ) VALUES (
            'pos-jump', 'day0_window', 'forecast_qkernel_entry', 'buy_no',
            'Taipei', '2026-07-09', 'high', '35C', 0.80, 0.64,
            2.432, 3.8, 3.8, 'synced', 'partial', NULL, NULL,
            0.9461, 0.36, 1, 1, ?, NULL
        )
        """,
        (now.isoformat(),),
    )
    monitor_rows = [
        (
            "evt-jump-1",
            "pos-jump",
            1,
            "MONITOR_REFRESHED",
            (now - timedelta(minutes=2)).isoformat(),
            json.dumps(
                {
                    "last_monitor_prob": 0.7625,
                    "last_monitor_prob_is_fresh": True,
                    "last_monitor_market_price": 0.35,
                    "last_monitor_market_price_is_fresh": True,
                    "selected_method": "day0_high_hard_fact_overlay",
                    "day0_monitor_probability_receipt": {
                        "temporal_context": {
                            "current_utc_timestamp": "2026-07-09 04:58:00+00:00",
                            "post_peak_confidence": 0.7778,
                        },
                        "observation": {
                            "observation_time": "2026-07-09T04:00:00+00:00",
                        },
                        "remaining_window": {
                            "forecast_source_validations": [
                                "forecast_source_cycle_time:2026-07-08T18:00:00+00:00",
                            ],
                        },
                    },
                }
            ),
        ),
        (
            "evt-jump-2",
            "pos-jump",
            2,
            "MONITOR_REFRESHED",
            now.isoformat(),
            json.dumps(
                {
                    "last_monitor_prob": 0.9461,
                    "last_monitor_prob_is_fresh": True,
                    "last_monitor_market_price": 0.36,
                    "last_monitor_market_price_is_fresh": True,
                    "selected_method": "day0_high_hard_fact_overlay",
                    "day0_monitor_probability_receipt": {
                        "temporal_context": {
                            "current_utc_timestamp": "2026-07-09 05:00:00+00:00",
                            "post_peak_confidence": 0.9206,
                        },
                        "observation": {
                            "observation_time": "2026-07-09T04:00:00+00:00",
                        },
                        "remaining_window": {
                            "forecast_source_validations": [
                                "forecast_source_cycle_time:2026-07-08T18:00:00+00:00",
                            ],
                        },
                    },
                    "exit_decision_reason": "",
                }
            ),
        ),
    ]
    trade.executemany(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, source_module,
            env, payload_json
        ) VALUES (?, ?, 1, ?, ?, ?, 'day0_window', 'day0_window',
                  'forecast_qkernel_entry', 'test.monitor', 'test', ?)
        """,
        monitor_rows,
    )
    trade.commit()
    trade.close()
    world = _init_live_probability_reality_world_db(world_db)
    world.execute(
        """
        CREATE TABLE diurnal_peak_prob (
            city TEXT,
            month INTEGER,
            hour INTEGER,
            p_high_set REAL
        )
        """
    )
    world.executemany(
        """
        INSERT INTO diurnal_peak_prob (city, month, hour, p_high_set)
        VALUES ('Taipei', 7, ?, ?)
        """,
        [(12, 0.7778), (13, 0.9206)],
    )
    world.commit()
    world.close()

    report = audit.audit_live_probability_reality(
        trade_db=trade_db,
        world_db=world_db,
        days=1.0,
    )

    assert report["open_summary"]["monitor_probability_jump_count"] == 1
    sample = report["open_summary"]["monitor_probability_jump_samples"][0]
    assert sample["position_id"] == "pos-jump"
    assert sample["city"] == "Taipei"
    assert sample["previous_prob"] == pytest.approx(0.7625)
    assert sample["prob"] == pytest.approx(0.9461)
    assert sample["delta_prob"] == pytest.approx(0.1836)
    assert sample["delta_market_price"] == pytest.approx(0.01)
    assert sample["previous_current_source_local_hour"] == pytest.approx(12.9667, abs=0.0001)
    assert sample["previous_current_source_post_peak_confidence"] == pytest.approx(0.9158, abs=0.0001)
    assert sample["previous_receipt_current_source_post_peak_delta"] == pytest.approx(-0.1380, abs=0.0001)
    assert sample["jump_driver"] == "current_source_semantic_mismatch"
    assert report["open_summary"]["monitor_probability_jump_driver_counts"] == {
        "current_source_semantic_mismatch": 1,
    }


def test_audit_live_probability_reality_flags_unconditioned_daily_extrema_jump(tmp_path):
    audit = _load("audit_live_probability_reality_daily_extrema_jump", "audit_live_probability_reality.py")
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    trade = _init_live_probability_reality_trade_db(trade_db)
    now = datetime.now(timezone.utc)
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, strategy_key, direction, city, target_date,
            temperature_metric, bin_label, p_posterior, entry_price,
            cost_basis_usd, shares, chain_shares, chain_state, order_status,
            realized_pnl_usd, settled_at, last_monitor_prob,
            last_monitor_market_price, last_monitor_prob_is_fresh,
            last_monitor_market_price_is_fresh, updated_at, exit_reason
        ) VALUES (
            'pos-daily-extrema', 'day0_window', 'forecast_qkernel_entry', 'buy_no',
            'Taipei', '2026-07-09', 'high', '35C', 0.80, 0.64,
            2.432, 3.8, 3.8, 'synced', 'partial', NULL, NULL,
            0.9461, 0.36, 1, 1, ?, NULL
        )
        """,
        (now.isoformat(),),
    )
    monitor_rows = []
    for seq, prob in ((1, 0.7625), (2, 0.9461)):
        monitor_rows.append(
            (
                f"evt-daily-extrema-{seq}",
                "pos-daily-extrema",
                seq,
                "MONITOR_REFRESHED",
                (now - timedelta(minutes=2 - seq)).isoformat(),
                json.dumps(
                    {
                        "last_monitor_prob": prob,
                        "last_monitor_prob_is_fresh": True,
                        "last_monitor_market_price": 0.35 + (0.01 if seq == 2 else 0.0),
                        "last_monitor_market_price_is_fresh": True,
                        "exit_decision_should_exit": False,
                        "exit_decision_reason": "",
                        "selected_method": "day0_observation_remaining_window",
                        "day0_monitor_probability_receipt": {
                            "selected_method": "day0_observation_remaining_window",
                            "temporal_context": {
                                "current_utc_timestamp": f"2026-07-09 0{3 + seq}:00:00+00:00",
                                "post_peak_confidence": 0.75 + (0.1 if seq == 2 else 0.0),
                            },
                            "observation": {
                                "observation_time": "2026-07-09T04:00:00+00:00",
                            },
                            "remaining_window": {
                                "source": "day0_raw_model_extrema",
                                "forecast_source_validations": [
                                    "forecast_source_id:raw_model_forecasts.single_runs",
                                    "forecast_source_role:day0_daily_extrema_live",
                                    "forecast_source_cycle_time:2026-07-09T02:14:47+00:00",
                                ],
                            },
                        },
                    }
                ),
            )
        )
    trade.executemany(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, source_module,
            env, payload_json
        ) VALUES (?, ?, 1, ?, ?, ?, 'day0_window', 'day0_window',
                  'forecast_qkernel_entry', 'test.monitor', 'test', ?)
        """,
        monitor_rows,
    )
    trade.commit()
    trade.close()
    world = _init_live_probability_reality_world_db(world_db)
    world.commit()
    world.close()

    report = audit.audit_live_probability_reality(
        trade_db=trade_db,
        world_db=world_db,
        days=1.0,
    )

    assert report["open_summary"]["monitor_probability_jump_count"] == 1
    assert report["verdict"] == "OPEN_DAY0_UNCONDITIONED_DAILY_EXTREMA_HOLD_EVIDENCE"
    assert report["open_summary"]["monitor_probability_jump_driver_counts"] == {
        "unconditioned_daily_extrema_used_as_remaining_window": 1,
    }
    sample = report["open_summary"]["monitor_probability_jump_samples"][0]
    assert sample["remaining_window_source"] == "day0_raw_model_extrema"
    assert sample["forecast_source_role"] == "day0_daily_extrema_live"
    assert sample["jump_driver"] == "unconditioned_daily_extrema_used_as_remaining_window"
    assert report["open_summary"]["unconditioned_daily_extrema_hold_count"] == 1
    hold_sample = report["open_summary"]["unconditioned_daily_extrema_hold_samples"][0]
    assert hold_sample["position_id"] == "pos-daily-extrema"
    assert hold_sample["exit_decision_should_exit"] == 0
    assert hold_sample["remaining_window_source"] == "day0_raw_model_extrema"


def test_audit_live_probability_reality_reports_lost_dust_exit_projection(tmp_path):
    audit = _load("audit_live_probability_reality_dust_projection", "audit_live_probability_reality.py")
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    trade = _init_live_probability_reality_trade_db(trade_db)
    now = datetime.now(timezone.utc).isoformat()
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, strategy_key, direction, city, target_date,
            temperature_metric, bin_label, p_posterior, entry_price,
            cost_basis_usd, shares, chain_shares, chain_state, order_status,
            realized_pnl_usd, settled_at, last_monitor_prob,
            last_monitor_market_price, last_monitor_prob_is_fresh,
            last_monitor_market_price_is_fresh, updated_at, exit_reason
        ) VALUES (
            'pos-dust-lost', 'day0_window', 'forecast_qkernel_entry', 'buy_no',
            'Taipei', '2026-07-09', 'high', '35C', 0.80, 0.64,
            2.432, 3.8, 3.8, 'synced', 'partial', NULL, NULL,
            0.9461, 0.36, 1, 1, ?, NULL
        )
        """,
        (now,),
    )
    trade.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, source_module,
            env, payload_json
        ) VALUES (
            'evt-dust-lost', 'pos-dust-lost', 1, 1, 'EXIT_ORDER_REJECTED',
            ?, 'pending_exit', 'pending_exit', 'forecast_qkernel_entry',
            'test.exit', 'test', ?
        )
        """,
        (
            now,
            json.dumps(
                {
                    "status": "backoff_exhausted",
                    "exit_reason": (
                        "FAMILY_DIRECT_SELL_DOMINATES_HOLD "
                        "[DUST: executable_snapshot_gate: size 3.8 is below snapshot min_order_size 5]"
                    ),
                    "error": "executable_snapshot_gate: size 3.8 is below snapshot min_order_size 5",
                }
            ),
        ),
    )
    trade.commit()
    trade.close()
    world = _init_live_probability_reality_world_db(world_db)
    world.commit()
    world.close()

    report = audit.audit_live_probability_reality(
        trade_db=trade_db,
        world_db=world_db,
        days=1.0,
    )

    assert report["open_summary"]["dust_exit_blocked_count"] == 1
    assert report["open_summary"]["dust_exit_projection_lost_count"] == 1
    sample = report["open_summary"]["dust_exit_projection_lost_samples"][0]
    assert sample["position_id"] == "pos-dust-lost"
    assert sample["phase"] == "day0_window"
    assert sample["order_status"] == "partial"
    assert "min_order_size" in sample["dust_reject_error"]


def test_audit_live_probability_reality_reports_runtime_gate_exit_block(tmp_path):
    audit = _load("audit_live_probability_reality_runtime_gate", "audit_live_probability_reality.py")
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    trade = _init_live_probability_reality_trade_db(trade_db)
    now = datetime.now(timezone.utc).isoformat()
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, strategy_key, direction, city, target_date,
            temperature_metric, bin_label, p_posterior, entry_price,
            cost_basis_usd, shares, chain_shares, chain_state, order_status,
            realized_pnl_usd, settled_at, last_monitor_prob,
            last_monitor_market_price, last_monitor_prob_is_fresh,
            last_monitor_market_price_is_fresh, updated_at, exit_reason
        ) VALUES (
            'pos-runtime-gate', 'day0_window', 'forecast_qkernel_entry', 'buy_no',
            'Taipei', '2026-07-09', 'high', '36C', 0.81, 0.57,
            6.6, 11.6, 11.6, 'synced', 'partial', NULL, NULL,
            0.4785, 0.63, 1, 1, ?, 'FAMILY_DIRECT_SELL_DOMINATES_HOLD'
        )
        """,
        (now,),
    )
    for seq in (1, 2):
        trade.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no, event_type,
                occurred_at, phase_before, phase_after, strategy_key, source_module,
                env, payload_json
            ) VALUES (?, 'pos-runtime-gate', 1, ?, 'EXIT_ORDER_REJECTED',
                      ?, 'pending_exit', 'pending_exit', 'forecast_qkernel_entry',
                      'test.exit', 'test', ?)
            """,
            (
                f"evt-runtime-gate-{seq}",
                seq,
                now,
                json.dumps(
                        {
                            "status": "retry_pending",
                            "runtime_submit_gate_block": True,
                            "exit_reason": "FAMILY_DIRECT_SELL_DOMINATES_HOLD",
                            "error": "structured_runtime_gate_block_without_legacy_text",
                        }
                    ),
                ),
        )
    trade.commit()
    trade.close()
    world = _init_live_probability_reality_world_db(world_db)
    world.commit()
    world.close()

    report = audit.audit_live_probability_reality(
        trade_db=trade_db,
        world_db=world_db,
        days=1.0,
    )

    assert report["open_summary"]["runtime_gate_exit_block_count"] == 1
    assert report["verdict"] == "OPEN_RUNTIME_GATE_EXIT_BLOCK_EVIDENCE"
    sample = report["open_summary"]["runtime_gate_exit_block_samples"][0]
    assert sample["position_id"] == "pos-runtime-gate"
    assert sample["runtime_gate_reject_count"] == 2
    assert sample["latest_runtime_gate_reject_status"] == "retry_pending"
    assert sample["latest_runtime_gate_error"] == "structured_runtime_gate_block_without_legacy_text"


def test_deploy_live_knows_sidecar_labels():
    dl = _load("deploy_live_sidecars", "deploy_live.py")
    assert dl.DAEMONS["substrate-observer"] == "com.zeus.substrate-observer"
    assert dl.DAEMONS["price-channel-ingest"] == "com.zeus.price-channel-ingest"
    assert dl.DAEMONS["post-trade-capital"] == "com.zeus.post-trade-capital"
    assert "deploy/launchd/" in dl.RUNTIME_PATHSPECS


def test_deploy_live_resolves_repo_from_live_trading_plist(tmp_path, monkeypatch):
    dl = _load("deploy_live_plist_repo", "deploy_live.py")
    import plistlib

    live_repo = tmp_path / "live-main"
    live_repo.mkdir()
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_bytes(plistlib.dumps({"WorkingDirectory": str(live_repo)}))
    monkeypatch.delenv("ZEUS_LIVE_REPO", raising=False)
    monkeypatch.setattr(dl, "LIVE_TRADING_PLIST", plist)

    assert dl._resolve_live_repo() == str(live_repo.resolve())


def test_deploy_live_resolve_repo_fails_closed_without_live_plist(tmp_path, monkeypatch):
    dl = _load("deploy_live_missing_plist", "deploy_live.py")
    monkeypatch.delenv("ZEUS_LIVE_REPO", raising=False)
    monkeypatch.setattr(dl, "LIVE_TRADING_PLIST", tmp_path / "missing.plist")
    monkeypatch.setattr(dl, "LIVE_REPO", "")

    with pytest.raises(RuntimeError, match="unreadable live-trading plist"):
        dl._resolve_live_repo()
    assert dl.main(["status"]) == 2


def test_deploy_live_gate_refuses_dirty(tmp_path, capsys):
    """The clean-tree gate refuses dirty checkout even when only unpushed is allowed."""
    dl = _load("deploy_live_smoke2", "deploy_live.py")
    # Build a throwaway git repo with an uncommitted src/ file and no remote.
    import subprocess
    repo = tmp_path / "fake_live"
    (repo / "src").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "src" / "x.py").write_text("# dirty runtime file\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    # Now make it dirty again (uncommitted change to src/).
    (repo / "src" / "x.py").write_text("# dirty runtime file EDITED\n")
    dl.LIVE_REPO = str(repo)

    ok, blockers = dl._gate(allow_dirty=False)
    assert ok is False
    assert blockers  # has at least the dirty-file + unpushed blockers
    blob = " ".join(blockers)
    assert "uncommitted" in blob or "unpushed" in blob
    ok_unpushed, unpushed_blockers = dl._gate(allow_dirty=False, allow_unpushed=True)
    assert ok_unpushed is False
    assert "uncommitted" in " ".join(unpushed_blockers)
    # --allow-dirty overrides the refusal.
    ok2, _ = dl._gate(allow_dirty=True)
    assert ok2 is True


def test_deploy_live_gate_allows_clean_unpushed_without_dirty_override(tmp_path):
    """A clean committed local HEAD can be allowed without allowing dirty files."""
    dl = _load("deploy_live_clean_unpushed", "deploy_live.py")
    import subprocess

    repo = tmp_path / "fake_live"
    remote = tmp_path / "remote.git"
    (repo / "src").mkdir(parents=True)
    subprocess.run(["git", "-C", str(tmp_path), "init", "--bare", str(remote), "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
    (repo / "src" / "x.py").write_text("# committed runtime file\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
    (repo / "src" / "y.py").write_text("# committed but unpushed runtime file\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "local"], check=True)
    dl.LIVE_REPO = str(repo)

    ok, blockers = dl._gate(allow_dirty=False, allow_unpushed=False)
    assert ok is False
    assert "unpushed" in " ".join(blockers)

    ok_unpushed, unpushed_blockers = dl._gate(
        allow_dirty=False,
        allow_unpushed=True,
    )
    assert ok_unpushed is True
    assert "unpushed" in " ".join(unpushed_blockers)


def test_deploy_live_gate_fails_closed_when_git_status_fails(monkeypatch):
    dl = _load("deploy_live_smoke_git_status_fail", "deploy_live.py")

    def _fake_git(*args, repo=None):
        import subprocess

        if args and args[0] == "status":
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad pathspec")
        return subprocess.CompletedProcess(args, 0, "main\n", "")

    monkeypatch.setattr(dl, "_git", _fake_git)

    ok, blockers = dl._gate(allow_dirty=False)

    assert ok is False
    blob = " ".join(blockers)
    assert "git status failed" in blob
    assert "fatal: bad pathspec" in blob


def test_deploy_live_trading_restart_requires_preflight(monkeypatch, tmp_path):
    dl = _load("deploy_live_preflight_gate", "deploy_live.py")
    dl.LIVE_REPO = str(tmp_path)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)

    calls = []

    def _fake_run(cmd, **kwargs):
        import subprocess

        calls.append(cmd)
        assert (kwargs.get("env") or {}).get("ZEUS_LIVE_RESTART_IN_PROGRESS") == "1"
        if cmd[:2] == ["python", "scripts/check_live_restart_preflight.py"] or (
            len(cmd) >= 2 and cmd[1] == "scripts/check_live_restart_preflight.py"
        ):
            return subprocess.CompletedProcess(cmd, 1, '{"ok": false}', "")
        raise AssertionError(f"unexpected subprocess call: {cmd!r}")

    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._run_restart_preflight_if_needed(["com.zeus.live-trading"])

    assert ok is False
    assert "preflight failed" in detail
    assert calls


def test_deploy_live_trading_restart_requires_price_band_attestation(monkeypatch, tmp_path):
    dl = _load("deploy_live_price_band_attestation_missing", "deploy_live.py")
    dl.LIVE_REPO = str(tmp_path)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)

    def _fake_run(cmd, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(
            cmd,
            0,
            '{"ok": true, "checks": []}',
            "",
        )

    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._run_restart_preflight_if_needed(["com.zeus.live-trading"])

    assert ok is False
    assert "omitted required absolute_live_unit_price_band" in detail


@pytest.mark.parametrize("payload", ("[]", '{"ok": true, "checks": {}}'))
def test_deploy_live_trading_restart_rejects_malformed_attestation_shape(
    monkeypatch, tmp_path, payload
):
    dl = _load("deploy_live_price_band_attestation_malformed", "deploy_live.py")
    dl.LIVE_REPO = str(tmp_path)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)

    def _fake_run(cmd, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(cmd, 0, payload, "")

    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._run_restart_preflight_if_needed(["com.zeus.live-trading"])

    assert ok is False
    assert detail == "live restart preflight returned invalid attestation shape"


def test_deploy_live_trading_restart_accepts_price_band_attestation(monkeypatch, tmp_path):
    dl = _load("deploy_live_price_band_attestation_present", "deploy_live.py")
    dl.LIVE_REPO = str(tmp_path)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)

    def _fake_run(cmd, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "ok": True,
                    "checks": [
                        {
                            "name": "absolute_live_unit_price_band",
                            "ok": True,
                            "restart_blocking": True,
                        }
                    ],
                }
            ),
            "",
        )

    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._run_restart_preflight_if_needed(["com.zeus.live-trading"])

    assert ok is True
    assert detail == "live restart preflight passed"


def test_deploy_live_trading_restart_runs_recovery(monkeypatch, tmp_path):
    dl = _load("deploy_live_restart_recovery", "deploy_live.py")
    dl.LIVE_REPO = str(tmp_path)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)

    calls = []

    def _fake_run(cmd, **kwargs):
        import subprocess

        calls.append(cmd)
        if cmd[1] == "-c" and "restart_preflight" in cmd[2]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                '{"advanced": 1, "errors": 0, "scope": "restart_preflight"}\n',
                "",
            )
        raise AssertionError(f"unexpected subprocess call: {cmd!r}")

    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._run_restart_recovery_if_needed(["com.zeus.live-trading"])

    assert ok is True
    assert "restart recovery passed" in detail
    assert calls
    assert "_ensure_restart_world_schemas(world_conn)" in calls[0][2]
    assert "RESTART_WORLD_MIGRATION_TARGETS" in calls[0][2]
    assert "RESTART_TRADE_MIGRATION_TARGETS" in calls[0][2]
    assert "for result_key, target in RESTART_WORLD_MIGRATION_TARGETS" in calls[0][2]
    assert "for result_key, target in RESTART_TRADE_MIGRATION_TARGETS" in calls[0][2]
    assert "world_ghost_cleanup" not in calls[0][2]
    assert [target for _key, target in dl.RESTART_WORLD_MIGRATION_TARGETS] == [
        "202607_drop_world_collateral_unsettled_ghost",
        "202608_edli_active_redecision_projection",
        "202608_edli_active_redecision_projection_receipt_notnull",
    ]
    assert "get_world_connection_read_only" in calls[0][2]
    assert "PRAGMA table_info(opportunity_event_processing_type_backfill)" in calls[0][2]
    assert "assert_active_projection_ready" in calls[0][2]
    assert "world_active_redecision_projection_receipt" in calls[0][2]
    assert "world_active_redecision_backfill_notnull" in calls[0][2]
    assert "EDLI_BACKFILL_RECEIPT_CONSUMER_NOTNULL_REQUIRED" in calls[0][2]
    assert "EDLI_ACTIVE_REDECISION_PROJECTION_UNSEEDED" in calls[0][2]
    assert "_assert_restart_trade_schema_ready(trade_conn)" in calls[0][2]
    assert "init_schema_trade_only" not in calls[0][2]
    assert dl.RESTART_TRADE_MIGRATION_TARGETS == (
        ("trade", "202607_cas_reservation_ledger"),
    )
    assert calls[0][2].count("_assert_restart_trade_schema_ready(trade_conn)") == 1
    assert "get_trade_connection(write_class='live')" in calls[0][2]
    assert "get_world_connection_with_trades_required(write_class='live')" in calls[0][2]
    assert "get_trade_connection_with_world_required(write_class='live')" not in calls[0][2]
    assert "append_rest_filled_orphan_trade_facts_to_edli" not in calls[0][2]
    assert "append_prepared_trade_fact_bridge_evidence" in calls[0][2]
    assert "_edli_trade_fact_bridge_candidates_read_only" in calls[0][2]
    assert "for evidence in confirmed_candidates:" in calls[0][2]
    assert "for evidence in rest_orphan_candidates:" in calls[0][2]
    assert "append_prepared_trade_fact_bridge_evidence(" in calls[0][2]
    assert "candidates=()" in calls[0][2]
    assert "absorbed_fill_aggregate_ids=absorbed_fill_aggregate_ids" in calls[0][2]
    assert "recovery_deadline_monotonic = time.monotonic() + 60.0" in calls[0][2]
    assert "deadline_monotonic=recovery_deadline_monotonic" in calls[0][2]
    assert "bridge_deadline_monotonic = time.monotonic() + 15.0" in calls[0][2]
    assert "summary['edli_trade_fact_bridge_deferred'] = True" in calls[0][2]
    recovery_script = calls[0][2]
    assert recovery_script.index(
        "for result_key, target in RESTART_WORLD_MIGRATION_TARGETS"
    ) < recovery_script.index("world_conn.commit()") < recovery_script.index(
        "PRAGMA table_info(opportunity_event_processing_type_backfill)"
    )
    assert recovery_script.index(
        "for result_key, target in RESTART_TRADE_MIGRATION_TARGETS"
    ) < recovery_script.index(
        "_assert_restart_trade_schema_ready(trade_conn)"
    ) < recovery_script.index("reconcile_unresolved_commands")
    import inspect

    assert "init_schema_trade_only" not in inspect.getsource(
        dl._assert_restart_trade_schema_ready
    )


def test_deploy_live_restart_recovery_failure_preserves_stderr(monkeypatch, tmp_path):
    dl = _load("deploy_live_restart_recovery_stderr", "deploy_live.py")
    dl.LIVE_REPO = str(tmp_path)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)

    def _fake_run(cmd, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(
            cmd,
            1,
            '{"advanced": 0, "errors": 1, "scope": "restart_preflight"}\n',
            "recovery: command exact-id raised RuntimeError: exact failure\n",
        )

    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._run_restart_recovery_if_needed(["com.zeus.live-trading"])

    assert ok is False
    assert "command exact-id" in detail
    assert "exact failure" in detail
    assert '"errors": 1' in detail


def _restart_trade_schema_fixture(tmp_path, *, include_reason=True, include_ledger=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(tmp_path / "zeus_trades.db")
    reason = "'chain_only_auto_resolved_match'," if include_reason else ""
    conn.executescript(
        f"""
        CREATE TABLE token_suppression_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            condition_id TEXT,
            suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
                'operator_quarantine_clear', 'chain_only_quarantined', {reason} 'settled_position'
            )),
            source_module TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{{}}',
            operation TEXT NOT NULL DEFAULT 'record', recorded_at TEXT NOT NULL
        );
        CREATE VIEW token_suppression_current AS
        SELECT token_id, condition_id, suppression_reason, source_module,
               created_at, updated_at, evidence_json
        FROM token_suppression_history;
        CREATE VIEW token_suppression AS SELECT * FROM token_suppression_current;
        CREATE TABLE settlement_commands (
            command_id TEXT, state TEXT, condition_id TEXT, market_id TEXT,
            payout_asset TEXT, pusd_amount_micro INTEGER, token_amounts_json TEXT,
            winning_index_set TEXT, tx_hash TEXT, block_number INTEGER,
            confirmation_count INTEGER, requested_at TEXT, submitted_at TEXT,
            terminal_at TEXT, error_payload TEXT, polymarket_end_anchor_source TEXT,
            autoretry_eligible INTEGER
        );
        CREATE TABLE _migrations_applied (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
        """
    )
    if include_ledger:
        conn.execute(
            "INSERT INTO _migrations_applied VALUES (?, ?)",
            ("202607_cas_reservation_ledger", "2026-08-11T00:00:00Z"),
        )
    conn.commit()
    return conn


def test_deploy_restart_trade_schema_assertion_is_read_only(tmp_path, monkeypatch):
    dl = _load("deploy_live_restart_trade_schema_ready", "deploy_live.py")
    conn = _restart_trade_schema_fixture(tmp_path)
    monkeypatch.setattr(
        "src.state.table_registry.assert_db_matches_registry",
        lambda _conn, _identity: None,
    )
    before = (
        conn.execute("PRAGMA schema_version").fetchone()[0],
        conn.total_changes,
        conn.in_transaction,
    )
    dl._assert_restart_trade_schema_ready(conn)
    assert (
        conn.execute("PRAGMA schema_version").fetchone()[0],
        conn.total_changes,
        conn.in_transaction,
    ) == before
    conn.close()


def test_deploy_restart_trade_schema_assertion_fails_without_reason_or_ledger(tmp_path, monkeypatch):
    dl = _load("deploy_live_restart_trade_schema_fail_closed", "deploy_live.py")
    monkeypatch.setattr(
        "src.state.table_registry.assert_db_matches_registry",
        lambda _conn, _identity: None,
    )
    for kwargs, reason in (
        ({"include_reason": False}, "reason"),
        ({"include_ledger": False}, "ledger"),
    ):
        conn = _restart_trade_schema_fixture(tmp_path / reason, **kwargs)
        before = (conn.execute("PRAGMA schema_version").fetchone()[0], conn.total_changes)
        with pytest.raises(RuntimeError):
            dl._assert_restart_trade_schema_ready(conn)
        assert (conn.execute("PRAGMA schema_version").fetchone()[0], conn.total_changes) == before
        conn.close()


def test_deploy_live_restart_world_schemas_are_atomic_and_idempotent(tmp_path):
    dl = _load("deploy_live_restart_world_schema", "deploy_live.py")
    db_path = tmp_path / "zeus-world.db"
    conn = sqlite3.connect(db_path)

    dl._ensure_restart_world_schemas(conn)
    dl._ensure_restart_world_schemas(conn)

    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert {
        "edli_live_order_events",
        "edli_live_profit_audit_supersessions",
        "settlement_attribution_supersessions",
    } <= tables


def test_deploy_live_restart_world_schema_failure_rolls_back(tmp_path):
    dl = _load("deploy_live_restart_world_schema_rollback", "deploy_live.py")
    conn = sqlite3.connect(tmp_path / "zeus-world.db")

    def deny_settlement_table(action, arg1, _arg2, _db_name, _trigger):
        if action == sqlite3.SQLITE_CREATE_TABLE and arg1 == "settlement_attribution":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(deny_settlement_table)
    with pytest.raises(sqlite3.DatabaseError):
        dl._ensure_restart_world_schemas(conn)
    conn.set_authorizer(None)

    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "edli_live_profit_audit_supersessions" not in tables
    assert "settlement_attribution_supersessions" not in tables


def test_deploy_live_waits_for_fresh_prerequisite_code_identity(monkeypatch, tmp_path):
    dl = _load("deploy_live_prerequisite_identity", "deploy_live.py")
    launched = datetime.now(timezone.utc)
    state = tmp_path / "state"
    state.mkdir()
    expected = "a" * 40
    (state / "daemon-heartbeat-price-channel-ingest.json").write_text(
        json.dumps(
            {
                "git_head": expected,
                "alive_at": launched.isoformat(),
            }
        )
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_prerequisite_code_identity(
        [dl.DAEMONS["price-channel-ingest"]],
        expected_sha=expected,
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "verified" in detail


def test_deploy_live_requires_data_ingest_code_identity(monkeypatch, tmp_path):
    """The 5-second Day0 source writer must load the deployed checkout."""

    dl = _load("deploy_live_data_ingest_identity", "deploy_live.py")
    launched = datetime.now(timezone.utc)
    state = tmp_path / "state"
    state.mkdir()
    expected = "c" * 40
    (state / "daemon-heartbeat-ingest.json").write_text(
        json.dumps(
            {
                "git_head": expected[:9],
                "alive_at": launched.isoformat(),
            }
        )
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_prerequisite_code_identity(
        [dl.DAEMONS["data-ingest"]],
        expected_sha=expected,
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "verified" in detail


def test_deploy_live_accepts_sidecar_abbreviated_head(monkeypatch, tmp_path):
    dl = _load("deploy_live_prerequisite_identity_short", "deploy_live.py")
    launched = datetime.now(timezone.utc)
    state = tmp_path / "state"
    state.mkdir()
    expected = "8a89dc110e2489a8c9e7ba90688311c6be9b9b7f"
    (state / "daemon-heartbeat-price-channel-ingest.json").write_text(
        json.dumps(
            {
                "git_head": expected[:9],
                "alive_at": launched.isoformat(),
            }
        )
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_prerequisite_code_identity(
        [dl.DAEMONS["price-channel-ingest"]],
        expected_sha=expected,
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "verified" in detail
    assert dl._git_head_matches(expected, expected[:6]) is False


def test_deploy_live_prerequisite_code_identity_rejects_stale_sha(monkeypatch, tmp_path):
    dl = _load("deploy_live_prerequisite_identity_stale", "deploy_live.py")
    launched = datetime.now(timezone.utc)
    state = tmp_path / "state"
    state.mkdir()
    (state / "daemon-heartbeat-price-channel-ingest.json").write_text(
        json.dumps(
            {
                "git_head": "b" * 40,
                "alive_at": launched.isoformat(),
            }
        )
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_prerequisite_code_identity(
        [dl.DAEMONS["price-channel-ingest"]],
        expected_sha="a" * 40,
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is False
    assert "did not verify" in detail


def test_deploy_live_non_trading_restart_skips_preflight(monkeypatch):
    dl = _load("deploy_live_preflight_skip", "deploy_live.py")

    def _boom(*args, **kwargs):
        raise AssertionError("preflight subprocess should not run")

    monkeypatch.setattr(dl.subprocess, "run", _boom)

    ok, detail = dl._run_restart_preflight_if_needed(["com.zeus.price-channel-ingest"])

    assert ok is True
    assert "not required" in detail


def test_deploy_live_non_trading_restart_skips_recovery(monkeypatch):
    dl = _load("deploy_live_recovery_skip", "deploy_live.py")

    def _boom(*args, **kwargs):
        raise AssertionError("recovery subprocess should not run")

    monkeypatch.setattr(dl.subprocess, "run", _boom)

    ok, detail = dl._run_restart_recovery_if_needed(["com.zeus.price-channel-ingest"])

    assert ok is True
    assert "not required" in detail


def test_deploy_live_bootstraps_when_service_not_loaded(monkeypatch, tmp_path):
    dl = _load("deploy_live_bootstrap_unloaded", "deploy_live.py")
    label = "com.zeus.live-trading"
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_text("plist")
    calls = []

    def _fake_run(cmd, **kwargs):
        import subprocess

        calls.append(cmd)
        if cmd[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(cmd, 113, "", "Could not find service")
        if cmd[:2] == ["launchctl", "bootstrap"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected subprocess call: {cmd!r}")

    monkeypatch.setattr(dl, "LIVE_TRADING_PLIST", plist)
    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._launch_or_restart_label(label)

    assert ok is True
    assert "bootstrapped" in detail
    assert calls[-1] == ["launchctl", "bootstrap", dl.GUI_DOMAIN, str(plist)]


def test_deploy_live_reloads_when_service_loaded(monkeypatch, tmp_path):
    dl = _load("deploy_live_reload_loaded", "deploy_live.py")
    label = "com.zeus.live-trading"
    plist = tmp_path / "com.zeus.live-trading.plist"
    plist.write_text("plist")
    calls = []
    loaded = True

    def _fake_run(cmd, **kwargs):
        import subprocess
        nonlocal loaded

        calls.append(cmd)
        if cmd[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(
                cmd,
                0 if loaded else 3,
                "state = running" if loaded else "",
                "" if loaded else "Could not find service",
            )
        if cmd[:2] == ["launchctl", "bootout"]:
            loaded = False
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["launchctl", "bootstrap"]:
            loaded = True
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected subprocess call: {cmd!r}")

    monkeypatch.setattr(dl, "LIVE_TRADING_PLIST", plist)
    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._launch_or_restart_label(label)

    assert ok is True
    assert "reloaded" in detail
    assert ["launchctl", "bootout", f"{dl.GUI_DOMAIN}/{label}"] in calls
    assert calls[-1] == ["launchctl", "bootstrap", dl.GUI_DOMAIN, str(plist)]


def test_deploy_live_retries_bootstrap_after_reload_race(monkeypatch, tmp_path):
    dl = _load("deploy_live_reload_retry", "deploy_live.py")
    label = "com.zeus.forecast-live"
    plist = tmp_path / "com.zeus.forecast-live.plist"
    plist.write_text("plist")
    calls = []
    loaded = True

    def _fake_run(cmd, **kwargs):
        import subprocess
        nonlocal loaded

        calls.append(cmd)
        if cmd[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(
                cmd,
                0 if loaded else 3,
                "state = running" if loaded else "",
                "" if loaded else "Could not find service",
            )
        if cmd[:2] == ["launchctl", "bootout"]:
            loaded = False
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["launchctl", "bootstrap"]:
            bootstrap_count = sum(1 for call in calls if call[:2] == ["launchctl", "bootstrap"])
            if bootstrap_count == 1:
                return subprocess.CompletedProcess(cmd, 5, "", "Bootstrap failed: 5")
            loaded = True
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected subprocess call: {cmd!r}")

    monkeypatch.setattr(dl, "LAUNCHAGENTS_DIR", tmp_path)
    monkeypatch.setattr(dl.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(dl.subprocess, "run", _fake_run)

    ok, detail = dl._launch_or_restart_label(label)

    assert ok is True
    assert "after 2 attempts" in detail
    assert sum(1 for call in calls if call[:2] == ["launchctl", "bootstrap"]) == 2


def test_deploy_live_waits_for_loaded_process_identity(monkeypatch, tmp_path):
    dl = _load("deploy_live_runtime_fresh_wait", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    expected = "a" * 40
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    (state / "loaded_sha.json").write_text(
        json.dumps({"loaded_sha": expected, "generated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    (state / "deployment_freshness.json").write_text(
        json.dumps(
            {
                "boot_sha": expected,
                "current_sha": expected,
                "status": "fresh",
                "pause_reason": None,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_live_runtime_fresh(
        expected_sha=expected,
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "loaded_sha" in detail


def test_deploy_live_loaded_process_identity_survives_concurrent_checkout_advance(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_runtime_fresh_checkout_advance", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    expected = "a" * 40
    current = "b" * 40
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    (state / "loaded_sha.json").write_text(
        json.dumps({"loaded_sha": expected, "generated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    (state / "deployment_freshness.json").write_text(
        json.dumps(
            {
                "boot_sha": expected,
                "current_sha": current,
                "status": "dirty_runtime_worktree",
                "pause_reason": None,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_live_runtime_fresh(
        expected_sha=expected,
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "loaded_sha=" in detail
    assert "worktree_freshness_observation=dirty_runtime_worktree" in detail


def test_deploy_live_runtime_fresh_wait_rejects_stale_loaded_sha(monkeypatch, tmp_path):
    dl = _load("deploy_live_runtime_fresh_wait_stale", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    expected = "b" * 40
    launched = datetime.now(timezone.utc)
    (state / "loaded_sha.json").write_text(
        json.dumps(
            {
                "loaded_sha": expected,
                "generated_at": (launched - timedelta(minutes=5)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl.time, "sleep", lambda _seconds: None)

    ok, detail = dl._wait_for_live_runtime_fresh(
        expected_sha=expected,
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is False
    assert "did not verify" in detail


def test_deploy_live_runtime_fresh_wait_allows_boot_timestamp_boundary(monkeypatch, tmp_path):
    dl = _load("deploy_live_runtime_fresh_wait_boundary", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    expected = "f" * 40
    launched = datetime.now(timezone.utc)
    (state / "loaded_sha.json").write_text(
        json.dumps(
            {
                "loaded_sha": expected,
                "generated_at": (launched - timedelta(seconds=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (state / "deployment_freshness.json").write_text(
        json.dumps(
            {
                "boot_sha": expected,
                "current_sha": expected,
                "status": "fresh",
                "pause_reason": None,
                "detected_at": (launched - timedelta(seconds=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_live_runtime_fresh(
        expected_sha=expected,
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "verified" in detail


def test_deploy_live_waits_for_post_start_monitor_refresh(monkeypatch, tmp_path):
    dl = _load("deploy_live_monitor_cadence_wait", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO position_current VALUES ('pos-1', 'active', 1.0, 1.0)"
    )
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'pos-1', 'MONITOR_REFRESHED', ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "last_monitor_prob": 0.5,
                    "last_monitor_prob_is_fresh": True,
                    "last_monitor_market_price": 0.5,
                    "last_monitor_market_price_is_fresh": True,
                }
            ),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "post-start monitor cadence verified" in detail


def test_deploy_live_accepts_post_start_probability_degraded_monitor_attempt(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_monitor_probability_degraded", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn = sqlite3.connect(trade_db)
    conn.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        );
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        INSERT INTO position_current VALUES ('pos-1', 'active', 1.0, 1.0);
        """
    )
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'pos-1', 'MONITOR_REFRESHED', ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "last_monitor_prob": None,
                    "last_monitor_prob_is_fresh": False,
                    "last_monitor_market_price": 0.61,
                    "last_monitor_market_price_is_fresh": True,
                    "exit_decision_available": False,
                    "applied_validations": ["DATA_DEGRADED"],
                }
            ),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "probability_degraded_positions=1" in detail


def test_deploy_live_quote_only_monitor_staleness_requires_complete_held_auction(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_monitor_quote_only_auction", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn = sqlite3.connect(trade_db)
    conn.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        );
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY,
            mode TEXT,
            started_at TEXT,
            completed_at TEXT,
            artifact_json TEXT
        );
        INSERT INTO position_current VALUES ('pos-1', 'active', 1.0, 1.0);
        """
    )
    monitor_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'pos-1', 'MONITOR_REFRESHED', ?, ?)
        """,
        (
            monitor_at,
            json.dumps(
                {
                    "last_monitor_prob": 0.99,
                    "last_monitor_prob_is_fresh": True,
                    "last_monitor_market_price": 0.999,
                    "last_monitor_market_price_is_fresh": False,
                }
            ),
        ),
    )
    conn.commit()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is False
    assert "complete_post_start_held_auction_receipt=missing" in detail

    conn.close()
    monkeypatch.setattr(
        dl,
        "_latest_complete_global_auction_receipt",
        lambda *_args, **kwargs: (
            (1, 1, 1)
            if kwargs.get("require_held_position_ids") == ("pos-1",)
            else None
        ),
    )

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is True
    assert "quote_only_positions=1" in detail
    assert "held_auction_receipt=1" in detail


def test_exact_held_restart_proof_rejects_legacy_global_auction_receipt(tmp_path):
    from src.ops.monitor_cadence import latest_complete_global_auction_receipt

    trade_db = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(trade_db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY,
            mode TEXT,
            started_at TEXT,
            completed_at TEXT,
            artifact_json TEXT
        )
        """
    )
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO decision_log VALUES (1, 'global_single_order_auction', ?, NULL, ?)",
        (
            now.isoformat(),
            json.dumps(
                {
                    "summary": {
                        "schema_version": 20,
                        "candidate_coverage_complete": True,
                        "scope_family_coverage_complete": True,
                        "candidate_evaluation_count": 1,
                        "full_scope_family_count": 1,
                        "held_position_coverage_complete": True,
                        "held_position_expected_count": 1,
                        "held_position_evaluated_count": 0,
                        "held_position_excluded_count": 1,
                    }
                }
            ),
        ),
    )
    assert latest_complete_global_auction_receipt(
        conn,
        completed_not_before=now - timedelta(seconds=1),
        require_held_coverage_count=1,
        require_held_position_ids=("pos-1",),
    ) is None
    conn.close()


def test_held_restart_proof_accepts_receipt_superset_but_not_missing_current(monkeypatch):
    from src.ops import monitor_cadence

    summary = {
        "schema_version": 22,
        "candidate_coverage_complete": True,
        "scope_family_coverage_complete": True,
        "candidate_evaluation_count": 1,
        "full_scope_family_count": 1,
        "held_position_coverage_complete": True,
        "held_position_expected_count": 2,
        "held_position_evaluated_count": 0,
        "held_position_excluded_count": 2,
        "decision_at_utc": "2026-08-13T00:00:01+00:00",
    }

    class FakeConn:
        class Cursor(list):
            def fetchall(self):
                return self

        def execute(self, *_args, **_kwargs):
            return self.Cursor([
                {
                    "id": 1,
                    "mode": "global_single_order_auction",
                    "started_at": "2026-08-13T00:00:00+00:00",
                    "completed_at": "2026-08-13T00:00:01+00:00",
                    "artifact_json": json.dumps(
                        {
                            "completed_at": "2026-08-13T00:00:01+00:00",
                            "summary": summary,
                        }
                    ),
                }
            ])

    monkeypatch.setattr(
        "src.contracts.global_auction_receipt.assert_global_auction_summary_integrity",
        lambda _summary: None,
    )
    monkeypatch.setattr(
        "src.control.live_health._current_global_auction_holding_payload",
        lambda _conn, _summary: [
            {"position_id": "current", "status": "EXCLUDED"},
            {"position_id": "closed-after-receipt", "status": "EXCLUDED"},
        ],
    )
    assert monitor_cadence.latest_complete_global_auction_receipt(
        FakeConn(),
        completed_not_before=datetime.fromisoformat(
            "2026-08-13T00:00:00+00:00"
        ),
        require_held_coverage_count=1,
        require_held_position_ids=("current",),
    ) == (1, 1, 1)
    assert monitor_cadence.latest_complete_global_auction_receipt(
        FakeConn(),
        completed_not_before=datetime.fromisoformat(
            "2026-08-13T00:00:00+00:00"
        ),
        require_held_coverage_count=2,
        require_held_position_ids=("current", "new-current"),
    ) is None


def test_deploy_live_review_management_does_not_replace_monitor_refresh(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_monitor_review_wait", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        )
        """
    )
    conn.execute("INSERT INTO position_current VALUES ('pos-1', 'day0_window', 3.0, 3.0)")
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'pos-1', 'REVIEW_REQUIRED', ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "reason": "confirmed_entry_fill_token_absent_market_not_resolved",
                    "chain_mirror_classification": "review_open_absent",
                    "reconciler": "chain_mirror",
                }
            ),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is False
    assert "did not verify" in detail


def test_deploy_live_post_start_monitor_wait_rejects_stale_chain_only_projection(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_monitor_cadence_wait_stale", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    launched = datetime.now(timezone.utc)
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO position_current VALUES ('pos-1', 'active', 1.0, 1.0, ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at
        ) VALUES (1, 'pos-1', 'CHAIN_SIZE_CORRECTED', ?)
        """,
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at
        ) VALUES (2, 'pos-1', 'MONITOR_REFRESHED', ?)
        """,
        ((launched - timedelta(minutes=20)).isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl.time, "sleep", lambda _seconds: None)

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is False
    assert "did not verify" in detail
    assert "last_monitor_refreshed_at" in detail


def test_deploy_live_post_start_monitor_wait_is_per_position(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_monitor_cadence_wait_per_position", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        )
        """
    )
    conn.execute("INSERT INTO position_current VALUES ('pos-1', 'active', 1.0, 1.0)")
    conn.execute("INSERT INTO position_current VALUES ('pos-2', 'active', 1.0, 1.0)")
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at, payload_json
        ) VALUES (1, 'pos-1', 'MONITOR_REFRESHED', ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "last_monitor_prob": 0.5,
                    "last_monitor_prob_is_fresh": True,
                    "last_monitor_market_price": 0.5,
                    "last_monitor_market_price_is_fresh": True,
                }
            ),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl.time, "sleep", lambda _seconds: None)

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is False
    assert "stale_or_missing_positions=1" in detail
    assert "pos-2" in detail


def test_deploy_live_post_start_monitor_wait_rejects_partial_coverage_tranche(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_monitor_cadence_wait_tranche", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        )
        """
    )
    for index in range(6):
        conn.execute(
            "INSERT INTO position_current VALUES (?, 'active', 1.0, 1.0)",
            (f"pos-{index}",),
        )
    for sequence_no, position_id in enumerate(("pos-0", "pos-1"), start=1):
        conn.execute(
            """
            INSERT INTO position_events (
                sequence_no, position_id, event_type, occurred_at, payload_json
            ) VALUES (?, ?, 'MONITOR_REFRESHED', ?, ?)
            """,
            (
                sequence_no,
                position_id,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(
                    {
                        "last_monitor_prob": 0.5,
                        "last_monitor_prob_is_fresh": True,
                        "last_monitor_market_price": 0.5,
                        "last_monitor_market_price_is_fresh": True,
                    }
                ),
            ),
        )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is False
    assert "stale_or_missing_positions=4" in detail
    assert "pos-2" in detail


def test_deploy_live_post_start_monitor_wait_rejects_future_monitor_event(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_monitor_cadence_wait_future", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT
        )
        """
    )
    conn.execute("INSERT INTO position_current VALUES ('pos-1', 'active', 1.0, 1.0)")
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at
        ) VALUES (1, 'pos-1', 'MONITOR_REFRESHED', ?)
        """,
        ((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl.time, "sleep", lambda _seconds: None)

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=launched,
        timeout_seconds=0,
    )

    assert ok is False
    assert "future_monitor_events=1" in detail


def test_deploy_live_post_start_monitor_wait_rejects_non_monitor_chain_risk(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_monitor_cadence_wait_non_monitor_chain_risk", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    conn = sqlite3.connect(trade_db)
    conn.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            chain_shares REAL,
            chain_state TEXT
        );
        CREATE TABLE position_events (
            sequence_no INTEGER PRIMARY KEY,
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT
        );
        INSERT INTO position_current VALUES ('pos-voided', 'voided', 1.0, 'synced');
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_monitor_cadence(
        launched_after=datetime.now(timezone.utc),
        timeout_seconds=0,
    )

    assert ok is False
    assert "non_monitor_chain_risk_position_count=1" in detail


def _init_edli_queue_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE opportunity_event_processing (
            consumer_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            processing_status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            processed_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (consumer_name, event_id)
        )
        """
    )
    return conn


def _init_paused_entry_park_authority(
    state: Path,
    *,
    paused: bool = True,
) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    """Create only the canonical pause/exposure/command surfaces this gate reads."""

    world = _init_edli_queue_db(state / "zeus-world.db")
    world.execute(
        """
        CREATE TABLE control_overrides (
            override_id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_key TEXT NOT NULL,
            action_type TEXT NOT NULL,
            value TEXT NOT NULL,
            issued_by TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            effective_until TEXT,
            reason TEXT NOT NULL,
            precedence INTEGER NOT NULL
        )
        """
    )
    world.execute(
        """
        INSERT INTO control_overrides VALUES (
            'control_plane:global:entries_paused', 'global', 'entries', 'gate',
            ?, 'control_plane', ?, NULL, 'deploy_test_pause', 100
        )
        """,
        ("true" if paused else "false", datetime.now(timezone.utc).isoformat()),
    )
    trade = sqlite3.connect(state / "zeus_trades.db")
    trade.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            chain_shares REAL,
            shares REAL,
            chain_state TEXT NOT NULL
        );
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            side TEXT NOT NULL,
            state TEXT NOT NULL
        );
        """
    )
    return world, trade


def _insert_claimable_edli_entry_backlog(
    conn: sqlite3.Connection,
    *,
    event_id: str = "evt-paused-entry",
) -> None:
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count,
            claimed_at, processed_at, last_error, updated_at
        ) VALUES ('edli_reactor_v1', ?, 'pending', 0, NULL, NULL, NULL, ?)
        """,
        (event_id, datetime.now(timezone.utc).isoformat()),
    )


def _publish_exact_held_sell_wake(state: Path, *, position_id: str):
    from src.runtime import reactor_wake

    wake_path = state / reactor_wake.REACTOR_WAKE_FILENAME
    request = reactor_wake.make_held_sell_reauction_request(
        position_id=position_id,
        family=("Paris", "2026-08-02", "low"),
        probability_content_identity=f"q-{position_id}",
        held_token_id=f"token-{position_id}",
        held_best_bid=0.22,
        bid_observed_at="2026-08-02T19:00:00+00:00",
    )
    wake = reactor_wake.publish_reactor_wake(
        source="test",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        held_sell_reauction_requests=(request,),
    )
    return wake_path, wake, request


def test_deploy_live_paused_entry_backlog_is_explicitly_expected_parked(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_paused_entry_backlog", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is True
    assert "post-start EDLI queue expected parked" in detail
    assert "durable_entries_paused=true" in detail
    assert "canonical_unresolved_positions=0" in detail
    assert "nonterminal_sell_commands=0" in detail
    assert "held_sell_global_auction_debt=0" in detail


def test_deploy_live_unpaused_entry_backlog_does_not_get_parked(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_unpaused_entry_backlog", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state, paused=False)
    _insert_claimable_edli_entry_backlog(world)
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert "expected_parked=durable_entries_paused=false" in detail


def test_deploy_live_paused_entry_backlog_requires_post_start_freshness(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_paused_entry_unfresh", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=False,
        timeout_seconds=0,
    )

    assert ok is False
    assert "expected_parked=post_start_freshness=unverified" in detail


@pytest.mark.parametrize("phase", ("active", "day0_window", "pending_exit"))
def test_deploy_live_paused_entry_backlog_allows_fresh_monitored_open_exposure(
    monkeypatch, tmp_path, phase
):
    dl = _load(f"deploy_live_paused_entry_open_exposure_{phase}", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    trade.executescript(
        """
        ALTER TABLE position_current ADD COLUMN cost_basis_usd REAL;
        """
    )
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, chain_shares, chain_state, shares, cost_basis_usd
        ) VALUES ('pos-open', ?, 7, 'synced', 7, 42)
        """,
        (phase,),
    )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is True
    assert "canonical_unresolved_positions=0" in detail


def test_deploy_live_paused_entry_backlog_ignores_terminal_historical_exposure(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_paused_entry_terminal_economics", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    trade.executescript(
        """
        ALTER TABLE position_current ADD COLUMN cost_basis_usd REAL;
        """
    )
    trade.executemany(
        """
        INSERT INTO position_current (
            position_id, phase, chain_shares, chain_state, shares, cost_basis_usd
        ) VALUES (?, ?, 7, 'closed_redeemed', 7, 42)
        """,
        (
            (f"terminal-{index}", phase)
            for index, phase in enumerate(
                ("settled",) * 1_587 + ("voided", "admin_closed", "economically_closed")
            )
        ),
    )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is True
    assert "canonical_unresolved_positions=0" in detail


@pytest.mark.parametrize(
    ("case", "phase", "shares", "chain_shares", "chain_state"),
    (
        ("voided_chain_risk", "voided", 0.0, 1.0, "synced"),
        ("active_null_shares", "active", None, 1.0, "synced"),
        ("active_infinite_shares", "active", float("inf"), 1.0, "synced"),
        ("active_null_chain_shares", "active", 1.0, None, "synced"),
        ("active_infinite_chain_shares", "active", 1.0, float("inf"), "synced"),
    ),
)
def test_deploy_live_paused_entry_backlog_rejects_unresolved_position_authority(
    monkeypatch, tmp_path, case, phase, shares, chain_shares, chain_state
):
    dl = _load(f"deploy_live_paused_entry_unresolved_{case}", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, chain_shares, shares, chain_state
        ) VALUES ('pos-unresolved', ?, ?, ?, ?)
        """,
        (phase, chain_shares, shares, chain_state),
    )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert "expected_parked=canonical_unresolved_positions=1" in detail


def test_deploy_live_paused_entry_backlog_rejects_pending_fill_unknown_exposure(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_paused_entry_pending_unknown", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, chain_shares, chain_state
        ) VALUES ('pos-pending', 'pending_entry', 0, 'unknown')
        """
    )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert "expected_parked=canonical_unresolved_positions=1" in detail


@pytest.mark.parametrize("phase", (None, "", "unknown", "unrecognized_phase"))
def test_deploy_live_paused_entry_backlog_rejects_ungoverned_lifecycle_phase(
    monkeypatch, tmp_path, phase
):
    dl = _load("deploy_live_paused_entry_ungoverned_phase", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, chain_shares, chain_state
        ) VALUES ('pos-ungoverned', ?, 0, 'closed_redeemed')
        """,
        (phase,),
    )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert "expected_parked=canonical_unresolved_positions=1" in detail


def test_deploy_live_paused_entry_backlog_rejects_nonterminal_sell_debt(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_paused_entry_sell_debt", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    trade.execute("INSERT INTO venue_commands VALUES ('sell-open', 'SELL', 'POSTING')")
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert "expected_parked=nonterminal_sell_commands=1" in detail


@pytest.mark.parametrize("phase", ("pending_entry", "active", "day0_window", "pending_exit"))
def test_deploy_live_loaded_restart_blocks_every_open_capital_phase(
    monkeypatch, tmp_path, phase
):
    dl = _load(f"deploy_live_restart_open_{phase}", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    trade.execute(
        "INSERT INTO position_current VALUES ('pos-open', ?, 7, 7, 'synced')",
        (phase,),
    )
    world.close()
    trade.commit()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=True,
    )

    assert ok is False
    assert "open_positions=1" in detail
    assert "pos-open" in detail


def test_deploy_live_loaded_restart_allows_paused_current_monitor_handoff(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_restart_paused_handoff", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    trade.execute(
        "INSERT INTO position_current VALUES ('pos-open', 'day0_window', 7, 7, 'synced')"
    )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(
        dl,
        "_pre_stop_monitor_handoff_evidence",
        lambda _trade_db: {
            "green": True,
            "open_position_count": 1,
            "probability_degraded_position_count": 1,
        },
    )

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=True,
    )

    assert ok is True
    assert "repair handoff verified" in detail
    assert "durable_entries_pause=true" in detail
    assert "probability_degraded_positions=1" in detail


def _stuck_monitor_handoff(position_ids, **overrides):
    handoff = {
        "green": False,
        "open_position_count": len(position_ids),
        "fresh_position_count": 0,
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
        "quote_only_stale_position_count": 0,
        "quote_only_stale_position_ids": (),
        "quote_only_stale_shape_valid": True,
        "quote_only_stale_shape_error": None,
        "reauction_handoff_position_count": 0,
        "probability_degraded_position_count": 1,
        "probability_degraded_position_ids": (position_ids[0],),
        "restart_blocking_position_count": len(position_ids) - 1,
        "restart_blocking_position_ids": tuple(position_ids[1:]),
        "stale_classified_position_ids": tuple(position_ids),
        "missing_monitor_timestamp_position_ids": (),
        "invalid_monitor_timestamp_position_ids": (),
    }
    handoff.update(overrides)
    return handoff


def _fresh_failed_monitor_handoff(position_ids, **overrides):
    handoff = {
        "green": False,
        "open_position_count": len(position_ids),
        "fresh_position_count": 0,
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
        "quote_only_stale_position_count": 0,
        "quote_only_stale_shape_valid": True,
        "reauction_handoff_position_count": 0,
        "fresh_failed_monitor_no_action_position_count": len(position_ids),
        "fresh_failed_monitor_no_action_position_ids": tuple(position_ids),
        "fresh_failed_monitor_duplicate_position_ids": (),
        "fresh_failed_monitor_other_classified_position_ids": (),
        "fresh_failed_monitor_timestamp_stale_position_ids": (),
        "missing_monitor_timestamp_position_ids": (),
        "invalid_monitor_timestamp_position_ids": (),
    }
    handoff.update(overrides)
    return handoff


def test_deploy_live_loaded_restart_admits_fresh_failed_monitor_repair_handoff(
    monkeypatch, tmp_path
):
    """A current all-no-action monitor partition may restart into pending repair only."""
    dl = _load("deploy_live_restart_fresh_failed_monitor_admit", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    position_ids = ("pos-a", "pos-b")
    for position_id in position_ids:
        trade.execute(
            "INSERT INTO position_current VALUES (?, 'day0_window', 7, 7, 'synced')",
            (position_id,),
        )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(
        dl,
        "_pre_stop_monitor_handoff_evidence",
        lambda _trade_db: _fresh_failed_monitor_handoff(position_ids),
    )
    monkeypatch.setattr(
        dl,
        "_loaded_live_runtime_repair_pending",
        lambda: {"pending": True, "loaded_sha": "old", "current_head": "new"},
    )
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: {"current": True, "age_seconds": 1.0},
    )

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=True,
    )

    assert ok is True
    assert "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_ADMITTED" in detail
    assert "restart_permission_only=true" in detail


def test_deploy_live_loaded_restart_admits_mixed_fresh_repair_handoff(
    monkeypatch, tmp_path
):
    """Fresh actions plus typed no-actions may cover the whole paused portfolio."""
    dl = _load("deploy_live_restart_mixed_fresh_failed_monitor_admit", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    position_ids = ("pos-fresh", "pos-repair")
    for position_id in position_ids:
        trade.execute(
            "INSERT INTO position_current VALUES (?, 'day0_window', 7, 7, 'synced')",
            (position_id,),
        )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    handoff = _fresh_failed_monitor_handoff(
        position_ids,
        fresh_position_count=1,
        fresh_failed_monitor_no_action_position_count=1,
        fresh_failed_monitor_no_action_position_ids=("pos-repair",),
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl, "_pre_stop_monitor_handoff_evidence", lambda _db: handoff)
    monkeypatch.setattr(
        dl,
        "_loaded_live_runtime_repair_pending",
        lambda: {"pending": True, "loaded_sha": "old", "current_head": "new"},
    )
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: {"current": True, "age_seconds": 1.0},
    )

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=True,
    )

    assert ok is True
    assert "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_ADMITTED" in detail
    assert "fresh_actionable_positions=1" in detail


def test_deploy_live_pre_stop_handoff_classifies_current_all_no_action_failures(
    monkeypatch, tmp_path
):
    """Current probability+CLOB failures are a complete restart-only partition."""
    dl = _load("deploy_live_restart_fresh_failed_monitor_evidence", "deploy_live.py")
    trade_db = tmp_path / "zeus_trades.db"
    sqlite3.connect(trade_db).close()
    occurred_at = datetime.now(timezone.utc).isoformat()
    position_ids = ("pos-a", "pos-b")
    evidence = {
        "open_position_count": 2,
        "monitored_position_ids": list(position_ids),
        "fresh_position_count": 0,
        "stale_or_missing_position_count": 2,
        "stale_or_missing_positions": [
            {
                "position_id": position_id,
                "issue": "monitor_probability_and_clob_stale",
                "last_monitor_refreshed_at": occurred_at,
            }
            for position_id in position_ids
        ],
        "blocking_stale_position_count": 2,
        "blocking_stale_positions": [
            {
                "position_id": position_id,
                "issue": "monitor_probability_and_clob_stale",
                "last_monitor_refreshed_at": occurred_at,
            }
            for position_id in position_ids
        ],
        "quote_only_stale_position_count": 0,
        "quote_only_stale_positions": [],
        "probability_only_stale_position_count": 0,
        "probability_only_stale_positions": [],
        "settlement_recoverable_position_count": 0,
        "settlement_recoverable_positions": [],
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    monkeypatch.setattr(
        dl, "collect_monitor_cadence_evidence", lambda *_args, **_kwargs: evidence
    )

    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)

    assert handoff["green"] is False
    assert handoff["fresh_position_count"] == 0
    assert handoff["fresh_failed_monitor_no_action_position_ids"] == position_ids
    assert handoff["fresh_failed_monitor_timestamp_stale_position_ids"] == ()


def test_deploy_live_pre_stop_handoff_classifies_current_closed_market_no_action(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_restart_fresh_failed_closed_market", "deploy_live.py")
    trade_db = tmp_path / "zeus_trades.db"
    sqlite3.connect(trade_db).close()
    occurred_at = datetime.now(timezone.utc).isoformat()
    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["pos-closed"],
        "fresh_position_count": 0,
        "stale_or_missing_position_count": 0,
        "stale_or_missing_positions": [],
        "blocking_stale_position_count": 0,
        "blocking_stale_positions": [],
        "quote_only_stale_position_count": 0,
        "quote_only_stale_positions": [],
        "probability_only_stale_position_count": 0,
        "probability_only_stale_positions": [],
        "settlement_recoverable_position_count": 1,
        "settlement_recoverable_positions": [
            {
                "position_id": "pos-closed",
                "last_monitor_refreshed_at": occurred_at,
                "cadence_source": "MONITOR_REFRESHED_CLOSED_MARKET_PENDING_SETTLEMENT",
            }
        ],
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    monkeypatch.setattr(
        dl, "collect_monitor_cadence_evidence", lambda *_args, **_kwargs: evidence
    )

    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)

    assert handoff["fresh_failed_monitor_no_action_position_ids"] == ("pos-closed",)
    assert handoff["fresh_failed_monitor_timestamp_stale_position_ids"] == ()


def test_deploy_live_fresh_failed_handoff_rejects_closed_market_restart_duplicate(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_restart_fresh_failed_duplicate", "deploy_live.py")
    trade_db = tmp_path / "zeus_trades.db"
    sqlite3.connect(trade_db).close()
    occurred_at = datetime.now(timezone.utc).isoformat()
    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["pos-duplicate"],
        "fresh_position_count": 0,
        "stale_or_missing_position_count": 1,
        "stale_or_missing_positions": [],
        "blocking_stale_position_count": 1,
        "blocking_stale_positions": [
            {
                "position_id": "pos-duplicate",
                "issue": "monitor_probability_and_clob_stale",
                "last_monitor_refreshed_at": occurred_at,
            }
        ],
        "quote_only_stale_position_count": 0,
        "quote_only_stale_positions": [],
        "probability_only_stale_position_count": 0,
        "probability_only_stale_positions": [],
        "settlement_recoverable_position_count": 1,
        "settlement_recoverable_positions": [
            {
                "position_id": "pos-duplicate",
                "last_monitor_refreshed_at": occurred_at,
                "cadence_source": "MONITOR_REFRESHED_CLOSED_MARKET_PENDING_SETTLEMENT",
            }
        ],
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    monkeypatch.setattr(
        dl, "collect_monitor_cadence_evidence", lambda *_args, **_kwargs: evidence
    )

    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)
    monkeypatch.setattr(
        dl, "_held_quote_sidecar_current_evidence", lambda: {"current": True}
    )
    ok, detail = dl._fresh_failed_monitor_repair_handoff_admission(
        obligations={
            "open_position_count": 1,
            "all_open_position_ids": ("pos-duplicate",),
            "nonterminal_command_count": 0,
        },
        pause_state={"entries_paused": True},
        handoff=handoff,
        repair_pending={"pending": True},
    )

    assert handoff["fresh_failed_monitor_duplicate_position_ids"] == ("pos-duplicate",)
    assert ok is False
    assert detail == "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:no_action_partition_duplicate"


@pytest.mark.parametrize(
    ("loaded_sha", "current_sha", "expected_pending"),
    (
        ("a" * 40, "a" * 40, False),
        ("a" * 7, "a" * 40, False),
        ("b" * 7, "a" * 40, True),
    ),
)
def test_deploy_live_fresh_failed_repair_requires_loaded_sha_to_predate_head(
    monkeypatch, tmp_path, loaded_sha, current_sha, expected_pending
):
    dl = _load("deploy_live_restart_fresh_failed_repair_sha", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    (state / "loaded_sha.json").write_text(
        json.dumps(
            {
                "loaded_sha": loaded_sha,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl, "head_sha", lambda short=False: current_sha)

    result = dl._loaded_live_runtime_repair_pending()

    assert result["pending"] is expected_pending


@pytest.mark.parametrize(
    "mutation",
    (
        "bool_count",
        "negative_count",
        "nonsequence_ids",
        "nonstring_id",
        "nonmapping_handoff",
    ),
)
def test_deploy_live_fresh_failed_handoff_rejects_malformed_internal_evidence(
    monkeypatch, mutation
):
    dl = _load(f"deploy_live_restart_fresh_failed_shape_{mutation}", "deploy_live.py")
    position_ids = ("pos-a", "pos-b")
    obligations = {
        "open_position_count": 2,
        "all_open_position_ids": position_ids,
        "nonterminal_command_count": 0,
    }
    handoff = _fresh_failed_monitor_handoff(position_ids)
    if mutation == "bool_count":
        handoff["fresh_position_count"] = False
    elif mutation == "negative_count":
        handoff["fresh_position_count"] = -1
    elif mutation == "nonsequence_ids":
        handoff["fresh_failed_monitor_no_action_position_ids"] = "pos-a"
    elif mutation == "nonstring_id":
        handoff["fresh_failed_monitor_no_action_position_ids"] = ("pos-a", 7)
    elif mutation == "nonmapping_handoff":
        handoff = []
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: pytest.fail("malformed evidence must refuse before sidecar read"),
    )

    ok, detail = dl._fresh_failed_monitor_repair_handoff_admission(
        obligations=obligations,
        pause_state={"entries_paused": True},
        handoff=handoff,
        repair_pending={"pending": True},
    )

    assert ok is False
    assert detail == "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:handoff_evidence_invalid"


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    (
        ("partition_overflow", "open_no_action_partition_incomplete"),
        ("nonterminal_command", "nonterminal_commands"),
        ("unpaused", "durable_entries_pause_false"),
        ("missing_id", "open_no_action_partition_incomplete"),
        ("future_monitor", "future_monitor_evidence"),
        ("stale_timestamp", "monitor_timestamp_stale"),
        ("sidecar_stale", "held_quote_sidecar_stale"),
        ("partial_mix", "no_action_partition_not_disjoint"),
        ("repair_not_pending", "repair_code_not_pending"),
    ),
)
def test_deploy_live_fresh_failed_monitor_repair_handoff_refuses_boundaries(
    monkeypatch, mutation, expected_reason
):
    dl = _load(f"deploy_live_restart_fresh_failed_monitor_refuse_{mutation}", "deploy_live.py")
    position_ids = ("pos-a", "pos-b")
    obligations = {
        "open_position_count": 2,
        "all_open_position_ids": position_ids,
        "nonterminal_command_count": 0,
    }
    pause_state = {"entries_paused": True}
    handoff = _fresh_failed_monitor_handoff(position_ids)
    repair_pending = {"pending": True}
    quote_sidecar = {"current": True, "age_seconds": 1.0}
    if mutation == "partition_overflow":
        handoff["fresh_position_count"] = 1
    elif mutation == "nonterminal_command":
        obligations["nonterminal_command_count"] = 1
    elif mutation == "unpaused":
        pause_state["entries_paused"] = False
    elif mutation == "missing_id":
        handoff["fresh_failed_monitor_no_action_position_count"] = 1
        handoff["fresh_failed_monitor_no_action_position_ids"] = (position_ids[0],)
    elif mutation == "future_monitor":
        handoff["future_monitor_event_count"] = 1
    elif mutation == "stale_timestamp":
        handoff["fresh_failed_monitor_timestamp_stale_position_ids"] = (
            position_ids[0],
        )
    elif mutation == "sidecar_stale":
        quote_sidecar = {"current": False, "reason": "held_quote_sidecar_stale"}
    elif mutation == "partial_mix":
        handoff["fresh_failed_monitor_other_classified_position_ids"] = (
            position_ids[1],
        )
    elif mutation == "repair_not_pending":
        repair_pending = {"pending": False}
    monkeypatch.setattr(dl, "_held_quote_sidecar_current_evidence", lambda: quote_sidecar)

    ok, detail = dl._fresh_failed_monitor_repair_handoff_admission(
        obligations=obligations,
        pause_state=pause_state,
        handoff=handoff,
        repair_pending=repair_pending,
    )

    assert ok is False
    assert detail == f"FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:{expected_reason}"


def test_deploy_live_loaded_restart_admits_exact_stuck_monitor_recovery(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_restart_stuck_monitor_admit", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    position_ids = ("pos-a", "pos-b")
    for position_id in position_ids:
        trade.execute(
            "INSERT INTO position_current VALUES (?, 'day0_window', 7, 7, 'synced')",
            (position_id,),
        )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(
        dl,
        "_pre_stop_monitor_handoff_evidence",
        lambda _trade_db: _stuck_monitor_handoff(position_ids),
    )
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: {"current": True, "age_seconds": 1.0},
    )

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=True,
    )

    assert ok is True
    assert "STUCK_MONITOR_RECOVERY_ADMITTED" in detail
    assert "fresh_positions=0" in detail


def test_deploy_live_loaded_restart_admits_exact_total_stall_with_quote_only_partition(
    monkeypatch,
):
    dl = _load("deploy_live_restart_stuck_monitor_quote_only_admit", "deploy_live.py")
    position_ids = ("pos-probability", "pos-restart", "pos-quote")
    handoff = _stuck_monitor_handoff(
        position_ids,
        probability_degraded_position_count=1,
        probability_degraded_position_ids=("pos-probability",),
        restart_blocking_position_count=1,
        restart_blocking_position_ids=("pos-restart",),
        quote_only_stale_position_count=1,
        quote_only_stale_position_ids=("pos-quote",),
    )
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: {"current": True, "age_seconds": 1.0},
    )

    ok, detail = dl._stuck_monitor_recovery_admission(
        obligations={
            "open_position_count": len(position_ids),
            "all_open_position_ids": position_ids,
            "nonterminal_command_count": 0,
        },
        pause_state={"entries_paused": True},
        handoff=handoff,
    )

    assert ok is True
    assert "STUCK_MONITOR_RECOVERY_ADMITTED" in detail
    assert "quote_only_stale_positions=1" in detail


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    (
        ("quote_sidecar_stale", "held_quote_sidecar_stale"),
        ("nonterminal_command", "nonterminal_commands"),
        ("open_unclassified", "open_classification_incomplete"),
        ("future_monitor", "future_monitor_evidence"),
        ("partial_fresh", "partial_or_fresh_handoff"),
        ("quote_identity_overlap", "open_classification_incomplete"),
    ),
)
def test_deploy_live_stuck_monitor_recovery_refuses_each_non_total_stall_boundary(
    monkeypatch, mutation, expected_reason
):
    dl = _load(f"deploy_live_restart_stuck_monitor_refuse_{mutation}", "deploy_live.py")
    position_ids = ("pos-a", "pos-b")
    obligations = {
        "open_position_count": 2,
        "all_open_position_ids": position_ids,
        "nonterminal_command_count": 0,
    }
    handoff = _stuck_monitor_handoff(position_ids)
    quote_sidecar = {"current": True, "age_seconds": 1.0}
    if mutation == "quote_sidecar_stale":
        quote_sidecar = {"current": False, "reason": "held_quote_sidecar_stale"}
    elif mutation == "nonterminal_command":
        obligations["nonterminal_command_count"] = 1
    elif mutation == "open_unclassified":
        handoff["restart_blocking_position_count"] = 0
        handoff["restart_blocking_position_ids"] = ()
    elif mutation == "future_monitor":
        handoff["future_monitor_event_count"] = 1
    elif mutation == "partial_fresh":
        handoff["fresh_position_count"] = 1
    elif mutation == "quote_identity_overlap":
        handoff["quote_only_stale_position_count"] = 1
        handoff["quote_only_stale_position_ids"] = (position_ids[0],)
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: quote_sidecar,
    )

    ok, detail = dl._stuck_monitor_recovery_admission(
        obligations=obligations,
        pause_state={"entries_paused": True},
        handoff=handoff,
    )

    assert ok is False
    assert detail == f"STUCK_MONITOR_RECOVERY_REFUSED:{expected_reason}"


@pytest.mark.parametrize(
    ("timestamp", "field", "expected_reason"),
    (
        (None, "missing_monitor_timestamp_position_ids", "monitor_timestamp_missing"),
        ("not-a-time", "invalid_monitor_timestamp_position_ids", "monitor_timestamp_invalid"),
        ("2026-08-28T12:00:00", "invalid_monitor_timestamp_position_ids", "monitor_timestamp_invalid"),
    ),
)
def test_deploy_live_stuck_monitor_recovery_rejects_missing_invalid_or_naive_timestamp(
    monkeypatch, tmp_path, timestamp, field, expected_reason
):
    dl = _load(f"deploy_live_restart_timestamp_{expected_reason}", "deploy_live.py")
    trade_db = tmp_path / "zeus_trades.db"
    sqlite3.connect(trade_db).close()
    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["pos-open"],
        "fresh_position_count": 0,
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    groups = {
        "probability_only_stale_position_count": 1,
        "probability_only_stale_positions": [
            {
                "position_id": "pos-open",
                "issue": "monitor_probability_stale",
                "last_monitor_refreshed_at": timestamp,
            }
        ],
        "restart_blocking_stale_position_count": 0,
        "restart_blocking_stale_positions": [],
        "quote_only_stale_position_count": 0,
    }
    monkeypatch.setattr(dl, "collect_monitor_cadence_evidence", lambda *_a, **_k: evidence)
    monkeypatch.setattr(dl, "monitor_restart_blocking_evidence", lambda _e: groups)
    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: {"current": True, "age_seconds": 1.0},
    )

    ok, detail = dl._stuck_monitor_recovery_admission(
        obligations={
            "open_position_count": 1,
            "all_open_position_ids": ("pos-open",),
            "nonterminal_command_count": 0,
        },
        pause_state={"entries_paused": True},
        handoff=handoff,
    )

    assert handoff["stale_classified_position_ids"] == ()
    assert handoff[field] == ("pos-open",)
    assert ok is False
    assert detail == f"STUCK_MONITOR_RECOVERY_REFUSED:{expected_reason}"


@pytest.mark.parametrize(
    ("timestamp", "expected_ok", "expected_reason"),
    (
        (None, False, "monitor_timestamp_missing"),
        ("not-a-time", False, "monitor_timestamp_invalid"),
        ("stale", True, None),
        (
            "2026-08-28T12:00:00+00:00",
            False,
            "monitor_evidence_not_stale",
        ),
    ),
)
def test_deploy_live_stuck_monitor_quote_only_partition_requires_stale_parseable_timestamp(
    monkeypatch, tmp_path, timestamp, expected_ok, expected_reason
):
    dl = _load("deploy_live_restart_quote_only_timestamp", "deploy_live.py")
    trade_db = tmp_path / "zeus_trades.db"
    sqlite3.connect(trade_db).close()
    now = datetime.now(timezone.utc)
    if timestamp == "stale":
        timestamp = (
            now
            - timedelta(seconds=dl.LIVE_STUCK_MONITOR_RECOVERY_STALE_SECONDS + 1)
        ).isoformat()
    elif timestamp == "2026-08-28T12:00:00+00:00":
        timestamp = now.isoformat()
    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["pos-quote"],
        "fresh_position_count": 0,
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    groups = {
        "probability_only_stale_position_count": 0,
        "probability_only_stale_positions": [],
        "restart_blocking_stale_position_count": 0,
        "restart_blocking_stale_positions": [],
        "quote_only_stale_position_count": 1,
        "quote_only_stale_positions": [
            {
                "position_id": "pos-quote",
                "issue": "monitor_clob_stale",
                "last_monitor_refreshed_at": timestamp,
            }
        ],
    }
    monkeypatch.setattr(dl, "collect_monitor_cadence_evidence", lambda *_a, **_k: evidence)
    monkeypatch.setattr(dl, "monitor_restart_blocking_evidence", lambda _e: groups)
    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: {"current": True, "age_seconds": 1.0},
    )

    ok, detail = dl._stuck_monitor_recovery_admission(
        obligations={
            "open_position_count": 1,
            "all_open_position_ids": ("pos-quote",),
            "nonterminal_command_count": 0,
        },
        pause_state={"entries_paused": True},
        handoff=handoff,
    )

    assert handoff["quote_only_stale_position_ids"] == ("pos-quote",)
    assert ok is expected_ok
    if expected_ok:
        assert "STUCK_MONITOR_RECOVERY_ADMITTED" in detail
    else:
        assert detail == f"STUCK_MONITOR_RECOVERY_REFUSED:{expected_reason}"


@pytest.mark.parametrize(
    ("count", "records"),
    (
        (
            0,
            [
                {
                    "position_id": "pos-quote",
                    "issue": "monitor_clob_stale",
                    "last_monitor_refreshed_at": "2026-08-28T11:00:00+00:00",
                }
            ],
        ),
        (
            2,
            [
                {
                    "position_id": "pos-quote",
                    "issue": "monitor_clob_stale",
                    "last_monitor_refreshed_at": "2026-08-28T11:00:00+00:00",
                },
                {
                    "position_id": "pos-quote",
                    "issue": "monitor_clob_stale",
                    "last_monitor_refreshed_at": "2026-08-28T11:00:00+00:00",
                },
            ],
        ),
        (
            1,
            [
                {
                    "position_id": "pos-quote",
                    "issue": "monitor_probability_stale",
                    "last_monitor_refreshed_at": "2026-08-28T11:00:00+00:00",
                }
            ],
        ),
        (1, ["not-a-record"]),
    ),
)
def test_deploy_live_stuck_monitor_quote_only_records_require_exact_shape(
    monkeypatch, tmp_path, count, records
):
    dl = _load("deploy_live_restart_quote_only_shape", "deploy_live.py")
    trade_db = tmp_path / "zeus_trades.db"
    sqlite3.connect(trade_db).close()
    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["pos-quote"],
        "fresh_position_count": 0,
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    groups = {
        "probability_only_stale_position_count": 0,
        "probability_only_stale_positions": [],
        "restart_blocking_stale_position_count": 0,
        "restart_blocking_stale_positions": [],
        "quote_only_stale_position_count": count,
        "quote_only_stale_positions": records,
    }
    monkeypatch.setattr(dl, "collect_monitor_cadence_evidence", lambda *_a, **_k: evidence)
    monkeypatch.setattr(dl, "monitor_restart_blocking_evidence", lambda _e: groups)
    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: {"current": True, "age_seconds": 1.0},
    )

    ok, detail = dl._stuck_monitor_recovery_admission(
        obligations={
            "open_position_count": 1,
            "all_open_position_ids": ("pos-quote",),
            "nonterminal_command_count": 0,
        },
        pause_state={"entries_paused": True},
        handoff=handoff,
    )

    assert handoff["quote_only_stale_shape_valid"] is False
    assert ok is False
    assert detail == "STUCK_MONITOR_RECOVERY_REFUSED:quote_only_shape_invalid"


def test_deploy_live_normal_green_handoff_does_not_use_stuck_monitor_recovery(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_restart_normal_handoff_unchanged", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    trade.execute(
        "INSERT INTO position_current VALUES ('pos-open', 'day0_window', 7, 7, 'synced')"
    )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(
        dl,
        "_pre_stop_monitor_handoff_evidence",
        lambda _trade_db: {
            "green": True,
            "open_position_count": 1,
            "probability_degraded_position_count": 0,
            "reauction_handoff_position_count": 0,
        },
    )
    monkeypatch.setattr(
        dl,
        "_held_quote_sidecar_current_evidence",
        lambda: pytest.fail("normal green handoff must not enter stuck recovery"),
    )

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=True,
    )

    assert ok is True
    assert "repair handoff verified" in detail


def test_deploy_live_waits_for_post_sidecar_handoff_recovery(monkeypatch):
    dl = _load("deploy_live_restart_handoff_wait", "deploy_live.py")
    outcomes = iter(
        [
            (False, "restart_blocking_stale_position_count=1"),
            (True, "loaded live-trading repair handoff verified"),
        ]
    )
    calls = []
    monkeypatch.setattr(
        dl,
        "_loaded_live_restart_obligation_gate",
        lambda labels, *, live_was_loaded: (
            calls.append((tuple(labels), live_was_loaded)) or next(outcomes)
        ),
    )
    monkeypatch.setattr(dl.time, "sleep", lambda _seconds: None)

    ok, detail = dl._wait_for_loaded_live_restart_handoff(
        [dl.LIVE_TRADING_LABEL],
        timeout_seconds=5,
    )

    assert ok is True
    assert "handoff verified" in detail
    assert calls == [
        ((dl.LIVE_TRADING_LABEL,), True),
        ((dl.LIVE_TRADING_LABEL,), True),
    ]


@pytest.mark.parametrize(
    ("issue", "probability_only_count", "expected_green"),
    (
        ("monitor_probability_stale", 1, True),
        ("monitor_probability_and_clob_stale", 0, False),
    ),
)
def test_deploy_live_pre_stop_handoff_requires_fresh_held_quote(
    monkeypatch, tmp_path, issue, probability_only_count, expected_green
):
    dl = _load(f"deploy_live_restart_handoff_{issue}", "deploy_live.py")
    trade_db = tmp_path / "zeus_trades.db"
    sqlite3.connect(trade_db).close()
    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["pos-open"],
        "fresh_position_count": 0,
        "stale_or_missing_position_count": 1,
        "stale_or_missing_positions": [
            {"position_id": "pos-open", "issue": issue}
        ],
        "blocking_stale_position_count": 1,
        "blocking_stale_positions": [
            {"position_id": "pos-open", "issue": issue}
        ],
        "quote_only_stale_position_count": 0,
        "quote_only_stale_positions": [],
        "probability_only_stale_position_count": probability_only_count,
        "probability_only_stale_positions": (
            [{"position_id": "pos-open", "issue": issue}]
            if probability_only_count
            else []
        ),
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    monkeypatch.setattr(
        dl,
        "collect_monitor_cadence_evidence",
        lambda *_args, **_kwargs: evidence,
    )

    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)

    assert handoff["green"] is expected_green


@pytest.mark.parametrize(
    ("mutation", "expected_green"),
    (
        ("valid", True),
        ("wrong_request_marker", False),
        ("wrong_token", False),
        ("expired_deadline", False),
        ("stale_probability", False),
        ("invalid_schema", False),
    ),
)
def test_deploy_live_pre_stop_handoff_requires_exact_v4_reauction_debt(
    monkeypatch,
    tmp_path,
    mutation,
    expected_green,
):
    dl = _load(f"deploy_live_v4_reauction_handoff_{mutation}", "deploy_live.py")
    trade_db = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(trade_db)
    conn.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            direction TEXT,
            token_id TEXT,
            no_token_id TEXT
        );
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    now = datetime.now(timezone.utc)
    occurred_at = now.isoformat()
    request_id = "request-exact-v4"
    held_token_id = "held-no-token"
    obligation = {
        "schema_version": 4,
        "position_id": "pos-open",
        "held_token_id": held_token_id,
        "request_id": request_id,
        "book_state": "EXECUTABLE",
        "held_best_bid": 0.72,
        "probability_content_identity": "q-current",
        "completion_deadline_at": (now + timedelta(seconds=60)).isoformat(),
    }
    validations = [
        "global_auction_completion_request_failed",
        "global_auction_completion_debt:REQUEST_REJECTED",
        "GLOBAL_REAUCTION_PENDING",
        f"global_auction_completion_request_id:{request_id}",
    ]
    probability_fresh = True
    if mutation == "wrong_request_marker":
        validations[-1] = "global_auction_completion_request_id:other"
    elif mutation == "wrong_token":
        obligation["held_token_id"] = "other-token"
    elif mutation == "expired_deadline":
        obligation["completion_deadline_at"] = (
            now - timedelta(seconds=1)
        ).isoformat()
    elif mutation == "stale_probability":
        probability_fresh = False
    elif mutation == "invalid_schema":
        obligation["schema_version"] = "four"
    conn.execute(
        "INSERT INTO position_current VALUES (?, ?, ?, ?)",
        ("pos-open", "buy_no", "yes-token", held_token_id),
    )
    conn.execute(
        "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "pos-open:monitor_refreshed:1",
            "pos-open",
            1,
            "MONITOR_REFRESHED",
            occurred_at,
            json.dumps(
                {
                    "last_monitor_prob_is_fresh": probability_fresh,
                    "last_monitor_market_price_is_fresh": True,
                    "held_sell_reauction_obligation": obligation,
                    "applied_validations": validations,
                },
                sort_keys=True,
            ),
        ),
    )
    conn.commit()
    conn.close()
    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["pos-open"],
        "fresh_position_count": 0,
        "stale_or_missing_position_count": 1,
        "stale_or_missing_positions": [
            {
                "position_id": "pos-open",
                "issue": "monitor_exit_completion_unavailable",
                "last_monitor_refreshed_at": occurred_at,
            }
        ],
        "blocking_stale_position_count": 1,
        "blocking_stale_positions": [
            {
                "position_id": "pos-open",
                "issue": "monitor_exit_completion_unavailable",
                "last_monitor_refreshed_at": occurred_at,
            }
        ],
        "quote_only_stale_position_count": 0,
        "quote_only_stale_positions": [],
        "probability_only_stale_position_count": 0,
        "probability_only_stale_positions": [],
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    monkeypatch.setattr(
        dl,
        "collect_monitor_cadence_evidence",
        lambda *_args, **_kwargs: evidence,
    )

    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)

    assert handoff["green"] is expected_green
    assert handoff["reauction_handoff_position_count"] == int(expected_green)
    assert handoff["restart_blocking_position_count"] == int(not expected_green)


@pytest.mark.parametrize(
    ("mutation", "expected_green"),
    (
        ("valid", True),
        ("missing_lineage", False),
        ("wrong_token", False),
        ("wrong_family", False),
        ("stale_quote", False),
        ("out_of_band_current_bid", False),
        ("missing_current_lineage", False),
    ),
)
def test_deploy_live_pre_stop_handoff_pairs_fresh_monitor_with_v4_lineage(
    monkeypatch,
    tmp_path,
    mutation,
    expected_green,
):
    from src.runtime import reactor_wake

    dl = _load(
        f"deploy_live_v4_lineage_reauction_handoff_{mutation}",
        "deploy_live.py",
    )
    state = tmp_path / "state"
    state.mkdir()
    trade_db = state / "zeus_trades.db"
    conn = sqlite3.connect(trade_db)
    conn.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            direction TEXT,
            token_id TEXT,
            no_token_id TEXT
        );
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    now = datetime.now(timezone.utc)
    occurred_at = now.isoformat()
    held_token_id = "held-no-token"
    conn.execute(
        "INSERT INTO position_current VALUES (?, ?, ?, ?)",
        ("pos-open", "buy_no", "yes-token", held_token_id),
    )
    current_lineage = {
        "selection_epoch_identity": "selection-current",
        "sell_book_witness_identity": "book-current",
    }
    if mutation == "missing_current_lineage":
        current_lineage["sell_book_witness_identity"] = ""
    latest_payload = {
        "city": "Paris",
        "target_date": "2026-08-28",
        "metric": "low",
        "last_monitor_prob": 0.64,
        "last_monitor_prob_is_fresh": True,
        "last_monitor_best_bid": (
            0.99 if mutation == "out_of_band_current_bid" else 0.72
        ),
        "last_monitor_market_price_is_fresh": mutation != "stale_quote",
        "held_sell_full_depth_action_authority": True,
        "held_sell_reauction_monitor_lineage": current_lineage,
        "monitor_probability_receipt": {
            "probability_content_identity": "q-current"
        },
        "applied_validations": [
            "sell_reversal",
            "global_auction_completion_request_failed",
            "global_auction_completion_debt:REQUEST_REJECTED",
            "GLOBAL_REAUCTION_PENDING",
        ],
    }
    conn.execute(
        "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "pos-open:monitor_refreshed:2",
            "pos-open",
            2,
            "MONITOR_REFRESHED",
            occurred_at,
            json.dumps(latest_payload, sort_keys=True),
        ),
    )
    conn.commit()
    conn.close()

    if mutation != "missing_lineage":
        request = reactor_wake.make_held_sell_reauction_request(
            position_id="pos-open",
            family=(
                "London" if mutation == "wrong_family" else "Paris",
                "2026-08-28",
                "low",
            ),
            probability_content_identity="q-prior-attempt",
            held_token_id=(
                "other-token" if mutation == "wrong_token" else held_token_id
            ),
            held_best_bid=0.71,
            bid_observed_at=(now - timedelta(seconds=30)).isoformat(),
            probability_observed_at=(now - timedelta(seconds=30)).isoformat(),
            completion_deadline_at=(now - timedelta(seconds=1)).isoformat(),
            selection_epoch_identity="selection-prior",
            sell_book_witness_identity="book-prior",
            debt_event_id="pos-open:monitor_refreshed:1",
            monitor_event_id="pos-open:monitor_refreshed:1",
            schema_version=4,
        )
        reactor_wake.publish_reactor_wake(
            source="test",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=state / reactor_wake.REACTOR_WAKE_FILENAME,
            held_sell_reauction_requests=(request,),
        )

    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["pos-open"],
        "fresh_position_count": 0,
        "stale_or_missing_position_count": 1,
        "stale_or_missing_positions": [
            {
                "position_id": "pos-open",
                "issue": "monitor_exit_completion_unavailable",
                "last_monitor_refreshed_at": occurred_at,
            }
        ],
        "blocking_stale_position_count": 1,
        "blocking_stale_positions": [
            {
                "position_id": "pos-open",
                "issue": "monitor_exit_completion_unavailable",
                "last_monitor_refreshed_at": occurred_at,
            }
        ],
        "quote_only_stale_position_count": 0,
        "quote_only_stale_positions": [],
        "probability_only_stale_position_count": 0,
        "probability_only_stale_positions": [],
        "future_monitor_event_count": 0,
        "non_monitor_chain_risk_position_count": 0,
    }
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(
        dl,
        "collect_monitor_cadence_evidence",
        lambda *_args, **_kwargs: evidence,
    )

    handoff = dl._pre_stop_monitor_handoff_evidence(trade_db)

    assert handoff["green"] is expected_green
    assert handoff["reauction_handoff_position_count"] == int(expected_green)
    assert handoff["restart_blocking_position_count"] == int(not expected_green)


def test_deploy_live_loaded_restart_refuses_unpaused_monitor_handoff(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_restart_unpaused_handoff", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state, paused=False)
    trade.execute(
        "INSERT INTO position_current VALUES ('pos-open', 'day0_window', 7, 7, 'synced')"
    )
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(
        dl,
        "_pre_stop_monitor_handoff_evidence",
        lambda _trade_db: pytest.fail("unpaused restart must refuse before handoff"),
    )

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=True,
    )

    assert ok is False
    assert "durable_entries_pause=false" in detail


def test_deploy_live_loaded_restart_blocks_nonterminal_command_without_position(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_restart_nonterminal_command", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    trade.execute("INSERT INTO venue_commands VALUES ('buy-live', 'BUY', 'ACKED')")
    world.close()
    trade.commit()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=True,
    )

    assert ok is False
    assert "nonterminal_commands=1" in detail
    assert "buy-live" in detail


def test_deploy_live_absent_daemon_bootstrap_restores_monitoring_with_exposure(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_restart_absent_recovery", "deploy_live.py")
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._loaded_live_restart_obligation_gate(
        [dl.LIVE_TRADING_LABEL],
        live_was_loaded=False,
    )

    assert ok is True
    assert "absent-daemon recovery" in detail


def test_deploy_live_command_refuses_before_entry_pause_when_capital_is_open(
    monkeypatch, capsys
):
    dl = _load("deploy_live_restart_refusal_order", "deploy_live.py")
    monkeypatch.setattr(dl, "_gate", lambda *_args: (True, []))
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda _label: True)
    monkeypatch.setattr(
        dl,
        "_loaded_live_restart_obligation_gate",
        lambda *_args, **_kwargs: (False, "open_positions=1"),
    )
    monkeypatch.setattr(
        dl,
        "_pause_entries_with_stuck_live_recovery",
        lambda *_args, **_kwargs: pytest.fail("pause must not run before refusal"),
    )

    rc = dl._cmd_restart_locked(
        types.SimpleNamespace(
            daemon="live-trading",
            allow_dirty=False,
            allow_unpushed=False,
        )
    )

    assert rc == 1
    assert "continuous monitoring" in capsys.readouterr().out


def test_deploy_live_paused_entry_backlog_ignores_generic_global_auction_marker(
    monkeypatch, tmp_path
):
    from src.runtime.reactor_wake import (
        GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        REACTOR_WAKE_FILENAME,
        publish_reactor_wake,
    )

    dl = _load("deploy_live_paused_entry_auction_debt", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    publish_reactor_wake(
        source="test",
        reason=GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=state / REACTOR_WAKE_FILENAME,
    )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is True
    assert "held_sell_global_auction_debt=0" in detail


def test_deploy_live_paused_entry_backlog_rejects_any_queued_exact_held_sell_debt(
    monkeypatch, tmp_path
):
    from src.runtime import reactor_wake

    dl = _load("deploy_live_paused_entry_exact_held_debt", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    wake_path, completed_wake, completed_request = _publish_exact_held_sell_wake(
        state, position_id="pos-completed"
    )
    _wake_path, outstanding_wake, outstanding_request = _publish_exact_held_sell_wake(
        state, position_id="pos-outstanding"
    )
    completed_receipt = reactor_wake.HeldSellReauctionReceipt(
        request_id=completed_request.request_id,
        material_identity=completed_request.material_identity,
        generation=completed_request.generation,
        status=reactor_wake.POSITION_NO_LONGER_EXPOSED,
        reason=reactor_wake.SELL_OBLIGATION_ENDED_BY_CANONICAL_CHAIN_ZERO,
        lifecycle_phase="economically_closed",
        chain_state="chain_confirmed_zero",
        chain_shares=0.0,
        schema_version=completed_request.schema_version,
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (completed_receipt,), path=wake_path
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (completed_request,), path=wake_path)
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (outstanding_request,), path=wake_path)
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=wake_path) == {
        completed_wake.wake_id,
        outstanding_wake.wake_id,
    }
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert "expected_parked=held_sell_global_auction_debt=2" in detail


@pytest.mark.parametrize(
    ("surface", "expected"),
    (("malformed_legacy", "EDLI_EXPECTED_PARKED_WAKE_SURFACE_INVALID"),
     ("malformed_queue", "EDLI_EXPECTED_PARKED_WAKE_SURFACE_INVALID"),
     ("unreadable", "EDLI_EXPECTED_PARKED_WAKE_SURFACE_UNREADABLE")),
)
def test_deploy_live_paused_entry_backlog_rejects_invalid_wake_surface(
    monkeypatch, tmp_path, surface, expected
):
    dl = _load("deploy_live_paused_entry_invalid_wake", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world, trade = _init_paused_entry_park_authority(state)
    _insert_claimable_edli_entry_backlog(world)
    world.commit()
    trade.commit()
    world.close()
    trade.close()
    wake_path = state / "edli-reactor-wake.json"
    if surface == "malformed_legacy":
        wake_path.write_text("{not-json", encoding="utf-8")
    elif surface == "malformed_queue":
        queue_dir = state / "edli-reactor-wake.json.d"
        queue_dir.mkdir()
        (queue_dir / "malformed.json").write_text("{not-json", encoding="utf-8")
    else:
        from src.runtime import reactor_wake

        reactor_wake.publish_reactor_wake(
            source="test",
            reason="forecast_posterior_advanced",
            path=wake_path,
        )

        def unreadable_wake(*_args, **_kwargs):
            raise OSError("test unreadable wake")

        monkeypatch.setattr(
            reactor_wake, "_read_reactor_wake_path", unreadable_wake
        )
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert expected in detail


def test_deploy_live_paused_entry_backlog_rejects_unknown_pause_authority(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_paused_entry_unknown_pause", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world = _init_edli_queue_db(state / "zeus-world.db")
    _insert_claimable_edli_entry_backlog(world)
    world.commit()
    world.close()
    trade = sqlite3.connect(state / "zeus_trades.db")
    trade.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY, phase TEXT,
            chain_shares REAL, chain_state TEXT
        );
        CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, side TEXT, state TEXT);
        """
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=datetime.now(timezone.utc),
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert "EDLI_EXPECTED_PARKED_PAUSE_UNREADABLE:missing_table" in detail


def test_deploy_live_monitor_wait_budget_covers_runtime_coverage_contract(
    monkeypatch,
):
    monkeypatch.delenv(
        "ZEUS_DEPLOY_LIVE_MONITOR_CADENCE_VERIFY_TIMEOUT_SECONDS",
        raising=False,
    )
    dl = _load("deploy_live_monitor_wait_budget", "deploy_live.py")

    assert dl.LIVE_MONITOR_FULL_COVERAGE_CYCLES == 3
    assert dl.LIVE_MONITOR_CADENCE_CONTRACT_SECONDS == 3 * 2 * 60
    assert (
        dl.LIVE_MONITOR_CADENCE_VERIFY_TIMEOUT_SECONDS
        >= dl.LIVE_MONITOR_CADENCE_CONTRACT_SECONDS
        + dl.LIVE_MONITOR_CADENCE_VERIFY_GRACE_SECONDS
    )


def test_deploy_live_post_start_edli_queue_wait_rejects_stale_processing_claim(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_edli_queue_wait_stale", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world_db = state / "zeus-world.db"
    launched = datetime.now(timezone.utc)
    conn = _init_edli_queue_db(world_db)
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count,
            claimed_at, processed_at, last_error, updated_at
        ) VALUES ('edli_reactor_v1', 'evt-stale', 'processing', 1, ?, NULL, NULL, ?)
        """,
        (
            (launched - timedelta(minutes=20)).isoformat(),
            (launched - timedelta(minutes=20)).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))
    monkeypatch.setattr(dl.time, "sleep", lambda _seconds: None)

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=launched,
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is False
    assert "stale_processing=1" in detail
    assert "oldest_stale_claimed_at" in detail


def test_deploy_live_post_start_edli_queue_wait_accepts_reclaimed_claim(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_edli_queue_wait_reclaimed", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world_db = state / "zeus-world.db"
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    conn = _init_edli_queue_db(world_db)
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count,
            claimed_at, processed_at, last_error, updated_at
        ) VALUES ('edli_reactor_v1', 'evt-reclaimed', 'processing', 2, ?, NULL, NULL, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=launched,
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is True
    assert "post-start EDLI queue progress verified" in detail


def test_deploy_live_post_start_edli_queue_wait_accepts_complete_auction_receipt(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_edli_queue_wait_auction", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    launched = datetime.now(timezone.utc) - timedelta(seconds=1)
    world = _init_edli_queue_db(state / "zeus-world.db")
    world.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count,
            claimed_at, processed_at, last_error, updated_at
        ) VALUES ('edli_reactor_v1', 'evt-paused', 'pending', 1, NULL, NULL, NULL, ?)
        """,
        (launched.isoformat(),),
    )
    world.commit()
    world.close()
    trade = sqlite3.connect(state / "zeus_trades.db")
    trade.execute(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY,
            mode TEXT,
            started_at TEXT,
            completed_at TEXT,
            artifact_json TEXT
        )
        """
    )
    completed = datetime.now(timezone.utc)
    artifact = {
        "mode": "global_single_order_auction",
        "started_at": launched.isoformat(),
        "completed_at": completed.isoformat(),
        "summary": {
            "candidate_coverage_complete": True,
            "scope_family_coverage_complete": True,
            "candidate_evaluation_count": 42,
            "full_scope_family_count": 4,
        },
    }
    trade.execute(
        "INSERT INTO decision_log VALUES (7, ?, ?, ?, ?)",
        (
            "global_single_order_auction",
            launched.isoformat(),
            completed.isoformat(),
            json.dumps(artifact),
        ),
    )
    trade.commit()
    trade.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=launched,
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is True
    assert "auction_receipt=7" in detail
    assert "candidates=42" in detail
    assert "scope_families=4" in detail


def test_deploy_live_post_start_edli_queue_wait_skips_future_retry_floor(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_edli_queue_wait_future_retry", "deploy_live.py")
    state = tmp_path / "state"
    state.mkdir()
    world_db = state / "zeus-world.db"
    launched = datetime.now(timezone.utc)
    conn = _init_edli_queue_db(world_db)
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count,
            claimed_at, processed_at, last_error, updated_at
        ) VALUES ('edli_reactor_v1', 'evt-future', 'pending', 1, ?, NULL, NULL, ?)
        """,
        (
            (launched + timedelta(minutes=10)).isoformat(),
            launched.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "LIVE_REPO", str(tmp_path))

    ok, detail = dl._wait_for_post_start_edli_queue_progress(
        launched_after=launched,
        post_start_freshness_verified=True,
        timeout_seconds=0,
    )

    assert ok is True
    assert "no claimable reactor work" in detail


def test_deploy_live_live_restart_runs_recovery_before_preflight(monkeypatch, capsys):
    dl = _load("deploy_live_restart_order_live", "deploy_live.py")
    calls = []
    pause_expected_shas = []
    live_head = {"sha": "c" * 40}

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty, allow_unpushed=False: (True, []))

    def _head_sha(short=True):
        captured = live_head["sha"]
        live_head["sha"] = "f" * 40
        return captured

    monkeypatch.setattr(dl, "head_sha", _head_sha)
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda label: True)

    def _stop(label):
        calls.append(("stop", label))
        return True, f"stopped {label}"

    def _preflight(labels):
        calls.append(("preflight", tuple(labels)))
        return True, "live restart preflight passed"

    def _recovery(labels):
        calls.append(("recovery", tuple(labels)))
        return True, "live restart recovery passed"

    def _pause(labels, **kwargs):
        calls.append(("pause_entries", tuple(labels)))
        pause_expected_shas.append(kwargs["expected_sha"])
        assert live_head["sha"] == "f" * 40
        return True, "live restart entry pause guard armed"

    def _launch(label):
        calls.append(("launch", label))
        return True, f"bootstrapped {label}"

    def _verify(**kwargs):
        calls.append(("verify", kwargs["expected_sha"][:8]))
        return True, "live runtime freshness verified"

    def _prerequisite(labels, **kwargs):
        calls.append(("prerequisite", tuple(labels)))
        return True, "sidecar code identity verified"

    def _handoff(labels, **kwargs):
        calls.append(("pre_stop_handoff", tuple(labels)))
        return True, "loaded live-trading repair handoff verified"

    def _monitor(**kwargs):
        calls.append(("monitor", "post-start"))
        return True, "post-start monitor cadence verified"

    def _queue(**kwargs):
        calls.append(("queue", kwargs["post_start_freshness_verified"]))
        return True, "post-start EDLI queue progress verified"

    def _resume(labels):
        calls.append(("resume_entries", tuple(labels)))
        return True, "verified live restart entry posture"

    monkeypatch.setattr(dl, "_stop_label", _stop)
    monkeypatch.setattr(dl, "_pause_entries_for_live_restart_if_needed", _pause)
    monkeypatch.setattr(dl, "_run_restart_recovery_if_needed", _recovery)
    monkeypatch.setattr(dl, "_run_restart_preflight_if_needed", _preflight)
    monkeypatch.setattr(dl, "_launch_or_restart_label", _launch)
    monkeypatch.setattr(dl, "_wait_for_prerequisite_code_identity", _prerequisite)
    monkeypatch.setattr(dl, "_wait_for_loaded_live_restart_handoff", _handoff)
    monkeypatch.setattr(dl, "_wait_for_live_runtime_fresh", _verify)
    monkeypatch.setattr(dl, "_wait_for_post_start_edli_queue_progress", _queue)
    monkeypatch.setattr(dl, "_wait_for_post_start_monitor_cadence", _monitor)
    monkeypatch.setattr(
        dl,
        "_resume_entries_after_verified_live_restart_if_needed",
        _resume,
    )
    monkeypatch.setattr(
        dl,
        "_live_restart_exclusive_lock",
        contextlib.nullcontext,
    )

    rc = dl.main(["restart", "live-trading"])

    assert rc == 0
    expanded_labels = [*dl.LIVE_TRADING_PREREQUISITE_LABELS, dl.LIVE_TRADING_LABEL]
    heartbeat_supervisor = dl.DAEMONS["venue-heartbeat"]
    preflight_prerequisites = tuple(
        label
        for label in dl.LIVE_TRADING_PREREQUISITE_LABELS
        if label != heartbeat_supervisor
    )
    assert calls == [
        ("pause_entries", tuple(expanded_labels)),
        *[("launch", label) for label in preflight_prerequisites],
        ("prerequisite", preflight_prerequisites),
        ("pre_stop_handoff", tuple(expanded_labels)),
        ("stop", dl.LIVE_TRADING_LABEL),
        ("stop", heartbeat_supervisor),
        *[("stop", label) for label in preflight_prerequisites],
        ("recovery", tuple(expanded_labels)),
        *[("launch", label) for label in preflight_prerequisites],
        ("prerequisite", preflight_prerequisites),
        ("preflight", tuple(expanded_labels)),
        ("launch", dl.LIVE_TRADING_LABEL),
        ("verify", "cccccccc"),
        ("launch", heartbeat_supervisor),
        ("monitor", "post-start"),
        ("queue", True),
        ("resume_entries", tuple(expanded_labels)),
    ]
    assert pause_expected_shas == ["c" * 40]
    assert "live restart preflight passed" in capsys.readouterr().out


def test_deploy_live_current_migrations_keep_main_until_warm_preflight(monkeypatch):
    dl = _load("deploy_live_continuous_monitor_cutover", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_gate", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "c" * 40)
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda _label: True)
    monkeypatch.setattr(
        dl,
        "_loaded_live_restart_obligation_gate",
        lambda *_args, **_kwargs: (True, "continuous handoff admitted"),
    )
    monkeypatch.setattr(
        dl,
        "_pause_entries_for_live_restart_if_needed",
        lambda *_args, **_kwargs: (True, "pause armed"),
    )
    monkeypatch.setattr(
        dl,
        "_restart_migration_targets_current",
        lambda: (True, "migrations current"),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_prerequisite_code_identity",
        lambda labels, **_kwargs: (
            calls.append(("prerequisite", tuple(labels))) or (True, "ready")
        ),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_loaded_live_restart_handoff",
        lambda labels: (calls.append(("handoff", tuple(labels))) or (True, "fresh")),
    )

    def preflight(labels, **kwargs):
        calls.append(
            (
                "preflight",
                kwargs.get("expected_live_process_state", "absent"),
                kwargs.get("process_state_only", False),
            )
        )
        return True, "preflight passed"

    monkeypatch.setattr(dl, "_run_restart_preflight_if_needed", preflight)
    monkeypatch.setattr(
        dl,
        "_run_restart_recovery_with_quiesced_prerequisites",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("current migration fast path must skip quiesced recovery")
        ),
    )
    monkeypatch.setattr(
        dl,
        "_launch_or_restart_label",
        lambda label: (calls.append(("launch", label)) or (True, "launched")),
    )
    monkeypatch.setattr(
        dl,
        "_stop_label",
        lambda label: (calls.append(("stop", label)) or (True, "stopped")),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_live_runtime_fresh",
        lambda **_kwargs: (calls.append(("runtime",)) or (True, "fresh")),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_post_start_monitor_cadence",
        lambda **_kwargs: (calls.append(("monitor",)) or (True, "monitor")),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_post_start_edli_queue_progress",
        lambda **_kwargs: (calls.append(("queue",)) or (True, "queue")),
    )
    monkeypatch.setattr(
        dl,
        "_resume_entries_after_verified_live_restart_if_needed",
        lambda _labels: (calls.append(("resume",)) or (True, "resumed")),
    )
    monkeypatch.setattr(dl, "_live_restart_exclusive_lock", contextlib.nullcontext)

    assert dl.main(["restart", "live-trading"]) == 0

    warm = calls.index(("preflight", "running", False))
    handoff = next(i for i, call in enumerate(calls) if call[0] == "handoff")
    one_main = calls.index(("preflight", "running", True))
    stop_main = calls.index(("stop", dl.LIVE_TRADING_LABEL))
    zero_main = calls.index(("preflight", "absent", True))
    launch_main = calls.index(("launch", dl.LIVE_TRADING_LABEL))
    assert warm < handoff < one_main < stop_main < zero_main < launch_main


def test_deploy_live_failed_zero_main_witness_never_bootstraps_second_main(
    monkeypatch,
):
    dl = _load("deploy_live_zero_main_fail_closed", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_gate", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "d" * 40)
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda _label: True)
    monkeypatch.setattr(
        dl,
        "_loaded_live_restart_obligation_gate",
        lambda *_args, **_kwargs: (True, "admitted"),
    )
    monkeypatch.setattr(
        dl,
        "_pause_entries_for_live_restart_if_needed",
        lambda *_args, **_kwargs: (True, "pause armed"),
    )
    monkeypatch.setattr(dl, "_restart_migration_targets_current", lambda: (True, "current"))
    monkeypatch.setattr(
        dl,
        "_wait_for_prerequisite_code_identity",
        lambda *_args, **_kwargs: (True, "ready"),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_loaded_live_restart_handoff",
        lambda *_args, **_kwargs: (True, "fresh"),
    )

    def preflight(_labels, **kwargs):
        state = kwargs.get("expected_live_process_state", "absent")
        calls.append(("preflight", state))
        return (state == "running", "witness")

    monkeypatch.setattr(dl, "_run_restart_preflight_if_needed", preflight)
    monkeypatch.setattr(
        dl,
        "_launch_or_restart_label",
        lambda label: (calls.append(("launch", label)) or (True, "launched")),
    )
    monkeypatch.setattr(
        dl,
        "_stop_label",
        lambda label: (calls.append(("stop", label)) or (True, "stopped")),
    )
    monkeypatch.setattr(dl, "_live_restart_exclusive_lock", contextlib.nullcontext)

    assert dl.main(["restart", "live-trading"]) == 1
    stop_main = calls.index(("stop", dl.LIVE_TRADING_LABEL))
    assert ("preflight", "absent") in calls[stop_main + 1 :]
    assert ("launch", dl.LIVE_TRADING_LABEL) not in calls


def test_restart_migration_ledger_uses_primary_root_not_checkout_state(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_migration_primary_root", "deploy_live.py")
    live_repo = tmp_path / "checkout"
    primary_root = tmp_path / "runtime"
    (live_repo / "state").mkdir(parents=True)
    (primary_root / "state").mkdir(parents=True)
    monkeypatch.setattr(dl, "LIVE_REPO", str(live_repo))
    monkeypatch.setattr(
        dl,
        "_live_trading_subprocess_env",
        lambda: {"ZEUS_PRIMARY_ROOT": str(primary_root)},
    )

    for filename, targets in (
        ("zeus-world.db", dl.RESTART_WORLD_MIGRATION_TARGETS),
        ("zeus_trades.db", dl.RESTART_TRADE_MIGRATION_TARGETS),
    ):
        conn = sqlite3.connect(primary_root / "state" / filename)
        conn.execute(
            "CREATE TABLE _migrations_applied (name TEXT PRIMARY KEY, applied_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO _migrations_applied VALUES (?, 'now')",
            [(target,) for _key, target in targets],
        )
        conn.commit()
        conn.close()

    ok, detail = dl._restart_migration_targets_current()

    assert ok is True
    assert str(primary_root / "state") in detail


def test_restart_runtime_relative_overrides_resolve_from_live_repo(
    monkeypatch, tmp_path
):
    dl = _load("deploy_live_relative_runtime_paths", "deploy_live.py")
    live_repo = tmp_path / "checkout"
    live_repo.mkdir()
    monkeypatch.setattr(dl, "LIVE_REPO", str(live_repo))

    monkeypatch.setattr(
        dl,
        "_live_trading_subprocess_env",
        lambda: {"ZEUS_STATE_DIR": "runtime-state"},
    )
    assert dl._restart_runtime_db_paths() == (
        (live_repo / "runtime-state" / "zeus-world.db").resolve(),
        (live_repo / "runtime-state" / "zeus_trades.db").resolve(),
    )

    monkeypatch.setattr(
        dl,
        "_live_trading_subprocess_env",
        lambda: {
            "ZEUS_WORLD_DB": "db/world.sqlite",
            "ZEUS_TRADE_DB": "db/trade.sqlite",
        },
    )
    assert dl._restart_runtime_db_paths() == (
        (live_repo / "db" / "world.sqlite").resolve(),
        (live_repo / "db" / "trade.sqlite").resolve(),
    )


def test_deploy_live_projection_recovery_failure_restores_paused_monitoring(
    monkeypatch,
):
    dl = _load("deploy_live_projection_recovery_failure", "deploy_live.py")
    stops: list[str] = []
    launches: list[str] = []

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty, allow_unpushed=False: (True, []))
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "d" * 40)
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda label: True)
    monkeypatch.setattr(
        dl,
        "_pause_entries_for_live_restart_if_needed",
        lambda labels, **_kwargs: (True, "pause armed"),
    )
    monkeypatch.setattr(
        dl,
        "_stop_label",
        lambda label: (stops.append(label) or (True, f"stopped {label}")),
    )
    monkeypatch.setattr(
        dl,
        "_run_restart_recovery_if_needed",
        lambda labels: (False, "EDLI_BACKFILL_RECEIPT_COPY_COUNT_MISMATCH"),
    )
    monkeypatch.setattr(
        dl,
        "_launch_or_restart_label",
        lambda label: (launches.append(label) or (True, f"launched {label}")),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_prerequisite_code_identity",
        lambda labels, **kwargs: (True, "prerequisites verified"),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_live_runtime_fresh",
        lambda **_kwargs: (True, "runtime verified"),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_post_start_monitor_cadence",
        lambda **_kwargs: (True, "held monitor verified"),
    )
    monkeypatch.setattr(dl, "_live_restart_exclusive_lock", contextlib.nullcontext)

    assert dl.main(["restart", "live-trading"]) == 1
    prerequisites = [
        label
        for label in dl.LIVE_TRADING_PREREQUISITE_LABELS
        if label != dl.DAEMONS["venue-heartbeat"]
    ]
    assert stops == [
        dl.LIVE_TRADING_LABEL,
        dl.DAEMONS["venue-heartbeat"],
        *prerequisites,
    ]
    assert launches == [
        *prerequisites,
        *prerequisites,
        dl.LIVE_TRADING_LABEL,
        dl.DAEMONS["venue-heartbeat"],
    ]


def test_deploy_live_starts_heartbeat_before_monitor_and_stops_after_failure(
    monkeypatch,
):
    dl = _load("deploy_live_heartbeat_before_monitor", "deploy_live.py")
    calls = []
    monkeypatch.setattr(
        dl, "_gate", lambda allow_dirty, allow_unpushed=False: (True, [])
    )
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "e" * 40)
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda label: True)
    monkeypatch.setattr(
        dl,
        "_pause_entries_for_live_restart_if_needed",
        lambda labels, **_kwargs: (True, "pause armed"),
    )
    monkeypatch.setattr(dl, "_stop_label", lambda label: (True, f"stopped {label}"))
    monkeypatch.setattr(
        dl, "_run_restart_recovery_if_needed", lambda labels: (True, "recovered")
    )
    monkeypatch.setattr(
        dl, "_run_restart_preflight_if_needed", lambda labels: (True, "preflight")
    )
    monkeypatch.setattr(
        dl,
        "_launch_or_restart_label",
        lambda label: (calls.append(("launch", label)) or (True, f"launched {label}")),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_prerequisite_code_identity",
        lambda labels, **kwargs: (True, "prerequisites"),
    )
    monkeypatch.setattr(
        dl, "_wait_for_live_runtime_fresh", lambda **kwargs: (True, "runtime")
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_post_start_monitor_cadence",
        lambda **kwargs: (
            calls.append(("monitor", "failed"))
            or (False, "monitor failed")
        ),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_post_start_edli_queue_progress",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("queue wait cannot repair a failed monitor proof")
        ),
    )
    monkeypatch.setattr(dl, "_live_restart_exclusive_lock", contextlib.nullcontext)

    assert dl.main(["restart", "live-trading"]) == 1
    heartbeat = ("launch", dl.DAEMONS["venue-heartbeat"])
    assert calls.index(heartbeat) < calls.index(("monitor", "failed"))


def test_deploy_live_restart_pause_guard_is_indefinite_control_plane(monkeypatch, tmp_path):
    dl = _load("deploy_live_restart_pause_guard_indefinite", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_require_live_repo", lambda: str(tmp_path))
    monkeypatch.setattr(dl, "_live_trading_subprocess_env", lambda: {})
    monkeypatch.setattr(dl, "head_sha", lambda short=False: "a" * 40)

    class Result:
        returncode = 0
        stdout = "entries pause guard armed\n"
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setattr(dl.subprocess, "run", fake_run)

    ok, detail = dl._pause_entries_for_live_restart_if_needed([dl.LIVE_TRADING_LABEL])

    assert ok is True
    assert "entries pause guard armed" in detail
    assert calls
    code = calls[-1][0][2]
    assert "deploy_live_restart_guard" in code
    assert "arm_deploy_live_restart_guard" in code
    assert "entries pause guard preserved" in code
    assert "effective_until=None" in code
    assert "system_auto_pause" not in code


def test_deploy_live_restart_pause_stops_lock_stuck_live_before_retry(monkeypatch):
    dl = _load("deploy_live_restart_pause_stuck_writer", "deploy_live.py")
    calls = []
    outcomes = iter(
        (
            (False, "live restart entry pause guard could not run: timed out after 30s"),
            (True, "live restart entry pause guard armed"),
        )
    )

    def pause(labels):
        calls.append(("pause", tuple(labels)))
        return next(outcomes)

    def stop(label):
        calls.append(("stop", label))
        return True, f"stopped {label}"

    def wait_unloaded(label):
        calls.append(("wait_unloaded", label))
        return True

    monkeypatch.setattr(dl, "_pause_entries_for_live_restart_if_needed", pause)
    monkeypatch.setattr(dl, "_stop_label", stop)
    monkeypatch.setattr(dl, "_wait_for_launchctl_unloaded", wait_unloaded)
    labels = [dl.LIVE_TRADING_LABEL]

    ok, detail = dl._pause_entries_with_stuck_live_recovery(
        labels,
        live_was_loaded=True,
    )

    assert ok is True
    assert calls == [
        ("pause", tuple(labels)),
        ("stop", dl.LIVE_TRADING_LABEL),
        ("wait_unloaded", dl.LIVE_TRADING_LABEL),
        ("pause", tuple(labels)),
    ]
    assert "pause guard retry after requested daemon absence" in detail


def test_deploy_live_restart_pause_stops_requested_writer_sidecars_before_retry(
    monkeypatch,
):
    dl = _load("deploy_live_restart_pause_stuck_sidecar_writer", "deploy_live.py")
    calls = []
    outcomes = iter(
        (
            (False, "live restart entry pause guard could not run: timed out after 30s"),
            (True, "live restart entry pause guard armed"),
        )
    )

    def pause(labels):
        calls.append(("pause", tuple(labels)))
        return next(outcomes)

    def stop(label):
        calls.append(("stop", label))
        return True, f"stopped {label}"

    def wait_unloaded(label):
        calls.append(("wait_unloaded", label))
        return True

    monkeypatch.setattr(dl, "_pause_entries_for_live_restart_if_needed", pause)
    monkeypatch.setattr(dl, "_stop_label", stop)
    monkeypatch.setattr(dl, "_wait_for_launchctl_unloaded", wait_unloaded)
    labels = [
        dl.DAEMONS["data-ingest"],
        dl.LIVE_TRADING_LABEL,
        dl.DAEMONS["price-channel-ingest"],
    ]

    ok, detail = dl._pause_entries_with_stuck_live_recovery(
        labels,
        live_was_loaded=False,
    )

    assert ok is True
    assert calls == [
        ("pause", tuple(labels)),
        ("stop", dl.LIVE_TRADING_LABEL),
        ("wait_unloaded", dl.LIVE_TRADING_LABEL),
        ("stop", dl.DAEMONS["data-ingest"]),
        ("wait_unloaded", dl.DAEMONS["data-ingest"]),
        ("stop", dl.DAEMONS["price-channel-ingest"]),
        ("wait_unloaded", dl.DAEMONS["price-channel-ingest"]),
        ("pause", tuple(labels)),
    ]
    assert "live-trading was already absent" in detail
    assert "pause guard retry after requested daemon absence" in detail


def test_deploy_live_restart_pause_does_not_retry_before_scoped_unload(
    monkeypatch,
):
    dl = _load("deploy_live_restart_pause_unload_timeout", "deploy_live.py")
    calls = []

    def pause(labels):
        calls.append(("pause", tuple(labels)))
        return False, "live restart entry pause guard could not run: timed out after 30s"

    def stop(label):
        calls.append(("stop", label))
        return True, f"stopped {label}"

    def wait_unloaded(label):
        calls.append(("wait_unloaded", label))
        return label == dl.LIVE_TRADING_LABEL

    monkeypatch.setattr(dl, "_pause_entries_for_live_restart_if_needed", pause)
    monkeypatch.setattr(dl, "_stop_label", stop)
    monkeypatch.setattr(dl, "_wait_for_launchctl_unloaded", wait_unloaded)
    labels = [
        dl.DAEMONS["data-ingest"],
        dl.LIVE_TRADING_LABEL,
        dl.DAEMONS["price-channel-ingest"],
    ]

    ok, detail = dl._pause_entries_with_stuck_live_recovery(
        labels,
        live_was_loaded=True,
    )

    assert ok is False
    assert calls == [
        ("pause", tuple(labels)),
        ("stop", dl.LIVE_TRADING_LABEL),
        ("wait_unloaded", dl.LIVE_TRADING_LABEL),
        ("stop", dl.DAEMONS["data-ingest"]),
        ("wait_unloaded", dl.DAEMONS["data-ingest"]),
    ]
    assert "FAILED unload wait" in detail


def test_deploy_live_restart_pause_preserves_existing_operator_pause(monkeypatch, tmp_path):
    dl = _load("deploy_live_restart_pause_guard_preserve_operator", "deploy_live.py")

    monkeypatch.setattr(dl, "_require_live_repo", lambda: str(tmp_path))
    monkeypatch.setattr(dl, "_live_trading_subprocess_env", lambda: {})
    monkeypatch.setattr(dl, "head_sha", lambda short=False: "a" * 40)

    control_mod = types.ModuleType("src.control.control_plane")
    control_mod.arm_deploy_live_restart_guard = lambda **_kwargs: {
        "status": "preserved",
        "reason": "operator_investigation",
        "issued_by": "control_plane",
    }
    monkeypatch.setitem(sys.modules, "src.control.control_plane", control_mod)

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(args[2], {})
        return Result(out.getvalue())

    monkeypatch.setattr(dl.subprocess, "run", fake_run)

    ok, detail = dl._pause_entries_for_live_restart_if_needed([dl.LIVE_TRADING_LABEL])

    assert ok is True
    assert "entries pause guard preserved" in detail
    assert "operator_investigation" in detail


def test_deploy_live_verified_restart_clears_only_its_control_plane_guard(
    monkeypatch,
    tmp_path,
):
    dl = _load("deploy_live_restart_resume_exact_guard", "deploy_live.py")

    monkeypatch.setattr(dl, "_require_live_repo", lambda: str(tmp_path))
    monkeypatch.setattr(dl, "_live_trading_subprocess_env", lambda: {})

    control_mod = types.ModuleType("src.control.control_plane")
    control_mod.recover_deploy_live_restart_guard = lambda: {"status": "reset"}
    monkeypatch.setitem(sys.modules, "src.control.control_plane", control_mod)

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **_kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(args[2], {})
        return Result(out.getvalue())

    monkeypatch.setattr(dl.subprocess, "run", fake_run)

    ok, detail = dl._resume_entries_after_verified_live_restart_if_needed(
        [dl.LIVE_TRADING_LABEL]
    )

    assert ok is True
    assert "restart guard cleared" in detail


def test_deploy_live_verified_restart_preserves_non_deploy_pause(monkeypatch, tmp_path):
    dl = _load("deploy_live_restart_resume_preserves_operator", "deploy_live.py")

    monkeypatch.setattr(dl, "_require_live_repo", lambda: str(tmp_path))
    monkeypatch.setattr(dl, "_live_trading_subprocess_env", lambda: {})

    class Result:
        returncode = 0
        stdout = (
            "entries pause guard preserved after deploy: "
            "issued_by=operator reason=operator_investigation\n"
        )
        stderr = ""

    monkeypatch.setattr(dl.subprocess, "run", lambda *_args, **_kwargs: Result())

    ok, detail = dl._resume_entries_after_verified_live_restart_if_needed(
        [dl.LIVE_TRADING_LABEL]
    )

    assert ok is True
    assert "operator_investigation" in detail


def test_deploy_live_all_restarts_sidecars_before_live_preflight(monkeypatch):
    dl = _load("deploy_live_restart_order_all", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty, allow_unpushed=False: (True, []))
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "d" * 40)
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda label: True)
    monkeypatch.setattr(
        dl,
        "_pause_entries_for_live_restart_if_needed",
        lambda labels, **_kwargs: (calls.append(("pause_entries", tuple(labels))) or (True, "pause ok")),
    )
    monkeypatch.setattr(
        dl,
        "_stop_label",
        lambda label: (calls.append(("stop", label)) or (True, f"stopped {label}")),
    )
    monkeypatch.setattr(
        dl,
        "_run_restart_recovery_if_needed",
        lambda labels: (calls.append(("recovery", tuple(labels))) or (True, "recovery ok")),
    )
    monkeypatch.setattr(
        dl,
        "_run_restart_preflight_if_needed",
        lambda labels: (calls.append(("preflight", tuple(labels))) or (True, "preflight ok")),
    )
    monkeypatch.setattr(
        dl,
        "_launch_or_restart_label",
        lambda label: (calls.append(("launch", label)) or (True, f"bootstrapped {label}")),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_prerequisite_code_identity",
        lambda labels, **kwargs: (
            calls.append(("prerequisite", tuple(labels))) or (True, "prerequisites verified")
        ),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_live_runtime_fresh",
        lambda **kwargs: (calls.append(("verify", kwargs["expected_sha"][:8])) or (True, "verified")),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_post_start_edli_queue_progress",
        lambda **kwargs: (
            calls.append(("queue", kwargs["post_start_freshness_verified"]))
            or (True, "queue verified")
        ),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_post_start_monitor_cadence",
        lambda **kwargs: (calls.append(("monitor", "post-start")) or (True, "monitor verified")),
    )
    monkeypatch.setattr(
        dl,
        "_live_restart_exclusive_lock",
        contextlib.nullcontext,
    )
    monkeypatch.setattr(
        dl,
        "_resume_entries_after_verified_live_restart_if_needed",
        lambda labels: (calls.append(("resume_entries", tuple(labels))) or (True, "resumed")),
    )

    rc = dl.main(["restart", "all"])

    assert rc == 0
    assert calls[0] == ("pause_entries", tuple(dl.DAEMONS.values()))
    stop_index = calls.index(("stop", dl.LIVE_TRADING_LABEL))
    recovery_index = calls.index(("recovery", tuple(dl.DAEMONS.values())))
    preflight_index = calls.index(("preflight", tuple(dl.DAEMONS.values())))
    prerequisite_index = calls.index(
        (
            "prerequisite",
            tuple(
                label
                for label in dl.DAEMONS.values()
                if label not in {dl.LIVE_TRADING_LABEL, dl.DAEMONS["venue-heartbeat"]}
            ),
        )
    )
    assert prerequisite_index < stop_index < recovery_index < preflight_index
    live_launch_index = calls.index(("launch", dl.LIVE_TRADING_LABEL))
    assert live_launch_index > preflight_index
    heartbeat_launch_index = calls.index(("launch", dl.DAEMONS["venue-heartbeat"]))
    assert calls.index(("verify", "dddddddd")) < heartbeat_launch_index
    assert heartbeat_launch_index < calls.index(("monitor", "post-start"))
    assert calls.index(("verify", "dddddddd")) > live_launch_index
    assert calls.index(("queue", True)) > calls.index(("verify", "dddddddd"))
    assert calls.index(("monitor", "post-start")) > calls.index(("verify", "dddddddd"))
    assert calls.index(("monitor", "post-start")) < calls.index(("queue", True))
    preflight_launches = [
        call for call in calls[:stop_index]
        if call[0] == "launch"
    ]
    assert {label for _, label in preflight_launches} == {
        label
        for label in dl.DAEMONS.values()
        if label not in {dl.LIVE_TRADING_LABEL, dl.DAEMONS["venue-heartbeat"]}
    }


def test_deploy_live_preflight_failure_restores_paused_held_monitoring(
    monkeypatch,
    capsys,
):
    dl = _load("deploy_live_restart_preflight_failure", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty, allow_unpushed=False: (True, []))
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "d" * 40)
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda label: True)
    monkeypatch.setattr(
        dl,
        "_pause_entries_for_live_restart_if_needed",
        lambda labels, **_kwargs: (calls.append(("pause_entries", tuple(labels))) or (True, "pause ok")),
    )
    monkeypatch.setattr(
        dl,
        "_stop_label",
        lambda label: (calls.append(("stop", label)) or (True, f"stopped {label}")),
    )
    monkeypatch.setattr(
        dl,
        "_run_restart_recovery_if_needed",
        lambda labels: (calls.append(("recovery", tuple(labels))) or (True, "recovery ok")),
    )
    monkeypatch.setattr(
        dl,
        "_run_restart_preflight_if_needed",
        lambda labels: (calls.append(("preflight", tuple(labels))) or (False, "not green")),
    )
    monkeypatch.setattr(
        dl,
        "_launch_or_restart_label",
        lambda label: (calls.append(("launch", label)) or (True, f"bootstrapped {label}")),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_prerequisite_code_identity",
        lambda labels, **kwargs: (
            calls.append(("prerequisite", tuple(labels))) or (True, "prerequisites verified")
        ),
    )
    monkeypatch.setattr(
        dl,
        "_live_restart_exclusive_lock",
        contextlib.nullcontext,
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_live_runtime_fresh",
        lambda **kwargs: (
            calls.append(("verify", kwargs["expected_sha"][:8]))
            or (True, "runtime verified")
        ),
    )
    monkeypatch.setattr(
        dl,
        "_wait_for_post_start_monitor_cadence",
        lambda **_kwargs: (
            calls.append(("monitor", "paused-recovery"))
            or (True, "held monitor verified")
        ),
    )

    rc = dl.main(["restart", "live-trading"])

    assert rc == 1
    expanded_labels = [*dl.LIVE_TRADING_PREREQUISITE_LABELS, dl.LIVE_TRADING_LABEL]
    heartbeat_supervisor = dl.DAEMONS["venue-heartbeat"]
    preflight_prerequisites = tuple(
        label
        for label in dl.LIVE_TRADING_PREREQUISITE_LABELS
        if label != heartbeat_supervisor
    )
    assert calls == [
        ("pause_entries", tuple(expanded_labels)),
        *[("launch", label) for label in preflight_prerequisites],
        ("prerequisite", preflight_prerequisites),
        ("stop", dl.LIVE_TRADING_LABEL),
        ("stop", heartbeat_supervisor),
        *[("stop", label) for label in preflight_prerequisites],
        ("recovery", tuple(expanded_labels)),
        *[("launch", label) for label in preflight_prerequisites],
        ("prerequisite", preflight_prerequisites),
        ("preflight", tuple(expanded_labels)),
        ("launch", dl.LIVE_TRADING_LABEL),
        ("launch", heartbeat_supervisor),
        ("verify", "dddddddd"),
        ("monitor", "paused-recovery"),
    ]
    err = capsys.readouterr().err
    assert "paused live monitoring restored" in err
    assert "deploy entry guard remains armed" in err
    assert "live-trading left stopped" not in err


def test_deploy_live_restart_lock_excludes_watchdog_shared_lease(
    monkeypatch,
    tmp_path,
):
    dl = _load("deploy_live_restart_flock", "deploy_live.py")
    monkeypatch.setattr(
        dl,
        "_live_restart_lock_path",
        lambda: tmp_path / "deploy-live-restart.lock",
    )

    with dl._live_restart_exclusive_lock():
        fd = dl.os.open(
            dl._live_restart_lock_path(),
            dl.os.O_RDWR | dl.os.O_CREAT,
            0o644,
        )
        try:
            with pytest.raises(BlockingIOError):
                dl.fcntl.flock(
                    fd,
                    dl.fcntl.LOCK_SH | dl.fcntl.LOCK_NB,
                )
        finally:
            dl.os.close(fd)


def test_deploy_live_unknown_daemon_rejected(capsys):
    dl = _load("deploy_live_smoke3", "deploy_live.py")
    rc = dl.main(["restart", "no-such-daemon"])
    assert rc == 2  # unknown daemon, never reaches kickstart


# --------------------------------------------------------------------------
# gen_schema_cheatsheet
# --------------------------------------------------------------------------
def test_gen_schema_cheatsheet_on_temp_db(tmp_path, capsys):
    """Generator runs read-only over a temp DB and renders table names + types."""
    gsc = _load("gen_schema_smoke", "generate_schema_cheatsheet.py")
    db = tmp_path / "mini.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT, qty REAL)")
    conn.executemany("INSERT INTO widgets (name, qty) VALUES (?, ?)",
                     [("a", 1.0), ("b", 2.0), ("c", 3.0)])
    conn.commit()
    conn.close()
    # Repoint the DB list at the temp DB and render.
    gsc.DBS = [("mini.db", str(db))]
    content = gsc.build()
    assert "# Zeus live-DB schema cheatsheet" in content
    assert "## mini.db" in content
    assert "**widgets**" in content
    assert "name:TEXT" in content and "qty:REAL" in content
    assert "rows≈3" in content  # exact small-table count

    # row_estimate skips >1M tables (synthetic: patch threshold low).
    conn = sqlite3.connect(str(db))
    gsc.ROWCOUNT_SKIP_THRESHOLD = 1  # force the skip branch
    assert gsc.row_estimate(conn, "widgets") == "-"
    conn.close()


def test_gen_schema_cheatsheet_without_rowid_table(tmp_path):
    """WITHOUT ROWID tables hit the bounded-COUNT fallback, not '?'.

    `SELECT max(rowid)` raises on a WITHOUT ROWID table (no such column) —
    the fallback must catch that and produce a real count.
    """
    gsc = _load("gen_schema_smoke3", "generate_schema_cheatsheet.py")
    db = tmp_path / "wr.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID")
    conn.executemany("INSERT INTO kv VALUES (?, ?)", [("a", "1"), ("b", "2")])
    conn.commit()
    assert gsc.row_estimate(conn, "kv") == "2"
    conn.close()


def test_gen_schema_cheatsheet_handles_missing_db(tmp_path):
    """A missing DB renders an ERR line, does not raise."""
    gsc = _load("gen_schema_smoke2", "generate_schema_cheatsheet.py")
    gsc.DBS = [("ghost.db", str(tmp_path / "nope.db"))]
    content = gsc.build()
    assert "## ghost.db" in content
    assert "ERR" in content


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
