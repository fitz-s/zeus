#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-07-04; last_reused=2026-07-04
# Purpose: heartbeat funnel CLI that answers '为什么没单' in 5 seconds — one invocation
#   prints the full money path (daemons → events → blocks → surface → positions).
# Reuse: READ-ONLY over the three live DBs (zeus-world.db / zeus_trades.db /
#   zeus-forecasts.db) via file:...?mode=ro. Registered in SQLITE_CONNECT_ALLOWLIST
#   (src/state/db_writer_lock.py). ISO-T cutoff law (probe_lib §law1) baked in: never
#   compare T-format timestamps to datetime('now') — the 'T'>' ' lexicographic trap
#   silently widens every window to "all of today".
# Last reused/audited: 2026-07-04
# Authority basis: operator big-direction 2026-06-12 ("大方向现在也只是添加几个文件现在做")
#   + external-review mediums inventory 2026-06-12 (price-cache hole census, Task 3)
"""Zeus money-funnel heartbeat — one invocation, full picture, ~5 seconds.

Prints, top to bottom, the path money would travel and where it stops:

  DAEMONS   launchctl com.zeus processes (pid / last-exit-status).
  EVENTS    opportunity_events backlog + processing outcomes (zeus-world.db).
  BLOCKS    no_trade_regret_events rejection_stage/reason, classified into
            substrate-transient vs honest-economics vs unknown (zeus-world.db).
  SURFACE   forecast posteriors (+ q_lcb coverage & basis), price-cache
            coverage, YES-side screen-edge counts (zeus-forecasts + zeus_trades).
  POSITIONS open position_current rows + belief-freshness (zeus_trades.db).
  ORDERS    venue_commands recent state + 24h fills (zeus_trades.db).

Every section is fail-soft: a locked / missing DB prints "ERR …" for that
section only; the rest of the funnel still prints. Read-only always
(mode=ro URI + PRAGMA query_only=ON). --json dumps the same data as JSON.

USAGE
    .venv/bin/python scripts/zeus_status.py
    .venv/bin/python scripts/zeus_status.py --json
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Live DB paths (absolute; read-only). These are the K1-split canonical DBs.
# --------------------------------------------------------------------------
STATE = "/Users/leofitz/zeus/state"
WORLD_DB = f"{STATE}/zeus-world.db"
TRADES_DB = f"{STATE}/zeus_trades.db"
FORECASTS_DB = f"{STATE}/zeus-forecasts.db"
EVENT_REACTOR_CONSUMER = "edli_reactor_v1"
EVENT_PROCESSING_STATUSES = (
    "processed",
    "ignored",
    "expired",
    "dead_letter",
    "pending",
    "processing",
)
EXPECTED_DAEMONS = (
    "live-trading",
    "data-ingest",
    "forecast-live",
    "substrate-observer",
    "price-channel-ingest",
    "post-trade-capital",
    "riskguard-live",
    "venue-heartbeat",
)
LOAD_BEARING_DAEMONS = {
    "live-trading",
    "data-ingest",
    "forecast-live",
    "substrate-observer",
    "price-channel-ingest",
    "post-trade-capital",
}
DAEMON_HEARTBEATS = {
    "live-trading": "daemon-heartbeat.json",
    "data-ingest": "daemon-heartbeat-ingest.json",
    "forecast-live": "forecast-live-heartbeat.json",
    "substrate-observer": "daemon-heartbeat-substrate-observer.json",
    "price-channel-ingest": "daemon-heartbeat-price-channel-ingest.json",
    "post-trade-capital": "daemon-heartbeat-post-trade-capital.json",
}
DAEMON_HEARTBEAT_STALE_SECONDS = 300.0
OPEN_POSITION_PHASES = ("active", "day0_window", "pending_exit")

# Substrate-transient vs honest-economics classification for BLOCKS.
# Substring sets (case-insensitive) — display-only, not authority.
#
# We classify on the REASON, not the stage name: stage names like
# "TRADE_SCORE" are economic-sounding but routinely carry substrate reasons
# (e.g. LIVE_INFERENCE_INPUTS_MISSING = a missing q_ucb input, which is a
# data-availability problem, NOT honest "no edge"). The reason text is the
# decisive signal; transient patterns are checked first because a missing
# input / blocked snapshot / non-submit scope gate means we never even got to
# weigh the economics.
_TRANSIENT_TOKENS = (
    "TRANSIENT", "STALE", "LOCK", "NOT_COMPLETE", "NOT_LIVE_ELIGIBLE",
    "SNAPSHOT_BLOCKED", "EXHAUSTED", "SOURCE_RUN", "FSR_",
    "NATIVE_ASK_MISSING", "INPUTS_MISSING", "MISSING", "BUSY", "TIMEOUT",
    "RETRY", "DEGRADED", "REVIEW_REQUIRED", "AVAILABILITY", "FRESH",
    "SCOPE", "UNAVAILABLE", "RISK_GUARD",
)
_ECONOMIC_TOKENS = (
    "NON_POSITIVE", "NEGATIVE", "EDGE", "NO_EDGE", "Q_LCB",
    "EXPRESSIB", "BOUNDARY", "KELLY", "SIZING", "REVERSED", "COVERAGE",
    "P_FILL", "SCORE_BELOW", "BELOW_BAR", "EV_",
)


# --------------------------------------------------------------------------
# Read-only connection + time helpers (probe_lib pattern, inlined so this
# script is self-contained on any branch).
# --------------------------------------------------------------------------
def ro(db_path: str, timeout: float = 4.0) -> sqlite3.Connection:
    """Read-only connection to a live DB (the only sanctioned probe mode)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def iso_cutoff(hours: float = 0.0, minutes: float = 0.0) -> str:
    """UTC cutoff in the SAME ISO-T format Zeus persists (probe_lib law 1).

    ``WHERE created_at > ?`` with this value is the ONLY correct recency
    filter against T-format timestamp columns. datetime('now') renders a
    SPACE separator and 'T'(0x54) > ' '(0x20), so a naive comparison admits
    every same-day row.
    """
    dt = datetime.now(timezone.utc) - timedelta(hours=hours, minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def age_str(ts: str | None) -> str:
    """Render an ISO timestamp as a compact age ('6m', '4.8h', '3.1d')."""
    if not ts:
        return "-"
    s = str(ts).strip().replace(" ", "T")
    # Tolerate trailing 'Z' and fractional seconds; normalize to aware UTC.
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return "?"
    delta = (_now() - dt).total_seconds()
    if delta < 0:
        delta = 0.0
    if delta < 90:
        return f"{int(delta)}s"
    if delta < 5400:
        return f"{delta / 60:.0f}m"
    if delta < 172800:
        return f"{delta / 3600:.1f}h"
    return f"{delta / 86400:.1f}d"


def classify_block(stage: str | None, reason: str | None) -> str:
    """transient | economic | unknown — reason-led substring heuristic, display only.

    The REASON dominates the stage name: a substrate cause (missing input,
    blocked snapshot, non-submit scope gate, riskguard storm) means the trade
    never reached an honest economic verdict, so transient is checked first
    and against the reason text. The stage name is only a weak tiebreaker.
    """
    reason_u = (reason or "").upper()
    stage_u = (stage or "").upper()
    # 1. Reason carries the decisive signal — check it first.
    if any(t in reason_u for t in _TRANSIENT_TOKENS):
        return "transient"
    if any(t in reason_u for t in _ECONOMIC_TOKENS):
        return "economic"
    # 2. No verdict from the reason — fall back to the stage name.
    if any(t in stage_u for t in _TRANSIENT_TOKENS):
        return "transient"
    if any(t in stage_u for t in _ECONOMIC_TOKENS):
        return "economic"
    return "unknown"


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return current table columns; empty if the table is missing."""

    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def select_column_or_null(columns: set[str], column: str) -> str:
    return column if column in columns else f"NULL AS {column}"


# --------------------------------------------------------------------------
# Section: DAEMONS
# --------------------------------------------------------------------------
def section_daemons() -> dict:
    out: dict = {"rows": []}
    try:
        res = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=8.0,
        )
        if res.returncode != 0:
            out["error"] = f"launchctl rc={res.returncode}"
            return out
        observed: dict[str, dict] = {}
        for line in res.stdout.splitlines():
            if "com.zeus" not in line:
                continue
            parts = line.split("\t") if "\t" in line else line.split()
            if len(parts) < 3:
                continue
            pid, status, label = parts[0], parts[1], parts[-1]
            short_label = label.replace("com.zeus.", "")
            observed[short_label] = {
                "label": short_label,
                "pid": pid,
                "status": status,
                "present": True,
            }
        ordered = list(EXPECTED_DAEMONS)
        ordered.extend(label for label in sorted(observed) if label not in EXPECTED_DAEMONS)
        rows: list[dict] = []
        for label in ordered:
            row = dict(
                observed.get(
                    label,
                    {
                        "label": label,
                        "pid": "-",
                        "status": "missing",
                        "present": False,
                    },
                )
            )
            pid_text = str(row.get("pid") or "-")
            row["critical"] = label in LOAD_BEARING_DAEMONS
            row["alive"] = _pid_is_alive(pid_text)
            issues: list[str] = []
            if not row.get("present"):
                issues.append("missing_from_launchctl")
            if pid_text in {"-", "", "0"}:
                issues.append("pid_missing")
            elif not row["alive"]:
                issues.append("pid_not_running")
            heartbeat = _daemon_heartbeat_status(label)
            if heartbeat is not None:
                row["heartbeat"] = heartbeat
                if not heartbeat.get("ok", False):
                    issues.append(str(heartbeat.get("issue") or "heartbeat_unhealthy"))
            row["ok"] = bool(row["alive"]) and not any(
                issue.startswith("heartbeat_") for issue in issues
            )
            row["issues"] = issues
            rows.append(row)
        out["rows"] = rows
    except (subprocess.SubprocessError, OSError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _pid_is_alive(pid_text: str) -> bool:
    if pid_text in {"-", "", "0"}:
        return False
    try:
        int(pid_text)
    except ValueError:
        return False
    try:
        res = subprocess.run(
            ["ps", "-p", pid_text],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return res.returncode == 0


def _current_git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def _daemon_heartbeat_status(label: str) -> dict | None:
    filename = DAEMON_HEARTBEATS.get(label)
    if not filename:
        return None
    path = Path(STATE) / filename
    if not path.exists():
        return {"ok": False, "issue": "heartbeat_missing", "path": str(path)}
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return {
            "ok": False,
            "issue": "heartbeat_unreadable",
            "path": str(path),
            "error": f"{type(exc).__name__}: {exc}",
        }
    raw_ts = (
        payload.get("timestamp")
        or payload.get("alive_at")
        or payload.get("written_at")
        or payload.get("generated_at")
    )
    try:
        parsed = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "issue": "heartbeat_timestamp_invalid",
            "path": str(path),
            "timestamp": raw_ts,
        }
    age_seconds = (_now() - parsed).total_seconds()
    heartbeat_git_head = str(payload.get("git_head") or "").strip()
    current_git_head = _current_git_head()
    git_head_matches = True
    if heartbeat_git_head and current_git_head:
        git_head_matches = (
            current_git_head.startswith(heartbeat_git_head)
            or heartbeat_git_head.startswith(current_git_head)
        )
    fresh = 0.0 <= age_seconds <= DAEMON_HEARTBEAT_STALE_SECONDS
    ok = fresh and git_head_matches
    issue = None
    if not fresh:
        issue = "heartbeat_stale"
    elif not git_head_matches:
        issue = "heartbeat_git_head_mismatch"
    return {
        "ok": ok,
        "issue": issue,
        "path": str(path),
        "timestamp": parsed.isoformat(),
        "age_seconds": round(age_seconds, 1),
        "git_head": heartbeat_git_head or None,
        "current_git_head": current_git_head or None,
    }


# --------------------------------------------------------------------------
# Section: EVENTS (zeus-world.db)
# --------------------------------------------------------------------------
def section_events() -> dict:
    out: dict = {}
    try:
        conn = ro(WORLD_DB)
        try:
            c1 = iso_cutoff(hours=1)
            c24 = iso_cutoff(hours=24)
            # Current backlog = reactor-owned rows currently marked pending.
            #
            # Do not scan opportunity_events with a NOT EXISTS over the full
            # processing history here. That answers a historical data-shape
            # question, not the operator's current runtime question, and it
            # hangs on live DBs with millions of ignored cache events.
            out["pending"] = conn.execute(
                "SELECT count(*) FROM opportunity_event_processing "
                "WHERE consumer_name=? AND processing_status='pending'",
                (EVENT_REACTOR_CONSUMER,),
            ).fetchone()[0]

            def _proc_counts(cut: str) -> dict:
                counts: dict[str, int] = {}
                for status in EVENT_PROCESSING_STATUSES:
                    n = conn.execute(
                        "SELECT count(*) FROM opportunity_event_processing "
                        "WHERE consumer_name=? AND processing_status=? AND updated_at > ?",
                        (EVENT_REACTOR_CONSUMER, status, cut),
                    ).fetchone()[0]
                    if n:
                        counts[status] = int(n)
                return counts

            out["proc_1h"] = _proc_counts(c1)
            out["proc_24h"] = _proc_counts(c24)
            out["dead_reasons_24h"] = [
                {"stage": r[0], "n": r[1]}
                for r in conn.execute(
                    "SELECT failure_stage, count(*) FROM event_dead_letters "
                    "WHERE created_at > ? GROUP BY failure_stage "
                    "ORDER BY count(*) DESC LIMIT 5",
                    (c24,),
                ).fetchall()
            ]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# --------------------------------------------------------------------------
# Section: BLOCKS (zeus-world.db, no_trade_regret_events)
# --------------------------------------------------------------------------
def section_blocks() -> dict:
    out: dict = {}
    try:
        conn = ro(WORLD_DB)
        try:
            def _window(cut: str) -> dict:
                rows = conn.execute(
                    "SELECT rejection_stage, rejection_reason, count(*) AS n "
                    "FROM no_trade_regret_events WHERE created_at > ? "
                    "GROUP BY rejection_stage, rejection_reason "
                    "ORDER BY n DESC LIMIT 10",
                    (cut,),
                ).fetchall()
                klass = Counter()
                top = []
                for r in rows:
                    cls = classify_block(r["rejection_stage"], r["rejection_reason"])
                    klass[cls] += r["n"]
                    top.append(
                        {
                            "stage": r["rejection_stage"],
                            "reason": r["rejection_reason"],
                            "n": r["n"],
                            "class": cls,
                        }
                    )
                total = conn.execute(
                    "SELECT count(*) FROM no_trade_regret_events WHERE created_at > ?",
                    (cut,),
                ).fetchone()[0]
                return {"total": total, "top": top, "class": dict(klass)}

            out["w2h"] = _window(iso_cutoff(hours=2))
            out["w24h"] = _window(iso_cutoff(hours=24))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# --------------------------------------------------------------------------
# Section: SURFACE (zeus-forecasts.db posteriors + zeus_trades.db price cache)
# --------------------------------------------------------------------------
def section_surface() -> dict:
    out: dict = {}
    today = _now().strftime("%Y-%m-%d")
    # --- posteriors (forecasts DB) ---
    try:
        fc = ro(FORECASTS_DB)
        try:
            # Latest posterior per family today+forward.
            fams = fc.execute(
                "SELECT city, target_date, temperature_metric, "
                "       q_lcb_json, provenance_json, computed_at "
                "FROM forecast_posteriors p "
                "WHERE target_date >= ? "
                "  AND computed_at = (SELECT max(computed_at) FROM forecast_posteriors q "
                "        WHERE q.city=p.city AND q.target_date=p.target_date "
                "          AND q.temperature_metric=p.temperature_metric)",
                (today,),
            ).fetchall()
            n_fam = len(fams)
            n_qlcb = 0
            basis = Counter()
            for r in fams:
                if r["q_lcb_json"]:
                    n_qlcb += 1
                try:
                    prov = json.loads(r["provenance_json"]) if r["provenance_json"] else {}
                    b = prov.get("q_lcb_basis") or "none"
                except (json.JSONDecodeError, TypeError):
                    b = "?"
                basis[b] += 1
            out["families"] = n_fam
            out["families_with_qlcb"] = n_qlcb
            out["qlcb_basis"] = dict(basis.most_common(6))
        finally:
            fc.close()
    except sqlite3.Error as exc:
        out["posteriors_error"] = f"{type(exc).__name__}: {exc}"

    # --- price-cache coverage + screen-edge (trades DB snapshots, forecasts bins) ---
    try:
        tr = ro(TRADES_DB)
        fc2 = ro(FORECASTS_DB)
        try:
            # price-cache coverage: latest current surface, not the full historical
            # snapshot ledger. The historical table is audit evidence; status must
            # answer the current operator question in bounded time.
            cov = tr.execute(
                "SELECT count(DISTINCT condition_id) FROM executable_market_snapshot_latest "
                "WHERE captured_at > ?",
                (iso_cutoff(hours=24),),
            ).fetchone()[0]
            out["price_cache_conditions_24h"] = cov
            # captured_at age percentiles over latest condition surface.
            ages = tr.execute(
                "SELECT max(captured_at) AS captured_at "
                "FROM executable_market_snapshot_latest "
                "WHERE captured_at > ? "
                "GROUP BY condition_id "
                "ORDER BY captured_at",
                (iso_cutoff(hours=24),),
            ).fetchall()
            if ages:
                n = len(ages)
                p50 = ages[n // 2]["captured_at"]
                p90 = ages[max(0, int(n * 0.1))]["captured_at"]  # 10th oldest = ~p90 stale
                out["price_age_p50"] = age_str(p50)
                out["price_age_p90"] = age_str(p90)
            # screen-edge: YES-side q_lcb - yes_ask > 3pt / 5pt, label-joined per bin.
            e3, e5 = _screen_edges(fc2, tr, today)
            out["yes_edge_gt3pt"] = e3
            out["yes_edge_gt5pt"] = e5
        finally:
            tr.close()
            fc2.close()
    except sqlite3.Error as exc:
        out["pricecache_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _screen_edges(fc: sqlite3.Connection, tr: sqlite3.Connection, today: str) -> tuple[int, int]:
    """Count YES bins where our q_lcb beats the live YES ask by >3pt / >5pt.

    Join logic mirrors scripts/probe_favorite_capture.py: market_events gives
    (city,date,range_label,condition_id); forecast_posteriors.q_lcb_json gives
    our per-label q_lcb; executable_market_snapshots (YES outcome) gives the ask.
    """
    e3 = e5 = 0
    fams = fc.execute(
        "SELECT city, target_date, temperature_metric, q_lcb_json FROM forecast_posteriors p "
        "WHERE target_date >= ? AND q_lcb_json IS NOT NULL "
        "  AND computed_at = (SELECT max(computed_at) FROM forecast_posteriors q "
        "        WHERE q.city=p.city AND q.target_date=p.target_date "
        "          AND q.temperature_metric=p.temperature_metric)",
        (today,),
    ).fetchall()
    for f in fams:
        try:
            qlcb = json.loads(f["q_lcb_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        # temperature_metric filter: same city/date carries both HIGH and LOW
        # markets — joining a HIGH posterior to LOW condition_ids (or vice
        # versa) reports edge for the wrong market family (external review
        # 2026-06-12).
        bins = fc.execute(
            "SELECT range_label, condition_id FROM market_events "
            "WHERE city=? AND target_date=? AND temperature_metric=?",
            (f["city"], f["target_date"], f["temperature_metric"]),
        ).fetchall()
        for b in bins:
            cond = b["condition_id"]
            label = b["range_label"]
            if not cond or label not in qlcb:
                continue
            row = tr.execute(
                "SELECT orderbook_top_ask FROM executable_market_snapshot_latest "
                "WHERE condition_id=? AND outcome_label='YES' "
                "ORDER BY captured_at DESC LIMIT 1",
                (cond,),
            ).fetchone()
            if not row or row["orderbook_top_ask"] is None:
                continue
            try:
                ask = float(row["orderbook_top_ask"])
                edge = float(qlcb[label]) - ask
            except (TypeError, ValueError):
                continue
            if edge > 0.03:
                e3 += 1
            if edge > 0.05:
                e5 += 1
    return e3, e5


# --------------------------------------------------------------------------
# Section: SELECTION (C2 winner's-curse slope diagnostic) — read-only
# --------------------------------------------------------------------------
def section_selection() -> dict:
    """Winner's-curse slope diagnostic (authority addendum 2026-06-13 D3).

    When >= 20 SETTLED traded receipts carry a non-NULL edge_shrunk, regress
    realized PnL/contract on the shrunk edge and report slope + intercept. A
    slope near 1 means the EB-shrunk edge is an unbiased predictor of realized
    edge (winner's curse corrected); slope << 1 means still under-shrinking;
    intercept != 0 means residual center bias. This is the settlement-graded
    winner's-curse diagnostic over the EB-shrinkage audit columns (the
    decision-replacement flag was removed 2026-06-13; the live selection gate is
    the BH/FDR pass unconditionally — these columns remain settlement telemetry).

    Join: edli_no_submit_receipts (WORLD: token_id, edge_shrunk,
    selection_authority) <-> position_current settled rows (TRADES: token_id,
    realized_pnl_usd, shares). Read-only, fail-soft per section.
    """
    out: dict = {}
    try:
        wconn = ro(WORLD_DB)
        try:
            cols = {
                r[1]
                for r in wconn.execute("PRAGMA table_info(edli_no_submit_receipts)").fetchall()
            }
            if "edge_shrunk" not in cols:
                # Pre-migration window: the column is added on the next daemon
                # boot (_ensure_column). Honest status, not an error.
                out["status"] = "edge_shrunk column not yet migrated on this DB"
                out["receipts_with_edge_shrunk"] = 0
                return out
            receipts = wconn.execute(
                "SELECT token_id, edge_shrunk, selection_authority "
                "FROM edli_no_submit_receipts "
                "WHERE edge_shrunk IS NOT NULL AND token_id IS NOT NULL"
            ).fetchall()
        finally:
            wconn.close()
        shrunk_by_token: dict[str, float] = {}
        authority_by_token: dict[str, str] = {}
        for r in receipts:
            shrunk_by_token[r["token_id"]] = float(r["edge_shrunk"])
            if r["selection_authority"]:
                authority_by_token[r["token_id"]] = r["selection_authority"]
        out["receipts_with_edge_shrunk"] = len(shrunk_by_token)
        if not shrunk_by_token:
            out["status"] = "no receipts carry edge_shrunk yet (audit column not populated)"
            return out

        tconn = ro(TRADES_DB)
        try:
            settled = tconn.execute(
                "SELECT token_id, realized_pnl_usd, shares FROM position_current "
                "WHERE phase='settled' AND realized_pnl_usd IS NOT NULL "
                "  AND shares IS NOT NULL AND shares > 0 AND token_id IS NOT NULL"
            ).fetchall()
        finally:
            tconn.close()

        xs: list[float] = []  # shrunk edge
        ys: list[float] = []  # realized pnl / contract
        for s in settled:
            tok = s["token_id"]
            if tok not in shrunk_by_token:
                continue
            try:
                pnl_per_contract = float(s["realized_pnl_usd"]) / float(s["shares"])
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            xs.append(shrunk_by_token[tok])
            ys.append(pnl_per_contract)

        n = len(xs)
        out["settled_traded_with_edge_shrunk"] = n
        if n < 20:
            out["status"] = f"need >=20 settled traded receipts with edge_shrunk (have {n})"
            return out

        # Ordinary least-squares slope + intercept (no numpy dependency).
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        sxx = sum((x - mean_x) ** 2 for x in xs)
        sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        if sxx <= 0.0:
            out["status"] = "degenerate: zero variance in shrunk edge"
            return out
        slope = sxy / sxx
        intercept = mean_y - slope * mean_x
        out["slope"] = round(slope, 4)
        out["intercept"] = round(intercept, 5)
        out["n"] = n
        out["interpretation"] = (
            "slope~1 = unbiased (winner's curse corrected); "
            "slope<<1 = under-shrinking; intercept!=0 = residual center bias"
        )
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# --------------------------------------------------------------------------
# Section: POSITIONS (zeus_trades.db)
# --------------------------------------------------------------------------
def section_positions() -> dict:
    out: dict = {}
    try:
        conn = ro(TRADES_DB)
        try:
            columns = table_columns(conn, "position_current")
            placeholders = ",".join("?" for _ in OPEN_POSITION_PHASES)
            select_columns = (
                "phase",
                "city",
                "target_date",
                "bin_label",
                "direction",
                "shares",
                "entry_price",
                "last_monitor_prob",
                "last_monitor_market_price",
                "last_monitor_prob_is_fresh",
                "last_monitor_market_price_is_fresh",
                "chain_state",
                "updated_at",
            )
            order_expr = "updated_at DESC" if "updated_at" in columns else "rowid DESC"
            rows = conn.execute(
                "SELECT "
                + ", ".join(select_column_or_null(columns, column) for column in select_columns)
                + f" FROM position_current "
                  f"WHERE lower(coalesce(phase, '')) IN ({placeholders}) "
                  f"ORDER BY {order_expr} LIMIT 12",
                tuple(OPEN_POSITION_PHASES),
            ).fetchall()
            out["open"] = [
                {
                    "phase": r["phase"],
                    "city": r["city"],
                    "date": r["target_date"],
                    "bin": r["bin_label"],
                    "dir": r["direction"],
                    "shares": r["shares"],
                    "entry": r["entry_price"],
                    "mon_prob": r["last_monitor_prob"],
                    "mon_market_price": r["last_monitor_market_price"],
                    "mon_prob_fresh": r["last_monitor_prob_is_fresh"],
                    "mon_market_price_fresh": r["last_monitor_market_price_is_fresh"],
                    "chain_state": r["chain_state"],
                    "mon_age": age_str(r["updated_at"]),
                }
                for r in rows
            ]
            phase_counts = {
                str(r["phase"] or ""): int(r["n"] or 0)
                for r in conn.execute(
                    f"""
                    SELECT lower(coalesce(phase, '')) AS phase, count(*) AS n
                      FROM position_current
                     WHERE lower(coalesce(phase, '')) IN ({placeholders})
                     GROUP BY lower(coalesce(phase, ''))
                    """,
                    tuple(OPEN_POSITION_PHASES),
                ).fetchall()
            }
            out["n_open_by_phase"] = {phase: phase_counts.get(phase, 0) for phase in OPEN_POSITION_PHASES}
            out["n_open"] = sum(out["n_open_by_phase"].values())
            out["n_active"] = out["n_open_by_phase"].get("active", 0)
            # exit-fallback rate 24h: fraction of recently-settled with a fallback exit_reason.
            settled = conn.execute(
                "SELECT exit_reason, count(*) FROM position_current "
                "WHERE settled_at > ? GROUP BY exit_reason",
                (iso_cutoff(hours=24),),
            ).fetchall()
            if settled:
                tot = sum(r[1] for r in settled)
                # substring match on exit_reason text; lowercase needles so the
                # CI semantic classifier's state-literal regex does not read a
                # display filter as a new state-machine member (PR #407 P0 flag)
                _degraded_needles = ("fallback", "stale", "stuck")
                fb = sum(
                    r[1] for r in settled
                    if r[0] and any(n in r[0].lower() for n in _degraded_needles)
                )
                out["exit_fallback_24h"] = f"{fb}/{tot}"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# --------------------------------------------------------------------------
# Section: ORDERS (zeus_trades.db, venue_commands)
# --------------------------------------------------------------------------
def section_orders() -> dict:
    out: dict = {}
    try:
        conn = ro(TRADES_DB)
        try:
            out["last5"] = [
                {
                    "kind": r["intent_kind"],
                    "side": r["side"],
                    "size": r["size"],
                    "price": r["price"],
                    "state": r["state"],
                    "age": age_str(r["created_at"]),
                }
                for r in conn.execute(
                    "SELECT intent_kind, side, size, price, state, created_at "
                    "FROM venue_commands ORDER BY created_at DESC LIMIT 5"
                ).fetchall()
            ]
            out["state_24h"] = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT state, count(*) FROM venue_commands "
                    "WHERE created_at > ? GROUP BY state ORDER BY count(*) DESC",
                    (iso_cutoff(hours=24),),
                ).fetchall()
            }
            out["fills_24h"] = conn.execute(
                "SELECT count(*) FROM venue_commands "
                "WHERE created_at > ? AND state IN ('FILLED','PARTIAL')",
                (iso_cutoff(hours=24),),
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# --------------------------------------------------------------------------
# Collect + render
# --------------------------------------------------------------------------
def collect() -> dict:
    return {
        "generated_at": _now().isoformat(),
        "daemons": section_daemons(),
        "events": section_events(),
        "blocks": section_blocks(),
        "surface": section_surface(),
        "obs_holes": section_obs_holes(),
        "price_holes": section_price_holes(),
        "positions": section_positions(),
        "orders": section_orders(),
        "selection": section_selection(),
    }


def _err_line(d: dict) -> str | None:
    for k in ("error", "posteriors_error", "pricecache_error"):
        if k in d:
            return d[k]
    return None


def render_text(data: dict) -> str:
    L: list[str] = []
    L.append(f"ZEUS FUNNEL  {data['generated_at']}  (read-only)")
    L.append("=" * 72)

    # DAEMONS
    d = data["daemons"]
    if d.get("error"):
        L.append(f"DAEMONS  ERR {d['error']}")
    else:
        cells = []
        for r in d["rows"]:
            if r.get("ok"):
                cells.append(f"{r['label']}=pid{r['pid']}/{r['status']}")
                continue
            issues = ",".join(r.get("issues") or ["down"])
            cells.append(f"{r['label']}=DOWN({issues})")
        L.append("DAEMONS  " + ("  ".join(cells) if cells else "(none)"))
    L.append("")

    # EVENTS
    e = data["events"]
    if e.get("error"):
        L.append(f"EVENTS   ERR {e['error']}")
    else:
        p1 = e.get("proc_1h", {})
        p24 = e.get("proc_24h", {})

        def fmt(p: dict) -> str:
            order = ["processed", "ignored", "expired", "dead_letter", "pending"]
            return " ".join(f"{k}={p[k]}" for k in order if k in p) or "-"

        L.append(f"EVENTS   pending={e.get('pending', '?')}")
        L.append(f"         1h:  {fmt(p1)}")
        L.append(f"         24h: {fmt(p24)}")
        dr = e.get("dead_reasons_24h", [])
        if dr:
            L.append("         dead-letter 24h top: "
                     + ", ".join(f"{x['stage']}={x['n']}" for x in dr))
    L.append("")

    # BLOCKS
    b = data["blocks"]
    if b.get("error"):
        L.append(f"BLOCKS   ERR {b['error']}")
    else:
        for win, key in (("2h", "w2h"), ("24h", "w24h")):
            w = b.get(key, {})
            cls = w.get("class", {})
            clss = " ".join(
                f"{k}={cls.get(k, 0)}" for k in ("transient", "economic", "unknown")
            )
            L.append(f"BLOCKS {win:<4} total={w.get('total', 0):<7} [{clss}]")
            for t in w.get("top", [])[:5]:
                tag = {"transient": "~", "economic": "$", "unknown": "?"}[t["class"]]
                reason = (t["reason"] or "")[:34]
                L.append(f"   {tag} {t['stage']:<22} {reason:<34} {t['n']}")
    L.append("")

    # SURFACE
    s = data["surface"]
    ferr = s.get("posteriors_error")
    perr = s.get("pricecache_error")
    if ferr:
        L.append(f"SURFACE  ERR(post) {ferr}")
    else:
        basis = " ".join(f"{k}:{v}" for k, v in s.get("qlcb_basis", {}).items())
        L.append(
            f"SURFACE  families={s.get('families', '?')} "
            f"q_lcb={s.get('families_with_qlcb', '?')} "
            f"basis[{basis}]"
        )
    if perr:
        L.append(f"         ERR(price) {perr}")
    else:
        L.append(
            f"         price-cache conds(24h)={s.get('price_cache_conditions_24h', '?')} "
            f"age p50={s.get('price_age_p50', '-')} p90={s.get('price_age_p90', '-')}"
        )
        L.append(
            f"         YES screen-edge: >3pt={s.get('yes_edge_gt3pt', '?')} "
            f">5pt={s.get('yes_edge_gt5pt', '?')}"
        )
    L.append("")

    # OBS HOLES (data holes are not allowed — operator law 2026-06-12)
    oh = data["obs_holes"]
    if oh.get("error"):
        L.append(f"OBS      ERR {oh['error']}")
    else:
        holes = oh.get("holes", [])
        if holes:
            names = ", ".join(f"{h['city']}({h['age']})" for h in holes[:10])
            more = f" +{len(holes) - 10} more" if len(holes) > 10 else ""
            L.append(
                f"OBS      HOLES={len(holes)}/{oh.get('cities_total', '?')} "
                f"(> {oh.get('stale_hours')}h): {names}{more}"
            )
        else:
            L.append(
                f"OBS      holes=0/{oh.get('cities_total', '?')} "
                f"(all cities fresh within {oh.get('stale_hours')}h)"
            )
    L.append("")

    # PRICE TRUTH (execution-feasibility BBA/depth for market-event scope)
    ph = data.get("price_holes", {})
    if ph.get("error"):
        L.append(f"PRICE    ERR {ph['error']}")
    else:
        holes = ph.get("holes", [])
        cities_total = ph.get("cities_total", "?")
        fresh = ph.get("fresh_count", 0)
        bba = ph.get("bba_token_coverage", {})
        depth = ph.get("full_depth_token_coverage", {})
        topology = ph.get("topology_metadata_staleness", {})
        if holes:
            names = ", ".join(
                f"{h['city']}/{h.get('token_id') or '?'}({h['age']})" for h in holes[:10]
            )
            more = f" +{len(holes) - 10} more" if len(holes) > 10 else ""
            L.append(
                f"PRICE    BBA-MISSING={len(holes)} "
                f"(token evidence > {ph.get('stale_hours')}h): {names}{more}"
            )
        else:
            L.append(
                f"PRICE    BBA green-cities={fresh}/{cities_total} "
                f"(all scoped tokens fresh within {ph.get('stale_hours')}h)"
            )
        L.append(
            f"         BBA tokens={bba.get('fresh_tokens', 0)}/{bba.get('tokens_total', 0)} "
            f"depth tokens={depth.get('fresh_tokens', 0)}/{depth.get('tokens_total', 0)}"
        )
        if topology.get("error"):
            L.append(f"         topology metadata ERR {topology['error']}")
        else:
            stale = len(topology.get("stale_or_missing_conditions", []))
            L.append(
                f"         topology metadata stale={stale}/{topology.get('conditions_total', 0)} "
                f"(not price evidence)"
            )
    L.append("")

    # POSITIONS
    p = data["positions"]
    if p.get("error"):
        L.append(f"POSITIONS ERR {p['error']}")
    else:
        by_phase = p.get("n_open_by_phase") or {}
        phase_text = " ".join(
            f"{phase}={by_phase.get(phase, 0)}" for phase in OPEN_POSITION_PHASES
        )
        L.append(
            f"POSITIONS open={p.get('n_open', '?')}  {phase_text}"
            + (
                f"  exit-fallback 24h={p['exit_fallback_24h']}"
                if "exit_fallback_24h" in p else ""
            )
        )
        for r in p.get("open", [])[:8]:
            mp = f"{r['mon_prob']:.2f}" if isinstance(r["mon_prob"], (int, float)) else "-"
            ep = f"{r['entry']:.2f}" if isinstance(r["entry"], (int, float)) else "-"
            sh = f"{r['shares']:.0f}" if isinstance(r["shares"], (int, float)) else "-"
            phase = str(r.get("phase") or "?")
            L.append(
                f"   {phase:<12} {(r['city'] or '?'):<10} {(r['date'] or ''):<10} "
                f"{(r['bin'] or '')[:12]:<12} {(r['dir'] or ''):<4} "
                f"sh={sh:<6} entry={ep:<5} mon={mp} ({r['mon_age']})"
            )
    L.append("")

    # ORDERS
    o = data["orders"]
    if o.get("error"):
        L.append(f"ORDERS   ERR {o['error']}")
    else:
        st = " ".join(f"{k}={v}" for k, v in o.get("state_24h", {}).items())
        L.append(f"ORDERS   24h states[{st}]  fills24h={o.get('fills_24h', '?')}")
        for r in o.get("last5", []):
            sz = f"{r['size']:.1f}" if isinstance(r["size"], (int, float)) else "-"
            pr = f"{r['price']:.3f}" if isinstance(r["price"], (int, float)) else "-"
            L.append(
                f"   {(r['kind'] or '?'):<14} {(r['side'] or ''):<4} "
                f"sz={sz:<6} px={pr:<6} {(r['state'] or ''):<14} ({r['age']})"
            )
    L.append("")

    # SELECTION (C2 winner's-curse slope diagnostic)
    sel = data.get("selection", {})
    if sel.get("error"):
        L.append(f"SELECTION ERR {sel['error']}")
    elif "slope" in sel:
        L.append(
            f"SELECTION slope={sel['slope']} intercept={sel['intercept']} "
            f"n={sel['n']}  (target slope~1; <<1=under-shrink, intercept!=0=center bias)"
        )
    else:
        L.append(
            "SELECTION " + str(sel.get("status", "n/a"))
            + f"  (receipts_with_edge_shrunk={sel.get('receipts_with_edge_shrunk', 0)})"
        )
    return "\n".join(L)


# --------------------------------------------------------------------------
# Section: OBS HOLES (zeus-world.db observation_instants per-city freshness)
# --------------------------------------------------------------------------
OBS_HOLE_STALE_HOURS = 2.0


def section_obs_holes() -> dict:
    """Per-city observation freshness census — data holes are not allowed.

    Operator law 2026-06-12: every city must have observation data; a city
    whose freshest observation_instants row is older than OBS_HOLE_STALE_HOURS
    is a HOLE (the Denver settlement-day blindness class). Display-only.
    """
    out: dict = {"stale_hours": OBS_HOLE_STALE_HOURS}
    today = _now().strftime("%Y-%m-%d")
    try:
        w = ro(WORLD_DB)
        try:
            rows = w.execute(
                "SELECT city, max(utc_timestamp) m FROM observation_instants "
                "WHERE target_date >= ? GROUP BY city ORDER BY m ASC",
                (today,),
            ).fetchall()
        finally:
            w.close()
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    holes = []
    for r in rows:
        age = age_str(r["m"])
        # age_str renders h/d for old rows; classify via raw parse instead.
        try:
            ts = str(r["m"]).replace(" ", "T")
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            hours = (_now() - dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            hours = float("inf")
        if hours > OBS_HOLE_STALE_HOURS:
            holes.append({"city": r["city"], "age": age})
    out["cities_total"] = len(rows)
    out["holes"] = holes
    return out


# Section: PRICE TRUTH COVERAGE (execution feasibility BBA/depth per token)
# --------------------------------------------------------------------------
PRICE_HOLE_STALE_HOURS = 2.0


def section_price_holes() -> dict:
    """Census current executable price evidence for today's/tomorrow's market events.

    ``execution_feasibility_latest`` is the price-truth surface: a fresh token
    needs a valid bid/ask pair, and a full-depth token additionally needs a
    non-empty bids and asks ladder.  The snapshot-latest table is retained as
    a separate topology-metadata freshness signal only; its ``captured_at`` is
    not price truth and must never make a token look executable.

    The legacy ``holes``/``fresh_count`` keys remain for callers, but now mean
    BBA token-coverage failures / BBA-complete cities respectively.
    """
    out: dict = {
        "stale_hours": PRICE_HOLE_STALE_HOURS,
        "scope": "market_events target_date today_or_tomorrow",
        "holes_semantics": "BBA token coverage failures, not snapshot topology",
    }
    today = _now().strftime("%Y-%m-%d")
    tomorrow = (_now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # 1. Fetch the complete city×condition×token decision scope.  ``direction``
    # rows in feasibility are intentionally deduplicated below by token; market
    # events are the contract topology and may contain repeated directions.
    try:
        fc = ro(FORECASTS_DB)
        try:
            scope_rows = fc.execute(
                "SELECT DISTINCT city, condition_id, token_id FROM market_events "
                "WHERE target_date IN (?, ?)",
                (today, tomorrow),
            ).fetchall()
        finally:
            fc.close()
    except sqlite3.Error as exc:
        out["error"] = f"forecasts_db: {type(exc).__name__}: {exc}"
        return out

    targets = [
        {
            "city": r["city"],
            "condition_id": r["condition_id"],
            "token_id": r["token_id"],
        }
        for r in scope_rows
    ]
    cities = {target["city"] for target in targets}
    out["cities_with_market_events"] = len(cities)

    if not targets:
        out["holes"] = []
        out["cities_total"] = 0
        out["fresh_count"] = 0
        out["bba_token_coverage"] = {
            "tokens_total": 0,
            "fresh_tokens": 0,
            "cities": [],
        }
        out["full_depth_token_coverage"] = {
            "tokens_total": 0,
            "fresh_tokens": 0,
            "cities": [],
        }
        out["topology_metadata_staleness"] = {
            "conditions_total": 0,
            "fresh_conditions": 0,
            "stale_or_missing_conditions": [],
        }
        return out

    def _timestamp(value: object) -> datetime | None:
        if value is None:
            return None
        try:
            text = str(value).strip().replace(" ", "T")
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _fresh(value: object) -> bool:
        parsed = _timestamp(value)
        return parsed is not None and (_now() - parsed).total_seconds() <= PRICE_HOLE_STALE_HOURS * 3600

    def _valid_bba(row: sqlite3.Row) -> bool:
        try:
            bid = float(row["best_bid_before"])
            ask = float(row["best_ask_before"])
        except (TypeError, ValueError):
            return False
        return math.isfinite(bid) and math.isfinite(ask) and 0.0 <= bid <= ask <= 1.0

    def _has_full_depth(row: sqlite3.Row) -> bool:
        try:
            depth = json.loads(row["depth_before_json"] or "")
        except (TypeError, json.JSONDecodeError):
            return False
        return (
            isinstance(depth, dict)
            and isinstance(depth.get("bids"), list)
            and bool(depth["bids"])
            and isinstance(depth.get("asks"), list)
            and bool(depth["asks"])
        )

    def _latest(existing: object | None, candidate: object) -> object:
        prior = _timestamp(existing)
        current = _timestamp(candidate)
        if current is None:
            return existing
        if prior is None or current > prior:
            return candidate
        return existing

    # 2. Read price truth once per token.  A token can have up to four latest
    # direction rows; aggregate evidence by token instead of allowing one
    # direction or one city-wide maximum to hide a stale sibling.
    tokens = sorted({str(t["token_id"]) for t in targets if t["token_id"]})
    feasibility_rows: list[sqlite3.Row] = []
    try:
        tr = ro(TRADES_DB)
        try:
            if tokens:
                placeholders = ",".join("?" * len(tokens))
                feasibility_rows = tr.execute(
                    f"SELECT token_id, quote_seen_at, best_bid_before, best_ask_before, depth_before_json "
                    f"FROM execution_feasibility_latest WHERE token_id IN ({placeholders})",
                    tokens,
                ).fetchall()
        finally:
            tr.close()
    except sqlite3.Error as exc:
        out["error"] = f"trades_db: {type(exc).__name__}: {exc}"
        return out

    bba_seen_at: dict[str, object] = {}
    depth_seen_at: dict[str, object] = {}
    for row in feasibility_rows:
        token = row["token_id"]
        if _valid_bba(row):
            bba_seen_at[token] = _latest(bba_seen_at.get(token), row["quote_seen_at"])
        if _valid_bba(row) and _has_full_depth(row):
            depth_seen_at[token] = _latest(depth_seen_at.get(token), row["quote_seen_at"])

    def _coverage(seen_at: dict[str, object], *, is_depth: bool) -> dict:
        missing: list[dict] = []
        by_city: dict[str, dict[str, int]] = {
            city: {"tokens_total": 0, "fresh_tokens": 0, "bba_fresh_tokens": 0}
            for city in cities
        }
        fresh_tokens = 0
        for target in targets:
            city = target["city"]
            token = target["token_id"]
            by_city[city]["tokens_total"] += 1
            if _fresh(bba_seen_at.get(token)):
                by_city[city]["bba_fresh_tokens"] += 1
            seen = seen_at.get(token) if token else None
            if _fresh(seen):
                fresh_tokens += 1
                by_city[city]["fresh_tokens"] += 1
                continue
            missing.append(
                {
                    "city": city,
                    "condition_id": target["condition_id"],
                    "token_id": token,
                    "age": age_str(str(seen)) if seen is not None else "NONE",
                    "reason": "market_event_token_missing" if not token else "stale_or_missing_evidence",
                }
            )

        city_rows = []
        for city in sorted(by_city):
            stats = by_city[city]
            if stats["fresh_tokens"] == stats["tokens_total"]:
                status = "green"
            elif is_depth and stats["bba_fresh_tokens"]:
                # BBA proves a current top-of-book, but not a two-sided ladder.
                status = "partial"
            elif stats["fresh_tokens"]:
                status = "partial"
            else:
                status = "missing"
            city_rows.append({"city": city, **stats, "status": status})
        return {
            "tokens_total": len(targets),
            "fresh_tokens": fresh_tokens,
            "missing_or_stale_tokens": missing,
            "cities": city_rows,
        }

    bba_coverage = _coverage(bba_seen_at, is_depth=False)
    depth_coverage = _coverage(depth_seen_at, is_depth=True)

    # 3. Snapshot freshness remains visible, but solely as topology metadata.
    # It cannot satisfy any BBA/depth coverage requirement above.
    topology: dict = {
        "conditions_total": 0,
        "fresh_conditions": 0,
        "stale_or_missing_conditions": [],
    }
    conditions = sorted({str(t["condition_id"]) for t in targets if t["condition_id"]})
    try:
        tr = ro(TRADES_DB)
        try:
            if conditions:
                placeholders = ",".join("?" * len(conditions))
                cond_snap_rows = tr.execute(
                    f"SELECT condition_id, max(captured_at) AS freshest "
                    f"FROM executable_market_snapshot_latest "
                    f"WHERE condition_id IN ({placeholders}) "
                    f"GROUP BY condition_id",
                    conditions,
                ).fetchall()
            else:
                cond_snap_rows = []
        finally:
            tr.close()
    except sqlite3.Error as exc:
        topology["error"] = f"trades_db: {type(exc).__name__}: {exc}"
    else:
        condition_freshest = {r["condition_id"]: r["freshest"] for r in cond_snap_rows}
        condition_city = {
            condition: next(t["city"] for t in targets if t["condition_id"] == condition)
            for condition in conditions
        }
        topology["conditions_total"] = len(conditions)
        for condition in conditions:
            freshest = condition_freshest.get(condition)
            if _fresh(freshest):
                topology["fresh_conditions"] += 1
            else:
                topology["stale_or_missing_conditions"].append(
                    {
                        "city": condition_city[condition],
                        "condition_id": condition,
                        "age": age_str(freshest),
                        "freshest": freshest,
                    }
                )

    out["cities_total"] = len(cities)
    out["bba_token_coverage"] = bba_coverage
    out["full_depth_token_coverage"] = depth_coverage
    out["topology_metadata_staleness"] = topology
    out["holes"] = bba_coverage["missing_or_stale_tokens"]
    out["fresh_count"] = sum(city["status"] == "green" for city in bba_coverage["cities"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Zeus money-funnel heartbeat (read-only).")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)
    data = collect()
    if args.json:
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(render_text(data) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
