#!/usr/bin/env python3
# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/LORE_EXTRACTION_PROTOCOL.md
"""lore_promoter — move a draft lore card from _drafts/ to its topic directory.

Usage:
    python3 scripts/lore_promoter.py promote <draft_id> <topic> [--dry-run]
    python3 scripts/lore_promoter.py list-drafts

The promoter:
    1. Resolves _drafts/<draft_id>.md (or _drafts/<draft_id> with .md added).
    2. Parses and validates required frontmatter fields.
    3. Verifies the topic matches the card's frontmatter topic field.
    4. Creates the destination directory if needed.
    5. Moves the file to docs/lore/<topic>/<draft_id>.md.

Required frontmatter fields (per LORE_EXTRACTION_PROTOCOL):
    id, title, topic, extracted_from, extracted_on, status, authority_class, last_verified

Valid topics:
    topology, hooks, runtime, data, calibration, execution, settlement,
    vendor, browser, identity, packet
"""

from __future__ import annotations

import argparse
import shutil
import sys
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
DRAFTS_DIR_NAME = "_drafts"

VALID_TOPICS = frozenset(
    {
        "topology",
        "hooks",
        "runtime",
        "data",
        "calibration",
        "execution",
        "settlement",
        "vendor",
        "browser",
        "identity",
        "packet",
    }
)

REQUIRED_FIELDS = (
    "id",
    "title",
    "topic",
    "extracted_from",
    "extracted_on",
    "status",
    "authority_class",
    "last_verified",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict | None:
    """Return parsed YAML frontmatter dict or None on failure."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    raw = parts[1].strip()
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install it with: pip install PyYAML")
    try:
        fm = yaml.safe_load(raw)
    except Exception:  # noqa: BLE001
        return None
    return fm if isinstance(fm, dict) else None


def _validate_frontmatter(fm: dict, path: Path) -> list[str]:
    """Return list of validation error messages (empty = valid)."""
    errors: list[str] = []
    for field_name in REQUIRED_FIELDS:
        if not fm.get(field_name):
            errors.append(f"missing required field: '{field_name}'")
    topic = fm.get("topic", "")
    if topic and topic not in VALID_TOPICS:
        errors.append(f"invalid topic '{topic}'; must be one of {sorted(VALID_TOPICS)}")
    return errors


def _resolve_draft(lore_root: Path, draft_id: str) -> Path | None:
    """Find the draft file; try with/without .md extension."""
    drafts_dir = lore_root / DRAFTS_DIR_NAME
    candidates = [
        drafts_dir / draft_id,
        drafts_dir / f"{draft_id}.md",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_promote(
    draft_id: str,
    topic: str,
    lore_root: Path,
    dry_run: bool,
) -> int:
    """Promote a draft card to its topic directory. Returns 0 on success."""
    if topic not in VALID_TOPICS:
        print(
            f"ERROR: '{topic}' is not a valid topic. Valid topics: {sorted(VALID_TOPICS)}",
            file=sys.stderr,
        )
        return 1

    draft_path = _resolve_draft(lore_root, draft_id)
    if draft_path is None:
        drafts_dir = lore_root / DRAFTS_DIR_NAME
        print(
            f"ERROR: Draft not found. Looked in: {drafts_dir / draft_id}[.md]",
            file=sys.stderr,
        )
        return 1

    text = draft_path.read_text(encoding="utf-8", errors="replace")
    fm = _parse_frontmatter(text)
    if fm is None:
        print(
            f"ERROR: Could not parse YAML frontmatter in {draft_path}\n"
            "       File must start with --- YAML block ---",
            file=sys.stderr,
        )
        return 1

    errors = _validate_frontmatter(fm, draft_path)
    if errors:
        print(
            f"ERROR: Frontmatter validation failed for {draft_path}:",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    card_topic = str(fm.get("topic", ""))
    if card_topic != topic:
        print(
            f"ERROR: Argument topic '{topic}' does not match card's frontmatter "
            f"topic '{card_topic}'. Update the card's frontmatter or use the correct topic.",
            file=sys.stderr,
        )
        return 1

    dest_dir = lore_root / topic
    dest_filename = draft_path.name if draft_path.name.endswith(".md") else f"{draft_path.name}.md"
    dest_path = dest_dir / dest_filename

    if dest_path.exists():
        print(
            f"ERROR: Destination already exists: {dest_path}",
            file=sys.stderr,
        )
        return 1

    if dry_run:
        print(f"DRY-RUN: would move\n  {draft_path}\n  → {dest_path}")
        if not dest_dir.exists():
            print(f"DRY-RUN: would create directory {dest_dir}")
        return 0

    # Create destination dir if needed
    if not dest_dir.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {dest_dir}")

    shutil.move(str(draft_path), str(dest_path))
    print(f"Promoted: {draft_path.name} → {dest_path}")
    return 0


def cmd_list_drafts(lore_root: Path) -> int:
    """List all draft lore cards."""
    drafts_dir = lore_root / DRAFTS_DIR_NAME
    if not drafts_dir.is_dir():
        print(f"No drafts directory found at {drafts_dir}")
        return 0

    drafts = sorted(drafts_dir.glob("*.md"))
    if not drafts:
        print("No draft lore cards found.")
        return 0

    print(f"Draft lore cards in {drafts_dir}:")
    for d in drafts:
        text = d.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        if fm:
            title = fm.get("title", "(no title)")
            topic = fm.get("topic", "(no topic)")
            status = fm.get("status", "(no status)")
            print(f"  {d.stem:40s}  topic={topic}  status={status}")
            print(f"    {title}")
        else:
            print(f"  {d.stem:40s}  (unparseable frontmatter)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Promote lore draft cards to their topic directories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Subcommands:
  promote <draft_id> <topic>   Move _drafts/<draft_id>.md to <topic>/<draft_id>.md
  list-drafts                  List all files in the _drafts/ directory

Examples:
  python3 scripts/lore_promoter.py promote 20260515-my-card topology
  python3 scripts/lore_promoter.py promote 20260515-my-card topology --dry-run
  python3 scripts/lore_promoter.py list-drafts
""",
    )
    p.add_argument(
        "--lore-root",
        default=str(DEFAULT_LORE_ROOT),
        help="Root lore directory (default: docs/lore/)",
    )

    sub = p.add_subparsers(dest="subcommand", title="subcommands")

    promote_p = sub.add_parser("promote", help="Promote a draft card to a topic directory")
    promote_p.add_argument("draft_id", help="Draft card ID (filename without .md)")
    promote_p.add_argument(
        "topic",
        help=f"Target topic ({', '.join(sorted(VALID_TOPICS))})",
    )
    promote_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making changes",
    )

    sub.add_parser("list-drafts", help="List all draft cards")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    lore_root = Path(args.lore_root)

    if args.subcommand == "promote":
        return cmd_promote(
            draft_id=args.draft_id,
            topic=args.topic,
            lore_root=lore_root,
            dry_run=args.dry_run,
        )
    elif args.subcommand == "list-drafts":
        return cmd_list_drafts(lore_root)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
