#!/usr/bin/env python3
# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/LORE_EXTRACTION_PROTOCOL.md
"""lore_indexer — walks docs/lore/**/*.md and builds a topic-keyed INDEX.json.

Usage:
    python3 scripts/lore_indexer.py [--output PATH] [--validate-only] [--lore-root PATH]

Outputs:
    docs/lore/INDEX.json  — topic-keyed mapping: {topic: [{id, title, status, ...}, ...]}

Validation rules (per LORE_EXTRACTION_PROTOCOL schema):
    Required frontmatter fields: id, title, topic, extracted_from, extracted_on,
                                  status, authority_class, last_verified
    Valid topics: topology, hooks, runtime, data, calibration, execution, settlement,
                  vendor, browser, identity, packet
    Card topic must match its containing directory name.
    _drafts/ and retired/ directories are excluded from the live index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
DEFAULT_OUTPUT = DEFAULT_LORE_ROOT / "INDEX.json"

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

EXCLUDED_DIRS = frozenset({"_drafts", "retired"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class LoreCard:
    """Parsed, validated lore card frontmatter."""

    id: str
    title: str
    topic: str
    extracted_from: str
    extracted_on: str
    status: str
    authority_class: str
    last_verified: str
    verification_command: str = ""
    related: list[str] = field(default_factory=list)
    source_path: str = ""

    def to_index_entry(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("source_path", None)
        return d


@dataclass
class ValidationError:
    path: str
    message: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_yaml_frontmatter(text: str, path: Path) -> tuple[dict[str, Any] | None, str]:
    """Return (frontmatter_dict, body) or (None, full_text) on parse failure."""
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    raw_fm = parts[1].strip()
    body = parts[2]
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required. Install it with: pip install PyYAML"
        )
    try:
        fm = yaml.safe_load(raw_fm)
    except Exception as exc:  # noqa: BLE001
        return None, text
    if not isinstance(fm, dict):
        return None, text
    return fm, body


def _validate_card(
    fm: dict[str, Any], path: Path, containing_dir: str
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    rel = str(path)

    # Required fields
    for field_name in REQUIRED_FIELDS:
        if not fm.get(field_name):
            errors.append(
                ValidationError(rel, f"missing required frontmatter field: '{field_name}'")
            )

    # Topic must be in valid set
    topic = fm.get("topic", "")
    if topic and topic not in VALID_TOPICS:
        errors.append(
            ValidationError(rel, f"invalid topic '{topic}'; must be one of {sorted(VALID_TOPICS)}")
        )

    # Topic must match containing directory
    if topic and containing_dir not in EXCLUDED_DIRS and topic != containing_dir:
        errors.append(
            ValidationError(
                rel,
                f"topic '{topic}' does not match containing directory '{containing_dir}'",
            )
        )

    return errors


def _card_from_fm(fm: dict[str, Any], path: Path) -> LoreCard:
    related_raw = fm.get("related", [])
    if isinstance(related_raw, str):
        related_raw = [r.strip() for r in related_raw.strip("[]").split(",") if r.strip()]
    related = [str(r) for r in (related_raw or [])]
    return LoreCard(
        id=str(fm.get("id", "")),
        title=str(fm.get("title", "")),
        topic=str(fm.get("topic", "")),
        extracted_from=str(fm.get("extracted_from", "")),
        extracted_on=str(fm.get("extracted_on", "")),
        status=str(fm.get("status", "")),
        authority_class=str(fm.get("authority_class", "")),
        last_verified=str(fm.get("last_verified", "")),
        verification_command=str(fm.get("verification_command", "")),
        related=related,
        source_path=str(path),
    )


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------


def walk_lore(
    lore_root: Path,
) -> tuple[list[LoreCard], list[ValidationError]]:
    """Walk lore_root/**/*.md; skip _drafts/ and retired/. Return cards + errors."""
    cards: list[LoreCard] = []
    errors: list[ValidationError] = []

    if not lore_root.is_dir():
        errors.append(ValidationError(str(lore_root), "lore root directory does not exist"))
        return cards, errors

    for md_path in sorted(lore_root.rglob("*.md")):
        # Determine relative parts
        try:
            rel = md_path.relative_to(lore_root)
        except ValueError:
            continue

        # Skip excluded dirs (first part of relative path)
        parts = rel.parts
        if not parts:
            continue
        containing_dir = parts[0] if len(parts) > 1 else ""
        if containing_dir in EXCLUDED_DIRS:
            continue
        # Also skip root-level .md files (INDEX.md, POLICY.md, etc.)
        if len(parts) == 1:
            continue

        text = md_path.read_text(encoding="utf-8", errors="replace")
        fm, _ = _parse_yaml_frontmatter(text, md_path)

        if fm is None:
            errors.append(
                ValidationError(
                    str(md_path),
                    "could not parse YAML frontmatter (file must start with ---)",
                )
            )
            continue

        card_errors = _validate_card(fm, md_path, containing_dir)
        errors.extend(card_errors)

        # Only include card in index if no validation errors
        if not card_errors:
            cards.append(_card_from_fm(fm, md_path))

    return cards, errors


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------


def build_index(cards: list[LoreCard]) -> dict[str, Any]:
    """Build topic-keyed index dict from validated cards."""
    index: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "scripts/lore_indexer.py",
        "topics": {},
    }
    for card in cards:
        topic = card.topic
        if topic not in index["topics"]:
            index["topics"][topic] = []
        index["topics"][topic].append(card.to_index_entry())
    # Sort entries within each topic by id for determinism
    for topic in index["topics"]:
        index["topics"][topic].sort(key=lambda e: e["id"])
    return index


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build docs/lore/INDEX.json from lore card frontmatter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/lore_indexer.py
  python3 scripts/lore_indexer.py --validate-only
  python3 scripts/lore_indexer.py --output /tmp/lore_index.json
""",
    )
    p.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output path for INDEX.json (default: docs/lore/INDEX.json)",
    )
    p.add_argument(
        "--lore-root",
        default=str(DEFAULT_LORE_ROOT),
        help="Root directory to walk for lore cards (default: docs/lore/)",
    )
    p.add_argument(
        "--validate-only",
        action="store_true",
        help="Parse and validate cards but do not write INDEX.json",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any validation errors are found (default: warn only)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    lore_root = Path(args.lore_root)
    output_path = Path(args.output)

    cards, errors = walk_lore(lore_root)

    # Report errors
    if errors:
        for err in errors:
            print(f"WARN  {err.path}: {err.message}", file=sys.stderr)

    # Summary
    print(f"Scanned {lore_root}: {len(cards)} valid cards, {len(errors)} validation issue(s)")

    if args.validate_only:
        if errors and args.strict:
            return 1
        return 0

    # Build and write index
    index = build_index(cards)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(index, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output_path} ({len(cards)} cards across {len(index['topics'])} topic(s))")

    if errors and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
