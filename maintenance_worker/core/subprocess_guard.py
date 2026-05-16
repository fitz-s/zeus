# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/subprocess_guard.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Forbidden Actions"
"""
subprocess_guard — SubprocessGuard.check_subprocess(argv) -> ValidatorResult

Covers SAFETY_CONTRACT.md "Forbidden Actions":
  - rm (unconditionally per SCAFFOLD ND1; zero-byte deletion uses os.unlink via
    Operation.DELETE, never subprocess rm)
  - chmod / chown (file permission / ownership changes)
  - ln -s across safety boundary
  - pip install / npm install / cargo add / cargo install (package managers)
  - pytest / shell commands that mutate state outside evidence dir
  - Network commands beyond gh allowlist: curl, wget, ssh, scp
  - git / gh: delegated to git_operation_guard / gh_operation_guard respectively;
    subprocess_guard does NOT duplicate that logic.

ALLOWED:
  - pytest (read-only inspection, python -m pytest)
  - python / python3 (non-mutating invocations)
  - gh (delegated; subprocess_guard returns ALLOWED so gh_operation_guard handles)
  - git (delegated; subprocess_guard returns ALLOWED so git_operation_guard handles)
  - Standard read-only tools: ls, cat, find, grep, sort, wc, head, tail, diff,
    echo, date, which, env, true, false

No logic outside guard decisions. Stdlib only. Zero project-specific identifiers.
"""
from __future__ import annotations

from typing import Optional

from maintenance_worker.types.operations import Operation
from maintenance_worker.types.results import ValidatorResult


# ---------------------------------------------------------------------------
# Blocked command root names (argv[0] basename)
# ---------------------------------------------------------------------------

# Blocked unconditionally — no argv flag combination permits these.
_BLOCKED_COMMANDS: frozenset[str] = frozenset(
    {
        "rm",
        "chmod",
        "chown",
        "curl",
        "wget",
        "ssh",
        "scp",
        "sftp",
        "rsync",
        "nc",
        "netcat",
        "ncat",
        "telnet",
        "ftp",
        "socat",
    }
)

# Package managers — blocked in all install/add/remove/uninstall forms.
_PACKAGE_MANAGER_COMMANDS: frozenset[str] = frozenset(
    {
        "pip",
        "pip3",
        "npm",
        "yarn",
        "pnpm",
        "cargo",
        "gem",
        "brew",
        "apt",
        "apt-get",
        "yum",
        "dnf",
        "apk",
        "pacman",
    }
)

# Subcommands of package managers that are always blocked.
_PACKAGE_MANAGER_BLOCKED_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "install",
        "add",
        "remove",
        "uninstall",
        "update",
        "upgrade",
        "downgrade",
        "reinstall",
    }
)

# Commands delegated to their own guard modules; subprocess_guard returns ALLOWED
# so the caller can invoke the specialized guard.
_DELEGATED_COMMANDS: frozenset[str] = frozenset({"git", "gh"})

# Pytest variants — allowed only in read-only forms; see _is_pytest_mutating.
_PYTEST_COMMANDS: frozenset[str] = frozenset({"pytest", "py.test"})

# Allowed read-only tools (non-exhaustive allowlist; anything not blocked).
# REMOVED escape hatches: env (wrapper execution), xargs (wrapper execution),
# tee (writes to arbitrary paths), sed (has -i in-place mutation flag).
# awk is retained (no in-place mutation flag; output always to stdout).
_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "python",
        "python3",
        "ls",
        "cat",
        "find",
        "grep",
        "sort",
        "wc",
        "head",
        "tail",
        "diff",
        "echo",
        "date",
        "which",
        "true",
        "false",
        "test",
        "printf",
        "awk",
        "tr",
        "cut",
        "uniq",
        "du",
        "df",
        "stat",
        "file",
        "basename",
        "dirname",
        "pwd",
        "id",
        "whoami",
        "uname",
        "arch",
        "hostname",
        "uptime",
    }
)

# sed in-place flags — block mutation forms; read-only (stdout) forms are allowed.
_SED_INPLACE_FLAGS: frozenset[str] = frozenset({"-i", "--in-place"})

# Blocked pytest flags — these mutate state outside the evidence dir.
_PYTEST_BLOCKED_FLAGS: frozenset[str] = frozenset(
    {
        "--create-fixtures",
        "--fixtures-only",
        "--generate-fixtures",
        "--cache-clear",
        "--basetemp",
        "--lf",
        "--last-failed-no-failures",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cmd_basename(argv: list[str]) -> str:
    """Return the basename of argv[0], or '' if argv is empty."""
    if not argv:
        return ""
    import os
    return os.path.basename(argv[0])


def _argv_has_flag(argv: list[str], flags: frozenset[str]) -> bool:
    """
    Return True if any token in argv matches a flag in flags.

    Handles both '--flag value' and '--flag=value' forms.
    """
    for token in argv:
        # Exact match
        if token in flags:
            return True
        # --flag=value form: check prefix up to '='
        if "=" in token:
            prefix = token.split("=", 1)[0]
            if prefix in flags:
                return True
    return False


def _get_subcommand(argv: list[str]) -> Optional[str]:
    """Return argv[1] (the subcommand), or None if argv has fewer than 2 tokens."""
    if len(argv) < 2:
        return None
    return argv[1]


def _is_python_pytest_invocation(argv: list[str]) -> bool:
    """
    Detect: python -m pytest ... or python3 -m pytest ...
    Returns True if argv looks like a Python pytest invocation.
    """
    basename = _cmd_basename(argv)
    if basename not in {"python", "python3"}:
        return False
    # Find -m flag and check next token
    for i, token in enumerate(argv):
        if token == "-m" and i + 1 < len(argv):
            return argv[i + 1] == "pytest"
    return False


def _is_pytest_mutating(argv: list[str]) -> bool:
    """
    Return True if this pytest invocation has flags that mutate state
    outside the evidence dir per SAFETY_CONTRACT.md "Forbidden Actions".
    """
    return _argv_has_flag(argv, _PYTEST_BLOCKED_FLAGS)


def _is_ln_across_boundary(argv: list[str]) -> bool:
    """
    Detect ln -s (symbolic link creation) — blocked unconditionally
    since we cannot verify boundary crossing at this layer without path resolution.
    SAFETY_CONTRACT: 'no ln -s from quarantine into forbidden-path set.'
    Block all ln -s; the allowed deletion path is Python-native os.unlink.
    """
    if _cmd_basename(argv) != "ln":
        return False
    # Any ln invocation with -s flag is blocked
    for token in argv[1:]:
        if token == "-s" or token.startswith("-") and "s" in token.lstrip("-"):
            return True
    return False


def _is_package_manager_blocked(cmd: str, argv: list[str]) -> bool:
    """
    Return True if this is a package manager command with a blocked subcommand.
    """
    if cmd not in _PACKAGE_MANAGER_COMMANDS:
        return False
    subcommand = _get_subcommand(argv)
    if subcommand is None:
        return False  # bare 'pip' with no subcommand — allow (list, show, etc.)
    return subcommand in _PACKAGE_MANAGER_BLOCKED_SUBCOMMANDS


def _check_env_argv(argv: list[str]) -> ValidatorResult:
    """
    Handle 'env' invocations by recursing on the inner command.

    env can be called as:
      env VAR=VAL ... COMMAND [ARGS...]   — skip VAR=VAL tokens, recurse on COMMAND
      env -i VAR=VAL ... COMMAND [ARGS...]
      env COMMAND [ARGS...]               — no VAR=VAL prefix

    If no inner command is present (bare 'env' or 'env VAR=VAL'), returns ALLOWED
    (read-only: prints environment). If an inner command is found, recurse into
    check_subprocess to evaluate it — this prevents 'env rm /' escaping the guard.
    """
    inner: list[str] = []
    i = 1
    while i < len(argv):
        token = argv[i]
        # Skip env-specific flags (-i, --ignore-environment, -u/--unset VAR, etc.)
        if token in {"-i", "--ignore-environment"}:
            i += 1
            continue
        if token in {"-u", "--unset"}:
            i += 2  # skip next token (the var name)
            continue
        if token.startswith("-u") or token.startswith("--unset="):
            i += 1
            continue
        # VAR=VAL — env variable assignment, skip
        if "=" in token and not token.startswith("-"):
            i += 1
            continue
        # First non-flag, non-assignment token is the inner command
        inner = argv[i:]
        break
        i += 1  # unreachable; satisfies linter

    if not inner:
        # bare env or env with only VAR=VAL — safe (prints environment)
        return ValidatorResult.ALLOWED

    # Recurse: evaluate inner command against the same guard
    return check_subprocess(inner)


def _is_sed_mutating(argv: list[str]) -> bool:
    """
    Return True if this sed invocation uses in-place mutation flags (-i/--in-place).
    """
    return _argv_has_flag(argv, _SED_INPLACE_FLAGS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_subprocess(argv: list[str]) -> ValidatorResult:
    """
    Check a subprocess invocation against the safety contract.

    Returns:
      ALLOWED            — operation is permitted
      FORBIDDEN_OPERATION — operation is blocked

    Delegation: git and gh argv[0] return ALLOWED here; callers must
    additionally invoke check_git_operation / check_gh_operation for those.

    rm is BLOCKED unconditionally (SCAFFOLD ND1, SCAFFOLD §3 subprocess_guard
    note). Zero-byte deletions use Python-native os.unlink via Operation.DELETE
    after validate_action; never subprocess rm.

    ln -s is BLOCKED unconditionally; we cannot verify boundary safety
    without full path resolution, which is the validator's job.
    """
    if not argv:
        return ValidatorResult.ALLOWED

    cmd = _cmd_basename(argv)

    # Delegated commands: git, gh are handled by their own guards.
    if cmd in _DELEGATED_COMMANDS:
        return ValidatorResult.ALLOWED

    # Unconditionally blocked commands.
    if cmd in _BLOCKED_COMMANDS:
        return ValidatorResult.FORBIDDEN_OPERATION

    # ln -s is blocked (symlink across boundary risk).
    if _is_ln_across_boundary(argv):
        return ValidatorResult.FORBIDDEN_OPERATION

    # Package manager install/add/remove subcommands blocked.
    # Non-mutating subcommands (list, show, freeze) pass through.
    if cmd in _PACKAGE_MANAGER_COMMANDS:
        if _is_package_manager_blocked(cmd, argv):
            return ValidatorResult.FORBIDDEN_OPERATION
        # Non-blocked package manager subcommand (e.g. pip show, pip list)
        return ValidatorResult.ALLOWED

    # Pytest — allowed unless mutating flags present.
    if cmd in _PYTEST_COMMANDS:
        if _is_pytest_mutating(argv):
            return ValidatorResult.FORBIDDEN_OPERATION
        return ValidatorResult.ALLOWED

    # python -m pytest — same rule.
    if _is_python_pytest_invocation(argv):
        if _is_pytest_mutating(argv):
            return ValidatorResult.FORBIDDEN_OPERATION
        return ValidatorResult.ALLOWED

    # env — allowed only for read-only inner command; recurse to evaluate.
    if cmd == "env":
        return _check_env_argv(argv)

    # sed — allowed in read-only (stdout) form; in-place mutation (-i) is blocked.
    if cmd == "sed":
        if _is_sed_mutating(argv):
            return ValidatorResult.FORBIDDEN_OPERATION
        return ValidatorResult.ALLOWED

    # Allowed explicitly-listed read-only tools.
    if cmd in _ALLOWED_COMMANDS:
        return ValidatorResult.ALLOWED

    # Default: any command not in the explicit allowlist and not in the
    # blocked set is treated as unknown. To avoid being a leaky allowlist,
    # unknown commands are FORBIDDEN_OPERATION.
    # Callers must explicitly permit new commands by adding to _ALLOWED_COMMANDS.
    return ValidatorResult.FORBIDDEN_OPERATION
