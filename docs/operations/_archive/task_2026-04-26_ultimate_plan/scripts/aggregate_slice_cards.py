#!/usr/bin/env python3
"""Aggregate slice_cards/*.yaml into dependency_graph.mmd + summary stats.

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: docs/operations/task_2026-04-26_ultimate_plan/ judge instructions

Uses line-regex extraction (NOT full yaml parse). Slice cards minted by
debate teammates contain markdown bold + parenthetical prose that breaks
strict yaml parsers. We only need 5 fields for the dep graph.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


REGION_COLOR = {
    "up": "#a3d9ff",
    "mid": "#ffd9a3",
    "down": "#d9ffa3",
}

ID_RE = re.compile(r"^id:\s*(\S+)\s*$")
TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$")
ESTH_RE = re.compile(r"^estimated_h:\s*([0-9.]+)")
RISK_RE = re.compile(r"^risk:\s*(\S+)")
GATE_RE = re.compile(r"^critic_gate:\s*(\S+)")
DEPS_HEADER_RE = re.compile(r"^depends_on:(\s*\[(.*?)\])?\s*$")
DEPS_INLINE_LIST_RE = re.compile(r"\[(.*?)\]")
DEPS_BLOCK_ITEM_RE = re.compile(r"^\s+-\s+([\w-]+)(\s*#.*)?\s*$")


def parse_card(text: str):
    card = {
        "id": None,
        "title": None,
        "estimated_h": None,
        "risk": None,
        "critic_gate": None,
        "depends_on": [],
    }
    in_deps_block = False
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if in_deps_block:
            m = DEPS_BLOCK_ITEM_RE.match(line)
            if m:
                dep_id = m.group(1)
                # Filter out non-card-id deps (comments framed as "X1 (judge ...)" etc.)
                if dep_id.startswith(("up-", "mid-", "down-")):
                    card["depends_on"].append(dep_id)
                continue
            # End of block: any non-list, non-blank line at column 0
            if line and not line.startswith(" ") and not line.startswith("#"):
                in_deps_block = False
            else:
                continue
        m = ID_RE.match(line)
        if m:
            card["id"] = m.group(1)
            continue
        m = TITLE_RE.match(line)
        if m and card["title"] is None:
            card["title"] = m.group(1)[:80]
            continue
        m = ESTH_RE.match(line)
        if m:
            try:
                card["estimated_h"] = float(m.group(1))
            except ValueError:
                pass
            continue
        m = RISK_RE.match(line)
        if m:
            card["risk"] = m.group(1)
            continue
        m = GATE_RE.match(line)
        if m:
            card["critic_gate"] = m.group(1)
            continue
        m = DEPS_HEADER_RE.match(line)
        if m:
            inline = m.group(2)
            if inline is not None:
                # Inline list: [a, b, c]; filter to real card ids only
                items = [
                    x.strip()
                    for x in inline.split(",")
                    if x.strip().startswith(("up-", "mid-", "down-"))
                ]
                card["depends_on"].extend(items)
            else:
                in_deps_block = True
            continue
    return card


def load_cards(cards_dir: Path):
    cards = []
    for path in sorted(cards_dir.glob("*.yaml")):
        text = path.read_text()
        c = parse_card(text)
        if c["id"] is None:
            c["id"] = path.stem
        cards.append((path.name, c))
    return cards


def region_of(card_id: str) -> str:
    if "-" not in card_id:
        return "x"
    return card_id.split("-", 1)[0]


def emit_mermaid(cards) -> str:
    lines = ["flowchart LR"]
    for _, c in cards:
        cid = c["id"]
        risk = c.get("risk") or "?"
        h = c.get("estimated_h")
        h_label = f"{h:g}h" if isinstance(h, (int, float)) else "?h"
        gate = c.get("critic_gate") or "standard"
        label = f"{cid}<br/>{risk}/{h_label}/{gate}"
        region = region_of(cid)
        color = REGION_COLOR.get(region, "#ffffff")
        lines.append(f'    {cid}["{label}"]')
        lines.append(f'    style {cid} fill:{color}')
    for _, c in cards:
        cid = c["id"]
        for dep in c["depends_on"]:
            lines.append(f"    {dep} --> {cid}")
    return "\n".join(lines) + "\n"


def emit_summary(cards):
    by_region: dict = {}
    total_h = 0.0
    for _, c in cards:
        cid = c["id"]
        region = region_of(cid)
        by_region.setdefault(region, {"count": 0, "hours": 0.0})
        by_region[region]["count"] += 1
        h = c.get("estimated_h")
        if isinstance(h, (int, float)):
            by_region[region]["hours"] += h
            total_h += h
    out = ["# Slice card aggregate", ""]
    out.append(f"- total_cards: {len(cards)}")
    out.append(f"- total_hours: {total_h:g}")
    out.append("")
    out.append("| Region | Cards | Hours |")
    out.append("|---|---|---|")
    for r in sorted(by_region):
        out.append(
            f"| {r} | {by_region[r]['count']} | {by_region[r]['hours']:g} |"
        )
    out.append("")
    out.append("## Per-card risk + gate + dependencies")
    out.append("")
    out.append("| ID | Risk | Hours | Gate | Depends on | Title |")
    out.append("|---|---|---|---|---|---|")
    for _, c in cards:
        cid = c["id"]
        risk = c.get("risk") or "?"
        h = c.get("estimated_h")
        h_str = f"{h:g}" if isinstance(h, (int, float)) else "?"
        gate = c.get("critic_gate") or "standard"
        deps = ", ".join(c["depends_on"]) if c["depends_on"] else "—"
        title = (c.get("title") or "").replace("|", "/")
        out.append(f"| {cid} | {risk} | {h_str} | {gate} | {deps} | {title} |")
    return "\n".join(out) + "\n"


def critical_path(cards):
    by_id = {c["id"]: c for _, c in cards}
    memo: dict = {}

    def longest(cid: str):
        if cid in memo:
            return memo[cid]
        c = by_id.get(cid)
        if c is None:
            return (0.0, [cid])
        h = c.get("estimated_h") or 0.0
        if not c["depends_on"]:
            memo[cid] = (h, [cid])
            return memo[cid]
        best_dep_path: list = []
        best_dep_h = 0.0
        for d in c["depends_on"]:
            dh, dpath = longest(d)
            if dh > best_dep_h:
                best_dep_h = dh
                best_dep_path = dpath
        memo[cid] = (h + best_dep_h, best_dep_path + [cid])
        return memo[cid]

    best_total = 0.0
    best_path: list = []
    for _, c in cards:
        h, path = longest(c["id"])
        if h > best_total:
            best_total = h
            best_path = path
    return best_total, best_path


def main():
    base = Path(__file__).resolve().parent.parent
    cards_dir = base / "slice_cards"
    cards = load_cards(cards_dir)
    if not cards:
        print("no slice cards found", file=sys.stderr)
        sys.exit(0)
    (base / "dependency_graph.mmd").write_text(emit_mermaid(cards))

    summary = emit_summary(cards)
    total_h, path = critical_path(cards)
    summary += "\n## Critical path\n\n"
    summary += f"- length: {total_h:g}h\n"
    summary += f"- path: {' → '.join(path)}\n"
    (base / "slice_summary.md").write_text(summary)
    print(
        f"wrote {len(cards)} cards → dependency_graph.mmd + slice_summary.md "
        f"(critical path {total_h:g}h)"
    )


if __name__ == "__main__":
    main()
