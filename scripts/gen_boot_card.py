#!/usr/bin/env python3
"""Boot-card generator -- representation contract §2 (generated metadata surface).
# repr-surface: class=generated writer=scripts/gen_boot_card.py drift_detector=--check
Assembles build/repr/boot_card.md deterministically from checked_policy_input sources
(architecture/canonical_vocabulary.yaml, architecture/invariants.yaml, AGENTS.md Boot
Digest) plus a small hand_kernel routing/stop-rule block owned by this script. No live
DB reads, no dates, no run-to-run nondeterminism -- output is a pure function of repo
state, so `--check` (regen + diff) is the drift detector for this surface.

Usage:
  python3 scripts/gen_boot_card.py            # write build/repr/boot_card.md
  python3 scripts/gen_boot_card.py --check    # regenerate to memory, diff vs disk, exit 1 on drift
  python3 scripts/gen_boot_card.py --stdout   # print card + token count, do not write
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from _yaml_bootstrap import import_yaml
except ModuleNotFoundError:
    from scripts._yaml_bootstrap import import_yaml

yaml = import_yaml()

ROOT = _REPO_ROOT
AGENTS_PATH = ROOT / "AGENTS.md"
INVARIANTS_PATH = ROOT / "architecture" / "invariants.yaml"
CANONICAL_VOCABULARY_PATH = ROOT / "architecture" / "canonical_vocabulary.yaml"
CONTRACT_PATH = ROOT / "docs" / "rebuild" / "representation_contract_2026-07-08.md"
OUTPUT_PATH = ROOT / "build" / "repr" / "boot_card.md"

# contract §2: root <=2.5K/scoped <=500 tokens ~= 4 chars/token estimate; same constant
# used by scripts/topology_doctor_repr_checks.py check_agents_token_budgets for
# comparability between the two token-budget reports.
CHARS_PER_TOKEN_ESTIMATE = 4
TOKEN_BUDGET = 5000

# Digest one-liners pulled verbatim from AGENTS.md Boot Digest by section name --
# this list IS the selection policy (edit here, not by re-deriving upstream text).
AXIOM_SECTIONS = ("Mission", "Time law", "DBs")

ROUTING_BLOCK = """## Query routing
structure/callers/blast-radius -> codegraph (code-review-graph MCP), not grep-first.
contract/schema of a surface -> the owning architecture/*.yaml (see vocab+invariant index below).
law/invariant -> INV-xx id, grep `architecture/invariants.yaml`, never restate from memory.
current runtime/data state -> runtime DB or `git log`/`git show`, NEVER inferred from docs prose.
unknown concept -> canonical vocabulary below first; only fall back to free-text search after.
"""

EVENT_CHAIN = [
    ("ForecastSnapshot", "atmospheric data -> frozen forecast object"),
    ("ServedBelief", "belief pre-serving-transform"),
    ("CandidateSet", "belief + market -> candidate opportunities"),
    ("DecisionReceipt", "admission/gating outcome, immutable"),
    ("SolveCertificate", "solver/repair proof object"),
    ("ExecutionIntent", "certificate -> intended venue action"),
    ("VenueCommand", "intent -> outbound venue instruction"),
    ("OrderFact", "venue-confirmed order/fill, ground truth"),
    ("PositionEvent", "order fact -> position state transition"),
    ("SettlementGrade", "terminal settled value, sole settlement authority"),
]

COMMAND_BLOCK = """## Query commands
rg '<symbol|error text>'                                    # name/error -> exact hit
python3 -m scripts.topology_doctor --repr                   # representation-contract advisory findings
python3 -m scripts.topology_doctor --full                   # governance/registry checks
grep -n 'INV-<nn>' architecture/invariants.yaml              # invariant statement + enforced_by
grep -n '<concept>' architecture/canonical_vocabulary.yaml   # canonical name + forbidden aliases
git log --oneline -- <path>                                  # provenance, not doc prose
"""

STOP_RULES = """## Stop rules
missing authority citation for a claim -> stop, do not assert.
data/state whose freshness cannot be proven -> stop, treat as stale (never stale-as-fresh).
vocabulary collision (two canonical terms claim one word) -> stop, resolve via canonical_vocabulary.yaml before writing.
side effect (write, order, DB mutation) with no registered owner -> stop, this is unregistered authority.
"""


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _axiom_kernel() -> str:
    text = AGENTS_PATH.read_text(encoding="utf-8")
    digest = text.split("## Boot Digest", 1)[1].split("## 0. Mission", 1)[0]
    lines = [line.strip() for line in digest.splitlines() if line.strip().startswith("**")]
    picked = []
    for line in lines:
        section = re.match(r"\*\*([A-Za-z &]+?)\s*\[", line)
        if section and section.group(1).strip() in AXIOM_SECTIONS:
            picked.append(line)
    body = "\n".join(picked)
    return (
        "## Axiom kernel (verbatim subset of AGENTS.md Boot Digest -- narrower surface wins;\n"
        "## this card licenses orientation only, not a substitute for AGENTS.md/scoped AGENTS.md)\n"
        f"{body}\n"
        f"Full representation contract: {CONTRACT_PATH.relative_to(ROOT)}\n"
    )


def _vocabulary_block() -> str:
    vocab = _load_yaml(CANONICAL_VOCABULARY_PATH)
    lines = ["## Canonical ontology (architecture/canonical_vocabulary.yaml)"]
    for term in vocab.get("terms") or []:
        canonical = term.get("canonical")
        aliases = ",".join(term.get("forbidden_aliases") or []) or "-"
        lines.append(f"{canonical} [not: {aliases}]")
    return "\n".join(lines) + "\n"


def _invariant_index() -> str:
    inv = _load_yaml(INVARIANTS_PATH)
    lines = ["## Invariant index (architecture/invariants.yaml; query: grep '<id>:' the file)"]
    for item in inv.get("invariants") or []:
        lines.append(f"{item.get('id')}: {item.get('statement')}")
    return "\n".join(lines) + "\n"


def _event_chain_block() -> str:
    lines = ["## Intent-flow skeleton (money path, typed-event chain)"]
    lines.append(" -> ".join(name for name, _ in EVENT_CHAIN))
    for name, gloss in EVENT_CHAIN:
        lines.append(f"{name}: {gloss}")
    return "\n".join(lines) + "\n"


def build_card() -> str:
    parts = [
        "# Zeus boot card (generated -- do not hand-edit; regenerate via scripts/gen_boot_card.py)",
        _axiom_kernel(),
        ROUTING_BLOCK,
        _vocabulary_block(),
        _invariant_index(),
        _event_chain_block(),
        COMMAND_BLOCK,
        STOP_RULES,
    ]
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="regen+diff, exit 1 on drift")
    parser.add_argument("--stdout", action="store_true", help="print card, do not write")
    args = parser.parse_args()

    card = build_card()
    tokens = _estimate_tokens(card)

    if args.stdout:
        print(card)
        print(f"# tokens (~{CHARS_PER_TOKEN_ESTIMATE} chars/token estimate): {tokens}", file=sys.stderr)
        return 0

    if args.check:
        on_disk = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else None
        if on_disk != card:
            print(f"DRIFT: {OUTPUT_PATH} is stale vs regenerated card (~{tokens} tokens)", file=sys.stderr)
            return 1
        print(f"OK: {OUTPUT_PATH} matches regenerated card (~{tokens} tokens)", file=sys.stderr)
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(card, encoding="utf-8")
    over = tokens > TOKEN_BUDGET
    print(
        f"wrote {OUTPUT_PATH} (~{tokens} tokens, budget {TOKEN_BUDGET}"
        f"{', OVER BUDGET' if over else ''})",
        file=sys.stderr,
    )
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
