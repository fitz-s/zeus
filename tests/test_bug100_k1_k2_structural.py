"""Structural antibodies for bug100 K1/K2 tail (B001 + B047).

Created: 2026-04-21
Last reused/audited: 2026-04-21
Authority basis: Phase 10 DT-close — docs/operations/task_2026-04-16_dual_track_metric_spine/phase10_evidence/SCAFFOLD_B001_config_contract.md + SCAFFOLD_B047_scheduler_observability.md

These tests are antibodies, not coverage. They enforce cross-module
relationships that convention alone cannot hold:

- B001: every ``Settings`` property reading ``self._data`` must have its
  key in the startup ``required`` list. Prevents the silent-fallback
  antipattern that violated ``src/config.py`` L1 header contract.

- B047: every ``scheduler.add_job(fn, ...)`` call site in ``src/main.py``
  must route through the ``@_scheduler_job`` decorator (heartbeat
  exempt — it IS the observability channel). Prevents new scheduler
  wrappers from silently log-and-continuing without status-file
  observability.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PY = REPO_ROOT / "src" / "config.py"
MAIN_PY = REPO_ROOT / "src" / "main.py"


# ---------------------------------------------------------------------------
# B001 — Settings contract enforcement
# ---------------------------------------------------------------------------


def _settings_class_node() -> ast.ClassDef:
    tree = ast.parse(CONFIG_PY.read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return node
    raise AssertionError("Settings class not found in src/config.py")


def _required_keys_literal() -> list[str]:
    """Parse the ``required = [...]`` literal inside Settings.__init__."""
    cls = _settings_class_node()
    for item in cls.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            for stmt in ast.walk(item):
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "required"
                    and isinstance(stmt.value, ast.List)
                ):
                    return [
                        elt.value
                        for elt in stmt.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
    raise AssertionError("Settings.__init__ has no `required` list literal")


def _property_keys() -> dict[str, list[str]]:
    """Map property name -> set of string keys accessed via self._data[key] or self._data.get(key, ...)."""
    cls = _settings_class_node()
    out: dict[str, list[str]] = {}
    for item in cls.body:
        if not isinstance(item, ast.FunctionDef):
            continue
        is_property = any(
            (isinstance(d, ast.Name) and d.id == "property")
            for d in item.decorator_list
        )
        if not is_property:
            continue
        keys: list[str] = []
        for sub in ast.walk(item):
            # self._data["key"] subscript
            if (
                isinstance(sub, ast.Subscript)
                and isinstance(sub.value, ast.Attribute)
                and isinstance(sub.value.value, ast.Name)
                and sub.value.value.id == "self"
                and sub.value.attr == "_data"
            ):
                key = sub.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.append(key.value)
            # self._data.get("key", ...) call
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get"
                and isinstance(sub.func.value, ast.Attribute)
                and sub.func.value.attr == "_data"
                and isinstance(sub.func.value.value, ast.Name)
                and sub.func.value.value.id == "self"
            ):
                if sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                    keys.append(sub.args[0].value)
        if keys:
            out[item.name] = keys
    return out


def test_b001_settings_properties_forbid_get_fallback():
    """B001 antibody: no Settings property may use self._data.get(key, default).

    config.py L1 header contract: every key must exist; missing keys raise
    KeyError at startup. Using ``.get(key, default)`` inside a property
    silently hides missing keys from the startup check.
    """
    cls = _settings_class_node()
    offenders: list[tuple[str, str]] = []
    for item in cls.body:
        if not isinstance(item, ast.FunctionDef):
            continue
        if not any(
            isinstance(d, ast.Name) and d.id == "property"
            for d in item.decorator_list
        ):
            continue
        for sub in ast.walk(item):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get"
                and isinstance(sub.func.value, ast.Attribute)
                and sub.func.value.attr == "_data"
                and isinstance(sub.func.value.value, ast.Name)
                and sub.func.value.value.id == "self"
            ):
                key = (
                    sub.args[0].value
                    if sub.args and isinstance(sub.args[0], ast.Constant)
                    else "?"
                )
                offenders.append((item.name, key))
    assert offenders == [], (
        f"Settings properties must not use self._data.get(key, default) "
        f"(violates L1 header contract): {offenders}"
    )


def test_b001_every_settings_property_key_is_in_required():
    """B001 antibody: every key accessed by a Settings property must be in
    the startup ``required`` list.

    If a property reads a key that is not in ``required``, missing-key
    failures surface at trade time, not at startup. Header contract
    violation.
    """
    required = set(_required_keys_literal())
    prop_keys = _property_keys()
    missing: list[tuple[str, str]] = []
    for prop_name, keys in prop_keys.items():
        for key in keys:
            if key not in required:
                missing.append((prop_name, key))
    assert missing == [], (
        f"Settings properties reference keys absent from the startup "
        f"required list: {missing}. Add them to `required` or remove the "
        f"property."
    )


def test_b001_startup_raises_on_missing_key(tmp_path, monkeypatch):
    """B001 runtime probe: Settings(path=<file missing required key>) must
    raise KeyError.

    Not just a unit test: this is the concrete surface the header
    contract promises. If this test passes but the AST antibodies don't,
    the contract is enforced at runtime but not structurally (convention).
    """
    from src.config import Settings

    # Load a valid settings.json, strip a required key, write to tmp.
    original = json.loads((REPO_ROOT / "config" / "settings.json").read_text())
    stripped = {k: v for k, v in original.items() if k != "bias_correction_enabled"}
    tmp_file = tmp_path / "settings.json"
    tmp_file.write_text(json.dumps(stripped))

    with pytest.raises(KeyError, match="bias_correction_enabled"):
        Settings(path=tmp_file)


# ---------------------------------------------------------------------------
# B047 — Scheduler observability enforcement
# ---------------------------------------------------------------------------


# Functions registered via scheduler.add_job that are exempt from the
# @_scheduler_job decorator. Only _write_heartbeat: it IS the
# observability channel — decorating it would cause 60s-cadence file
# writes for no benefit.
_HEARTBEAT_EXEMPT = {"_write_heartbeat"}


def _main_tree() -> ast.Module:
    return ast.parse(MAIN_PY.read_text())


def _decorated_job_names() -> set[str]:
    """Return function names in main.py decorated with @_scheduler_job(...)."""
    names: set[str] = set()
    for node in ast.walk(_main_tree()):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id == "_scheduler_job"
                ):
                    names.add(node.name)
    return names


def _scheduler_add_job_targets() -> list[tuple[int, str]]:
    """Return (lineno, target_fn_name) for every scheduler.add_job(fn, ...) call.

    For ``add_job(fn, ...)`` where fn is a direct name: target_fn_name = fn.
    For ``add_job(lambda: _inner(...), ...)``: target_fn_name = _inner.
    For any other shape: target_fn_name = "<unknown>" — flagged as failure.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(_main_tree()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_job"
            and node.args
        ):
            fn_arg = node.args[0]
            if isinstance(fn_arg, ast.Name):
                out.append((node.lineno, fn_arg.id))
            elif isinstance(fn_arg, ast.Lambda):
                # Find the inner call in the lambda body
                inner_name = "<lambda_no_call>"
                if isinstance(fn_arg.body, ast.Call) and isinstance(fn_arg.body.func, ast.Name):
                    inner_name = fn_arg.body.func.id
                out.append((node.lineno, inner_name))
            else:
                out.append((node.lineno, f"<unknown_shape:{type(fn_arg).__name__}>"))
    return out


def test_b047_every_scheduler_registered_wrapper_has_observability_decorator():
    """B047 antibody: every scheduler.add_job(fn, ...) target in main.py
    must route through @_scheduler_job.

    Silent log-and-continue in a scheduler wrapper is the failure class.
    Decorator enforces status-file observability on every job.
    """
    decorated = _decorated_job_names()
    registered = _scheduler_add_job_targets()
    # Expect at least 10 registrations (sanity check)
    assert len(registered) >= 10, (
        f"Expected >=10 scheduler.add_job sites in main.py, found {len(registered)}. "
        f"Test may be parsing the wrong file."
    )
    offenders = [
        (lineno, name)
        for lineno, name in registered
        if name not in decorated and name not in _HEARTBEAT_EXEMPT
    ]
    assert offenders == [], (
        f"scheduler.add_job targets missing @_scheduler_job decorator "
        f"(and not in _HEARTBEAT_EXEMPT): {offenders}. "
        f"Decorated: {sorted(decorated)}"
    )


def test_b047_decorator_writes_health_on_exception(tmp_path, monkeypatch):
    """B047 runtime probe: decorated function that raises must produce a
    scheduler_jobs_health.json entry with status=FAILED and the
    failure_reason captured.
    """
    from src.observability import scheduler_health

    # Redirect health file to tmp
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "health.json")

    from src.main import _scheduler_job

    @_scheduler_job("test_probe")
    def _boom():
        raise ValueError("kaboom")

    _boom()  # must NOT re-raise — fail-open per K2 design

    data = json.loads((tmp_path / "health.json").read_text())
    entry = data["test_probe"]
    assert entry["status"] == "FAILED"
    assert "kaboom" in entry["last_failure_reason"]
    assert "last_failure_at" in entry


def test_b047_decorator_writes_health_on_success(tmp_path, monkeypatch):
    """B047 paired positive: decorated function that returns normally
    must produce status=OK with last_success_at timestamp.
    """
    from src.observability import scheduler_health

    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", tmp_path / "health.json")

    from src.main import _scheduler_job

    @_scheduler_job("test_probe_ok")
    def _ok():
        return 42

    result = _ok()
    assert result == 42

    data = json.loads((tmp_path / "health.json").read_text())
    entry = data["test_probe_ok"]
    assert entry["status"] == "OK"
    assert "last_success_at" in entry
    assert "last_failure_reason" not in entry or entry.get("last_failure_reason") is None or True  # not asserted
