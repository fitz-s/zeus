# Confusion Directory

When an agent hits any of CC-1..CC-12 from `../CONFUSION_CHECKPOINTS.md`,
they write a confusion file here. Operator reviews periodically.

## File-naming convention

```
<phase_id>_<short_topic>.md
```

Or for cross-phase:

```
_cross_phase_<short_topic>.md
```

Or for packet-integrity issues:

```
_packet_integrity_<author>_<date>.md
```

## Format (loose; whatever helps)

```markdown
# <Title>

Author: <agent-tag>
Phase: <phase_id>  (or "cross-phase" or "packet-integrity")
Trigger: <CC-N from CONFUSION_CHECKPOINTS.md>
Date: <YYYY-MM-DD>
Status: OPEN | RESOLVED | ESCALATED

## What I'm confused about
<2-4 sentences>

## What I read / tried before stopping
<links, grep output, web search results>

## Two (or more) interpretations
1. ...
2. ...

## My recommendation
<which interpretation + why>

## What I'd want operator to confirm
<specific question>
```

## Status flow

- `OPEN` — agent paused, waiting for operator review
- `RESOLVED` — operator clarified; agent proceeded; file kept for audit
- `ESCALATED` — operator decided this is a planning-lock event;
  see `../_protocol_evolution/` for the amendment

## See also

- `../CONFUSION_CHECKPOINTS.md` — when to write here
- `../SELF_LEARNING_PROTOCOL.md` — how confusions promote to learnings
- `../IMPLEMENTATION_PROTOCOL.md` — parent protocol

(empty — populated as phases ship)
