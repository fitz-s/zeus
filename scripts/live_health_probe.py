#!/usr/bin/env python3
# Lifecycle: created=2026-05-11; last_reviewed=2026-05-16; last_reused=2026-05-16
# Purpose: One-shot live health signal for daemon, forecast-live owner, riskguard, status summary, and entry capability.
# Reuse: Run when live process ownership, forecast-live heartbeat semantics, or operator health alerts change.
# Created: 2026-05-11
# Last reused/audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md; docs/operations/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md Phase C
"""One-shot live health probe.

Reports a single JSON line per invocation summarizing:
  - daemon heartbeat age + alive status
  - cycle freshness (status_summary mtime + last cycle dict)
  - process liveness (src.main, forecast-live owner, legacy ingest, riskguard)
  - forecast-live heartbeat freshness
  - WS state (connected, subscription, gap reason)
  - block_registry blocking gates
  - lifecycle_funnel counts
  - v2_row_counts (delta vs last snapshot if persisted)
  - no_trade top reasons
  - execution_capability entry status

Designed to be called by Monitor with grep filter on "ALERT" lines.
"""
from __future__ import annotations
import json, os, sys, time, subprocess
from datetime import datetime, timezone

ROOT = "/Users/leofitz/.openclaw/workspace-venus/zeus"
SNAPSHOT_FILE = "/tmp/zeus_health_snapshot.json"
FORECAST_LIVE_HEARTBEAT = "state/forecast-live-heartbeat.json"
FORECAST_LIVE_STALE_SECONDS = 300
DEFAULT_EXPECTED_REF = "origin/main"
SETTLEMENT_TRUTH_STALE_SECONDS = int(os.environ.get("ZEUS_SETTLEMENT_TRUTH_STALE_SECONDS", str(48 * 3600)))
PROCESS_CODE_STALE_TOLERANCE_SECONDS = 2
PROCESS_CODE_SURFACES = {
    "daemon": (
        "src/main.py",
        "src/engine/cycle_runner.py",
        "src/control/ws_gap_guard.py",
        "src/execution/command_recovery.py",
        "src/execution/exchange_reconcile.py",
        "src/execution/executor.py",
        "src/execution/harvester_pnl_resolver.py",
    ),
    "forecast_live": (
        "src/ingest/forecast_live_daemon.py",
        "src/data/source_health_probe.py",
        "src/data/ecmwf_open_data.py",
    ),
    "data_ingest": (
        "src/ingest_main.py",
        "src/ingest/harvester_truth_writer.py",
        "src/data/source_health_probe.py",
    ),
    "riskguard": ("src/riskguard/riskguard.py",),
}

def _age(path):
    if not os.path.exists(path): return None
    return int(time.time() - os.stat(path).st_mtime)

def _alive(pattern):
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
        pids = []
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            pid_text, command = parts
            tokens = command.split()
            for idx, token in enumerate(tokens[:-1]):
                module_name = tokens[idx + 1]
                if token == "-m" and (module_name == pattern or module_name.startswith(f"{pattern}.")):
                    pids.append(int(pid_text))
                    break
        return pids
    except Exception:
        return []

def _process_env(pid):
    try:
        out = subprocess.run(["ps", "eww", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5).stdout.strip()
        env = {}
        for token in out.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            if key == "ZEUS_FORECAST_LIVE_OWNER":
                env[key] = value
        return env
    except Exception:
        return {}

def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}

def _parse_iso_epoch(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None

def _process_start_epoch(pid):
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None

def _max_source_mtime(root, rel_paths):
    mtimes = []
    for rel_path in rel_paths:
        path = os.path.join(root, rel_path)
        try:
            mtimes.append(os.stat(path).st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else None

def _process_loaded_code_status(procs, root=ROOT):
    stale = []
    unattested = []
    items = []
    for proc_name, rel_paths in PROCESS_CODE_SURFACES.items():
        source_mtime = _max_source_mtime(root, rel_paths)
        pids = list(procs.get(proc_name) or [])
        for pid in pids:
            started_at = _process_start_epoch(pid)
            item = {
                "process": proc_name,
                "pid": pid,
                "source_mtime": source_mtime,
                "started_at": started_at,
                "paths": list(rel_paths),
            }
            if source_mtime is None or started_at is None:
                item["issue"] = "process_loaded_code_unattested"
                unattested.append(item)
            elif started_at + PROCESS_CODE_STALE_TOLERANCE_SECONDS < source_mtime:
                item["issue"] = "process_started_before_source_mtime"
                stale.append(item)
            items.append(item)
    issue = None
    if stale:
        issue = "PROCESS_LOADED_CODE_STALE"
    elif unattested:
        issue = "PROCESS_LOADED_CODE_UNATTESTED"
    return {
        "ok": not stale and not unattested,
        "issue": issue,
        "stale": stale,
        "unattested": unattested,
        "items": items,
    }

def _settlement_truth_status(root=ROOT):
    path = os.path.join(root, "state", "zeus-forecasts.db")
    if not os.path.exists(path):
        return {
            "ok": False,
            "path": path,
            "issue": "SETTLEMENT_TRUTH_DB_MISSING",
        }
    try:
        result = subprocess.run(
            [
                "sqlite3",
                "-readonly",
                "-separator",
                "\t",
                path,
                "SELECT COUNT(*), COALESCE(MAX(settled_at), ''), "
                "COALESCE(MAX(recorded_at), '') FROM settlements_v2",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "sqlite3 query failed").strip())
        parts = result.stdout.strip().split("\t")
        if len(parts) != 3:
            raise RuntimeError("unexpected settlement freshness query output")
    except Exception as exc:
        return {
            "ok": False,
            "path": path,
            "issue": "SETTLEMENT_TRUTH_UNAVAILABLE",
            "error": str(exc),
        }
    count = int(parts[0] or 0)
    max_settled_at = parts[1] or None
    settled_epoch = _parse_iso_epoch(max_settled_at)
    age = None if settled_epoch is None else max(0, int(datetime.now(timezone.utc).timestamp() - settled_epoch))
    ok = count > 0 and age is not None and age <= SETTLEMENT_TRUTH_STALE_SECONDS
    issue = None
    if count <= 0:
        issue = "SETTLEMENT_TRUTH_EMPTY"
    elif age is None:
        issue = "SETTLEMENT_TRUTH_MAX_SETTLED_AT_UNPARSEABLE"
    elif not ok:
        issue = "SETTLEMENT_TRUTH_STALE"
    return {
        "ok": ok,
        "path": path,
        "count": count,
        "max_settled_at": max_settled_at,
        "max_recorded_at": parts[2] or None,
        "age_s": age,
        "stale_budget_s": SETTLEMENT_TRUTH_STALE_SECONDS,
        "issue": issue,
    }

def _process_liveness():
    forecast_live = _alive("src.ingest.forecast_live_daemon")
    legacy_ingest = _alive("src.ingest_main")
    legacy_ingest_opendata_owner = [
        pid for pid in legacy_ingest
        if _process_env(pid).get("ZEUS_FORECAST_LIVE_OWNER", "ingest_main") != "forecast_live"
    ]
    return {
        "daemon": _alive("src.main"),
        "forecast_live": forecast_live,
        "data_ingest": legacy_ingest,
        "legacy_ingest": legacy_ingest,
        "legacy_ingest_opendata_owner": legacy_ingest_opendata_owner,
        # Backward-compatible aggregate for older dashboard readers. Forecast-live
        # is now the canonical forecast owner; legacy ingest is a fallback signal.
        "ingest": forecast_live or legacy_ingest,
        "riskguard": _alive("src.riskguard"),
    }

def _git_text(root, args):
    try:
        result = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return None, str(exc)
    text = result.stdout.strip()
    if result.returncode != 0:
        return None, (result.stderr or text or f"git exited {result.returncode}").strip()
    return text, None

def _git_runtime_identity(root=ROOT):
    head, head_error = _git_text(root, ["rev-parse", "HEAD"])
    if head_error:
        return {
            "status": "git_unavailable",
            "repo": root,
            "error": head_error,
            "expected_ref": os.environ.get("ZEUS_LIVE_EXPECTED_REF", DEFAULT_EXPECTED_REF),
            "expected_commit": os.environ.get("ZEUS_LIVE_EXPECTED_COMMIT", "").strip(),
            "matches_expected": False,
            "dirty": None,
        }
    branch, _ = _git_text(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    status_text, status_error = _git_text(root, ["status", "--porcelain"])
    dirty = None if status_error else bool(status_text)
    expected_ref = os.environ.get("ZEUS_LIVE_EXPECTED_REF", DEFAULT_EXPECTED_REF).strip()
    expected_commit = os.environ.get("ZEUS_LIVE_EXPECTED_COMMIT", "").strip()
    expected_error = None
    if not expected_commit and expected_ref:
        expected_commit, expected_error = _git_text(root, ["rev-parse", expected_ref])
    return {
        "status": "ok" if expected_commit and dirty is not None else "incomplete",
        "repo": root,
        "head": head,
        "branch": branch,
        "dirty": dirty,
        "expected_ref": expected_ref,
        "expected_commit": expected_commit,
        "expected_error": expected_error,
        "matches_expected": bool(head and expected_commit and head == expected_commit),
    }

def _classify_alerts(report, ss_age):
    alerts = []
    code_plane = report.get("code_plane", {})
    if (
        code_plane.get("status") != "ok"
        or code_plane.get("dirty") is not False
        or code_plane.get("matches_expected") is not True
    ):
        alerts.append("LIVE_CODE_PLANE_DRIFT")
    if report["hb"].get("age_s") is None or report["hb"]["age_s"] > 90:
        alerts.append(f"hb_stale={report['hb'].get('age_s')}s")
    if not report["procs"]["daemon"]:
        alerts.append("daemon_dead")
    if not report["procs"].get("data_ingest"):
        alerts.append("data_ingest_dead")
    forecast_age = report.get("forecast_live_hb", {}).get("age_s")
    if not report["procs"].get("forecast_live"):
        alerts.append("forecast_live_dead")
    elif forecast_age is None or forecast_age > FORECAST_LIVE_STALE_SECONDS:
        alerts.append(f"forecast_live_stale={forecast_age}s")
    if report["procs"].get("legacy_ingest_opendata_owner"):
        alerts.append("legacy_ingest_opendata_owner_present")
    if not report["procs"]["riskguard"]:
        alerts.append("riskguard_dead")
    process_code = report.get("process_code", {})
    if process_code.get("ok") is not True:
        alerts.append(process_code.get("issue") or "PROCESS_LOADED_CODE_UNATTESTED")
    settlement_truth = report.get("settlement_truth", {})
    if settlement_truth.get("ok") is not True:
        alerts.append(settlement_truth.get("issue") or "SETTLEMENT_TRUTH_UNHEALTHY")
    if ss_age is not None and ss_age > 2700:
        alerts.append(f"cycle_stale={ss_age}s")
    if report.get("cycle", {}).get("ws_connected") is False:
        alerts.append("ws_disconnected")
    if report.get("cycle", {}).get("risk_level") not in ("GREEN", None):
        alerts.append(f"risk={report['cycle']['risk_level']}")
    entry_status = report.get("entry_capable", {}).get("status")
    if entry_status == "blocked" or report.get("blocking_gates"):
        alerts.append("entry_blocked")
    return alerts

def main():
    now = time.strftime("%H:%M:%SZ", time.gmtime())
    report = {"ts": now}

    # Daemon heartbeat
    hb_path = os.path.join(ROOT, "state/daemon-heartbeat.json")
    d = _load_json(hb_path)
    report["hb"] = {"alive": d.get("alive"), "age_s": _age(hb_path)}

    forecast_hb_path = os.path.join(ROOT, FORECAST_LIVE_HEARTBEAT)
    forecast_hb = _load_json(forecast_hb_path)
    report["forecast_live_hb"] = {
        "alive": forecast_hb.get("alive"),
        "status": forecast_hb.get("status"),
        "age_s": _age(forecast_hb_path),
    }

    # Process liveness
    report["procs"] = _process_liveness()
    report["code_plane"] = _git_runtime_identity(ROOT)
    report["process_code"] = _process_loaded_code_status(report["procs"], ROOT)
    report["settlement_truth"] = _settlement_truth_status(ROOT)

    # Status summary
    ss_path = os.path.join(ROOT, "state/status_summary.json")
    ss_age = _age(ss_path)
    report["status_summary_age_s"] = ss_age
    try:
        ss = _load_json(ss_path)
        if ss.get("error"):
            raise RuntimeError(ss["error"])
        cycle = ss.get("cycle", {})
        runtime = ss.get("runtime", {})
        risk = ss.get("risk", {})
        funnel = ss.get("lifecycle_funnel", {})
        ws = cycle.get("ws_user_channel", {})
        report["cycle"] = {
            "mode": cycle.get("mode"),
            "started": cycle.get("started_at"),
            "risk_level": cycle.get("risk_level") or risk.get("level"),
            "ws_connected": ws.get("connected"),
            "ws_subscription": ws.get("subscription_state"),
            "ws_gap_reason": ws.get("gap_reason"),
            "entries_blocked_reason": cycle.get("entries_blocked_reason"),
        }
        # block_registry blocking entries
        br = cycle.get("block_registry") or []
        blocking = [(b.get("name"), b.get("blocking_reason")) for b in br if b.get("state") == "blocking"]
        report["blocking_gates"] = blocking

        # lifecycle counts
        report["funnel"] = funnel.get("counts", {})
        report["v2_rows"] = ss.get("v2_row_counts", {})

        # no_trade
        report["no_trade"] = ss.get("no_trade", {}).get("recent_stage_counts", {})

        # execution_capability
        ec = ss.get("execution_capability", {})
        entry = ec.get("entry", {})
        report["entry_capable"] = {
            "status": entry.get("status"),
            "allow_submit": entry.get("global_allow_submit"),
            "authorized": entry.get("live_action_authorized"),
        }
    except Exception as e:
        report["cycle_error"] = str(e)

    # Compute delta vs last snapshot
    try:
        if os.path.exists(SNAPSHOT_FILE):
            prev = json.load(open(SNAPSHOT_FILE))
            prev_v2 = prev.get("v2_rows", {})
            now_v2 = report.get("v2_rows", {})
            delta = {k: now_v2.get(k, 0) - prev_v2.get(k, 0) for k in now_v2}
            report["v2_delta_since_last"] = delta
    except Exception:
        pass

    # Persist
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(report, f)

    # ALERT classification
    alerts = _classify_alerts(report, ss_age)

    prefix = "ALERT " if alerts else "OK    "
    flags = ",".join(alerts) if alerts else "all_healthy"
    summary = (
        f"{prefix}{now} hb={report['hb'].get('age_s')}s "
        f"cycle_age={ss_age}s "
        f"daemon={len(report['procs']['daemon'])} "
        f"forecast_live={len(report['procs']['forecast_live'])} "
        f"data_ingest={len(report['procs']['data_ingest'])} "
        f"legacy_ingest={len(report['procs']['legacy_ingest'])} "
        f"commit={report.get('code_plane',{}).get('head','?')} "
        f"expected={report.get('code_plane',{}).get('expected_commit','?')} "
        f"dirty={report.get('code_plane',{}).get('dirty','?')} "
        f"risk={report.get('cycle',{}).get('risk_level','?')} "
        f"ws={report.get('cycle',{}).get('ws_subscription','?')} "
        f"funnel={report.get('funnel',{}).get('evaluated','?')}/{report.get('funnel',{}).get('selected','?')}/{report.get('funnel',{}).get('filled','?')} "
        f"entry={report.get('entry_capable',{}).get('status','?')} "
        f"blocking_gates={len(report.get('blocking_gates',[]))} "
        f"settlement_age={report.get('settlement_truth',{}).get('age_s','?')}s "
        f"flags={flags}"
    )
    print(summary)

if __name__ == "__main__":
    main()
