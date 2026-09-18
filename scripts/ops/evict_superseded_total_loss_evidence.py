# Created: 2026-09-18
# Authority basis: docs/operations/current/family_books_compacted_and_total_loss_audit_2026-09-18.md
#   plus the two 2026-09-18 fixes in total_loss_loop.py: 47da51eee (the repair prompt
#   resolves the CURRENT generation) and d27f4bd62 (the reaper removes a superseded
#   generation). This script reclaims what those leaked BEFORE they landed; it adds no
#   retention policy and never decides that an incident is finished.
# WRITER_LOCK: none needed. memory.db is opened READ-ONLY and only files on disk are
#   unlinked. Nothing here writes to any database.
"""Evict total-loss evidence snapshots that no code path can reach any more.

Two disjoint classes, each proven unreachable rather than merely old:

1. SUPERSEDED GENERATIONS - `generations/<id>/` that `CURRENT` does not name.
   `_evidence_pair_paths` resolves evidence exclusively through `CURRENT`, so a
   generation the pointer has moved off cannot be read again by construction.
   Before d27f4bd62 the reaper skipped exactly these (it returned early whenever
   both `evidence.db` and `manifest.json` were present), so one leaked per rebuild.

2. LEGACY-LAYOUT SNAPSHOTS - `incidents/<id>/evidence.db`, the pre-migration path.
   Nothing has written it since 6721fa637 (2026-08-24), and after 47da51eee nothing
   reads it either while the incident has a valid CURRENT generation. It is evicted
   ONLY when such a generation exists, so the incident keeps a live snapshot; a
   legacy file that is an incident's only evidence is always retained, because the
   `_start_repair` fallback still names it.

Neither class deletes an incident directory, a manifest, a diagnosis, or any row in
memory.db. Every incident remains exactly as resumable as it was: `observing`,
`queued` and `retry_pending` are all treated as live, since `observing` is resumable
too (total_loss_loop.py:4191) and is therefore not a terminal state.

A missing or unreadable `CURRENT` retains everything under that incident: without the
pointer we cannot distinguish the live generation from a superseded one.

Dry run by default; `--apply` performs the unlinks.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Reuse the loop's own read-only opener rather than a bare sqlite3.connect(): the
# writer-lock antibody fails CI on direct connects, and this is the loop's own store,
# so its `query_only=ON` opener is the right authority for reading it.
_LOOP_SPEC = importlib.util.spec_from_file_location(
    "total_loss_loop_ro", ROOT / "total_loss_loop.py"
)


def _generation_is_complete(generation_dir: Path) -> bool:
    return (generation_dir / "evidence.db").is_file() and (
        generation_dir / "manifest.json"
    ).is_file()


def _current_generation(incident_dir: Path) -> str | None:
    """The generation `CURRENT` names, or None when it cannot be trusted.

    Mirrors `_evidence_pair_paths`: a pointer that escapes its own directory is
    rejected rather than followed.
    """
    try:
        pointer = (incident_dir / "CURRENT").read_text().strip()
    except OSError:
        return None
    if not pointer or Path(pointer).name != pointer:
        return None
    return pointer


def _plan(
    runtime: Path, *, hold_legacy_for: frozenset[str] = frozenset()
) -> tuple[list[Path], list[Path], dict[str, int]]:
    """Return (superseded generation dirs, redundant legacy files, retention counts).

    `hold_legacy_for` names incidents whose legacy file is retained even though a
    CURRENT generation exists — used to stay safe against a daemon still running
    pre-47da51eee code, which would still resolve the legacy path.
    """
    incidents = runtime / "incidents"
    superseded: list[Path] = []
    legacy: list[Path] = []
    kept = {
        "no_pointer": 0,
        "legacy_is_only_evidence": 0,
        "current_generations": 0,
        "held_for_running_daemon": 0,
    }
    if not incidents.is_dir():
        return superseded, legacy, kept

    for incident_dir in sorted(incidents.iterdir()):
        if not incident_dir.is_dir():
            continue
        pointer = _current_generation(incident_dir)
        generations = incident_dir / "generations"

        if pointer is None:
            # Cannot tell live from superseded: retain every generation, and retain
            # the legacy file too because it may be the only reachable evidence.
            if generations.is_dir() or (incident_dir / "evidence.db").is_file():
                kept["no_pointer"] += 1
            continue

        live_generation = generations / pointer
        live_is_usable = _generation_is_complete(live_generation)
        if live_is_usable:
            kept["current_generations"] += 1

        if generations.is_dir():
            for generation_dir in sorted(generations.iterdir()):
                if not generation_dir.is_dir() or generation_dir.name == pointer:
                    continue
                superseded.append(generation_dir)

        legacy_file = incident_dir / "evidence.db"
        if legacy_file.is_file() and not legacy_file.is_symlink():
            if live_is_usable and incident_dir.name in hold_legacy_for:
                kept["held_for_running_daemon"] += 1
            elif live_is_usable:
                legacy.append(legacy_file)
            else:
                # The `_start_repair` fallback still names this path, so it is this
                # incident's only evidence. Keep it.
                kept["legacy_is_only_evidence"] += 1

    return superseded, legacy, kept


def _directory_bytes(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _incident_states(runtime: Path) -> tuple[int | None, frozenset[str]]:
    """Return (live incident count, incidents a pre-fix daemon could still dispatch).

    The second set is `repair_waiting`/`queued`: `_dispatch_repair_waiting` selects
    exactly that pair, and a daemon running pre-47da51eee code resolves the legacy
    path for them. Everything else is unreachable regardless of daemon version.
    """
    memory_db = runtime / "memory.db"
    if not memory_db.is_file():
        return None, frozenset()
    assert _LOOP_SPEC and _LOOP_SPEC.loader
    loop = importlib.util.module_from_spec(_LOOP_SPEC)
    try:
        _LOOP_SPEC.loader.exec_module(loop)
    except Exception:
        return None, frozenset()
    try:
        with loop.open_ro(memory_db) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM incidents "
                "WHERE status IN ('queued','retry_pending','observing')"
            ).fetchone()
            live = int(row[0]) if row else None
            dispatchable = frozenset(
                str(candidate[0])
                for candidate in conn.execute(
                    "SELECT incident_id FROM incidents "
                    "WHERE stage='repair_waiting' AND status='queued'"
                )
            )
            return live, dispatchable
    except (sqlite3.Error, OSError):
        return None, frozenset()


_REPAIR_PATH_FIX = "47da51eee"


def _daemon_predates_the_fix() -> str | None:
    """Return a refusal reason when the running daemon is older than the fix.

    `--daemon-restarted` asserts that the daemon resolves evidence through
    `_evidence_pair_paths`. An assertion the script cannot check is not a guard: a
    stale daemon still resolves the legacy path, so evicting those files while it
    runs removes evidence it would hand to a repair agent. Compare the process start
    time against the fix's commit time and refuse rather than trust the operator.

    A daemon that is not running, or a repository that cannot date the fix, is not a
    refusal: there is then nothing that could read the legacy path.
    """
    try:
        pids = subprocess.run(
            ["pgrep", "-f", "total_loss_loop.py daemon"],
            capture_output=True, text=True, check=False,
        ).stdout.split()
    except OSError:
        return None
    if not pids:
        return None
    try:
        fix_epoch = int(
            subprocess.run(
                ["git", "log", "-1", "--format=%ct", _REPAIR_PATH_FIX],
                cwd=ROOT, capture_output=True, text=True, check=True,
            ).stdout.strip()
        )
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None

    for pid in pids:
        try:
            started = subprocess.run(
                ["ps", "-o", "lstart=", "-p", pid],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if not started:
                continue
            started_epoch = int(
                subprocess.run(
                    ["date", "-j", "-f", "%a %b %d %H:%M:%S %Y", started, "+%s"],
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
            )
        except (subprocess.CalledProcessError, OSError, ValueError):
            continue
        if started_epoch < fix_epoch:
            return (
                f"daemon pid {pid} started {started}, before {_REPAIR_PATH_FIX} "
                f"({datetime.fromtimestamp(fix_epoch).astimezone():%Y-%m-%d %H:%M:%S %z}), "
                "so it still resolves the legacy evidence path. Restart it first, or "
                "drop --daemon-restarted to keep those files."
            )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the unlinks")
    parser.add_argument(
        "--daemon-restarted",
        action="store_true",
        help=(
            "evict the legacy files of incidents queued at repair_waiting too. Only "
            "valid once the running daemon carries 47da51eee, and VERIFIED against the "
            "daemon's own start time rather than taken on trust: a daemon older than "
            "the fix refuses the flag."
        ),
    )
    parser.add_argument(
        "--runtime",
        default=str(ROOT / ".total_loss"),
        help="total-loss runtime directory (default: <repo>/.total_loss)",
    )
    args = parser.parse_args(argv)

    runtime = Path(args.runtime).resolve()
    if not runtime.is_dir():
        print(f"runtime directory not found: {runtime}", file=sys.stderr)
        return 2

    if args.daemon_restarted:
        refusal = _daemon_predates_the_fix()
        if refusal is not None:
            print(f"refusing --daemon-restarted: {refusal}", file=sys.stderr)
            return 3

    live, dispatchable = _incident_states(runtime)
    hold = frozenset() if args.daemon_restarted else dispatchable
    superseded, legacy, kept = _plan(runtime, hold_legacy_for=hold)
    superseded_bytes = sum(_directory_bytes(path) for path in superseded)
    legacy_bytes = 0
    for path in legacy:
        try:
            legacy_bytes += path.stat().st_size
        except OSError:
            continue

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] runtime={runtime}")
    if live is not None:
        print(f"  live incidents (queued/retry_pending/observing): {live} - all retained")
    print(
        f"  superseded generations : {len(superseded):>5} dirs   "
        f"{superseded_bytes / 1e9:7.2f} GB"
    )
    print(
        f"  redundant legacy files : {len(legacy):>5} files  "
        f"{legacy_bytes / 1e9:7.2f} GB"
    )
    print(f"  retained, live CURRENT generation      : {kept['current_generations']}")
    print(f"  retained, legacy file is only evidence : {kept['legacy_is_only_evidence']}")
    print(f"  retained, no usable CURRENT pointer    : {kept['no_pointer']}")
    if not args.daemon_restarted:
        print(
            f"  retained, running daemon may dispatch  : "
            f"{kept['held_for_running_daemon']}"
            "  (pass --daemon-restarted once the daemon carries 47da51eee)"
        )
    print(f"  total reclaimable: {(superseded_bytes + legacy_bytes) / 1e9:.2f} GB")

    if not args.apply:
        print("\nno changes made; re-run with --apply")
        return 0

    removed_dirs = removed_files = 0
    for path in superseded:
        try:
            shutil.rmtree(path)
            removed_dirs += 1
        except OSError as exc:
            print(f"  WARN could not remove {path}: {exc}", file=sys.stderr)
    for path in legacy:
        try:
            path.unlink()
            removed_files += 1
        except OSError as exc:
            print(f"  WARN could not remove {path}: {exc}", file=sys.stderr)

    print(f"\nremoved {removed_dirs} generation dirs and {removed_files} legacy files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
