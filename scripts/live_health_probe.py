#!/usr/bin/env python3
# Created: 2026-05-11
# Last reused/audited: 2026-05-11
# Authority basis: Live monitoring during first-order qualification (operator directive 2026-05-11)
"""One-shot live health probe.

Reports a single JSON line per invocation summarizing:
  - daemon heartbeat age + alive status
  - cycle freshness (status_summary mtime + last cycle dict)
  - 3-process liveness (src.main, src.ingest_main, riskguard)
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

ROOT = "/Users/leofitz/.openclaw/workspace-venus/zeus"
SNAPSHOT_FILE = "/tmp/zeus_health_snapshot.json"

def _age(path):
    if not os.path.exists(path): return None
    return int(time.time() - os.stat(path).st_mtime)

def _alive(pattern):
    try:
        out = subprocess.run(["pgrep","-f",pattern], capture_output=True, text=True, timeout=5).stdout.strip()
        return [int(p) for p in out.split('\n') if p]
    except Exception:
        return []

def main():
    now = time.strftime("%H:%M:%SZ", time.gmtime())
    report = {"ts": now}

    # Daemon heartbeat
    hb_path = os.path.join(ROOT, "state/daemon-heartbeat.json")
    try:
        d = json.load(open(hb_path))
        report["hb"] = {"alive": d.get("alive"), "age_s": _age(hb_path)}
    except Exception as e:
        report["hb"] = {"error": str(e)}

    # Process liveness
    report["procs"] = {
        "daemon": _alive("src.main"),
        "ingest": _alive("src.ingest_main"),
        "riskguard": _alive("src.riskguard"),
    }

    # Status summary
    ss_path = os.path.join(ROOT, "state/status_summary.json")
    ss_age = _age(ss_path)
    report["status_summary_age_s"] = ss_age
    try:
        ss = json.load(open(ss_path))
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
    alerts = []
    if report["hb"].get("age_s") is None or report["hb"]["age_s"] > 90:
        alerts.append(f"hb_stale={report['hb'].get('age_s')}s")
    if not report["procs"]["daemon"]:
        alerts.append("daemon_dead")
    if not report["procs"]["ingest"]:
        alerts.append("ingest_dead")
    if not report["procs"]["riskguard"]:
        alerts.append("riskguard_dead")
    if ss_age is not None and ss_age > 2700:
        alerts.append(f"cycle_stale={ss_age}s")
    if report.get("cycle", {}).get("ws_connected") is False:
        alerts.append("ws_disconnected")
    if report.get("cycle", {}).get("risk_level") not in ("GREEN", None):
        alerts.append(f"risk={report['cycle']['risk_level']}")

    prefix = "ALERT " if alerts else "OK    "
    flags = ",".join(alerts) if alerts else "all_healthy"
    summary = (
        f"{prefix}{now} hb={report['hb'].get('age_s')}s "
        f"cycle_age={ss_age}s "
        f"daemon={len(report['procs']['daemon'])} "
        f"ingest={len(report['procs']['ingest'])} "
        f"risk={report.get('cycle',{}).get('risk_level','?')} "
        f"ws={report.get('cycle',{}).get('ws_subscription','?')} "
        f"funnel={report.get('funnel',{}).get('evaluated','?')}/{report.get('funnel',{}).get('selected','?')}/{report.get('funnel',{}).get('filled','?')} "
        f"entry={report.get('entry_capable',{}).get('status','?')} "
        f"blocking_gates={len(report.get('blocking_gates',[]))} "
        f"flags={flags}"
    )
    print(summary)

if __name__ == "__main__":
    main()
