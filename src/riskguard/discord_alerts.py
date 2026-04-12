"""Zeus RiskGuard Discord alerting — sends halt/resume/warning notifications.

P12 finding #7: Zeus has no alerting mechanism. Adapted from Rainstorm's pattern
but using Zeus-native conventions (keychain resolver, risk_state.db path).

Webhook URL resolved from macOS Keychain: `zeus_discord_webhook`.
If not configured, alerts are silently skipped (no crash).
Cooldown tracking in risk_state.db to survive process restarts.

Usage from riskguard.py:
    from src.riskguard.discord_alerts import alert_halt, alert_resume, alert_warning, alert_redeem, alert_daily_report
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("zeus.riskguard.discord")

# Discord embed colors
COLOR_HALT = 0xFF0000     # Red — trading halted
COLOR_RESUME = 0x00FF00   # Green — trading resumed
COLOR_WARNING = 0xFFA500  # Orange — degraded but not halted
COLOR_REDEEM = 0x00FF00   # Green — redemption completed
COLOR_DAILY_REPORT = 0x3498DB  # Blue — daily summary
COLOR_TRADE = 0x3498DB    # Blue — trade notification

# Cooldown periods in seconds
_COOLDOWN_HALT_RESUME = 1800   # 30 minutes
_COOLDOWN_WARNING = 600        # 10 minutes
_COOLDOWN_TRADE = 0            # No cooldown for trade alerts
_COOLDOWN_DAILY_REPORT = 22 * 3600  # Keep daily summaries to once per day

_KEYCHAIN_RESOLVER = Path.home() / ".openclaw" / "bin" / "keychain_resolver.py"
_RISK_STATE_DB = Path(__file__).parent.parent.parent / "state" / "risk_state-{mode}.db"

_webhook_url: str | None = None
_webhook_resolved = False


def _alerts_disabled() -> bool:
    return os.environ.get("ZEUS_DISABLE_DISCORD_ALERTS", "").strip().lower() in {"1", "true", "yes"}


def _get_mode() -> str:
    from src.config import get_mode
    return get_mode()


def _get_risk_db_path() -> Path:
    return Path(str(_RISK_STATE_DB).format(mode=_get_mode()))


def _resolve_webhook() -> str | None:
    """Resolve Discord webhook URL from keychain. Cached after first call."""
    global _webhook_url, _webhook_resolved
    if _alerts_disabled():
        return None
    if _webhook_resolved:
        return _webhook_url

    _webhook_resolved = True

    env_val = os.environ.get("ZEUS_DISCORD_WEBHOOK")
    if env_val:
        _webhook_url = env_val
        return _webhook_url

    if not _KEYCHAIN_RESOLVER.exists():
        logger.debug("Keychain resolver not found; Discord alerts disabled")
        return None

    try:
        result = subprocess.run(
            ["python3", str(_KEYCHAIN_RESOLVER)],
            input=json.dumps({"ids": ["zeus_discord_webhook"]}),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            resp = json.loads(result.stdout)
            _webhook_url = resp.get("values", {}).get("zeus_discord_webhook")
    except Exception as e:
        logger.debug("Webhook resolution failed: %s", e)

    return _webhook_url


def _ensure_cooldown_table(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS alert_cooldown (
            alert_key    TEXT PRIMARY KEY,
            last_sent_at TEXT NOT NULL
        )
    """)


def _is_in_cooldown(db: sqlite3.Connection, alert_key: str, cooldown_seconds: int) -> bool:
    if cooldown_seconds <= 0:
        return False
    row = db.execute(
        "SELECT last_sent_at FROM alert_cooldown WHERE alert_key = ?",
        (alert_key,),
    ).fetchone()
    if not row:
        return False
    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now(timezone.utc) - last).total_seconds() < cooldown_seconds
    except (ValueError, TypeError):
        return False


def _record_sent(db: sqlite3.Connection, alert_key: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO alert_cooldown (alert_key, last_sent_at) VALUES (?, ?)",
        (alert_key, datetime.now(timezone.utc).isoformat()),
    )


def _send_embed(level: str, title: str, body: str, color: int | None = None) -> bool:
    """Send a Discord webhook embed. Returns True if sent."""
    import httpx

    url = _resolve_webhook()
    if not url:
        logger.debug("Discord webhook not configured; skipping '%s'", title)
        return False

    color = color if color is not None else {
        "halt": COLOR_HALT,
        "resume": COLOR_RESUME,
        "warning": COLOR_WARNING,
        "redeem": COLOR_REDEEM,
        "daily_report": COLOR_DAILY_REPORT,
        "trade": COLOR_TRADE,
    }.get(level, COLOR_WARNING)

    payload = {
        "embeds": [{
            "title": title,
            "description": body,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": f"Zeus {_get_mode()} | RiskGuard"}
        }]
    }

    try:
        resp = httpx.post(url, json=payload, timeout=5)
        if resp.status_code >= 400:
            logger.warning("Discord alert failed: %s %s", resp.status_code, resp.text[:200])
        return resp.status_code < 400
    except Exception as e:
        logger.warning("Discord alert request failed: %s", e)
        return False


def _with_cooldown(alert_key: str, cooldown_seconds: int, send_fn) -> bool:
    """Wrapper: check cooldown, send, record."""
    try:
        db = sqlite3.connect(str(_get_risk_db_path()), timeout=5)
        _ensure_cooldown_table(db)
        if _is_in_cooldown(db, alert_key, cooldown_seconds):
            db.close()
            return False
        db.commit()
    except Exception as e:
        logger.warning("Cooldown check failed (%s); proceeding", e)
        db = None

    sent = send_fn()

    if sent and db is not None:
        _record_sent(db, alert_key)
        db.commit()
        db.close()
    elif db is not None:
        db.close()

    return sent


def alert_halt(failed_rules: list[dict[str, Any]]) -> bool:
    """Send halt notification. Cooldown: 30 min."""
    lines = ["**Trading halted. Failed rules:**\n"]
    for r in failed_rules:
        lines.append(f"- **{r['name']}**: `{r['value']}` (threshold: `{r['threshold']}`)")
        if r.get("detail"):
            lines.append(f"  {r['detail']}")
    lines.append("\n*Check RiskGuard status and resolve before resuming.*")

    return _with_cooldown("halt", _COOLDOWN_HALT_RESUME,
                          lambda: _send_embed("halt", "RISKGUARD HALT", "\n".join(lines)))


def alert_resume(reason: str = "rules cleared") -> bool:
    """Send resume notification. Cooldown: 30 min."""
    return _with_cooldown("resume", _COOLDOWN_HALT_RESUME,
                          lambda: _send_embed("resume", "RISKGUARD RESUMED",
                                              f"Trading resumed. Reason: **{reason}**"))


def alert_warning(rule_name: str, value: float, threshold: float, detail: str = "") -> bool:
    """Send warning for degraded condition. Cooldown: 10 min per rule."""
    body = f"**{rule_name}** approaching threshold\nCurrent: `{value}` | Limit: `{threshold}`"
    if detail:
        body += f"\n{detail}"
    return _with_cooldown(f"warning:{rule_name}", _COOLDOWN_WARNING,
                          lambda: _send_embed("warning", f"WARNING: {rule_name}", body))


def alert_redeem(city: str, label: str, condition_id: str, tx_hash: str, gas_used: int | None = None) -> bool:
    """Send redemption notification for a winning position."""
    msg_lines = [
        f"**{city} {label}** (WINNER)",
        f"Condition: `{condition_id[:24]}...`",
        f"TX: `{tx_hash[:16]}...`",
    ]
    if gas_used is not None:
        msg_lines.append(f"Gas used: `{gas_used}`")
    return _send_embed("redeem", "TOKEN REDEEMED", "\n".join(msg_lines))


def alert_daily_report(report: dict[str, Any]) -> bool:
    """Send daily summary notification with PnL-aware coloring."""
    pnl = float(report.get("total_pnl_usd", 0.0) or 0.0)
    sign = "+" if pnl >= 0 else ""
    color = COLOR_DAILY_REPORT if pnl >= 0 else 0xFF4444
    limit = report.get("loss_limit_usd", 70)
    calibration = report.get("calibration", {}) or {}
    brier = calibration.get("brier_score")
    brier_text = "n/a" if brier is None else f"{float(brier):.3f}"
    msg = (
        f"**{report.get('date', 'unknown date')} Zeus Daily Summary**\n"
        f"PnL: `{sign}${pnl:.2f}` | Trades: `{report.get('total_trades', 0)}`\n"
        f"W/L: `{report.get('wins', 0)}/{report.get('losses', 0)}` ({float(report.get('win_rate', 0.0) or 0.0):.0%} win rate)\n"
        f"Runtime: `{report.get('runtime_state') or 'unknown'}` | Gate: `{report.get('trade_gate') or 'normal'}`\n"
        f"Exposure: `${float(report.get('total_exposure_usd', 0.0) or 0.0):.2f}` | Open positions: `{report.get('open_positions', 0)}`\n"
        f"Cumulative loss: `${float(report.get('cumulative_loss_usd', 0.0) or 0.0):.2f}` / ${float(limit):.0f}\n"
        f"Brier: `{brier_text}` | Status: `{'HALTED' if report.get('halted') else 'ACTIVE'}`"
    )
    return _with_cooldown("daily_report", _COOLDOWN_DAILY_REPORT,
                          lambda: _send_embed("daily_report", "Daily Report", msg, color=color))


def alert_trade(direction: str, market: str, price: float, size_usd: float,
                strategy: str, edge: float, mode: str = "") -> bool:
    """Send trade notification. No cooldown."""
    mode = mode or _get_mode()
    body = (
        f"**{direction}** {market[:60]}\n"
        f"Price: `{price:.3f}` | Size: `${size_usd:.2f}` | Edge: `{edge:.3f}`\n"
        f"Strategy: `{strategy}` | Mode: `{mode}`"
    )
    return _send_embed("trade", f"TRADE: {direction} @ {price:.2f}", body)


# Live operation milestones and safety alerts

_COOLDOWN_LIVE_MILESTONE = 0  # No cooldown u2014 milestones fire once by nature
_COOLDOWN_WALLET_DROP = 300   # 5 minutes between wallet-drop alerts
_COOLDOWN_CHAIN_SYNC = 600    # 10 minutes between chain-sync-failure alerts
_COOLDOWN_HEARTBEAT = 300     # 5 minutes between missed-heartbeat alerts

COLOR_LIVE_MILESTONE = 0x9B59B6  # Purple u2014 live milestone
COLOR_WARNING_CRITICAL = 0xFF4444  # Bright red u2014 critical safety warning


def alert_first_live_fill(
    trade_id: str,
    city: str,
    direction: str,
    price: float,
    size_usd: float,
) -> bool:
    """Alert on the first live order fill. No cooldown u2014 fires once."""
    body = (
        f"**First live fill executed**\n"
        f"Trade: `{trade_id}` | City: `{city}`\n"
        f"Direction: `{direction}` | Price: `{price:.3f}` | Size: `${size_usd:.2f}`\n"
        f"Verify position in DB before next cycle."
    )
    return _send_embed("trade", "FIRST LIVE FILL", body, color=COLOR_LIVE_MILESTONE)


def alert_first_live_settlement(
    trade_id: str,
    city: str,
    pnl: float,
    won: bool,
) -> bool:
    """Alert on the first live settlement. No cooldown u2014 fires once."""
    outcome = "WON" if won else "LOST"
    sign = "+" if pnl >= 0 else ""
    body = (
        f"**First live settlement: {outcome}**\n"
        f"Trade: `{trade_id}` | City: `{city}`\n"
        f"PnL: `{sign}${pnl:.2f}`"
    )
    color = COLOR_LIVE_MILESTONE if won else COLOR_WARNING_CRITICAL
    return _send_embed("trade", "FIRST LIVE SETTLEMENT", body, color=color)


def alert_wallet_drop_over_pct(
    wallet_before: float,
    wallet_after: float,
    drop_pct: float,
) -> bool:
    """Alert when wallet balance drops more than drop_pct%. Cooldown: 5 min."""
    body = (
        f"**Wallet balance dropped {drop_pct:.1f}%**\n"
        f"Before: `${wallet_before:.2f}` u2192 After: `${wallet_after:.2f}`\n"
        f"Investigate open positions and riskguard status."
    )
    return _with_cooldown(
        "wallet_drop",
        _COOLDOWN_WALLET_DROP,
        lambda: _send_embed("halt", f"WALLET DROP {drop_pct:.1f}%", body, color=COLOR_WARNING_CRITICAL),
    )


def alert_chain_sync_failure(failure_count: int, detail: str = "") -> bool:
    """Alert on repeated chain reconciliation failures. Cooldown: 10 min."""
    body = (
        f"**Chain sync failed {failure_count} consecutive time(s)**\n"
        f"Reconciliation could not fetch authoritative on-chain positions.\n"
    )
    if detail:
        body += f"Detail: {detail}\n"
    body += "Check Polymarket API connectivity and credentials."
    return _with_cooldown(
        "chain_sync_failure",
        _COOLDOWN_CHAIN_SYNC,
        lambda: _send_embed("halt", "CHAIN SYNC FAILURE", body, color=COLOR_WARNING_CRITICAL),
    )


def alert_daemon_heartbeat_missed(last_seen_at: str, stale_minutes: float) -> bool:
    """Alert when daemon heartbeat is stale (>5 min). Cooldown: 5 min."""
    body = (
        f"**Daemon heartbeat not updated for {stale_minutes:.1f} minutes**\n"
        f"Last seen: `{last_seen_at}`\n"
        f"Daemon may have silently crashed. Check process and logs."
    )
    return _with_cooldown(
        "heartbeat_missed",
        _COOLDOWN_HEARTBEAT,
        lambda: _send_embed("halt", "HEARTBEAT MISSED", body, color=COLOR_WARNING_CRITICAL),
    )
