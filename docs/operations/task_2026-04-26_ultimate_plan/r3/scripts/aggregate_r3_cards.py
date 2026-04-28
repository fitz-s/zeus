#!/usr/bin/env python3
"""Aggregate R3 phase cards into dependency_graph_r3.mmd + slice_summary_r3.md.

Created: 2026-04-26
Authority: docs/operations/task_2026-04-26_ultimate_plan/r3/R3_README.md

Robust regex-based parser (R3 cards have rich prose). Mirrors the R2
aggregator at ../../scripts/aggregate_slice_cards.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PHASE_COLOR = {
    "Z": "#ffe0b3",  # foundation
    "U": "#a3d9ff",  # snapshot/provenance
    "M": "#ffd9a3",  # execution lifecycle
    "R": "#d9a3ff",  # settlement
    "T": "#a3ffd9",  # parity testing
    "F": "#ffffa3",  # forecast
    "A": "#ffa3a3",  # strategy/risk
    "G": "#d9ffa3",  # gates
}

ID_RE = re.compile(r"^id:\s*(\S+)\s*$")
TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$")
ESTH_RE = re.compile(r"^estimated_h:\s*([0-9.]+)")
RISK_RE = re.compile(r"^risk:\s*(\S+)")
GATE_RE = re.compile(r"^critic_gate:\s*(\S.*)$")
PHASE_RE = re.compile(r"^phase:\s*(\S+)\s*$")
DEPS_HEADER_RE = re.compile(r"^depends_on:(\s*\[(.*?)\])?\s*$")
DEPS_BLOCK_ITEM_RE = re.compile(r"^\s+-\s+([\w.]+)(\s*#.*)?\s*$")


def parse_card(text: str):
    card = {
        "id": None,
        "phase": None,
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
                # Filter to letters-only phase ids (Z0..G1) + R2 cards (up-NN, mid-NN, down-NN)
                if (
                    re.fullmatch(r"[A-Z]\d+", dep_id)
                    or re.fullmatch(r"Z\d+\.\.Z\d+", dep_id)
                    or dep_id.startswith(("up-", "mid-", "down-"))
                ):
                    if ".." in dep_id:
                        # Expand "Z0..Z4" → Z0,Z1,Z2,Z3,Z4
                        m2 = re.fullmatch(r"([A-Z])(\d+)\.\.([A-Z])(\d+)", dep_id)
                        if m2 and m2.group(1) == m2.group(3):
                            for i in range(int(m2.group(2)), int(m2.group(4)) + 1):
                                card["depends_on"].append(f"{m2.group(1)}{i}")
                    else:
                        card["depends_on"].append(dep_id)
                continue
            if line and not line.startswith(" ") and not line.startswith("#"):
                in_deps_block = False
            else:
                continue
        m = ID_RE.match(line)
        if m:
            card["id"] = m.group(1)
            continue
        m = PHASE_RE.match(line)
        if m and card["phase"] is None:
            card["phase"] = m.group(1)
            continue
        m = TITLE_RE.match(line)
        if m and card["title"] is None:
            card["title"] = m.group(1)[:90]
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
            card["critic_gate"] = m.group(1).split()[0]
            continue
        m = DEPS_HEADER_RE.match(line)
        if m:
            inline = m.group(2)
            if inline is not None:
                items = [
                    x.strip()
                    for x in inline.split(",")
                    if re.fullmatch(r"[A-Z]\d+", x.strip())
                    or x.strip().startswith(("up-", "mid-", "down-"))
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


def emit_mermaid(cards) -> str:
    lines = ["flowchart LR"]
    for _, c in cards:
        cid = c["id"]
        risk = c.get("risk") or "?"
        h = c.get("estimated_h")
        h_label = f"{h:g}h" if isinstance(h, (int, float)) else "?h"
        gate = c.get("critic_gate") or "standard"
        label = f"{cid}<br/>{risk}/{h_label}/{gate}"
        phase = c.get("phase") or cid[0]
        color = PHASE_COLOR.get(phase, "#ffffff")
        lines.append(f'    {cid}["{label}"]')
        lines.append(f"    style {cid} fill:{color}")
    seen_ids = {c["id"] for _, c in cards}
    for _, c in cards:
        cid = c["id"]
        for dep in c["depends_on"]:
            if dep in seen_ids:
                lines.append(f"    {dep} --> {cid}")
    return "\n".join(lines) + "\n"


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
            if d not in by_id:
                continue
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


def emit_summary(cards):
    out = ["# R3 Slice card aggregate", ""]
    by_phase: dict = {}
    total_h = 0.0
    for _, c in cards:
        cid = c["id"]
        phase = c.get("phase") or cid[0]
        by_phase.setdefault(phase, {"count": 0, "hours": 0.0})
        by_phase[phase]["count"] += 1
        h = c.get("estimated_h")
        if isinstance(h, (int, float)):
            by_phase[phase]["hours"] += h
            total_h += h
    out.append(f"- total_cards: {len(cards)}")
    out.append(f"- total_hours: {total_h:g}")
    out.append("")
    out.append("| Phase | Cards | Hours |")
    out.append("|---|---|---|")
    for p in sorted(by_phase):
        out.append(f"| {p} | {by_phase[p]['count']} | {by_phase[p]['hours']:g} |")
    out.append("")
    out.append("## Per-card risk + gate + dependencies")
    out.append("")
    out.append("| ID | Phase | Risk | Hours | Gate | Depends on | Title |")
    out.append("|---|---|---|---|---|---|---|")
    for _, c in cards:
        cid = c["id"]
        phase = c.get("phase") or cid[0]
        risk = c.get("risk") or "?"
        h = c.get("estimated_h")
        h_str = f"{h:g}" if isinstance(h, (int, float)) else "?"
        gate = c.get("critic_gate") or "standard"
        deps = ", ".join(c["depends_on"]) if c["depends_on"] else "—"
        title = (c.get("title") or "").replace("|", "/")
        out.append(f"| {cid} | {phase} | {risk} | {h_str} | {gate} | {deps} | {title} |")
    out.append("")
    total_h_path, path = critical_path(cards)
    out.append("## Critical path")
    out.append("")
    out.append(f"- length: {total_h_path:g}h")
    out.append(f"- path: {' → '.join(path)}")
    return "\n".join(out) + "\n"


def main():
    base = Path(__file__).resolve().parent.parent
    cards_dir = base / "slice_cards"
    cards = load_cards(cards_dir)
    if not cards:
        print("no R3 cards found", file=sys.stderr)
        sys.exit(0)
    (base / "dependency_graph_r3.mmd").write_text(emit_mermaid(cards))
    (base / "slice_summary_r3.md").write_text(emit_summary(cards))
    total_h, path = critical_path(cards)
    print(
        f"R3 wrote {len(cards)} cards → dependency_graph_r3.mmd + "
        f"slice_summary_r3.md (critical path {total_h:g}h)"
    )


if __name__ == "__main__":
    main()
