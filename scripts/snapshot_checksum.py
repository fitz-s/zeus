# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: Data-readiness workstream closure follow-up
# (docs/operations/task_2026-04-23_data_readiness_remediation/first_principles.md
# closure-banner item 5 — SHA-256 for rollback snapshot hashes
# because MD5 is collision-broken). Preserves existing .md5 sidecars
# for audit continuity; adds .sha256 sidecars as the integrity source
# of truth going forward.

"""SHA-256 rollback snapshot integrity tool.

Reads/writes `.sha256` sidecars for DB snapshot files using the standard
`sha256sum(1)` format (<64-hex> + two-space separator + basename + LF).
Used to verify rollback-chain integrity during data-readiness audits.

Streaming hash (4 MiB chunks) so multi-GB snapshots do not blow memory.

Existing `.md5` sidecars are NOT removed — they stay for audit
continuity — but must NOT be trusted for integrity guarantees because
MD5 is collision-broken.

Usage:
    # Generate sidecar for a single snapshot
    .venv/bin/python scripts/snapshot_checksum.py --compute state/zeus-world.db.pre-pe_2026-04-23

    # Verify a single sidecar
    .venv/bin/python scripts/snapshot_checksum.py --verify state/zeus-world.db.pre-pe_2026-04-23.sha256

    # Verify every sidecar matching the default pattern
    .venv/bin/python scripts/snapshot_checksum.py --verify-all

    # Machine-readable output
    .venv/bin/python scripts/snapshot_checksum.py --verify-all --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOB = "state/zeus-world.db.pre-*.sha256"
CHUNK_BYTES = 4 * 1024 * 1024


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def sidecar_line(hex_digest: str, basename: str) -> str:
    return f"{hex_digest}  {basename}\n"


def write_sidecar(file_path: Path) -> tuple[Path, str]:
    digest = compute_sha256(file_path)
    sidecar = file_path.with_name(file_path.name + ".sha256")
    sidecar.write_text(sidecar_line(digest, file_path.name))
    return sidecar, digest


def parse_sidecar(sidecar: Path) -> tuple[str, str]:
    contents = sidecar.read_text().strip()
    if not contents:
        raise ValueError(f"sidecar empty: {sidecar}")
    parts = contents.split("  ", 1)
    if len(parts) != 2:
        raise ValueError(
            f"sidecar format invalid (expected '<hex>  <basename>'): {sidecar}"
        )
    expected, basename = parts[0], parts[1]
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError(
            f"sidecar digest wrong shape (want 64-char lowercase hex): {sidecar}"
        )
    return expected, basename


def verify_sidecar(sidecar: Path) -> tuple[bool, str]:
    if not sidecar.exists():
        return False, f"sidecar missing: {sidecar}"
    try:
        expected, basename = parse_sidecar(sidecar)
    except ValueError as exc:
        return False, str(exc)
    db_path = sidecar.parent / basename
    if not db_path.exists():
        return False, f"target file missing: {db_path}"
    try:
        actual = compute_sha256(db_path)
    except OSError as exc:
        return False, f"read error on {basename}: {exc}"
    if actual != expected:
        return (
            False,
            f"HASH MISMATCH: {basename} expected {expected[:16]}... got {actual[:16]}...",
        )
    return True, f"OK: {basename}"


def verify_all(pattern: str) -> list[tuple[bool, str]]:
    matches = sorted(ZEUS_ROOT.glob(pattern))
    if not matches:
        return [(False, f"no sidecars match pattern: {pattern}")]
    return [verify_sidecar(p) for p in matches]


def _emit(results: list[tuple[bool, str]], use_json: bool) -> None:
    ok = all(r[0] for r in results)
    if use_json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "count": len(results),
                    "results": [
                        {"ok": r[0], "detail": r[1]} for r in results
                    ],
                },
                indent=2,
            )
        )
    else:
        for r in results:
            print(r[1])
        print(f"\nSummary: {'OK' if ok else 'FAILED'} ({len(results)} checked)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SHA-256 rollback snapshot integrity tool"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--compute",
        metavar="FILE",
        help="Compute SHA-256 for FILE and write FILE.sha256 sidecar",
    )
    group.add_argument(
        "--verify",
        metavar="SIDECAR",
        help="Verify a single .sha256 sidecar against its target file",
    )
    group.add_argument(
        "--verify-all",
        action="store_true",
        help="Verify every sidecar matching --pattern (default: state snapshots)",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB,
        help=f"Glob for --verify-all (default: {DEFAULT_GLOB})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable output",
    )
    args = parser.parse_args()

    results: list[tuple[bool, str]]
    if args.compute:
        path = Path(args.compute).resolve()
        if not path.exists():
            msg = f"ERROR: file not found: {path}"
            if args.json:
                print(json.dumps({"ok": False, "error": msg}))
            else:
                print(msg, file=sys.stderr)
            return 2
        sidecar, digest = write_sidecar(path)
        results = [(True, f"wrote {sidecar.name} ({digest[:16]}...)")]
    elif args.verify:
        sidecar = Path(args.verify).resolve()
        results = [verify_sidecar(sidecar)]
    else:
        results = verify_all(args.pattern)

    _emit(results, args.json)
    return 0 if all(r[0] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
