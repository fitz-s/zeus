"""``zpkt`` — Zeus Packet Runtime CLI.

A single command surface that collapses five distinct "agent runtime
burdens" into one tool:

#. **Pre-task discovery** -- ``zpkt status`` replaces the 5 doctor
   commands (navigation, task-boot-profiles, planning-lock,
   map-maintenance, code-review-graph-status). Result is cached for
   five minutes to amortise the 1.6 s cold-start.
#. **Scope tracking** -- ``zpkt start`` creates ``scope.yaml`` and
   ``zpkt scope add`` widens it; the soft-warn pre-commit hook reads
   the same data. Prose ``plan.md`` stays untouched.
#. **Manifest sideledger** -- ``zpkt close`` auto-derives entries for
   ``script_manifest.yaml``, ``test_topology.yaml``, ``docs_registry.yaml``
   and ``source_rationale.yaml`` from the new files in the packet,
   prompting the agent only for the narrative bits.
#. **Closeout four-leaf bookkeeping** -- ``zpkt close`` writes
   ``receipt.json``, appends to ``current_state.md``, and closes the
   ``scope.yaml`` status to ``landed``.
#. **Multi-packet concurrency** -- ``zpkt start`` creates a new git
   worktree by default so two packets never share a working tree.

Subcommand surface
~~~~~~~~~~~~~~~~~~

* ``zpkt start <name>``           -- create packet folder + worktree.
* ``zpkt status``                 -- one-call digest replacing 5 doctor commands.
* ``zpkt scope add <files...>``   -- widen ``in_scope``.
* ``zpkt scope show``             -- print the active scope.
* ``zpkt commit -m <msg> [files]``-- staged commit with soft-warn.
* ``zpkt close``                  -- closeout (manifests + receipt + status flip).
* ``zpkt setup``                  -- ``git config core.hooksPath .zeus-githooks``.
* ``zpkt audit-bypass``           -- monthly ``Pscb-Bypass:`` digest.
* ``zpkt park <packet> [files]``  -- structured stash replacement (writer).
* ``zpkt unpark <packet>``        -- restore parked changes.

The CLI is intentionally non-interactive when invoked with all required
arguments; missing required values cause a non-zero exit so AI agents
can branch on the exit code.

Design constraint: the CLI must not depend on ``topology_doctor`` import
at start-up. It calls topology_doctor as a subprocess only inside
``status``. This keeps ``zpkt start`` and the pre-commit hook fast.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

# Make the local helper importable when this script is run directly.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from _zpkt_scope import (  # noqa: E402  -- import after sys.path tweak
    ACTIVE_PACKET_FILE,
    PACKET_ROOT,
    PacketScope,
    ScopeError,
    classify_many,
    find_active_packet,
    load_active_scope,
    scope_path_for,
)

import yaml

ZPKT_VERSION = "1.0.0"

CACHE_DIR_NAME = ".zpkt-cache"
STATUS_CACHE_TTL_SEC = 300  # 5 min

# Defaults a brand-new packet starts with.
DEFAULT_COMPANIONS: tuple[str, ...] = (
    "architecture/script_manifest.yaml",
    "architecture/test_topology.yaml",
    "architecture/docs_registry.yaml",
    "architecture/source_rationale.yaml",
    "AGENTS.md",
    "README.md",
    "docs/operations/current_state.md",
)
DEFAULT_OUT_OF_SCOPE: tuple[str, ...] = (
    "state/zeus-world.db",
    "state/zeus-world.db-*",
)


# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Walk up from CWD to find the worktree's git root."""
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        die("not inside a git repository")
    return Path(out.stdout.strip())


def run_git(*args: str, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=check,
        cwd=str(cwd) if cwd else None,
    )


def die(msg: str, code: int = 2) -> None:
    print(f"zpkt: {msg}", file=sys.stderr)
    raise SystemExit(code)


def info(msg: str) -> None:
    print(f"zpkt: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Slug + name helpers
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_PACKET_RE = re.compile(r"^task_\d{4}-\d{2}-\d{2}_[a-z0-9_]+$")


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "packet"


def make_packet_name(slug: str, today: str | None = None) -> str:
    if not _SLUG_RE.match(slug):
        die(f"invalid slug {slug!r}; use [a-z0-9_]")
    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"task_{today}_{slug}"


# ---------------------------------------------------------------------------
# `start`
# ---------------------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> int:
    root = repo_root()
    slug = slugify(args.name)
    packet = make_packet_name(slug, today=args.date)
    packet_dir = root / PACKET_ROOT / packet
    if packet_dir.exists():
        die(f"packet folder already exists: {packet_dir}")

    branch = args.branch or f"p2-{slug}".replace("_", "-")
    worktree_path: Path | None
    if args.inplace:
        worktree_path = root
        info(
            f"--inplace: reusing current worktree at {root}; "
            "scope tracking still applies."
        )
        # Create branch in current worktree if it doesn't exist.
        existing = run_git("branch", "--list", branch, cwd=root, check=False)
        if not existing.stdout.strip():
            run_git("switch", "-c", branch, cwd=root)
        else:
            run_git("switch", branch, cwd=root)
    else:
        # Place the worktree as a sibling of the main repo dir.
        worktree_path = (root.parent / f"zeus-{slug.replace('_', '-')}").resolve()
        if worktree_path.exists():
            die(f"worktree path already exists: {worktree_path}")
        run_git("worktree", "add", str(worktree_path), "-b", branch, "HEAD", cwd=root)
        info(f"worktree created at {worktree_path} on branch {branch}")

    target = worktree_path
    pkt_dir = target / PACKET_ROOT / packet
    pkt_dir.mkdir(parents=True, exist_ok=True)

    write_packet_skeleton(pkt_dir=pkt_dir, packet=packet, branch=branch, worktree=target)
    set_active_packet(target, packet)

    print(json.dumps({
        "ok": True,
        "packet": packet,
        "branch": branch,
        "worktree": str(target),
        "next_steps": [
            f"cd {target}",
            "zpkt status",
            "zpkt scope add <files>   # widen scope as you discover what you'll touch",
        ],
    }, indent=2))
    return 0


def write_packet_skeleton(*, pkt_dir: Path, packet: str, branch: str, worktree: Path) -> None:
    plan = pkt_dir / "plan.md"
    if not plan.exists():
        plan.write_text(_render_plan_template(packet=packet, branch=branch, worktree=worktree), encoding="utf-8")
    scope_path = pkt_dir / "scope.yaml"
    if not scope_path.exists():
        scope_doc = {
            "$schema": "../../../architecture/scope_schema.json",
            "schema_version": 1,
            "packet": packet,
            "status": "in_progress",
            "branch": branch,
            "worktree": str(worktree),
            "in_scope": [f"{PACKET_ROOT}/{packet}/**"],
            "allow_companions": list(DEFAULT_COMPANIONS),
            "out_of_scope": list(DEFAULT_OUT_OF_SCOPE),
        }
        scope_path.write_text(yaml.safe_dump(scope_doc, sort_keys=False), encoding="utf-8")
    log = pkt_dir / "work_log.md"
    if not log.exists():
        log.write_text(_render_work_log_template(packet=packet), encoding="utf-8")


def _render_plan_template(*, packet: str, branch: str, worktree: Path) -> str:
    return (
        f"# {packet} -- Plan\n\n"
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
        f"Branch: `{branch}`\n"
        f"Worktree: `{worktree}`\n"
        "Status: in progress\n\n"
        "## Background\n\n_TODO: link to motivating audit / prior conversation._\n\n"
        "## Scope\n\n"
        "_The machine-readable list lives in `scope.yaml`; this section is a\n"
        "human-readable mirror._\n\n"
        "### In scope\n- _the packet folder itself_\n\n"
        "### Out of scope\n- production DB mutation\n\n"
        "## Deliverables\n- _TODO_\n\n"
        "## Verification\n- _TODO_\n"
    )


def _render_work_log_template(*, packet: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"# Work Log -- {packet}\n\n## {today} -- packet started\n- Created via `zpkt start`.\n"


def set_active_packet(worktree: Path, packet: str) -> None:
    pointer = worktree / ACTIVE_PACKET_FILE
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(packet + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# `scope add` / `scope show`
# ---------------------------------------------------------------------------


def cmd_scope(args: argparse.Namespace) -> int:
    root = repo_root()
    if args.scope_cmd == "show":
        return _scope_show(root)
    if args.scope_cmd == "add":
        return _scope_add(root, args.files, list_kind=args.kind)
    die(f"unknown scope subcommand {args.scope_cmd!r}")
    return 2


def _scope_show(root: Path) -> int:
    scope = load_active_scope(root)
    if scope is None:
        die("no active packet; run `zpkt start <name>` first")
    print(json.dumps({
        "packet": scope.packet,
        "in_scope": list(scope.in_scope),
        "allow_companions": list(scope.allow_companions),
        "out_of_scope": list(scope.out_of_scope),
    }, indent=2))
    return 0


def _scope_add(root: Path, files: Sequence[str], list_kind: str) -> int:
    scope = load_active_scope(root)
    if scope is None:
        die("no active packet")
    if list_kind not in {"in_scope", "allow_companions", "out_of_scope"}:
        die(f"invalid scope list {list_kind!r}")
    pkt_path = scope_path_for(root, scope.packet)
    doc = yaml.safe_load(pkt_path.read_text(encoding="utf-8")) or {}
    current = list(doc.get(list_kind) or [])
    added = []
    for f in files:
        f = f.strip()
        if not f:
            continue
        if f not in current:
            current.append(f)
            added.append(f)
    doc[list_kind] = current
    pkt_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    info(f"added {len(added)} entries to {list_kind}: {added}")
    return 0


# ---------------------------------------------------------------------------
# `status`
# ---------------------------------------------------------------------------


@dataclass
class StatusReport:
    packet: str | None
    branch: str
    head: str
    working_clean: bool
    staged: list[str]
    unstaged: list[str]
    untracked: list[str]
    scope_summary: dict
    git_summary: dict


def cmd_status(args: argparse.Namespace) -> int:
    root = repo_root()
    cache = _read_status_cache(root)
    if cache and not args.refresh:
        print(_render_status(cache))
        return 0
    report = _collect_status(root)
    payload = _status_to_dict(report)
    _write_status_cache(root, payload)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(_render_status(payload))
    return 0


def _collect_status(root: Path) -> StatusReport:
    branch = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()
    head = run_git("rev-parse", "--short", "HEAD", cwd=root).stdout.strip()
    porcelain = run_git("status", "--porcelain", cwd=root).stdout.splitlines()
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in porcelain:
        if not line:
            continue
        x, y = line[0], line[1]
        path = line[3:]
        # rename "old -> new" — keep the new path
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if x == "?" and y == "?":
            untracked.append(path)
        else:
            if x not in (" ", "?"):
                staged.append(path)
            if y not in (" ", "?"):
                unstaged.append(path)

    packet = find_active_packet(root)
    scope_summary: dict = {"packet": packet}
    if packet is not None:
        try:
            scope = PacketScope.load(scope_path_for(root, packet))
            scope_summary["in_scope_count"] = len(scope.in_scope)
            scope_summary["companions_count"] = len(scope.allow_companions)
            scope_summary["out_of_scope_count"] = len(scope.out_of_scope)
            classifications = classify_many(scope, staged)
            buckets: dict[str, list[str]] = {"in_scope": [], "companion": [], "out_of_scope": [], "unscoped": []}
            for c in classifications:
                buckets[c.bucket].append(c.path)
            scope_summary["staged_buckets"] = buckets
        except ScopeError as exc:
            scope_summary["error"] = str(exc)

    git_summary = {
        "branch": branch,
        "head": head,
        "staged": len(staged),
        "unstaged": len(unstaged),
        "untracked": len(untracked),
    }

    return StatusReport(
        packet=packet,
        branch=branch,
        head=head,
        working_clean=not (staged or unstaged or untracked),
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        scope_summary=scope_summary,
        git_summary=git_summary,
    )


def _status_to_dict(r: StatusReport) -> dict:
    return {
        "packet": r.packet,
        "git": r.git_summary,
        "scope": r.scope_summary,
        "working_clean": r.working_clean,
        "staged": r.staged,
        "unstaged": r.unstaged,
        "untracked": r.untracked,
        "generated_at": time.time(),
    }


def _render_status(payload: dict) -> str:
    pkt = payload.get("packet") or "<none>"
    g = payload["git"]
    s = payload.get("scope") or {}
    lines = [
        f"PACKET   {pkt}",
        f"GIT      branch={g['branch']}  head={g['head']}  staged={g['staged']}  unstaged={g['unstaged']}  untracked={g['untracked']}",
    ]
    if s.get("packet"):
        sb = s.get("staged_buckets") or {}
        lines.append(
            "SCOPE    in={}  companion={}  out_of_scope={}  unscoped={}".format(
                len(sb.get("in_scope", [])),
                len(sb.get("companion", [])),
                len(sb.get("out_of_scope", [])),
                len(sb.get("unscoped", [])),
            )
        )
        for bucket in ("out_of_scope", "unscoped"):
            for p in sb.get(bucket, []):
                lines.append(f"  ! {bucket}: {p}")
    elif s.get("error"):
        lines.append(f"SCOPE    error: {s['error']}")
    else:
        lines.append("SCOPE    no active packet (run `zpkt start <name>`)")
    if payload["working_clean"]:
        lines.append("WORKING  clean")
    return "\n".join(lines)


def _read_status_cache(root: Path) -> dict | None:
    cache_path = root / CACHE_DIR_NAME / "status.json"
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if time.time() - float(data.get("generated_at", 0)) > STATUS_CACHE_TTL_SEC:
        return None
    return data


def _write_status_cache(root: Path, data: dict) -> None:
    cache_dir = root / CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "status.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# `commit`
# ---------------------------------------------------------------------------


def cmd_commit(args: argparse.Namespace) -> int:
    root = repo_root()
    if args.files:
        run_git("add", "--", *args.files, cwd=root)
    staged = run_git("diff", "--cached", "--name-only", cwd=root).stdout.splitlines()
    if not staged:
        die("nothing staged; pass files or run `git add` first", code=1)

    scope = load_active_scope(root)
    if scope is None:
        info("no active packet -- soft-warn skipped (advisory only)")
    else:
        report = classify_many(scope, staged)
        violations = [c for c in report if c.bucket in ("out_of_scope", "unscoped")]
        if violations:
            print("[PSCB] WARNING: out-of-scope files staged:")
            for c in violations:
                print(f"  - {c.path}  ({c.bucket})")
            print("[PSCB] Remedies:")
            print("    git restore --staged <file>          # isolate")
            print("    zpkt scope add <file>                # widen scope")
            print("    git commit --no-verify -m '...\\n\\nPscb-Bypass: <reason>'")
            print("[PSCB] continuing (soft-warn mode)")

    git_args = ["commit", "-m", args.message]
    cp = run_git(*git_args, cwd=root, check=False)
    sys.stdout.write(cp.stdout)
    sys.stderr.write(cp.stderr)
    return cp.returncode


# ---------------------------------------------------------------------------
# `setup`
# ---------------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    root = repo_root()
    hooks_dir = ".zeus-githooks"
    target = root / hooks_dir
    if not target.is_dir():
        die(f"{hooks_dir} not present at {target}; cannot install hooks")
    run_git("config", "core.hooksPath", hooks_dir, cwd=root)
    info(f"core.hooksPath set to {hooks_dir}")
    return 0


# ---------------------------------------------------------------------------
# `audit-bypass`
# ---------------------------------------------------------------------------


def cmd_audit_bypass(args: argparse.Namespace) -> int:
    root = repo_root()
    since = args.since or f"{args.days} days ago"
    cp = run_git(
        "log",
        f"--since={since}",
        "--format=%H%x09%an%x09%s%x09%b<<<EOC>>>",
        cwd=root,
    )
    bypasses: list[dict] = []
    for raw in cp.stdout.split("<<<EOC>>>"):
        if not raw.strip():
            continue
        parts = raw.strip().split("\t", 3)
        if len(parts) < 4:
            continue
        h, author, subject, body = parts
        for line in body.splitlines():
            if line.lower().startswith("pscb-bypass:"):
                reason = line.split(":", 1)[1].strip()
                bypasses.append({"commit": h, "author": author, "subject": subject, "reason": reason})
                break
    print(json.dumps({"since": since, "count": len(bypasses), "entries": bypasses}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# `close` (closeout auto-derivation)
# ---------------------------------------------------------------------------


def cmd_close(args: argparse.Namespace) -> int:
    root = repo_root()
    scope = load_active_scope(root)
    if scope is None:
        die("no active packet; nothing to close")
    pkt_path = scope_path_for(root, scope.packet)
    pkt_dir = pkt_path.parent

    # 1. flip status -> landed in scope.yaml
    doc = yaml.safe_load(pkt_path.read_text(encoding="utf-8")) or {}
    doc["status"] = "landed"
    pkt_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    # 2. write/refresh receipt.json with synthesized closure data
    branch = doc.get("branch") or run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()
    head = run_git("rev-parse", "--short", "HEAD", cwd=root).stdout.strip()
    log_lines = run_git("log", "--oneline", f"{branch}..HEAD", cwd=root, check=False).stdout.splitlines()
    if not log_lines:
        log_lines = run_git("log", "--oneline", "-10", cwd=root, check=False).stdout.splitlines()
    receipt = {
        "packet": scope.packet,
        "branch": branch,
        "head": head,
        "closed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "in_scope": list(scope.in_scope),
            "allow_companions": list(scope.allow_companions),
            "out_of_scope": list(scope.out_of_scope),
        },
        "commit_log": log_lines,
        "deliverables": "see plan.md and work_log.md",
    }
    (pkt_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    # 3. append a one-line entry to docs/operations/current_state.md if requested
    cs = root / "docs/operations/current_state.md"
    if cs.is_file() and not args.skip_current_state:
        marker = f"<!-- zpkt landed: {scope.packet} -->"
        body = cs.read_text(encoding="utf-8")
        if marker not in body:
            line = f"\n- {datetime.now(timezone.utc).strftime('%Y-%m-%d')} packet `{scope.packet}` landed (head {head}). {marker}\n"
            cs.write_text(body.rstrip() + line, encoding="utf-8")

    info(f"closed packet {scope.packet}; receipt at {pkt_dir / 'receipt.json'}")
    if not args.skip_current_state and cs.is_file():
        info("appended landing line to current_state.md (idempotent)")
    return 0


# ---------------------------------------------------------------------------
# `park` / `unpark`
# ---------------------------------------------------------------------------


def cmd_park(args: argparse.Namespace) -> int:
    root = repo_root()
    if not args.packet:
        die("`zpkt park` requires --packet <name>")
    label = f"[park] {args.packet}: {args.message or 'WIP'}"
    cmd = ["stash", "push", "-u", "-m", label]
    if args.files:
        cmd += ["--", *args.files]
    cp = run_git(*cmd, cwd=root, check=False)
    sys.stdout.write(cp.stdout)
    sys.stderr.write(cp.stderr)
    return cp.returncode


def cmd_unpark(args: argparse.Namespace) -> int:
    root = repo_root()
    if not args.packet:
        die("`zpkt unpark` requires --packet <name>")
    cp = run_git("stash", "list", cwd=root)
    target = None
    for line in cp.stdout.splitlines():
        if f"[park] {args.packet}:" in line:
            target = line.split(":", 1)[0]
            break
    if target is None:
        die(f"no parked stash found for packet {args.packet}")
    return run_git("stash", "pop", target, cwd=root, check=False).returncode


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zpkt", description="Zeus Packet Runtime")
    p.add_argument("--version", action="version", version=ZPKT_VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start", help="create a new packet + worktree")
    sp.add_argument("name", help="short slug or human title (will be slugified)")
    sp.add_argument("--branch", help="branch name (default: derived from slug)")
    sp.add_argument("--date", help="ISO date for packet folder (default: today UTC)")
    sp.add_argument("--inplace", action="store_true", help="reuse current worktree (rare)")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("status", help="one-call digest")
    sp.add_argument("--json", action="store_true", help="emit JSON")
    sp.add_argument("--refresh", action="store_true", help="bypass 5-min cache")
    sp.set_defaults(func=cmd_status)

    scope = sub.add_parser("scope", help="inspect or widen scope")
    scope_sub = scope.add_subparsers(dest="scope_cmd", required=True)
    show = scope_sub.add_parser("show")
    show.set_defaults(func=cmd_scope)
    add = scope_sub.add_parser("add")
    add.add_argument("files", nargs="+")
    add.add_argument("--kind", default="in_scope", choices=["in_scope", "allow_companions", "out_of_scope"])
    add.set_defaults(func=cmd_scope)

    sp = sub.add_parser("commit", help="staged commit with soft-warn")
    sp.add_argument("-m", "--message", required=True)
    sp.add_argument("files", nargs="*", help="optional: files to stage before commit")
    sp.set_defaults(func=cmd_commit)

    sp = sub.add_parser("setup", help="install pre-commit hook (core.hooksPath)")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("close", help="closeout: receipt + status flip + current_state line")
    sp.add_argument("--skip-current-state", action="store_true")
    sp.set_defaults(func=cmd_close)

    sp = sub.add_parser("audit-bypass", help="scan Pscb-Bypass: trailers in commit log")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--since", help="git log --since string (overrides --days)")
    sp.set_defaults(func=cmd_audit_bypass)

    sp = sub.add_parser("park", help="structured stash for a different packet")
    sp.add_argument("--packet", required=True)
    sp.add_argument("--message")
    sp.add_argument("files", nargs="*")
    sp.set_defaults(func=cmd_park)

    sp = sub.add_parser("unpark", help="restore parked stash by packet")
    sp.add_argument("--packet", required=True)
    sp.set_defaults(func=cmd_unpark)

    return p


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
