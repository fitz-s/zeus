# Lore Card Extraction Protocol

Status: SPEC
Purpose: Convert closed-packet evidence into durable, indexable "lore cards"
that survive packet archival and remain agent-discoverable across sessions.

## Why Lore Extraction

Most packet content is procedural: plan, execution log, critic transcript,
verification commands. That content is fine to archive — future agents will
not re-read a 4-month-old plan to learn what to do.

But every closed packet carries 1–5 NUGGETS that future agents need:
- A new constraint discovered (`SELECT * across DBs is INV-37 violation`)
- A failure mode named and reproduced (`LEXICAL_PROFILE_MISS`)
- A design rationale that justifies a non-obvious code shape
- A regression that explains why an apparently-redundant guard exists
- A vendor/data-source quirk worth permanent reference

These nuggets must NOT die with the packet. The lore extraction protocol
pulls them into a separate, indexed, agent-discoverable knowledge surface.

## Lore Card Schema

Every lore card is a single markdown file under `docs/lore/<topic>/<slug>.md`
(topic ∈ {topology, hooks, runtime, data, calibration, execution, settlement,
vendor, browser, identity, packet}).

```markdown
---
id: <YYYYMMDD-slug>
title: <one line, ≤80 chars>
topic: <one of the listed topics>
extracted_from: docs/operations/task_<date>_<slug>/<file>:<line-range>
extracted_on: <ISO date>
status: ACTIVE | SUPERSEDED_BY <id> | RETIRED
authority_class: HARD_RULE | DESIGN_RATIONALE | VENDOR_FACT | INCIDENT_LESSON
last_verified: <ISO date>
verification_command: <optional: shell command that proves the lore still holds>
related: [<id>, <id>, ...]
---

# <title>

## What
<one paragraph: the constraint or fact, in present tense>

## Why
<one paragraph: the cause or motivation; cite primary evidence>

## How To Apply
<one paragraph: when does this rule fire; what does the agent do>

## Anti-Pattern
<optional: one paragraph: what NOT to do, what failure happens if violated>

## Provenance
- Originating packet: <path>
- Originating commit: <SHA, optional>
- Operator confirmation: <YES/NO/IMPLICIT>
```

The frontmatter is the index; the body is the explanation. An indexer
script (NOT in this packet — see `05_execution_packets/PACKET_INDEX.md`)
walks `docs/lore/**` and produces a topic-keyed lookup the agent can query.

## Extraction Triggers

A lore card SHOULD be extracted when ANY of:

1. **Packet closure with named lesson**: PLAN.md or POSTMORTEM.md contains
   a section titled `Lessons:`, `What we learned:`, `New invariant:`, or
   similar. Each bullet becomes a candidate card.
2. **Memory feedback write**: an OMC `feedback_*` memory file has been
   written based on packet content. The memory line plus the packet evidence
   is a card.
3. **Critic identifies recurring pattern**: critic transcript names a
   pattern that has fired in 2+ packets (cross-reference required).
4. **Authority-doc-update missed in PR**: a code change merged but the
   sibling authority doc was not updated (drift event); the gap itself is
   lore worth recording.
5. **External vendor quirk**: any packet that ends with "vendor X does Y
   unexpectedly" produces one card under `topic: vendor`.

The agent runs the extraction proposal in DRY_RUN. The human approves which
candidates become cards. There is no auto-extraction without approval, ever.

## Extraction Workflow (Daily Tick, DRY_RUN)

For each packet flipping from ACTIVE → ARCHIVE_CANDIDATE on a maintenance
run:

1. Scan packet files for trigger patterns above.
2. Emit a proposal manifest at
   `02_daily_maintenance_agent/evidence_trail/<date>/lore_proposals/<packet-name>.md`:
   ```
   ## Lore Card Proposals From <packet-name>
   ### Proposal 1
   - candidate_topic: <topic>
   - candidate_title: <inferred from heading>
   - source_excerpt: <50–200 chars verbatim>
   - source_file: <path:lines>
   - trigger: <which trigger fired>
   ```
3. The proposal sits for `LORE_REVIEW_TTL_DAYS` (default 7) awaiting human
   approval. Approval = the human moves the proposal to a draft card under
   `docs/lore/_drafts/`. The agent then promotes drafts to live cards under
   the proper topic dir on the next tick.

## Card Lifecycle

- `ACTIVE`: card is current law; queryable by agents via the lore index.
- `SUPERSEDED_BY <id>`: a newer card subsumes this one. Card body remains
  for history; the agent surfaces only the successor.
- `RETIRED`: the constraint no longer holds. Card is moved under
  `docs/lore/retired/` with a `retired_reason:` field. Retired cards are
  NEVER deleted — agents reading old code may still need to understand why
  the rule existed.

The agent never sets `RETIRED` autonomously. Retirement requires human
action because it changes what current law is.

## Re-Verification

Each card with a `verification_command` field is re-verified on a
configurable cadence (`LORE_VERIFY_TTL_DAYS`, default 30). Re-verification
runs the command in a sandbox and compares output to a stored signature.
On signature mismatch the card is flagged `NEEDS_RE_VERIFICATION` and
surfaced to the human; the agent does not auto-retire on mismatch (the code
might have evolved correctly and the command needs updating).

## Authority

A lore card is NOT itself authority. It is a pointer + summary. The
authority chain is:
```
code / state / live system  >  authority docs (architecture/, docs/reference/)
                            >  active packet evidence
                            >  lore card (summary)
```

A lore card that contradicts current code or current authority docs is
either stale (flag `NEEDS_RE_VERIFICATION`) or the docs themselves are stale
(escalate to authority-drift remediation track).

## Lore Topics And Counts (Initial Targets)

For the first extraction sweep covering existing closed packets older than
30 days:

- `topology/`: ≥ 5 cards (one per redesign iteration named in
  `00_evidence/HIDDEN_BRANCH_INVENTORY.md`)
- `hooks/`: ≥ 2 cards (per hook-redesign packet)
- `runtime/`: ≥ 3 cards (worktree merge, dirty execution refusal, dispatch
  brief discipline)
- `data/`: as many as memory `feedback_*` files cite
- `vendor/`: per known external vendor incident

Total first sweep target: 15–25 cards. If the sweep produces more than 50
candidate proposals, the trigger heuristics are too loose and require
tightening before live extraction.

## Out Of Scope For This Protocol

- Auto-merging lore from multiple packets into a single super-card.
  Humans do this; agents do not (cross-packet synthesis is exactly the
  hidden-branch synthesis sonnet work, not daily maintenance).
- Translating lore between languages or formatting for end-user docs.
- Any UI; lore is a markdown directory, period.
