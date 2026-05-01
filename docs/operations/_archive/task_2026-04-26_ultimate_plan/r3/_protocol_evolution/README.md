# Protocol Evolution Directory

Proposals to amend `IMPLEMENTATION_PROTOCOL.md`, `CONFUSION_CHECKPOINTS.md`,
`SKILLS_MATRIX.md`, or `templates/phase_prompt_template.md`.

The protocol is a HYPOTHESIS at first. Implementation reveals where it's
wrong. This directory captures the corrections.

## File-naming convention

```
<short_topic>.md
```

## Format

See `../SELF_LEARNING_PROTOCOL.md` §2 Bucket C for the full template.

Quick form:

```markdown
# <Title>

Author: <agent-tag>
Phase context: <phase_id when discovered>
Date: <YYYY-MM-DD>
Status: PROPOSED | ACCEPTED | DECLINED | INCORPORATED

## Current protocol says
<quote>

## Reality showed
<what happened>

## Proposed amendment
<exact text>

## Risk if NOT amended
<predictable next-agent failure>
```

## Status flow

- `PROPOSED` — agent submitted; operator review pending
- `ACCEPTED` — operator agreed; awaiting amendment commit
- `DECLINED` — operator declined; rationale documented in this file
- `INCORPORATED` — amendment committed; link to commit in this file

## Naming examples

- `cc-13_partial_r2_already_shipped.md` (proposes new confusion checkpoint)
- `skills-matrix-architect-must-be-readonly.md` (clarifies skill usage)
- `prompt-template-add-rpc-eth-call-default.md` (template improvement)
- `boot-protocol-step-13-add-cross-phase-grep.md` (boot step addition)

## See also

- `../IMPLEMENTATION_PROTOCOL.md` — the document being evolved
- `../SELF_LEARNING_PROTOCOL.md` §2 — when to write here
- `../CONFUSION_CHECKPOINTS.md` — list of currently-defined checkpoints

(empty — populated as phases ship)
