# Lifecycle: created=2026-06-12; last_reviewed=2026-06-12; last_reused=2026-06-29
# Purpose: light smoke coverage for the three new ops scripts (zeus_status,
#   deploy_live, generate_schema_cheatsheet).
# Reuse: asserts the FAIL-SOFT contract (a locked/empty/missing DB degrades one
#   section to ERR, the rest still render) and that each script runs read-only
#   against temp DBs. No live DB is touched.
# Last reused/audited: 2026-06-29
# Authority basis: operator big-direction 2026-06-12 ("大方向现在也只是添加几个文件现在做")
"""Smoke tests for scripts/zeus_status.py, deploy_live.py, generate_schema_cheatsheet.py."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
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


def test_zeus_status_price_holes_fresh_city(tmp_path):
    """City with a fresh snapshot (<2h) must not appear in holes."""
    from datetime import datetime, timezone, timedelta
    zs = _load("zeus_status_smoke_price1", "zeus_status.py")

    fdb = tmp_path / "forecasts.db"
    tdb = tmp_path / "trades.db"

    fc = sqlite3.connect(str(fdb))
    fc.execute(
        "CREATE TABLE market_events "
        "(city TEXT, target_date TEXT, condition_id TEXT, "
        "temperature_metric TEXT, range_label TEXT)"
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fc.execute(
        "INSERT INTO market_events VALUES ('Tokyo', ?, 'cond-tok', 'high', '30-31')",
        (today,),
    )
    fc.commit()
    fc.close()

    tr = sqlite3.connect(str(tdb))
    tr.execute(
        "CREATE TABLE executable_market_snapshot_latest "
        "(condition_id TEXT, outcome_label TEXT, "
        "orderbook_top_ask REAL, captured_at TEXT)"
    )
    # Fresh snapshot (just now).
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    tr.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES ('cond-tok', 'YES', 0.55, ?)",
        (now_iso,),
    )
    tr.commit()
    tr.close()

    zs.FORECASTS_DB = str(fdb)
    zs.TRADES_DB = str(tdb)

    result = zs.section_price_holes()
    assert result.get("error") is None, result.get("error")
    assert result["cities_total"] == 1
    assert result["holes"] == [], f"Expected no holes, got {result['holes']}"
    assert result["fresh_count"] == 1


def test_zeus_status_price_holes_stale_city(tmp_path):
    """City with a snapshot older than 2h must appear in holes."""
    from datetime import datetime, timezone, timedelta
    zs = _load("zeus_status_smoke_price2", "zeus_status.py")

    fdb = tmp_path / "forecasts.db"
    tdb = tmp_path / "trades.db"

    fc = sqlite3.connect(str(fdb))
    fc.execute(
        "CREATE TABLE market_events "
        "(city TEXT, target_date TEXT, condition_id TEXT, "
        "temperature_metric TEXT, range_label TEXT)"
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fc.execute(
        "INSERT INTO market_events VALUES ('Seoul', ?, 'cond-seo', 'high', '28-29')",
        (today,),
    )
    fc.commit()
    fc.close()

    tr = sqlite3.connect(str(tdb))
    tr.execute(
        "CREATE TABLE executable_market_snapshot_latest "
        "(condition_id TEXT, outcome_label TEXT, "
        "orderbook_top_ask REAL, captured_at TEXT)"
    )
    # Stale snapshot (4h ago).
    stale_iso = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
    tr.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES ('cond-seo', 'YES', 0.55, ?)",
        (stale_iso,),
    )
    tr.commit()
    tr.close()

    zs.FORECASTS_DB = str(fdb)
    zs.TRADES_DB = str(tdb)

    result = zs.section_price_holes()
    assert result.get("error") is None, result.get("error")
    assert result["cities_total"] == 1
    assert len(result["holes"]) == 1
    assert result["holes"][0]["city"] == "Seoul"


def test_zeus_status_price_holes_no_snapshot(tmp_path):
    """City with open market but no snapshot at all must appear as NONE hole."""
    from datetime import datetime, timezone
    zs = _load("zeus_status_smoke_price3", "zeus_status.py")

    fdb = tmp_path / "forecasts.db"
    tdb = tmp_path / "trades.db"

    fc = sqlite3.connect(str(fdb))
    fc.execute(
        "CREATE TABLE market_events "
        "(city TEXT, target_date TEXT, condition_id TEXT, "
        "temperature_metric TEXT, range_label TEXT)"
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fc.execute(
        "INSERT INTO market_events VALUES ('Mumbai', ?, 'cond-mum', 'high', '32-33')",
        (today,),
    )
    fc.commit()
    fc.close()

    tr = sqlite3.connect(str(tdb))
    tr.execute(
        "CREATE TABLE executable_market_snapshot_latest "
        "(condition_id TEXT, outcome_label TEXT, "
        "orderbook_top_ask REAL, captured_at TEXT)"
    )
    # Intentionally leave table empty — no snapshot for Mumbai.
    tr.commit()
    tr.close()

    zs.FORECASTS_DB = str(fdb)
    zs.TRADES_DB = str(tdb)

    result = zs.section_price_holes()
    assert result.get("error") is None, result.get("error")
    assert result["cities_total"] == 1
    assert len(result["holes"]) == 1
    assert result["holes"][0]["city"] == "Mumbai"
    assert result["holes"][0]["age"] == "NONE"


# --------------------------------------------------------------------------
# deploy_live
# --------------------------------------------------------------------------
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
    """The clean-tree gate refuses a dirty/unpushed checkout and respects --allow-dirty."""
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
    # --allow-dirty overrides the refusal.
    ok2, _ = dl._gate(allow_dirty=True)
    assert ok2 is True


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


def test_deploy_live_waits_for_loaded_sha_and_freshness_state(monkeypatch, tmp_path):
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

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty: (True, []))
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "c" * 40)

    def _stop(label):
        calls.append(("stop", label))
        return True, f"stopped {label}"

    def _preflight(labels):
        calls.append(("preflight", tuple(labels)))
        return True, "live restart preflight passed"

    def _recovery(labels):
        calls.append(("recovery", tuple(labels)))
        return True, "live restart recovery passed"

    def _launch(label):
        calls.append(("launch", label))
        return True, f"bootstrapped {label}"

    def _verify(**kwargs):
        calls.append(("verify", kwargs["expected_sha"][:8]))
        return True, "live runtime freshness verified"

    def _monitor(**kwargs):
        calls.append(("monitor", "post-start"))
        return True, "post-start monitor cadence verified"

    def _queue(**kwargs):
        calls.append(("queue", "post-start"))
        return True, "post-start EDLI queue progress verified"

    monkeypatch.setattr(dl, "_stop_label", _stop)
    monkeypatch.setattr(dl, "_run_restart_recovery_if_needed", _recovery)
    monkeypatch.setattr(dl, "_run_restart_preflight_if_needed", _preflight)
    monkeypatch.setattr(dl, "_launch_or_restart_label", _launch)
    monkeypatch.setattr(dl, "_wait_for_live_runtime_fresh", _verify)
    monkeypatch.setattr(dl, "_wait_for_post_start_edli_queue_progress", _queue)
    monkeypatch.setattr(dl, "_wait_for_post_start_monitor_cadence", _monitor)

    rc = dl.main(["restart", "live-trading"])

    assert rc == 0
    expanded_labels = [*dl.LIVE_TRADING_PREREQUISITE_LABELS, dl.LIVE_TRADING_LABEL]
    assert calls == [
        ("stop", dl.LIVE_TRADING_LABEL),
        *[("launch", label) for label in dl.LIVE_TRADING_PREREQUISITE_LABELS],
        ("recovery", tuple(expanded_labels)),
        ("preflight", tuple(expanded_labels)),
        ("launch", dl.LIVE_TRADING_LABEL),
        ("verify", "cccccccc"),
        ("queue", "post-start"),
        ("monitor", "post-start"),
    ]
    assert "live restart preflight passed" in capsys.readouterr().out


def test_deploy_live_all_restarts_sidecars_before_live_preflight(monkeypatch):
    dl = _load("deploy_live_restart_order_all", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty: (True, []))
    monkeypatch.setattr(dl, "head_sha", lambda short=True: "d" * 40)
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

    rc = dl.main(["restart", "all"])

    assert rc == 0
    assert calls[0] == ("stop", dl.LIVE_TRADING_LABEL)
    recovery_index = calls.index(("recovery", tuple(dl.DAEMONS.values())))
    preflight_index = calls.index(("preflight", tuple(dl.DAEMONS.values())))
    assert recovery_index < preflight_index
    live_launch_index = calls.index(("launch", dl.LIVE_TRADING_LABEL))
    assert live_launch_index > preflight_index
    assert calls.index(("verify", "dddddddd")) > live_launch_index
    assert calls.index(("queue", "post-start")) > calls.index(("verify", "dddddddd"))
    assert calls.index(("monitor", "post-start")) > calls.index(("verify", "dddddddd"))
    non_live_launches = [
        call for call in calls[1:recovery_index]
        if call[0] == "launch"
    ]
    assert {label for _, label in non_live_launches} == {
        label for label in dl.DAEMONS.values() if label != dl.LIVE_TRADING_LABEL
    }


def test_deploy_live_preflight_failure_leaves_live_stopped(monkeypatch, capsys):
    dl = _load("deploy_live_restart_preflight_failure", "deploy_live.py")
    calls = []

    monkeypatch.setattr(dl, "_gate", lambda allow_dirty: (True, []))
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

    rc = dl.main(["restart", "live-trading"])

    assert rc == 1
    expanded_labels = [*dl.LIVE_TRADING_PREREQUISITE_LABELS, dl.LIVE_TRADING_LABEL]
    assert calls == [
        ("stop", dl.LIVE_TRADING_LABEL),
        *[("launch", label) for label in dl.LIVE_TRADING_PREREQUISITE_LABELS],
        ("recovery", tuple(expanded_labels)),
        ("preflight", tuple(expanded_labels)),
    ]
    err = capsys.readouterr().err
    assert "live-trading left stopped" in err


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
