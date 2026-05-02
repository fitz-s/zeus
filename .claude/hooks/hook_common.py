#!/usr/bin/env python3
"""Shared helpers for Zeus Claude/git hooks.

Created: 2026-05-02
Last reused/audited: 2026-05-02
Authority basis: docs/operations/task_2026-05-02_review_crash_remediation/PLAN.md Slice 4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path


GIT_SUBCOMMANDS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--config-env",
}

GIT_VALUELESS_OPTIONS = {
    "-p",
    "-P",
    "--paginate",
    "--no-pager",
    "--bare",
    "--literal-pathspecs",
    "--glob-pathspecs",
    "--noglob-pathspecs",
    "--icase-pathspecs",
    "--no-optional-locks",
}

SEPARATORS = {"&&", "||", ";", "|"}
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def _is_git_token(token: str) -> bool:
    return token == "git" or token.endswith("/git")


def _raw_mentions_git(command: str) -> bool:
    return bool(re.search(r"(^|[;&|\s])(/\S+/)?git([\s;&|]|$)", command))


def _shell_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def git_subcommands(command: str) -> list[str]:
    """Return git subcommands found on the command's first line.

    The parser is intentionally shell-token based rather than regex based. It
    handles env assignments, git global options with/without values, absolute
    git paths, and multiple git invocations separated by common shell operators.
    """
    if not command.strip():
        return []
    tokens = _shell_tokens(command)
    found: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in SEPARATORS or ENV_ASSIGNMENT.match(token):
            i += 1
            continue
        if not _is_git_token(token):
            i += 1
            continue

        i += 1
        while i < len(tokens):
            option = tokens[i]
            if option in SEPARATORS:
                break
            if option in GIT_SUBCOMMANDS_WITH_VALUE:
                i += 2
                continue
            if any(option.startswith(prefix + "=") for prefix in GIT_SUBCOMMANDS_WITH_VALUE if prefix.startswith("--")):
                i += 1
                continue
            if option in GIT_VALUELESS_OPTIONS:
                i += 1
                continue
            if option.startswith("--"):
                # Unknown long git global option: treat as value-less to avoid
                # missing the following real subcommand such as `merge`.
                i += 1
                continue
            if option.startswith("-") and option not in {"-"}:
                # Unknown short option. Review finding included value-less
                # short flags; skip one token rather than assuming a value.
                i += 1
                continue
            if "$" in option or "`" in option:
                raise ValueError(f"dynamic git subcommand is not statically parseable: {option!r}")
            found.append(option)
            break
        i += 1
    return found


def command_from_json(payload: str, field: str) -> str:
    try:
        data = json.loads(payload)
    except Exception as exc:  # noqa: BLE001 - hook diagnostic path
        raise ValueError(f"malformed hook JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("malformed hook JSON: root must be an object")
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        raise ValueError("malformed hook JSON: tool_input must be an object")
    value = tool_input.get(field) or ""
    if not isinstance(value, str):
        raise ValueError(f"malformed hook JSON: {field} must be a string")
    return value


def repo_relative(repo_root: str, file_path: str) -> tuple[int, str]:
    root = Path(repo_root).resolve()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = candidate.resolve(strict=False)
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return 10, ""
    return 0, rel.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    extract = sub.add_parser("extract-json-field")
    extract.add_argument("field")

    has = sub.add_parser("has-git-subcommand")
    has.add_argument("targets", nargs="+")

    rel = sub.add_parser("repo-relative")
    rel.add_argument("repo_root")
    rel.add_argument("file_path")

    args = parser.parse_args(argv)

    if args.cmd == "extract-json-field":
        try:
            print(command_from_json(sys.stdin.read(), args.field))
            return 0
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 64

    if args.cmd == "has-git-subcommand":
        command = os.environ.get("HOOK_COMMAND", "")
        targets = set(args.targets)
        try:
            subcommands = git_subcommands(command)
        except ValueError as exc:
            if _raw_mentions_git(command):
                print(f"could not parse git-looking command: {exc}", file=sys.stderr)
                return 64
            return 1
        return 0 if any(subcmd in targets for subcmd in subcommands) else 1

    if args.cmd == "repo-relative":
        code, path = repo_relative(args.repo_root, args.file_path)
        if path:
            print(path)
        return code

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
