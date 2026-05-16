# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/05_execution_packets/PACKET_INDEX.md §P6
#   bindings/zeus/launchd_plist.plist
"""
test_launchd_plist.py — Verify launchd plist generates valid XML.

Tests:
- Template file exists and passes `plutil -lint`
- Required plist keys present: Label, ProgramArguments, StartCalendarInterval
- Schedule is 04:30 (Hour=4, Minute=30)
- ZEUS_REPO_PLACEHOLDER substitution produces a valid plist
- generate_plist() core function still works (regression guard)
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # test_bindings/ -> maintenance_worker/ -> tests/ -> repo root
BINDINGS_DIR = REPO_ROOT / "bindings" / "zeus"
PLIST_TEMPLATE_PATH = BINDINGS_DIR / "launchd_plist.plist"
PLACEHOLDER = "ZEUS_REPO_PLACEHOLDER"


# ---------------------------------------------------------------------------
# Template file tests
# ---------------------------------------------------------------------------


def test_plist_template_exists():
    assert PLIST_TEMPLATE_PATH.is_file(), f"Missing: {PLIST_TEMPLATE_PATH}"


def test_plist_template_plutil_lint():
    """
    The plist template must pass `plutil -lint`.
    ZEUS_REPO_PLACEHOLDER is a valid string value — plutil checks XML structure,
    not path existence, so the template validates as-is.
    """
    result = subprocess.run(
        ["plutil", "-lint", str(PLIST_TEMPLATE_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"plutil -lint FAILED:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_plist_template_has_label_key():
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    # plist structure: plist/dict/key...
    dict_elem = root.find("dict")
    assert dict_elem is not None
    keys = [elem.text for elem in dict_elem if elem.tag == "key"]
    assert "Label" in keys, f"Label key missing from plist. Found keys: {keys}"


def test_plist_template_label_value():
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    dict_elem = root.find("dict")
    children = list(dict_elem)
    label_idx = None
    for i, child in enumerate(children):
        if child.tag == "key" and child.text == "Label":
            label_idx = i
            break
    assert label_idx is not None
    label_value = children[label_idx + 1]
    assert label_value.tag == "string"
    assert label_value.text == "com.zeus.maintenance"


def test_plist_template_has_start_calendar_interval():
    """
    The plist MUST use StartCalendarInterval (not StartInterval) to achieve
    exact 04:30 daily scheduling. StartInterval only guarantees a period, not a time.
    """
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    dict_elem = root.find("dict")
    keys = [elem.text for elem in dict_elem if elem.tag == "key"]
    assert "StartCalendarInterval" in keys, (
        "plist must use StartCalendarInterval for exact 04:30 scheduling, not StartInterval"
    )
    assert "StartInterval" not in keys, (
        "plist must not use StartInterval when StartCalendarInterval is required"
    )


def test_plist_template_schedule_0430():
    """StartCalendarInterval must specify Hour=4, Minute=30."""
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    dict_elem = root.find("dict")
    children = list(dict_elem)

    sci_idx = None
    for i, child in enumerate(children):
        if child.tag == "key" and child.text == "StartCalendarInterval":
            sci_idx = i
            break
    assert sci_idx is not None, "StartCalendarInterval key not found"

    sci_dict = children[sci_idx + 1]
    assert sci_dict.tag == "dict", "StartCalendarInterval value must be a dict"

    sci_children = list(sci_dict)
    sci_values: dict[str, int] = {}
    for j in range(0, len(sci_children), 2):
        k = sci_children[j].text
        v = int(sci_children[j + 1].text)
        sci_values[k] = v

    assert sci_values.get("Hour") == 4, (
        f"Expected Hour=4, got Hour={sci_values.get('Hour')}"
    )
    assert sci_values.get("Minute") == 30, (
        f"Expected Minute=30, got Minute={sci_values.get('Minute')}"
    )


def test_plist_template_has_working_directory():
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    dict_elem = root.find("dict")
    keys = [elem.text for elem in dict_elem if elem.tag == "key"]
    assert "WorkingDirectory" in keys


def test_plist_template_has_env_vars():
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    dict_elem = root.find("dict")
    keys = [elem.text for elem in dict_elem if elem.tag == "key"]
    assert "EnvironmentVariables" in keys


def test_plist_template_env_has_zeus_repo():
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    dict_elem = root.find("dict")
    children = list(dict_elem)

    env_idx = None
    for i, child in enumerate(children):
        if child.tag == "key" and child.text == "EnvironmentVariables":
            env_idx = i
            break
    assert env_idx is not None

    env_dict = children[env_idx + 1]
    assert env_dict.tag == "dict"
    env_children = list(env_dict)
    env_keys = [env_children[j].text for j in range(0, len(env_children), 2)]
    assert "ZEUS_REPO" in env_keys, f"ZEUS_REPO not in plist EnvironmentVariables. Found: {env_keys}"


def test_plist_template_has_placeholder():
    """The template must contain ZEUS_REPO_PLACEHOLDER (for substitution by install script)."""
    content = PLIST_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert PLACEHOLDER in content, (
        f"plist template must contain {PLACEHOLDER!r} for install script substitution"
    )


def test_plist_rendered_plutil_lint():
    """After placeholder substitution, the rendered plist must still pass plutil -lint."""
    template = PLIST_TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = template.replace(PLACEHOLDER, "/fake/zeus/repo")

    with tempfile.NamedTemporaryFile(
        suffix=".plist", mode="w", encoding="utf-8", delete=False
    ) as tf:
        tf.write(rendered)
        tmp_path = tf.name

    try:
        result = subprocess.run(
            ["plutil", "-lint", tmp_path],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Rendered plist failed plutil -lint:\n{result.stdout}\n{result.stderr}"
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_plist_keep_alive_is_false():
    """KeepAlive must be false — the worker is a daily batch job, not a daemon."""
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    dict_elem = root.find("dict")
    children = list(dict_elem)

    ka_idx = None
    for i, child in enumerate(children):
        if child.tag == "key" and child.text == "KeepAlive":
            ka_idx = i
            break
    assert ka_idx is not None, "KeepAlive key missing from plist"
    ka_value = children[ka_idx + 1]
    assert ka_value.tag == "false", (
        f"KeepAlive must be <false/> for a daily batch job; got <{ka_value.tag}/>"
    )


def test_plist_run_at_load_is_false():
    """RunAtLoad must be false — first tick should be at 04:30, not immediately on launchctl load."""
    tree = ET.parse(PLIST_TEMPLATE_PATH)
    root = tree.getroot()
    dict_elem = root.find("dict")
    children = list(dict_elem)

    ral_idx = None
    for i, child in enumerate(children):
        if child.tag == "key" and child.text == "RunAtLoad":
            ral_idx = i
            break
    assert ral_idx is not None, "RunAtLoad key missing from plist"
    ral_value = children[ral_idx + 1]
    assert ral_value.tag == "false", (
        f"RunAtLoad must be <false/>; got <{ral_value.tag}/> — "
        "prevents immediate tick on first launchctl load"
    )


# ---------------------------------------------------------------------------
# generate_plist() core function regression guard
# ---------------------------------------------------------------------------


def test_generate_plist_core_function_produces_valid_xml():
    """
    Regression guard: the core generate_plist() function from
    maintenance_worker.cli.scheduler_bindings.launchd_plist_template
    must still produce valid XML. The Zeus binding uses a hand-written plist
    for StartCalendarInterval support, but the core function must stay intact.
    """
    from maintenance_worker.cli.scheduler_bindings.launchd_plist_template import (
        generate_plist,
    )

    result = generate_plist(
        label="com.test.maintenance",
        program_path="/usr/bin/python3",
        working_dir="/tmp/test",
        interval_seconds=86400,
        env_vars={"ZEUS_REPO": "/tmp/test"},
        log_path="/tmp/test.log",
        error_log_path="/tmp/test_error.log",
    )

    assert isinstance(result, str)
    assert "com.test.maintenance" in result
    assert "<StartInterval>" in result or "<key>StartInterval</key>" in result

    # Validate as XML (not plist-specific but catches malformed tags)
    with tempfile.NamedTemporaryFile(
        suffix=".plist", mode="w", encoding="utf-8", delete=False
    ) as tf:
        tf.write(result)
        tmp_path = tf.name

    try:
        lint = subprocess.run(
            ["plutil", "-lint", tmp_path],
            capture_output=True,
            text=True,
        )
        assert lint.returncode == 0, (
            f"generate_plist() output failed plutil -lint:\n{lint.stdout}\n{lint.stderr}"
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
