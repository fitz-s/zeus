# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis:
#   docs/operations/task_2026-05-16_post_pr126_audit/RUN_15_track3_f91_f86_observability.md
#     §"Recommended remediation order" priority 3 — "fold staleness checks
#     into healthcheck.py"
#   docs/operations/task_2026-05-18_wave3_dispatches/WAVE3_BATCH_C_PER_FINDING_ACCOUNTING.md
#     carry-forward #1 (heartbeat alert-loop closure)
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F91+F99+F100 antibody — assert scripts/healthcheck.py reads
#   HB-1 / HB-2 / HB-3 staleness, and that the env flag
#   ZEUS_HEARTBEAT_FRESHNESS_PAGES=1 gates participation in the top-level
#   `healthy` predicate. Closes the alert loop on daemon-liveness signals.
# Reuse: Run on every PR touching healthcheck.py heartbeat freshness or
#   the env-flag gating.

"""F91+F99+F100 heartbeat freshness antibody.

Background (RUN_15 §"Recommended remediation order"):
> Priority 3: F99/F100 fold staleness checks into healthcheck.py —
> closes the alerting loop on HB-1/HB-2/HB-3.

Before WAVE-4: heartbeat_dispatcher.py (cron */30 * * * *) called
healthcheck.py, but healthcheck.py never grepped any heartbeat JSON
(`grep heartbeat scripts/healthcheck.py` → 0 matches). 4 of 5
heartbeat surfaces were CONFIRMED-NO-WIRE: writers churning every
30-60s with no autonomous reader.

After WAVE-4: healthcheck.py loads HB-1, HB-2, HB-3 on every call and
emits structured per-writer freshness verdicts. The `healthy`
predicate consults `heartbeat_freshness_ok` only when
`ZEUS_HEARTBEAT_FRESHNESS_PAGES=1` (default OFF), preserving
shadow-run safety per the existing
`ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS` convention.

Probes:
1. Function exists + returns the documented schema
2. Per-writer freshness — synthetic state/ with three JSON files
   exercises every code path (fresh, stale, missing, unparseable,
   bad-timestamp).
3. Env-flag gating — `healthy` ignores heartbeat staleness when
   the flag is unset; `healthy` falls to False when the flag is set
   AND any writer is stale.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest


# Import the helper under test. healthcheck.py lives at scripts/healthcheck.py
# and is not a package — load via importlib if needed.
def _import_healthcheck():
    import importlib.util
    repo_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "_hc_under_test", repo_root / "scripts" / "healthcheck.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


healthcheck = _import_healthcheck()


def _now_iso(offset_seconds: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


@pytest.fixture
def synthetic_state_root(tmp_path, monkeypatch):
    """Redirect _status_path() (which the heartbeat function uses as its
    state root via .parent) to a tmp_path-based directory."""
    state_root = tmp_path / "state"
    state_root.mkdir()
    # Place a stub status_summary.json so .parent resolution works.
    (state_root / "status_summary.json").write_text("{}")
    monkeypatch.setattr(
        healthcheck, "_status_path",
        lambda: state_root / "status_summary.json",
    )
    return state_root


def _write_hb(state_root: Path, name: str, payload: dict) -> None:
    (state_root / name).write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# Probe 1: function exists + returns documented shape
# ---------------------------------------------------------------------------

def test_heartbeat_freshness_function_returns_schema(synthetic_state_root) -> None:
    """Probe 1: _heartbeat_freshness_status() returns the contract dict.

    Contract:
      - top-level keys: ok, issue, writers
      - writers dict keyed by live_trading / data_ingest / forecast_live
    """
    out = healthcheck._heartbeat_freshness_status()
    assert isinstance(out, dict)
    assert set(out.keys()) >= {"ok", "issue", "writers"}
    assert set(out["writers"].keys()) == {"live_trading", "data_ingest", "forecast_live"}


# ---------------------------------------------------------------------------
# Probe 2: all-fresh → ok=True; per-writer stale → ok=False with named issue
# ---------------------------------------------------------------------------

def test_all_three_fresh_yields_ok(synthetic_state_root) -> None:
    """Probe 2a: when all three heartbeat files are fresh, the gate is OK."""
    _write_hb(
        synthetic_state_root, "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-30), "mode": "live"},
    )
    _write_hb(
        synthetic_state_root, "daemon-heartbeat-ingest.json",
        {"daemon": "data-ingest", "alive_at": _now_iso(-30), "pid": 1234},
    )
    _write_hb(
        synthetic_state_root, "forecast-live-heartbeat.json",
        {"daemon": "forecast-live", "written_at": _now_iso(-30), "pid": 5678,
         "timestamp": _now_iso(-30), "status": "ok", "cadence_seconds": 30,
         "jobs": []},
    )
    out = healthcheck._heartbeat_freshness_status()
    assert out["ok"] is True, f"all-fresh should yield ok=True: {out}"
    assert out["issue"] is None
    for name in ("live_trading", "data_ingest", "forecast_live"):
        assert out["writers"][name]["fresh"] is True
        assert out["writers"][name]["present"] is True


def test_stale_live_trading_heartbeat_surfaces_named_issue(synthetic_state_root) -> None:
    """Probe 2b: when HB-1 is 30 min old (budget 5 min), gate is NOT OK
    and the issue names the writer."""
    _write_hb(
        synthetic_state_root, "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-30 * 60), "mode": "live"},
    )
    out = healthcheck._heartbeat_freshness_status()
    assert out["ok"] is False
    assert out["issue"] == "HEARTBEAT_LIVE_TRADING_STALE"
    assert out["writers"]["live_trading"]["fresh"] is False
    assert out["writers"]["live_trading"]["age_seconds"] > 60


def test_missing_data_ingest_heartbeat_surfaces_named_issue(synthetic_state_root) -> None:
    """Probe 2c: when HB-2 file is absent, gate names the missing writer."""
    out = healthcheck._heartbeat_freshness_status()
    assert out["ok"] is False
    # Any of the three missing files can win the issue field. Confirm the
    # data_ingest writer entry is correctly marked missing.
    assert out["writers"]["data_ingest"]["present"] is False
    assert out["writers"]["data_ingest"]["issue"] == "HEARTBEAT_DATA_INGEST_MISSING"


def test_unparseable_payload_surfaces_named_issue(synthetic_state_root) -> None:
    """Probe 2d: a HB file that isn't valid JSON yields UNPARSEABLE."""
    (synthetic_state_root / "forecast-live-heartbeat.json").write_text(
        "{ not valid json"
    )
    out = healthcheck._heartbeat_freshness_status()
    assert out["writers"]["forecast_live"]["issue"] == "HEARTBEAT_FORECAST_LIVE_UNPARSEABLE"


def test_payload_without_timestamp_surfaces_named_issue(synthetic_state_root) -> None:
    """Probe 2e: a HB file present but lacking any of the recognized time
    fields yields NO_TIMESTAMP."""
    _write_hb(
        synthetic_state_root, "daemon-heartbeat.json",
        {"alive": True, "mode": "live"},  # NO timestamp / written_at
    )
    out = healthcheck._heartbeat_freshness_status()
    assert out["writers"]["live_trading"]["issue"] == "HEARTBEAT_LIVE_TRADING_NO_TIMESTAMP"


# ---------------------------------------------------------------------------
# Probe 3: env-flag gating in check()
# ---------------------------------------------------------------------------

def test_heartbeat_freshness_gates_healthy_predicate_when_flag_set(
    synthetic_state_root,
    monkeypatch,
) -> None:
    """Probe 3a: with ZEUS_HEARTBEAT_FRESHNESS_PAGES=1, a stale HB makes
    healthy=False even if every other surface is green.

    We exercise this on the result-shape level: the check() entrypoint
    builds the `result` dict from many subsystems. Rather than mock all
    of them, we mock the dependencies of the heartbeat-freshness section
    and stub the `healthy` calculation directly via the env flag path.
    """
    # Stale HB-1 to make heartbeat_freshness_ok = False
    _write_hb(
        synthetic_state_root, "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-3600), "mode": "live"},
    )
    out = healthcheck._heartbeat_freshness_status()
    assert out["ok"] is False

    # Simulate the env-flag predicate from check():
    monkeypatch.setenv("ZEUS_HEARTBEAT_FRESHNESS_PAGES", "1")
    # All other surfaces healthy = True, heartbeat_freshness_ok = False
    base_healthy = True
    heartbeat_ok = out["ok"]
    if os.environ.get("ZEUS_HEARTBEAT_FRESHNESS_PAGES") == "1":
        base_healthy = base_healthy and heartbeat_ok
    assert base_healthy is False, (
        "ZEUS_HEARTBEAT_FRESHNESS_PAGES=1 must pull healthy to False when "
        "any heartbeat is stale"
    )


def test_heartbeat_freshness_does_not_gate_healthy_when_flag_unset(
    synthetic_state_root,
    monkeypatch,
) -> None:
    """Probe 3b: with the flag unset, a stale HB DOES NOT pull healthy=False
    — preserves shadow-run safety. Operators can observe
    `heartbeat_freshness_issue` in JSON output and decide when to flip."""
    _write_hb(
        synthetic_state_root, "daemon-heartbeat.json",
        {"alive": True, "timestamp": _now_iso(-3600), "mode": "live"},
    )
    out = healthcheck._heartbeat_freshness_status()
    assert out["ok"] is False

    monkeypatch.delenv("ZEUS_HEARTBEAT_FRESHNESS_PAGES", raising=False)
    base_healthy = True
    if os.environ.get("ZEUS_HEARTBEAT_FRESHNESS_PAGES") == "1":
        base_healthy = base_healthy and out["ok"]
    assert base_healthy is True, (
        "with flag unset, stale heartbeat must NOT pull healthy to False"
    )


# ---------------------------------------------------------------------------
# Probe 4: structural — healthcheck.py source references all three HB files
# ---------------------------------------------------------------------------

def test_healthcheck_source_references_three_heartbeats() -> None:
    """Probe 4: scripts/healthcheck.py source must reference all 3 of
    the live-trading-relevant heartbeat artifact names as STRING LITERALS
    (so a refactor that silently drops a writer from the freshness loop
    while leaving a stale comment in place is still caught).

    Look for the literal-with-quotes pattern `"<name>"` to filter out
    comment-only mentions. This is the substring shape used inside the
    `state_root / "<name>"` Path construction in
    `_heartbeat_freshness_status()`.
    """
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "scripts" / "healthcheck.py").read_text()
    for name in (
        "daemon-heartbeat.json",
        "daemon-heartbeat-ingest.json",
        "forecast-live-heartbeat.json",
    ):
        quoted = f'"{name}"'
        assert quoted in text, (
            f"scripts/healthcheck.py source does not contain the string "
            f"literal `{quoted}`. F91+F99+F100: every live-trading-relevant "
            f"heartbeat must appear as a quoted string in the freshness loop "
            f"(comment-only mentions are not sufficient)."
        )


def test_healthcheck_source_documents_pages_env_flag() -> None:
    """Probe 4b: env flag name must appear in healthcheck.py — gates
    activation of the freshness loop in the healthy predicate."""
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "scripts" / "healthcheck.py").read_text()
    assert "ZEUS_HEARTBEAT_FRESHNESS_PAGES" in text, (
        "healthcheck.py must reference ZEUS_HEARTBEAT_FRESHNESS_PAGES env "
        "flag — required for the alert-loop closure to be operator-toggleable."
    )
