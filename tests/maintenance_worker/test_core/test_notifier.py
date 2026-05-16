# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/notifier.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Notification"
"""
Tests for maintenance_worker.core.notifier.

Covers:
- notify_tick_summary: returns '' when no webhook configured (no-op)
- notify_tick_summary: reads webhook from MAINTENANCE_NOTIFIER_WEBHOOK env var
- notify_tick_summary: returns error string when summary_path not found
- notify_tick_summary: POSTs JSON with summary contents on success
- notify_tick_summary: returns error string on HTTP error (graceful)
- notify_tick_summary: returns error string on network error (graceful)
- notify_tick_summary: never raises (always returns str)
"""
from __future__ import annotations

import json
import os
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maintenance_worker.core.notifier import notify_tick_summary, _WEBHOOK_ENV_VAR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_summary(tmp_path: Path, content: str = "# Summary\n\nAll good.\n") -> Path:
    p = tmp_path / "SUMMARY.md"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# No-op when no webhook configured
# ---------------------------------------------------------------------------


def test_notify_no_op_when_no_webhook(tmp_path: Path) -> None:
    """Returns '' (no-op) when neither webhook_url nor env var is set."""
    summary = _make_summary(tmp_path)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(_WEBHOOK_ENV_VAR, None)
        result = notify_tick_summary(summary, webhook_url=None)
    assert result == ""


def test_notify_no_op_empty_webhook_url(tmp_path: Path) -> None:
    """Returns '' when explicit webhook_url is empty string."""
    summary = _make_summary(tmp_path)
    result = notify_tick_summary(summary, webhook_url="")
    assert result == ""


# ---------------------------------------------------------------------------
# Reads webhook from env
# ---------------------------------------------------------------------------


def test_notify_reads_webhook_from_env(tmp_path: Path) -> None:
    """Uses MAINTENANCE_NOTIFIER_WEBHOOK env var when webhook_url not passed."""
    summary = _make_summary(tmp_path)
    fake_url = "https://hooks.example.com/test"

    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200

    with patch.dict(os.environ, {_WEBHOOK_ENV_VAR: fake_url}):
        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            result = notify_tick_summary(summary, webhook_url=None)

    assert result == ""
    mock_open.assert_called_once()
    req = mock_open.call_args[0][0]
    assert req.full_url == fake_url


# ---------------------------------------------------------------------------
# Missing summary file
# ---------------------------------------------------------------------------


def test_notify_error_on_missing_summary(tmp_path: Path) -> None:
    """Returns non-empty error string when summary_path does not exist."""
    missing = tmp_path / "nonexistent_SUMMARY.md"
    result = notify_tick_summary(missing, webhook_url="https://hooks.example.com/t")
    assert result != ""
    assert "summary_path" in result.lower() or "could not read" in result.lower()


# ---------------------------------------------------------------------------
# Successful POST
# ---------------------------------------------------------------------------


def test_notify_posts_json_payload(tmp_path: Path) -> None:
    """POSTs JSON containing summary contents to the webhook."""
    content = "# Summary\n\nTest summary content.\n"
    summary = _make_summary(tmp_path, content=content)
    fake_url = "https://hooks.example.com/test"

    captured_data: list[bytes] = []

    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200

    def fake_urlopen(req, timeout=None):
        captured_data.append(req.data)
        return mock_response

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = notify_tick_summary(summary, webhook_url=fake_url)

    assert result == ""
    assert captured_data, "No POST data captured"
    payload = json.loads(captured_data[0].decode("utf-8"))
    assert "summary" in payload
    assert content in payload["summary"]
    assert payload["source"] == "maintenance_worker"


def test_notify_payload_includes_summary_path(tmp_path: Path) -> None:
    """POST payload includes the summary_path."""
    summary = _make_summary(tmp_path)
    fake_url = "https://hooks.example.com/test"

    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200

    captured: list[dict] = []

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return mock_response

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        notify_tick_summary(summary, webhook_url=fake_url)

    assert captured
    assert "summary_path" in captured[0]
    assert str(summary) in captured[0]["summary_path"]


# ---------------------------------------------------------------------------
# Graceful error handling
# ---------------------------------------------------------------------------


def test_notify_graceful_on_http_error(tmp_path: Path) -> None:
    """Returns non-empty error string on HTTP error (does not raise)."""
    summary = _make_summary(tmp_path)
    fake_url = "https://hooks.example.com/test"

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(fake_url, 500, "Server Error", {}, None),
    ):
        result = notify_tick_summary(summary, webhook_url=fake_url)

    assert result != ""
    assert "500" in result or "http" in result.lower()


def test_notify_graceful_on_url_error(tmp_path: Path) -> None:
    """Returns non-empty error string on network/URL error (does not raise)."""
    summary = _make_summary(tmp_path)
    fake_url = "https://hooks.example.com/test"

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = notify_tick_summary(summary, webhook_url=fake_url)

    assert result != ""
    assert "connection refused" in result.lower() or "url error" in result.lower()


def test_notify_never_raises(tmp_path: Path) -> None:
    """notify_tick_summary never raises regardless of failure mode."""
    summary = _make_summary(tmp_path)

    # Simulate unexpected exception
    with patch("urllib.request.urlopen", side_effect=RuntimeError("unexpected")):
        # Should not raise
        result = notify_tick_summary(summary, webhook_url="https://hooks.example.com/t")

    assert isinstance(result, str)


def test_notify_returns_string_type(tmp_path: Path) -> None:
    """notify_tick_summary always returns a string."""
    summary = _make_summary(tmp_path)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(_WEBHOOK_ENV_VAR, None)
        result = notify_tick_summary(summary, webhook_url=None)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# HTTP status codes
# ---------------------------------------------------------------------------


def test_notify_error_on_non_2xx_status(tmp_path: Path) -> None:
    """Returns error string when webhook returns non-2xx status."""
    summary = _make_summary(tmp_path)
    fake_url = "https://hooks.example.com/test"

    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 403

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = notify_tick_summary(summary, webhook_url=fake_url)

    assert result != ""
    assert "403" in result
