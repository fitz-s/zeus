# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/ + §3.5
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Audit-by-Grep Discipline"
"""
Tests for maintenance_worker.core.provenance.

Covers:
- make_run_id: format, uniqueness across rapid calls
- set_commit_identity: round-trip restores prior identity; works when no prior identity
- wrap_file_with_header: per file_kind, pure function, unknown kind defaults to '#'
- make_commit_message: Run-Id trailer present, subject format
"""
from __future__ import annotations

from pathlib import Path

import pytest

from maintenance_worker.core.provenance import (
    make_commit_message,
    make_run_id,
    set_commit_identity,
    wrap_file_with_header,
)


# ---------------------------------------------------------------------------
# make_run_id
# ---------------------------------------------------------------------------


def test_make_run_id_format() -> None:
    """run_id matches YYYYMMDDTHHMMSSz-<8 hex chars>."""
    run_id = make_run_id()
    parts = run_id.split("-")
    # Format: <timestamp>-<8hex> — timestamp has no dashes, hex suffix has none
    assert len(parts) == 2, f"Expected 2 dash-separated parts, got: {run_id!r}"
    ts, suffix = parts
    assert len(ts) == 16, f"Timestamp part should be 16 chars (YYYYMMDDTHHMMSSz), got {ts!r}"
    assert ts.endswith("Z"), f"Timestamp should end with 'Z', got {ts!r}"
    assert len(suffix) == 8, f"Suffix should be 8 hex chars, got {suffix!r}"
    assert all(c in "0123456789abcdef" for c in suffix), f"Suffix should be hex, got {suffix!r}"


def test_make_run_id_uniqueness() -> None:
    """Rapid successive calls produce distinct run-ids."""
    ids = [make_run_id() for _ in range(20)]
    assert len(set(ids)) == 20, "All 20 rapid run_ids must be unique"


def test_make_run_id_is_string() -> None:
    assert isinstance(make_run_id(), str)


def test_make_run_id_sortable() -> None:
    """Later call produces a lexicographically >= run_id (timestamp prefix)."""
    import time
    id1 = make_run_id()
    time.sleep(0.01)
    id2 = make_run_id()
    # At same second, timestamp parts are equal — that's fine. Just check no regression.
    assert id1 <= id2 or id1[:16] == id2[:16], (
        f"Later run_id should sort >= earlier: {id1!r} vs {id2!r}"
    )


# ---------------------------------------------------------------------------
# set_commit_identity
# ---------------------------------------------------------------------------


def test_set_commit_identity_sets_git_author_env(tmp_path: Path) -> None:
    """Context manager sets GIT_AUTHOR_NAME to 'Maintenance Worker'."""
    import os as _os
    run_id = make_run_id()
    observed: list[str] = []

    with set_commit_identity(tmp_path, run_id):
        observed.append(_os.environ.get("GIT_AUTHOR_NAME", ""))

    assert observed == ["Maintenance Worker"], f"Expected GIT_AUTHOR_NAME=Maintenance Worker, got {observed}"


def test_set_commit_identity_sets_email_env(tmp_path: Path) -> None:
    """Context manager sets GIT_AUTHOR_EMAIL to 'maintenance@worker.local'."""
    import os as _os
    run_id = make_run_id()
    observed: list[str] = []

    with set_commit_identity(tmp_path, run_id):
        observed.append(_os.environ.get("GIT_AUTHOR_EMAIL", ""))

    assert observed == ["maintenance@worker.local"], f"Got {observed}"


def test_set_commit_identity_sets_committer_env(tmp_path: Path) -> None:
    """Context manager sets both AUTHOR and COMMITTER env vars."""
    import os as _os
    run_id = make_run_id()

    with set_commit_identity(tmp_path, run_id):
        author_name = _os.environ.get("GIT_AUTHOR_NAME", "")
        committer_name = _os.environ.get("GIT_COMMITTER_NAME", "")

    assert author_name == "Maintenance Worker"
    assert committer_name == "Maintenance Worker"


def test_set_commit_identity_restores_prior_env(tmp_path: Path) -> None:
    """Context manager restores prior GIT_AUTHOR_NAME on exit."""
    import os as _os
    run_id = make_run_id()
    prior = "Prior Author"
    _os.environ["GIT_AUTHOR_NAME"] = prior
    try:
        with set_commit_identity(tmp_path, run_id):
            pass
        assert _os.environ.get("GIT_AUTHOR_NAME") == prior, (
            f"Expected GIT_AUTHOR_NAME restored to {prior!r}, "
            f"got {_os.environ.get('GIT_AUTHOR_NAME')!r}"
        )
    finally:
        _os.environ.pop("GIT_AUTHOR_NAME", None)


def test_set_commit_identity_removes_env_when_no_prior(tmp_path: Path) -> None:
    """Context manager removes GIT_AUTHOR_NAME if it wasn't set before."""
    import os as _os
    run_id = make_run_id()
    _os.environ.pop("GIT_AUTHOR_NAME", None)

    with set_commit_identity(tmp_path, run_id):
        pass

    assert "GIT_AUTHOR_NAME" not in _os.environ, (
        "GIT_AUTHOR_NAME must be removed when it wasn't set before context entry"
    )


def test_set_commit_identity_restores_on_exception(tmp_path: Path) -> None:
    """Restore happens even if the body raises."""
    import os as _os
    run_id = make_run_id()
    _os.environ.pop("GIT_AUTHOR_NAME", None)

    with pytest.raises(ValueError):
        with set_commit_identity(tmp_path, run_id):
            raise ValueError("test error")

    assert "GIT_AUTHOR_NAME" not in _os.environ, (
        "GIT_AUTHOR_NAME must be cleaned up even when body raises"
    )


# ---------------------------------------------------------------------------
# wrap_file_with_header
# ---------------------------------------------------------------------------


def test_wrap_file_with_header_python() -> None:
    """Python files get '# Generated-By: ...' header."""
    content = "print('hello')\n"
    run_id = "20260515T173042Z-a3f2c1b8"
    result = wrap_file_with_header(content, run_id, "py")
    assert result.startswith(f"# Generated-By: maintenance_worker/{run_id}"), result
    assert "print('hello')" in result


def test_wrap_file_with_header_shell() -> None:
    result = wrap_file_with_header("#!/bin/bash\n", "testid-12345678", "sh")
    assert result.startswith("# Generated-By: maintenance_worker/testid-12345678")


def test_wrap_file_with_header_yaml() -> None:
    result = wrap_file_with_header("key: value\n", "testid-12345678", "yaml")
    assert result.startswith("# Generated-By:")


def test_wrap_file_with_header_markdown() -> None:
    """Markdown files get HTML comment header."""
    result = wrap_file_with_header("# Title\n", "testid-12345678", "md")
    assert result.startswith("<!-- Generated-By:"), result
    assert result.strip().split("\n")[0].endswith("-->"), result


def test_wrap_file_with_header_typescript() -> None:
    result = wrap_file_with_header("const x = 1;\n", "testid-12345678", "ts")
    assert result.startswith("// Generated-By:")


def test_wrap_file_with_header_json_unchanged() -> None:
    """JSON has no comment syntax — content returned unchanged."""
    content = '{"key": "value"}\n'
    result = wrap_file_with_header(content, "testid-12345678", "json")
    assert result == content


def test_wrap_file_with_header_unknown_kind_defaults_hash() -> None:
    """Unknown file_kind defaults to '#' prefix (fail-safe)."""
    result = wrap_file_with_header("data\n", "testid-12345678", "xyzunknown")
    assert result.startswith("# Generated-By:")


def test_wrap_file_with_header_dot_prefix_stripped() -> None:
    """Leading dot in file_kind is stripped (e.g. '.py' same as 'py')."""
    run_id = "testid-12345678"
    assert wrap_file_with_header("x\n", run_id, ".py") == wrap_file_with_header("x\n", run_id, "py")


def test_wrap_file_with_header_case_insensitive() -> None:
    """file_kind matching is case-insensitive."""
    run_id = "testid-12345678"
    assert wrap_file_with_header("x\n", run_id, "PY") == wrap_file_with_header("x\n", run_id, "py")


def test_wrap_file_with_header_blank_line_separator() -> None:
    """Header and content are separated by a blank line."""
    result = wrap_file_with_header("body\n", "testid-12345678", "py")
    lines = result.split("\n")
    # lines[0] = header, lines[1] = blank, lines[2] = "body"
    assert lines[1] == "", f"Expected blank line after header, got: {lines}"


def test_wrap_file_with_header_pure_function() -> None:
    """wrap_file_with_header does not mutate input or touch filesystem."""
    content = "original content\n"
    run_id = "testid-12345678"
    result = wrap_file_with_header(content, run_id, "py")
    assert content == "original content\n", "Input must not be mutated"
    assert result != content, "Output must differ from input"


def test_wrap_file_with_header_descriptive_alias_python() -> None:
    """'python' descriptive alias works same as 'py'."""
    run_id = "testid-12345678"
    assert wrap_file_with_header("x\n", run_id, "python") == wrap_file_with_header("x\n", run_id, "py")


def test_wrap_file_with_header_descriptive_alias_shell() -> None:
    run_id = "testid-12345678"
    assert wrap_file_with_header("x\n", run_id, "shell") == wrap_file_with_header("x\n", run_id, "sh")


def test_wrap_file_with_header_descriptive_alias_markdown() -> None:
    run_id = "testid-12345678"
    assert wrap_file_with_header("x\n", run_id, "markdown") == wrap_file_with_header("x\n", run_id, "md")


# ---------------------------------------------------------------------------
# make_commit_message
# ---------------------------------------------------------------------------


def test_make_commit_message_subject_format() -> None:
    """Subject line follows 'maint(<task_id>): <summary>' format."""
    msg = make_commit_message("zero_byte_cleanup", "20260515T173042Z-a3f2c1b8", "remove 42 zero-byte files")
    subject = msg.split("\n")[0]
    assert subject == "maint(zero_byte_cleanup): remove 42 zero-byte files", subject


def test_make_commit_message_run_id_trailer() -> None:
    """Run-Id trailer is present per Audit-by-Grep contract."""
    run_id = "20260515T173042Z-a3f2c1b8"
    msg = make_commit_message("task1", run_id, "summary")
    assert f"Run-Id: {run_id}" in msg, f"Run-Id trailer missing in:\n{msg}"


def test_make_commit_message_trailer_after_blank_line() -> None:
    """Trailer section is separated from subject by a blank line (git trailer format)."""
    msg = make_commit_message("task1", "testid-12345678", "summary")
    lines = msg.split("\n")
    # lines[0] = subject, lines[1] = blank, lines[2+] = trailers
    assert lines[1] == "", f"Expected blank line between subject and trailers, got: {lines}"
    assert lines[2].startswith("Run-Id:"), f"Expected Run-Id at line 2, got: {lines}"


def test_make_commit_message_is_string() -> None:
    assert isinstance(make_commit_message("t", "r", "s"), str)


def test_make_commit_message_different_task_ids() -> None:
    """Different task_ids produce different subjects."""
    msg1 = make_commit_message("task_a", "runid-12345678", "summary")
    msg2 = make_commit_message("task_b", "runid-12345678", "summary")
    assert msg1.split("\n")[0] != msg2.split("\n")[0]
