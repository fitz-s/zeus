# Lifecycle: created=2026-06-12; last_reviewed=2026-07-16; last_reused=2026-07-16
# Purpose: light smoke coverage for the three new ops scripts (zeus_status,
#   deploy_live, generate_schema_cheatsheet).
# Reuse: asserts the FAIL-SOFT contract (a locked/empty/missing DB degrades one
#   section to ERR, the rest still render) and that each script runs read-only
#   against temp DBs. No live DB is touched.
# Last reused/audited: 2026-07-16
# Authority basis: operator big-direction 2026-06-12 ("大方向现在也只是添加几个文件现在做")
"""Smoke tests for scripts/zeus_status.py, deploy_live.py, generate_schema_cheatsheet.py."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"


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
    assert "init_schema_trade_only" in calls[0][2]
    assert "get_trade_connection(write_class='live')" in calls[0][2]
    assert "get_world_connection_with_trades_required(write_class='live')" in calls[0][2]
    assert "get_trade_connection_with_world_required(write_class='live')" not in calls[0][2]
    assert "append_rest_filled_orphan_trade_facts_to_edli" in calls[0][2]


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
            occurred_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO position_current VALUES ('pos-1', 'active', 1.0, 1.0)"
    )
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at
        ) VALUES (1, 'pos-1', 'MONITOR_REFRESHED', ?)
        """,
        (datetime.now(timezone.utc).isoformat(),),
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


def test_deploy_live_accepts_post_start_typed_review_management(monkeypatch, tmp_path):
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

    assert ok is True
    assert "post-start monitor cadence verified" in detail


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
            occurred_at TEXT
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
            occurred_at TEXT
        )
        """
    )
    conn.execute("INSERT INTO position_current VALUES ('pos-1', 'active', 1.0, 1.0)")
    conn.execute("INSERT INTO position_current VALUES ('pos-2', 'active', 1.0, 1.0)")
    conn.execute(
        """
        INSERT INTO position_events (
            sequence_no, position_id, event_type, occurred_at
        ) VALUES (1, 'pos-1', 'MONITOR_REFRESHED', ?)
        """,
        (datetime.now(timezone.utc).isoformat(),),
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
        timeout_seconds=0,
    )

    assert ok is True
    assert "no claimable reactor work" in detail


def test_deploy_live_live_restart_runs_recovery_before_preflight(monkeypatch, capsys):
    dl = _load("deploy_live_restart_order_live", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty, allow_unpushed=False: (True, []))
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "c" * 40)
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

    def _pause(labels):
        calls.append(("pause_entries", tuple(labels)))
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

    def _monitor(**kwargs):
        calls.append(("monitor", "post-start"))
        return True, "post-start monitor cadence verified"

    def _queue(**kwargs):
        calls.append(("queue", "post-start"))
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
        ("stop", dl.LIVE_TRADING_LABEL),
        *[("stop", label) for label in dl.LIVE_TRADING_PREREQUISITE_LABELS],
        ("recovery", tuple(expanded_labels)),
        *[("launch", label) for label in preflight_prerequisites],
        ("prerequisite", preflight_prerequisites),
        ("preflight", tuple(expanded_labels)),
        ("launch", dl.LIVE_TRADING_LABEL),
        ("verify", "cccccccc"),
        ("queue", "post-start"),
        ("monitor", "post-start"),
        ("launch", heartbeat_supervisor),
        ("resume_entries", tuple(expanded_labels)),
    ]
    assert "live restart preflight passed" in capsys.readouterr().out


def test_deploy_live_restart_pause_guard_is_indefinite_control_plane(monkeypatch, tmp_path):
    dl = _load("deploy_live_restart_pause_guard_indefinite", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_require_live_repo", lambda: str(tmp_path))
    monkeypatch.setattr(dl, "_live_trading_subprocess_env", lambda: {})

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
    code = calls[0][0][2]
    assert "deploy_live_restart_guard" in code
    assert "entries pause guard preserved" in code
    assert "issued_by IN ('control_plane', 'operator')" in code
    assert "issued_by='control_plane'" in code
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

    monkeypatch.setattr(dl, "_pause_entries_for_live_restart_if_needed", pause)
    monkeypatch.setattr(dl, "_stop_label", stop)
    labels = [dl.LIVE_TRADING_LABEL]

    ok, detail = dl._pause_entries_with_stuck_live_recovery(
        labels,
        live_was_loaded=True,
    )

    assert ok is True
    assert calls == [
        ("pause", tuple(labels)),
        ("stop", dl.LIVE_TRADING_LABEL),
        ("pause", tuple(labels)),
    ]
    assert "pause guard retry after process absence" in detail


def test_deploy_live_restart_pause_preserves_existing_operator_pause(monkeypatch, tmp_path):
    dl = _load("deploy_live_restart_pause_guard_preserve_operator", "deploy_live.py")
    pause_calls = []
    sql_calls = []

    monkeypatch.setattr(dl, "_require_live_repo", lambda: str(tmp_path))
    monkeypatch.setattr(dl, "_live_trading_subprocess_env", lambda: {})

    control_mod = types.ModuleType("src.control.control_plane")
    state_db_mod = types.ModuleType("src.state.db")

    def fake_pause_entries(*args, **kwargs):
        pause_calls.append((args, kwargs))

    class _Cursor:
        def fetchone(self):
            return ("operator_investigation", "control_plane", "2026-07-03T00:00:00+00:00")

    class _Conn:
        def execute(self, sql, params=()):
            sql_calls.append((sql, params))
            return _Cursor()

        def close(self):
            return None

    control_mod.pause_entries = fake_pause_entries
    state_db_mod.get_world_connection = lambda: _Conn()
    monkeypatch.setitem(sys.modules, "src.control.control_plane", control_mod)
    monkeypatch.setitem(sys.modules, "src.state.db", state_db_mod)

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
    assert pause_calls == []
    assert sql_calls
    assert "effective_until IS NULL" in sql_calls[0][0]


def test_deploy_live_verified_restart_clears_only_its_control_plane_guard(
    monkeypatch,
    tmp_path,
):
    dl = _load("deploy_live_restart_resume_exact_guard", "deploy_live.py")
    resume_calls = []

    monkeypatch.setattr(dl, "_require_live_repo", lambda: str(tmp_path))
    monkeypatch.setattr(dl, "_live_trading_subprocess_env", lambda: {})

    control_mod = types.ModuleType("src.control.control_plane")
    state_db_mod = types.ModuleType("src.state.db")
    control_mod.resume_entries = lambda *args, **kwargs: resume_calls.append((args, kwargs))

    class _Cursor:
        def fetchone(self):
            return ("deploy_live_restart_guard", "control_plane")

    class _Conn:
        def execute(self, _sql, _params=()):
            return _Cursor()

        def close(self):
            return None

    state_db_mod.get_world_connection = lambda: _Conn()
    monkeypatch.setitem(sys.modules, "src.control.control_plane", control_mod)
    monkeypatch.setitem(sys.modules, "src.state.db", state_db_mod)

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
    assert resume_calls == [
        (
            ("deploy_live_restart_guard_verified_runtime_queue_monitor",),
            {"issued_by": "control_plane"},
        )
    ]


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
        lambda labels: (calls.append(("pause_entries", tuple(labels))) or (True, "pause ok")),
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
        lambda **kwargs: (calls.append(("queue", "post-start")) or (True, "queue verified")),
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
    assert stop_index < recovery_index
    assert recovery_index < prerequisite_index < preflight_index
    live_launch_index = calls.index(("launch", dl.LIVE_TRADING_LABEL))
    assert live_launch_index > preflight_index
    heartbeat_launch_index = calls.index(("launch", dl.DAEMONS["venue-heartbeat"]))
    assert heartbeat_launch_index > calls.index(("monitor", "post-start"))
    assert calls.index(("verify", "dddddddd")) > live_launch_index
    assert calls.index(("queue", "post-start")) > calls.index(("verify", "dddddddd"))
    assert calls.index(("monitor", "post-start")) > calls.index(("verify", "dddddddd"))
    preflight_launches = [
        call for call in calls[recovery_index:preflight_index]
        if call[0] == "launch"
    ]
    assert {label for _, label in preflight_launches} == {
        label
        for label in dl.DAEMONS.values()
        if label not in {dl.LIVE_TRADING_LABEL, dl.DAEMONS["venue-heartbeat"]}
    }


def test_deploy_live_preflight_failure_leaves_live_stopped(monkeypatch, capsys):
    dl = _load("deploy_live_restart_preflight_failure", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty, allow_unpushed=False: (True, []))
    monkeypatch.setattr(dl, "_launchctl_service_loaded", lambda label: True)
    monkeypatch.setattr(
        dl,
        "_pause_entries_for_live_restart_if_needed",
        lambda labels: (calls.append(("pause_entries", tuple(labels))) or (True, "pause ok")),
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
        ("stop", dl.LIVE_TRADING_LABEL),
        *[("stop", label) for label in dl.LIVE_TRADING_PREREQUISITE_LABELS],
        ("recovery", tuple(expanded_labels)),
        *[("launch", label) for label in preflight_prerequisites],
        ("prerequisite", preflight_prerequisites),
        ("preflight", tuple(expanded_labels)),
    ]
    err = capsys.readouterr().err
    assert "live-trading left stopped" in err


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
