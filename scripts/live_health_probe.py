#!/usr/bin/env python3
# Lifecycle: created=2026-05-11; last_reviewed=2026-05-18; last_reused=2026-05-22
# Purpose: One-shot live health signal for daemon, forecast-live owner, riskguard, status summary, and entry capability.
# Reuse: Run when live process ownership, forecast-live heartbeat semantics, or operator health alerts change.
# Created: 2026-05-11
# Last reused/audited: 2026-05-22
# Authority basis: docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md; docs/archive/2026-Q2/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md Phase C; 2026-05-17 volatile runtime-artifact code-plane contract.
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
import json, os, sqlite3, sys, time, subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = str(
    Path(
        os.environ.get("ZEUS_PRIMARY_ROOT")
        or os.environ.get("ZEUS_DIR")
        or Path(__file__).resolve().parents[1]
    ).resolve()
)
SNAPSHOT_FILE = "/tmp/zeus_health_snapshot.json"
FORECAST_LIVE_HEARTBEAT = "state/forecast-live-heartbeat.json"
FORECAST_LIVE_STALE_SECONDS = 300
DEFAULT_EXPECTED_REF = ""
MATERIAL_CODE_PLANE_DIRTY_PREFIXES = (
    "architecture/",
    "config/",
    "scripts/",
    "src/",
)
MATERIAL_CODE_PLANE_DIRTY_FILES = frozenset({
    "AGENTS.md",
    "pyproject.toml",
    "requirements.txt",
})
VOLATILE_CODE_PLANE_DIRTY_PATHS = frozenset({
    "station_migration_alerts.json",
    "state/station_migration_alerts.json",
})
REQUIRED_LIVE_HEALTH_SURFACES = (
    "heartbeat",
    "venue_heartbeat",
    "runtime_code",
    "main_daemon",
    "process_code",
    "run_mode",
    "forecast_pipeline",
    "forecast_event_bridge",
    "entry_q_version",
    "pending_exit_release_loop",
    "monitor_probability_freshness",
    "day0_decision_trace",
    "status_summary",
    "execution_capability",
)
SETTLEMENT_TRUTH_STALE_SECONDS = int(os.environ.get("ZEUS_SETTLEMENT_TRUTH_STALE_SECONDS", str(48 * 3600)))
PROCESS_CODE_STALE_TOLERANCE_SECONDS = 2
ENTRY_Q_VERSION_LOOKBACK_SECONDS = 2 * 3600
ENTRY_Q_VERSION_SAMPLE_LIMIT = 8
PROCESS_CODE_SURFACES = {
    "daemon": (
        "src/main.py",
        "src/control/cutover_guard.py",
        "src/control/heartbeat_supervisor.py",
        "src/control/live_health.py",
        "src/control/runtime_code_plane.py",
        "src/engine/cycle_runner.py",
        "src/engine/cycle_runtime.py",
        "src/engine/evaluator.py",
        "src/engine/event_reactor_adapter.py",
        "src/engine/monitor_refresh.py",
        "src/engine/position_belief.py",
        "src/contracts/no_trade_reason.py",
        "src/contracts/executable_market_snapshot.py",
        "src/contracts/execution_intent.py",
        "src/control/ws_gap_guard.py",
        "src/data/market_scanner.py",
        "src/events/event_store.py",
        "src/events/reactor.py",
        "src/execution/command_recovery.py",
        "src/execution/exchange_reconcile.py",
        "src/execution/executor.py",
        "src/execution/exit_lifecycle.py",
        "src/execution/exit_safety.py",
        "src/execution/harvester_pnl_resolver.py",
        "src/execution/staleness_cancel.py",
        "src/data/polymarket_client.py",
        "src/observability/status_summary.py",
        "src/state/chain_reconciliation.py",
        "src/state/chain_mirror_reconciler.py",
        "src/state/db.py",
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
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Zeus heartbeat producers write timezone-aware UTC strings. Legacy
            # naive strings are interpreted as UTC instead of host-local time so
            # health status does not vary with operator machine timezone.
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None

def _heartbeat_payload_age(payload, *, now_epoch=None):
    now = float(now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp())
    for key in ("written_at", "timestamp"):
        epoch = _parse_iso_epoch((payload or {}).get(key))
        if epoch is None:
            continue
        if epoch > now:
            return None, None
        return max(0, int(now - epoch)), key
    return None, None

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
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            table = "settlement_outcomes" if "settlement_outcomes" in tables else "settlements"
            if table not in tables:
                raise RuntimeError("no settlement truth table found")
            columns = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})")
            }
            settled_col = "settled_at" if "settled_at" in columns else None
            recorded_col = "recorded_at" if "recorded_at" in columns else settled_col
            if settled_col is None or recorded_col is None:
                raise RuntimeError(f"settlement truth table {table} missing timestamp columns")
            count, max_settled_at, max_recorded_at = conn.execute(
                f"SELECT COUNT(*), COALESCE(MAX({settled_col}), ''), "
                f"COALESCE(MAX({recorded_col}), '') FROM {table}"
            ).fetchone()
    except Exception as exc:
        return {
            "ok": False,
            "path": path,
            "issue": "SETTLEMENT_TRUTH_UNAVAILABLE",
            "error": str(exc),
        }
    count = int(count or 0)
    max_settled_at = max_settled_at or None
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
        "max_recorded_at": max_recorded_at or None,
        "age_s": age,
        "stale_budget_s": SETTLEMENT_TRUTH_STALE_SECONDS,
        "issue": issue,
    }

def _status_process_contract(status, daemon_pids):
    process = status.get("process") if isinstance(status, dict) else None
    if not isinstance(process, dict) or "pid" not in process:
        return {"ok": True, "issue": None, "pid": None, "daemon_pids": list(daemon_pids or [])}
    try:
        status_pid = int(process.get("pid") or 0)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "issue": "STATUS_SUMMARY_PROCESS_PID_INVALID",
            "pid": process.get("pid"),
            "daemon_pids": list(daemon_pids or []),
        }
    live_pids = [int(pid) for pid in (daemon_pids or []) if int(pid) > 0]
    if status_pid <= 0:
        return {
            "ok": False,
            "issue": "STATUS_SUMMARY_PROCESS_PID_INVALID",
            "pid": status_pid,
            "daemon_pids": live_pids,
        }
    if live_pids and status_pid not in live_pids:
        return {
            "ok": False,
            "issue": "STATUS_SUMMARY_PROCESS_PID_MISMATCH",
            "pid": status_pid,
            "daemon_pids": live_pids,
        }
    return {"ok": True, "issue": None, "pid": status_pid, "daemon_pids": live_pids}

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

def _porcelain_dirty_paths(status_text):
    paths = []
    for raw_line in str(status_text or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("?? "):
            path_text = line[3:]
        elif len(line) >= 3 and line[2] == " ":
            path_text = line[3:]
        elif len(line) >= 2 and line[1] == " ":
            path_text = line[2:]
        else:
            parts = line.split(maxsplit=1)
            path_text = parts[1] if len(parts) == 2 else line
        if " -> " in path_text:
            path_text = path_text.rsplit(" -> ", 1)[1]
        paths.append(path_text)
    return paths

def _code_plane_dirty_state(status_text):
    paths = _porcelain_dirty_paths(status_text)
    material = [
        p for p in paths
        if p not in VOLATILE_CODE_PLANE_DIRTY_PATHS
        and (
            p in MATERIAL_CODE_PLANE_DIRTY_FILES
            or any(p.startswith(prefix) for prefix in MATERIAL_CODE_PLANE_DIRTY_PREFIXES)
        )
    ]
    ignored = [p for p in paths if p not in material]
    return bool(material), material, ignored

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
    dirty = None
    dirty_paths = []
    ignored_dirty_paths = []
    if not status_error:
        dirty, dirty_paths, ignored_dirty_paths = _code_plane_dirty_state(status_text)
    expected_ref = os.environ.get("ZEUS_LIVE_EXPECTED_REF", DEFAULT_EXPECTED_REF).strip()
    expected_commit = os.environ.get("ZEUS_LIVE_EXPECTED_COMMIT", "").strip()
    expected_error = None
    if not expected_commit and expected_ref:
        expected_commit, expected_error = _git_text(root, ["rev-parse", expected_ref])
    elif not expected_commit:
        expected_ref = "HEAD"
        expected_commit = head
    return {
        "status": "ok" if expected_commit and dirty is not None else "incomplete",
        "repo": root,
        "head": head,
        "branch": branch,
        "dirty": dirty,
        "dirty_paths": dirty_paths,
        "ignored_dirty_paths": ignored_dirty_paths,
        "expected_ref": expected_ref,
        "expected_commit": expected_commit,
        "expected_error": expected_error,
        "matches_expected": bool(head and expected_commit and head == expected_commit),
    }

def _table_columns(conn, table):
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()

def _entry_q_version_status(root):
    """Read-only proof that live entry commands retain q-authority identity."""

    trade_db = os.path.join(root, "state/zeus_trades.db")
    if not os.path.exists(trade_db):
        return {"ok": True, "evaluated": False, "issue": "TRADE_DB_MISSING"}
    try:
        conn = sqlite3.connect(f"file:{trade_db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "evaluated": True,
            "issue": f"ENTRY_Q_VERSION_READ_UNAVAILABLE:{exc}",
        }
    try:
        command_columns = _table_columns(conn, "venue_commands")
        if not command_columns:
            return {
                "ok": False,
                "evaluated": True,
                "issue": "ENTRY_Q_VERSION_TABLE_MISSING:venue_commands",
            }
        required_command_columns = {
            "command_id",
            "position_id",
            "intent_kind",
            "state",
            "created_at",
            "q_version",
        }
        missing_command = sorted(required_command_columns - command_columns)
        if missing_command:
            return {
                "ok": False,
                "evaluated": True,
                "issue": "ENTRY_Q_VERSION_COLUMN_MISSING:" + ",".join(missing_command),
            }

        active_missing = 0
        active_sample = []
        position_columns = _table_columns(conn, "position_current")
        if position_columns:
            required_position_columns = {
                "position_id",
                "phase",
                "order_status",
                "shares",
                "chain_shares",
            }
            missing_position = sorted(required_position_columns - position_columns)
            if missing_position:
                return {
                    "ok": False,
                    "evaluated": True,
                    "issue": "ENTRY_Q_VERSION_COLUMN_MISSING:" + ",".join(missing_position),
                }
            active_missing = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT pc.position_id)
                      FROM position_current pc
                      JOIN venue_commands vc
                        ON vc.position_id = pc.position_id
                     WHERE vc.intent_kind = 'ENTRY'
                       AND pc.phase IN ('active', 'day0_window', 'pending_exit')
                       AND (
                           COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                           OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
                       )
                       AND (vc.q_version IS NULL OR TRIM(CAST(vc.q_version AS TEXT)) = '')
                    """
                ).fetchone()[0]
                or 0
            )
            active_sample = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT pc.position_id, pc.phase, pc.order_status,
                           pc.shares, pc.chain_shares,
                           vc.command_id, vc.state, vc.created_at
                      FROM position_current pc
                      JOIN venue_commands vc
                        ON vc.position_id = pc.position_id
                     WHERE vc.intent_kind = 'ENTRY'
                       AND pc.phase IN ('active', 'day0_window', 'pending_exit')
                       AND (
                           COALESCE(CAST(pc.chain_shares AS REAL), 0.0) > 0.0
                           OR COALESCE(CAST(pc.shares AS REAL), 0.0) > 0.0
                       )
                       AND (vc.q_version IS NULL OR TRIM(CAST(vc.q_version AS TEXT)) = '')
                     ORDER BY datetime(vc.created_at) DESC, vc.command_id DESC
                     LIMIT ?
                    """,
                    (ENTRY_Q_VERSION_SAMPLE_LIMIT,),
                ).fetchall()
            ]

        recent_missing = int(
            conn.execute(
                """
                SELECT COUNT(*)
                  FROM venue_commands
                 WHERE intent_kind = 'ENTRY'
                   AND datetime(created_at) >= datetime('now', ?)
                   AND (q_version IS NULL OR TRIM(CAST(q_version AS TEXT)) = '')
                """,
                (f"-{ENTRY_Q_VERSION_LOOKBACK_SECONDS} seconds",),
            ).fetchone()[0]
            or 0
        )
        recent_sample = [
            dict(row)
            for row in conn.execute(
                """
                SELECT command_id, position_id, state, created_at
                  FROM venue_commands
                 WHERE intent_kind = 'ENTRY'
                   AND datetime(created_at) >= datetime('now', ?)
                   AND (q_version IS NULL OR TRIM(CAST(q_version AS TEXT)) = '')
                 ORDER BY datetime(created_at) DESC, command_id DESC
                 LIMIT ?
                """,
                (
                    f"-{ENTRY_Q_VERSION_LOOKBACK_SECONDS} seconds",
                    ENTRY_Q_VERSION_SAMPLE_LIMIT,
                ),
            ).fetchall()
        ]
        detail = {
            "ok": active_missing == 0 and recent_missing == 0,
            "evaluated": True,
            "active_missing_q_version_count": active_missing,
            "active_missing_q_version_sample": active_sample,
            "recent_missing_q_version_count": recent_missing,
            "recent_missing_q_version_sample": recent_sample,
            "lookback_seconds": ENTRY_Q_VERSION_LOOKBACK_SECONDS,
        }
        if active_missing > 0:
            detail["issue"] = f"ENTRY_Q_VERSION_MISSING_ACTIVE_EXPOSURE:n={active_missing}"
        elif recent_missing > 0:
            detail["issue"] = f"ENTRY_Q_VERSION_MISSING_RECENT_ENTRY:n={recent_missing}"
        else:
            detail["issue"] = None
        return detail
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "evaluated": True,
            "issue": f"ENTRY_Q_VERSION_READ_UNAVAILABLE:{exc}",
        }
    finally:
        conn.close()

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
    entry_q_version = report.get("entry_q_version", {})
    if entry_q_version.get("ok") is False:
        alerts.append(entry_q_version.get("issue") or "ENTRY_Q_VERSION_UNHEALTHY")
    status_process = report.get("status_process", {})
    if status_process.get("ok") is False:
        alerts.append(status_process.get("issue") or "STATUS_SUMMARY_PROCESS_CONTRACT")
    composite = report.get("live_health_composite") or {}
    if composite and "error" not in composite:
        surfaces = composite.get("surfaces") if isinstance(composite.get("surfaces"), dict) else {}
        missing_surfaces = [
            surface for surface in REQUIRED_LIVE_HEALTH_SURFACES
            if surface not in surfaces
        ]
        if missing_surfaces:
            alerts.append(
                "LIVE_HEALTH_COMPOSITE_SURFACES_MISSING="
                + "|".join(missing_surfaces[:8])
            )
    if composite.get("status") == "DEGRADED" or composite.get("healthy") is False:
        surfaces = composite.get("surfaces") if isinstance(composite.get("surfaces"), dict) else {}
        failing = composite.get("failing_surfaces") or []
        for surface in failing:
            detail = surfaces.get(surface) if isinstance(surfaces, dict) else None
            issue = detail.get("issue") if isinstance(detail, dict) else None
            alerts.append(f"LIVE_HEALTH_{surface.upper()}={issue or 'DEGRADED'}")
        if not failing:
            alerts.append("LIVE_HEALTH_COMPOSITE_DEGRADED")
    if ss_age is not None and ss_age > 2700:
        alerts.append(f"cycle_stale={ss_age}s")
    if report.get("cycle", {}).get("ws_connected") is False:
        alerts.append("ws_disconnected")
    if report.get("cycle", {}).get("risk_level") not in ("GREEN", None):
        alerts.append(f"risk={report['cycle']['risk_level']}")
    entry_status = report.get("entry_capable", {}).get("status")
    if entry_status == "unavailable" or report.get("blocking_gates"):
        alerts.append("entry_unavailable")
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
    forecast_hb_age, forecast_hb_age_source = _heartbeat_payload_age(forecast_hb)
    if forecast_hb_age is None:
        forecast_hb_age = _age(forecast_hb_path)
        forecast_hb_age_source = "mtime"
    report["forecast_live_hb"] = {
        "alive": forecast_hb.get("alive"),
        "status": forecast_hb.get("status"),
        "age_s": forecast_hb_age,
        "age_source": forecast_hb_age_source,
    }

    # Process liveness
    report["procs"] = _process_liveness()
    report["code_plane"] = _git_runtime_identity(ROOT)
    report["process_code"] = _process_loaded_code_status(report["procs"], ROOT)
    report["settlement_truth"] = _settlement_truth_status(ROOT)
    report["entry_q_version"] = _entry_q_version_status(ROOT)

    # Status summary
    ss_path = os.path.join(ROOT, "state/status_summary.json")
    ss_age = _age(ss_path)
    report["status_summary_age_s"] = ss_age
    composite_path = os.path.join(ROOT, "state/live_health_composite.json")
    report["live_health_composite"] = _load_json(composite_path)
    try:
        ss = _load_json(ss_path)
        if ss.get("error"):
            raise RuntimeError(ss["error"])
        cycle = ss.get("cycle", {})
        runtime = ss.get("runtime", {})
        risk = ss.get("risk", {})
        funnel = ss.get("lifecycle_funnel", {})
        ws = cycle.get("ws_user_channel", {})
        report["status_process"] = _status_process_contract(
            ss,
            report.get("procs", {}).get("daemon") or [],
        )
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
    runtime_health = "DEAD" if "daemon_dead" in alerts else ("DEGRADED" if alerts else "OK")
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
        f"runtime_health={runtime_health} "
        f"cycle_risk={report.get('cycle',{}).get('risk_level','?')} "
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
