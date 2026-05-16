#!/usr/bin/env python3
# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/LORE_EXTRACTION_PROTOCOL.md
"""lore_reverify — re-run verification_command for each lore card and check output signature.

Usage:
    python3 scripts/lore_reverify.py [--lore-root PATH] [--timeout SECS] [--dry-run]

Signature algorithm:
    SHA256-hex of stdout with trailing whitespace stripped per line and
    normalized line endings (LF). If a card has no expected_signature field,
    the current output's signature is RECORDED (not compared) on the first run.
    On subsequent runs, the recorded signature is compared.

Frontmatter fields used:
    verification_command:  shell command to run (skipped if absent/empty)
    expected_signature:    SHA256-hex of expected stdout (updated on first run)
    status:                flipped to NEEDS_RE_VERIFICATION on mismatch

The script does NOT retire cards — that requires human action.
Mismatch → status field in frontmatter updated to NEEDS_RE_VERIFICATION.
The human reads the summary and decides whether the card or the command is stale.

Excluded directories: _drafts/, retired/
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from _yaml_bootstrap import import_yaml
except ModuleNotFoundError:
    try:
        from scripts._yaml_bootstrap import import_yaml
    except ModuleNotFoundError:
        import_yaml = None  # type: ignore[assignment]

if import_yaml is not None:
    yaml = import_yaml()
else:
    try:
        import yaml  # type: ignore[no-redef]
    except ImportError:
        yaml = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LORE_ROOT = ROOT / "docs" / "lore"
DEFAULT_TIMEOUT = 60
EXCLUDED_DIRS = frozenset({"_drafts", "retired"})


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def _compute_signature(stdout: str) -> str:
    """SHA256-hex of normalized stdout (strip trailing whitespace per line, LF line endings)."""
    lines = stdout.splitlines()
    normalized = "\n".join(line.rstrip() for line in lines)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Frontmatter I/O
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict | None, str, str]:
    """Return (fm_dict, raw_fm_block, body) or (None, '', full_text)."""
    if not text.startswith("---"):
        return None, "", text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, "", text
    raw_fm = parts[1]
    body = parts[2]
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install it with: pip install PyYAML")
    try:
        fm = yaml.safe_load(raw_fm)
    except Exception:  # noqa: BLE001
        return None, raw_fm, body
    return (fm if isinstance(fm, dict) else None), raw_fm, body


def _update_frontmatter_field(text: str, field_name: str, new_value: str) -> str:
    """
    Update a single field in the YAML frontmatter block.
    If the field doesn't exist, appends it before the closing ---.
    Returns updated full text.
    """
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    raw_fm = parts[1]
    body = parts[2]

    # Try to replace existing field value
    pattern = re.compile(rf"^({re.escape(field_name)}:\s*).*$", re.MULTILINE)
    if pattern.search(raw_fm):
        new_raw_fm = pattern.sub(rf"\g<1>{new_value}", raw_fm)
    else:
        # Append field at end of frontmatter block
        new_raw_fm = raw_fm.rstrip("\n") + f"\n{field_name}: {new_value}\n"

    return f"---{new_raw_fm}---{body}"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class VerifyResult:
    card_path: Path
    card_id: str
    outcome: str  # "skipped" | "recorded" | "ok" | "mismatch" | "error" | "timeout"
    message: str = ""
    expected_sig: str = ""
    actual_sig: str = ""


# ---------------------------------------------------------------------------
# Walk and verify
# ---------------------------------------------------------------------------


def _walk_cards_with_verification(
    lore_root: Path,
) -> list[tuple[Path, dict, str]]:
    """Yield (path, fm, text) for cards that have a non-empty verification_command."""
    results = []
    if not lore_root.is_dir():
        return results

    for md_path in sorted(lore_root.rglob("*.md")):
        try:
            rel = md_path.relative_to(lore_root)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            continue
        containing_dir = parts[0] if len(parts) > 1 else ""
        if containing_dir in EXCLUDED_DIRS:
            continue
        if len(parts) == 1:
            continue  # root-level .md files

        text = md_path.read_text(encoding="utf-8", errors="replace")
        fm, _, _ = _split_frontmatter(text)
        if fm is None:
            continue
        cmd = str(fm.get("verification_command", "")).strip()
        if not cmd:
            continue
        results.append((md_path, fm, text))
    return results


def _run_verification(
    cmd: str,
    timeout: int,
    cwd: Path,
) -> tuple[str | None, str | None]:
    """Run cmd in subprocess. Returns (stdout, error_message) — stdout is None on failure."""
    try:
        tokens = shlex.split(cmd)
    except ValueError as exc:
        return None, f"shlex parse error: {exc}"
    try:
        result = subprocess.run(
            tokens,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
        )
        return result.stdout, None
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s"
    except FileNotFoundError as exc:
        return None, f"command not found: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, f"subprocess error: {exc}"


def reverify_cards(
    lore_root: Path,
    timeout: int,
    dry_run: bool,
) -> list[VerifyResult]:
    """Walk lore cards with verification_command, run each, compare signatures."""
    cards = _walk_cards_with_verification(lore_root)
    results: list[VerifyResult] = []

    for md_path, fm, text in cards:
        card_id = str(fm.get("id", md_path.stem))
        cmd = str(fm.get("verification_command", "")).strip()
        expected_sig = str(fm.get("expected_signature", "")).strip()

        if dry_run:
            results.append(
                VerifyResult(
                    card_path=md_path,
                    card_id=card_id,
                    outcome="skipped",
                    message=f"dry-run: would run: {cmd}",
                )
            )
            continue

        stdout, err = _run_verification(cmd, timeout, cwd=ROOT)

        if err is not None:
            results.append(
                VerifyResult(
                    card_path=md_path,
                    card_id=card_id,
                    outcome="error",
                    message=err,
                )
            )
            continue

        actual_sig = _compute_signature(stdout or "")

        if not expected_sig:
            # First run: record the signature
            updated = _update_frontmatter_field(text, "expected_signature", actual_sig)
            md_path.write_text(updated, encoding="utf-8")
            results.append(
                VerifyResult(
                    card_path=md_path,
                    card_id=card_id,
                    outcome="recorded",
                    message="no prior expected_signature; signature recorded",
                    actual_sig=actual_sig,
                )
            )
            continue

        if actual_sig == expected_sig:
            results.append(
                VerifyResult(
                    card_path=md_path,
                    card_id=card_id,
                    outcome="ok",
                    expected_sig=expected_sig,
                    actual_sig=actual_sig,
                )
            )
        else:
            # Mismatch: flip status to NEEDS_RE_VERIFICATION
            updated = _update_frontmatter_field(text, "status", "NEEDS_RE_VERIFICATION")
            md_path.write_text(updated, encoding="utf-8")
            results.append(
                VerifyResult(
                    card_path=md_path,
                    card_id=card_id,
                    outcome="mismatch",
                    message="signature mismatch; status set to NEEDS_RE_VERIFICATION",
                    expected_sig=expected_sig,
                    actual_sig=actual_sig,
                )
            )

    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _print_summary(results: list[VerifyResult]) -> None:
    total = len(results)
    by_outcome: dict[str, int] = {}
    for r in results:
        by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1

    print(f"\nRe-verification summary: {total} card(s) with verification_command")
    for outcome, count in sorted(by_outcome.items()):
        print(f"  {outcome:20s}: {count}")

    mismatches = [r for r in results if r.outcome == "mismatch"]
    errors = [r for r in results if r.outcome == "error"]

    if mismatches:
        print("\nMismatches (require human review):")
        for r in mismatches:
            print(f"  {r.card_path}")
            print(f"    expected: {r.expected_sig[:16]}...")
            print(f"    actual:   {r.actual_sig[:16]}...")

    if errors:
        print("\nErrors:")
        for r in errors:
            print(f"  {r.card_path}: {r.message}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Re-run verification_command for lore cards and check signatures.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Signature algorithm:
    SHA256-hex of stdout (trailing whitespace stripped per line, LF line endings).

First-run behavior:
    If a card has no expected_signature, the current output is recorded.
    On subsequent runs, the recorded signature is compared.

Examples:
  python3 scripts/lore_reverify.py
  python3 scripts/lore_reverify.py --dry-run
  python3 scripts/lore_reverify.py --timeout 30 --strict
""",
    )
    p.add_argument(
        "--lore-root",
        default=str(DEFAULT_LORE_ROOT),
        help="Root lore directory (default: docs/lore/)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-command timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be run without executing commands or modifying cards",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any mismatches or errors are found",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    lore_root = Path(args.lore_root)
    results = reverify_cards(
        lore_root=lore_root,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    _print_summary(results)

    if args.strict:
        has_issues = any(r.outcome in ("mismatch", "error") for r in results)
        return 1 if has_issues else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
