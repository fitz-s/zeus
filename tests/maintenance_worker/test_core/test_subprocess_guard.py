# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/subprocess_guard.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Forbidden Actions"
"""
Tests for maintenance_worker.core.subprocess_guard.

Covers:
- rm blocked unconditionally (ND1)
- chmod / chown blocked
- curl / wget / ssh / scp network commands blocked
- Package manager install subcommands blocked (pip, npm, cargo, brew, etc.)
- Package manager non-mutating subcommands allowed (pip show, pip list)
- pytest allowed in read-only form; blocked with mutating flags
- python -m pytest delegation
- git / gh delegated (returned ALLOWED for specialized guard)
- Standard read-only tools allowed
- Empty argv allowed
- Unknown commands blocked (closed allowlist)
- ln -s blocked
"""
from __future__ import annotations

import pytest

from maintenance_worker.core.subprocess_guard import check_subprocess
from maintenance_worker.types.results import ValidatorResult

ALLOWED = ValidatorResult.ALLOWED
FORBIDDEN = ValidatorResult.FORBIDDEN_OPERATION


# ---------------------------------------------------------------------------
# rm — blocked unconditionally (ND1)
# ---------------------------------------------------------------------------


def test_rm_blocked_unconditionally() -> None:
    assert check_subprocess(["rm", "somefile.txt"]) == FORBIDDEN


def test_rm_blocked_with_force_flag() -> None:
    assert check_subprocess(["rm", "-f", "somefile.txt"]) == FORBIDDEN


def test_rm_blocked_with_rf_flag() -> None:
    assert check_subprocess(["rm", "-rf", "/tmp/dir"]) == FORBIDDEN


def test_rm_blocked_full_path() -> None:
    assert check_subprocess(["/bin/rm", "-f", "file"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# chmod / chown — blocked
# ---------------------------------------------------------------------------


def test_chmod_blocked() -> None:
    assert check_subprocess(["chmod", "755", "file.py"]) == FORBIDDEN


def test_chown_blocked() -> None:
    assert check_subprocess(["chown", "user:group", "file"]) == FORBIDDEN


def test_chmod_full_path_blocked() -> None:
    assert check_subprocess(["/bin/chmod", "+x", "script.sh"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# Network commands — blocked
# ---------------------------------------------------------------------------


def test_curl_blocked() -> None:
    assert check_subprocess(["curl", "https://example.com"]) == FORBIDDEN


def test_wget_blocked() -> None:
    assert check_subprocess(["wget", "https://example.com"]) == FORBIDDEN


def test_ssh_blocked() -> None:
    assert check_subprocess(["ssh", "user@host"]) == FORBIDDEN


def test_scp_blocked() -> None:
    assert check_subprocess(["scp", "file", "user@host:/path"]) == FORBIDDEN


def test_nc_blocked() -> None:
    assert check_subprocess(["nc", "-l", "8080"]) == FORBIDDEN


def test_socat_blocked() -> None:
    assert check_subprocess(["socat", "TCP:host:80", "-"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# Package managers — install/add/remove blocked
# ---------------------------------------------------------------------------


def test_pip_install_blocked() -> None:
    assert check_subprocess(["pip", "install", "requests"]) == FORBIDDEN


def test_pip3_install_blocked() -> None:
    assert check_subprocess(["pip3", "install", "numpy"]) == FORBIDDEN


def test_pip_uninstall_blocked() -> None:
    assert check_subprocess(["pip", "uninstall", "requests"]) == FORBIDDEN


def test_npm_install_blocked() -> None:
    assert check_subprocess(["npm", "install", "lodash"]) == FORBIDDEN


def test_npm_remove_blocked() -> None:
    assert check_subprocess(["npm", "remove", "lodash"]) == FORBIDDEN


def test_cargo_install_blocked() -> None:
    assert check_subprocess(["cargo", "install", "ripgrep"]) == FORBIDDEN


def test_cargo_add_blocked() -> None:
    assert check_subprocess(["cargo", "add", "serde"]) == FORBIDDEN


def test_brew_install_blocked() -> None:
    assert check_subprocess(["brew", "install", "jq"]) == FORBIDDEN


def test_yarn_add_blocked() -> None:
    assert check_subprocess(["yarn", "add", "react"]) == FORBIDDEN


def test_apt_install_blocked() -> None:
    assert check_subprocess(["apt", "install", "vim"]) == FORBIDDEN


def test_apt_get_install_blocked() -> None:
    assert check_subprocess(["apt-get", "install", "vim"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# Package managers — read-only subcommands allowed
# ---------------------------------------------------------------------------


def test_pip_show_allowed() -> None:
    assert check_subprocess(["pip", "show", "requests"]) == ALLOWED


def test_pip_list_allowed() -> None:
    assert check_subprocess(["pip", "list"]) == ALLOWED


def test_pip_freeze_allowed() -> None:
    assert check_subprocess(["pip", "freeze"]) == ALLOWED


def test_npm_list_allowed() -> None:
    assert check_subprocess(["npm", "list"]) == ALLOWED


# ---------------------------------------------------------------------------
# ln -s — blocked
# ---------------------------------------------------------------------------


def test_ln_s_blocked() -> None:
    assert check_subprocess(["ln", "-s", "/target", "/link"]) == FORBIDDEN


def test_ln_s_combined_flags_blocked() -> None:
    assert check_subprocess(["ln", "-sf", "/target", "/link"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# pytest — allowed in read-only form; blocked with mutating flags
# ---------------------------------------------------------------------------


def test_pytest_basic_allowed() -> None:
    assert check_subprocess(["pytest", "tests/"]) == ALLOWED


def test_pytest_with_k_flag_allowed() -> None:
    assert check_subprocess(["pytest", "-k", "test_something", "tests/"]) == ALLOWED


def test_pytest_with_v_flag_allowed() -> None:
    assert check_subprocess(["pytest", "-v", "tests/"]) == ALLOWED


def test_pytest_cache_clear_blocked() -> None:
    assert check_subprocess(["pytest", "--cache-clear", "tests/"]) == FORBIDDEN


def test_python_m_pytest_allowed() -> None:
    assert check_subprocess(["python", "-m", "pytest", "tests/"]) == ALLOWED


def test_python3_m_pytest_allowed() -> None:
    assert check_subprocess(["python3", "-m", "pytest", "tests/"]) == ALLOWED


def test_python_m_pytest_cache_clear_blocked() -> None:
    assert check_subprocess(["python3", "-m", "pytest", "--cache-clear"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# git / gh — delegated (returned ALLOWED for specialized guard)
# ---------------------------------------------------------------------------


def test_git_delegated_returns_allowed() -> None:
    # subprocess_guard returns ALLOWED; caller must also run check_git_operation
    assert check_subprocess(["git", "status"]) == ALLOWED


def test_git_push_force_delegated_returns_allowed() -> None:
    # Force push check is git_operation_guard's responsibility, not here
    assert check_subprocess(["git", "push", "--force"]) == ALLOWED


def test_gh_delegated_returns_allowed() -> None:
    assert check_subprocess(["gh", "pr", "list"]) == ALLOWED


def test_gh_pr_merge_delegated_returns_allowed() -> None:
    # gh_operation_guard handles this; subprocess_guard delegates
    assert check_subprocess(["gh", "pr", "merge", "123"]) == ALLOWED


# ---------------------------------------------------------------------------
# Standard read-only tools — allowed
# ---------------------------------------------------------------------------


def test_ls_allowed() -> None:
    assert check_subprocess(["ls", "-la"]) == ALLOWED


def test_find_allowed() -> None:
    assert check_subprocess(["find", ".", "-name", "*.py"]) == ALLOWED


def test_grep_allowed() -> None:
    assert check_subprocess(["grep", "-r", "pattern", "."]) == ALLOWED


def test_cat_allowed() -> None:
    assert check_subprocess(["cat", "file.txt"]) == ALLOWED


def test_diff_allowed() -> None:
    assert check_subprocess(["diff", "file1", "file2"]) == ALLOWED


def test_python_allowed() -> None:
    assert check_subprocess(["python", "--version"]) == ALLOWED


def test_python3_allowed() -> None:
    assert check_subprocess(["python3", "script.py"]) == ALLOWED


def test_echo_allowed() -> None:
    assert check_subprocess(["echo", "hello"]) == ALLOWED


def test_date_allowed() -> None:
    assert check_subprocess(["date"]) == ALLOWED


def test_stat_allowed() -> None:
    assert check_subprocess(["stat", "file"]) == ALLOWED


# ---------------------------------------------------------------------------
# Empty argv
# ---------------------------------------------------------------------------


def test_empty_argv_allowed() -> None:
    assert check_subprocess([]) == ALLOWED


# ---------------------------------------------------------------------------
# Unknown commands — blocked (closed allowlist)
# ---------------------------------------------------------------------------


def test_unknown_command_blocked() -> None:
    assert check_subprocess(["some_unknown_tool", "--flag"]) == FORBIDDEN


def test_make_blocked() -> None:
    # make is not in the allowlist
    assert check_subprocess(["make", "build"]) == FORBIDDEN


def test_docker_blocked() -> None:
    assert check_subprocess(["docker", "run", "image"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# SEV-1 #3 adversarial tests — escape hatch closures
# ---------------------------------------------------------------------------


class TestSev1EnvRecursion:
    """
    env wraps an inner command; the inner command must be evaluated by
    check_subprocess. Without this, 'env rm -rf /' bypasses the rm block.
    """

    def test_env_rm_blocked(self) -> None:
        assert check_subprocess(["env", "rm", "/tmp/x"]) == FORBIDDEN

    def test_env_var_then_rm_blocked(self) -> None:
        assert check_subprocess(["env", "FOO=bar", "rm", "/"]) == FORBIDDEN

    def test_env_ignore_env_then_rm_blocked(self) -> None:
        assert check_subprocess(["env", "-i", "rm", "/tmp/x"]) == FORBIDDEN

    def test_env_unset_then_rm_blocked(self) -> None:
        assert check_subprocess(["env", "-u", "PATH", "rm", "/etc/hosts"]) == FORBIDDEN

    def test_env_chmod_blocked(self) -> None:
        assert check_subprocess(["env", "chmod", "777", "/etc/hosts"]) == FORBIDDEN

    def test_env_curl_blocked(self) -> None:
        assert check_subprocess(["env", "curl", "https://evil.com"]) == FORBIDDEN

    def test_env_pip_install_blocked(self) -> None:
        assert check_subprocess(["env", "pip", "install", "malware"]) == FORBIDDEN

    def test_env_bare_allowed(self) -> None:
        """bare 'env' with no inner command prints environment — safe."""
        assert check_subprocess(["env"]) == ALLOWED

    def test_env_var_only_allowed(self) -> None:
        """'env VAR=val' with no inner command — safe."""
        assert check_subprocess(["env", "FOO=bar"]) == ALLOWED

    def test_env_ls_allowed(self) -> None:
        assert check_subprocess(["env", "ls", "/tmp"]) == ALLOWED

    def test_env_python_allowed(self) -> None:
        assert check_subprocess(["env", "python3", "script.py"]) == ALLOWED

    def test_env_grep_allowed(self) -> None:
        assert check_subprocess(["env", "LANG=C", "grep", "pattern", "file"]) == ALLOWED


class TestSev1XargsTeeRemoved:
    """
    xargs and tee were removed from _ALLOWED_COMMANDS.
    xargs wraps arbitrary commands; tee writes to arbitrary paths.
    """

    def test_xargs_blocked(self) -> None:
        assert check_subprocess(["xargs", "rm"]) == FORBIDDEN

    def test_xargs_bare_blocked(self) -> None:
        assert check_subprocess(["xargs"]) == FORBIDDEN

    def test_tee_blocked(self) -> None:
        assert check_subprocess(["tee", "/etc/passwd"]) == FORBIDDEN

    def test_tee_bare_blocked(self) -> None:
        assert check_subprocess(["tee"]) == FORBIDDEN


class TestSev1SedInplace:
    """
    sed -i / --in-place is a mutation form; read-only (stdout) sed is allowed.
    """

    def test_sed_inplace_short_blocked(self) -> None:
        assert check_subprocess(["sed", "-i", "s/a/b/", "/etc/hosts"]) == FORBIDDEN

    def test_sed_inplace_long_blocked(self) -> None:
        assert check_subprocess(["sed", "--in-place", "s/a/b/", "file.txt"]) == FORBIDDEN

    def test_sed_inplace_equals_form_blocked(self) -> None:
        assert check_subprocess(["sed", "--in-place=.bak", "s/a/b/", "file.txt"]) == FORBIDDEN

    def test_sed_read_only_allowed(self) -> None:
        """sed without -i writes to stdout — read-only form."""
        assert check_subprocess(["sed", "s/a/b/", "file.txt"]) == ALLOWED

    def test_sed_n_flag_allowed(self) -> None:
        assert check_subprocess(["sed", "-n", "p", "file.txt"]) == ALLOWED

    def test_sed_no_args_allowed(self) -> None:
        assert check_subprocess(["sed"]) == ALLOWED
