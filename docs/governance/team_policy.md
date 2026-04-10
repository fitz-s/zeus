# Team Usage Policy

> Extracted from original root AGENTS.md §7. Read only when entering team mode.

## When team mode is allowed

You may enter `$team`, `omx team`, `/team`, or `omc team` only when:
- There is an approved work packet
- Work is parallelizable
- One owner remains accountable
- Team members are not being asked to redefine authority

## Do NOT teamize

- `architecture/**`
- `docs/governance/**`
- Migration cutover decisions
- `.claude/CLAUDE.md` compatibility policy
- Supervisor/control-plane semantics
- Packet-less exploratory rewrites

## Use advisory lanes instead

For read-only consultation without team mode:
- `omx ask ...`
- `omc ask ...`
- `/ccg`
- Read-only critique/review

## Autonomy gates

- Before `P0.5` is complete: no broad autonomous multi-packet team execution
- `P0.5` does not self-authorize team autonomy while it is the active packet
- After `P0.5` is complete + `FOUNDATION-TEAM-GATE` packet is frozen and accepted:
  - Later phases may use autonomous packet-by-packet team execution
  - Still only one frozen packet at a time
  - Owner, file boundary, acceptance gate, and blocker policy must be frozen before team launch
- Even after `P0.5`:
  - Final destructive/cutover work remains human-gated
  - "Destructive" includes: live cutover timing, data/archive/delete transitions, irreversible migration switches, authority-surface deletion

## Serialization rule

When multiple agents edit files in parallel, serialize writes to the same file (one agent per file, or coordinate via SendMessage).
