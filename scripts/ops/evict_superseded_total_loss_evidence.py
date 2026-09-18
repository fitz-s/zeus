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
import sys
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


def _plan(runtime: Path) -> tuple[list[Path], list[Path], dict[str, int]]:
    """Return (superseded generation dirs, redundant legacy files, retention counts)."""
    incidents = runtime / "incidents"
    superseded: list[Path] = []
    legacy: list[Path] = []
    kept = {
        "no_pointer": 0,
        "legacy_is_only_evidence": 0,
        "current_generations": 0,
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
            if live_is_usable:
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


def _live_incident_count(runtime: Path) -> int | None:
    memory_db = runtime / "memory.db"
    if not memory_db.is_file():
        return None
    assert _LOOP_SPEC and _LOOP_SPEC.loader
    loop = importlib.util.module_from_spec(_LOOP_SPEC)
    try:
        _LOOP_SPEC.loader.exec_module(loop)
    except Exception:
        return None
    try:
        with loop.open_ro(memory_db) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM incidents "
                "WHERE status IN ('queued','retry_pending','observing')"
            ).fetchone()
            return int(row[0]) if row else None
    except (sqlite3.Error, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the unlinks")
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

    superseded, legacy, kept = _plan(runtime)
    superseded_bytes = sum(_directory_bytes(path) for path in superseded)
    legacy_bytes = 0
    for path in legacy:
        try:
            legacy_bytes += path.stat().st_size
        except OSError:
            continue

    live = _live_incident_count(runtime)
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
