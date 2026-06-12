# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator big-direction 2026-06-12 ("大方向现在也只是添加几个文件现在做") —
#   light smoke coverage for the three new ops scripts. Asserts the FAIL-SOFT contract
#   (a locked/empty/missing DB degrades one section to ERR, the rest still render) and
#   that each script runs read-only against temp DBs. No live DB is touched.
"""Smoke tests for scripts/zeus_status.py, deploy_live.py, gen_schema_cheatsheet.py."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
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
    for sect in ("daemons", "events", "blocks", "surface", "positions", "orders"):
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
    assert zs.classify_block("EXECUTOR_EXPRESSIBILITY", "DAY0_SCOPE_SHADOW_ONLY") == "transient"
    # Honest no-edge = economic.
    assert zs.classify_block("TRADE_SCORE", "TRADE_SCORE_NON_POSITIVE") == "economic"


def test_zeus_status_age_str():
    zs = _load("zeus_status_smoke4", "zeus_status.py")
    assert zs.age_str(None) == "-"
    assert zs.age_str("not-a-timestamp") == "?"
    # A recent timestamp renders as seconds/minutes, never crashes.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    out = zs.age_str(now_iso)
    assert out.endswith(("s", "m", "h", "d"))


# --------------------------------------------------------------------------
# deploy_live
# --------------------------------------------------------------------------
def test_deploy_live_status_runs(capsys):
    """status runs against the real (clean) live checkout and prints structured output."""
    dl = _load("deploy_live_smoke", "deploy_live.py")
    rc = dl.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deploy_live status" in out
    assert "branch" in out and "HEAD" in out and "daemons" in out


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


def test_deploy_live_unknown_daemon_rejected(capsys):
    dl = _load("deploy_live_smoke3", "deploy_live.py")
    rc = dl.main(["restart", "no-such-daemon"])
    assert rc == 2  # unknown daemon, never reaches kickstart


# --------------------------------------------------------------------------
# gen_schema_cheatsheet
# --------------------------------------------------------------------------
def test_gen_schema_cheatsheet_on_temp_db(tmp_path, capsys):
    """Generator runs read-only over a temp DB and renders table names + types."""
    gsc = _load("gen_schema_smoke", "gen_schema_cheatsheet.py")
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


def test_gen_schema_cheatsheet_handles_missing_db(tmp_path):
    """A missing DB renders an ERR line, does not raise."""
    gsc = _load("gen_schema_smoke2", "gen_schema_cheatsheet.py")
    gsc.DBS = [("ghost.db", str(tmp_path / "nope.db"))]
    content = gsc.build()
    assert "## ghost.db" in content
    assert "ERR" in content


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
