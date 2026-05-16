# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/notifier.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Notification"
"""
core/notifier — notify_tick_summary(summary_path, webhook_url)

Sends a POST request with the SUMMARY.md contents to a webhook URL.
Graceful on failure: all errors are caught and returned as a string
(never raises). Caller decides whether to log or ignore the error.

Webhook URL is read from the environment (MAINTENANCE_NOTIFIER_WEBHOOK)
if not passed explicitly — SAFETY_CONTRACT compliance (no credential files).

Zero Zeus identifiers. Stdlib + urllib only. No external deps.

Public API:
  error = notify_tick_summary(summary_path, webhook_url=None) -> str
    Returns '' on success, error description on failure.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Environment variable name for the webhook URL.
# Named MAINTENANCE_NOTIFIER_WEBHOOK — zero Zeus identifiers.
_WEBHOOK_ENV_VAR = "MAINTENANCE_NOTIFIER_WEBHOOK"

# Request timeout in seconds.
_REQUEST_TIMEOUT_SECONDS = 10

# Max bytes of SUMMARY.md to include in the notification payload.
_MAX_SUMMARY_BYTES = 8192


def notify_tick_summary(
    summary_path: Path,
    webhook_url: Optional[str] = None,
) -> str:
    """
    POST SUMMARY.md contents to a webhook URL.

    webhook_url: if None, reads MAINTENANCE_NOTIFIER_WEBHOOK from env.
    If neither is set, returns '' (no-op; webhook is optional).

    Returns '' on success.
    Returns a non-empty error description string on any failure.
    Never raises.

    Payload (JSON):
      {
        "summary_path": "<absolute path>",
        "summary": "<SUMMARY.md contents, truncated to 8192 bytes>",
        "source": "maintenance_worker"
      }
    """
    # Resolve webhook URL
    url = webhook_url or os.environ.get(_WEBHOOK_ENV_VAR, "")
    if not url:
        # No webhook configured — silent no-op.
        return ""

    # Read SUMMARY.md
    try:
        raw = summary_path.read_bytes()
        summary_text = raw[:_MAX_SUMMARY_BYTES].decode("utf-8", errors="replace")
    except OSError as exc:
        return f"notify: could not read summary_path {summary_path}: {exc}"

    # Build payload
    payload = json.dumps(
        {
            "summary_path": str(summary_path),
            "summary": summary_text,
            "source": "maintenance_worker",
        }
    ).encode("utf-8")

    # POST
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            status = resp.status
            if status not in (200, 201, 202, 204):
                return f"notify: webhook returned HTTP {status}"
        return ""
    except urllib.error.HTTPError as exc:
        return f"notify: HTTP error {exc.code} posting to webhook"
    except urllib.error.URLError as exc:
        return f"notify: URL error posting to webhook: {exc.reason}"
    except OSError as exc:
        return f"notify: network error posting to webhook: {exc}"
    except Exception as exc:  # pylint: disable=broad-except
        return f"notify: unexpected error: {exc}"
