# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/scheduler_bindings/
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Scheduler Bindings"
"""
launchd_plist_template — generate_plist(config) -> str

Generates a macOS launchd plist XML string for scheduling the maintenance
worker daemon. The caller writes the result to the appropriate LaunchAgents
directory (outside this module — no filesystem writes here).

All identifiers are parameterized; zero hardcoded Zeus-specific strings.
Stdlib only.

Public API:
  generate_plist(label, program_path, working_dir, interval_seconds,
                 env_vars, log_path, error_log_path) -> str
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


def generate_plist(
    label: str,
    program_path: str,
    working_dir: str,
    interval_seconds: int = 3600,
    env_vars: Optional[dict[str, str]] = None,
    log_path: Optional[str] = None,
    error_log_path: Optional[str] = None,
) -> str:
    """
    Generate a launchd plist XML string.

    label: the launchd job label (e.g. 'com.example.maintenance-worker').
    program_path: absolute path to the executable or script to run.
    working_dir: working directory for the job.
    interval_seconds: StartInterval in seconds (default 3600 = hourly).
    env_vars: environment variables to inject (dict of str → str).
    log_path: stdout log file path (optional).
    error_log_path: stderr log file path (optional).

    Returns a UTF-8 plist XML string with proper DOCTYPE declaration.
    Zero Zeus identifiers; all values are caller-provided.
    """
    plist_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"',
        '    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        '<plist version="1.0">',
        '<dict>',
        f'\t<key>Label</key>',
        f'\t<string>{_escape_xml(label)}</string>',
        '',
        '\t<key>ProgramArguments</key>',
        '\t<array>',
        f'\t\t<string>{_escape_xml(program_path)}</string>',
        '\t</array>',
        '',
        '\t<key>WorkingDirectory</key>',
        f'\t<string>{_escape_xml(working_dir)}</string>',
        '',
        '\t<key>StartInterval</key>',
        f'\t<integer>{interval_seconds}</integer>',
        '',
        '\t<key>RunAtLoad</key>',
        '\t<true/>',
        '',
        '\t<key>KeepAlive</key>',
        '\t<false/>',
    ]

    # Environment variables block
    if env_vars:
        plist_parts += [
            '',
            '\t<key>EnvironmentVariables</key>',
            '\t<dict>',
        ]
        for key, value in sorted(env_vars.items()):
            plist_parts += [
                f'\t\t<key>{_escape_xml(key)}</key>',
                f'\t\t<string>{_escape_xml(value)}</string>',
            ]
        plist_parts.append('\t</dict>')

    # Log paths
    if log_path:
        plist_parts += [
            '',
            '\t<key>StandardOutPath</key>',
            f'\t<string>{_escape_xml(log_path)}</string>',
        ]
    if error_log_path:
        plist_parts += [
            '',
            '\t<key>StandardErrorPath</key>',
            f'\t<string>{_escape_xml(error_log_path)}</string>',
        ]

    plist_parts += [
        '</dict>',
        '</plist>',
        '',  # trailing newline
    ]

    return '\n'.join(plist_parts)


def _escape_xml(value: str) -> str:
    """Escape XML special characters in attribute/element values."""
    return (
        value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
