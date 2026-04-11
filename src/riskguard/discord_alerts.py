"""Zeus RiskGuard Discord alerting — sends halt/resume/warning notifications.

P12 finding #7: Zeus has no alerting mechanism. Adapted from Rainstorm's pattern
but using Zeus-native conventions (keychain resolver, risk_state.db path).

Webhook URL resolved from macOS Keychain: `zeus_discord_webhook`.
If not configured, alerts are silently skipped (no crash).
Cooldown tracking in risk_state.db to survive process restarts.

Usage from riskguard.py:
    from src.riskguard.discord_alerts import alert_halt, alert_resume, alert_warning
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
COLOR_TRADE = 0x3498DB    # Blue — trade notification

# Cooldown periods in seconds
_COOLDOWN_HALT_RESUME = 1800   # 30 minutes
_COOLDOWN_WARNING = 600        # 10 minutes
_COOLDOWN_TRADE = 0            # No cooldown for trade alerts

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

    # Try env var first
    env_val = os.environ.get("ZEUS_DISCORD_WEBHOOK")
    if env_val:
        _webhook_url = env_val
        return _webhook_url

    # Try keychain
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


def _send_embed(level: str, title: str, body: str) -> bool:
    """Send a Discord webhook embed. Returns True if sent."""
    import httpx

    url = _resolve_webhook()
    if not url:
        logger.debug("Discord webhook not configured; skipping '%s'", title)
        return False

    color = {"halt": COLOR_HALT, "resume": COLOR_RESUME,
             "warning": COLOR_WARNING, "trade": COLOR_TRADE}.get(level, COLOR_WARNING)

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
    """Send halt notification. Cooldown: 30 min.

    Args:
        failed_rules: list of dicts with 'name', 'value', 'threshold', optional 'detail'
    """
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
