# Zeus Autonomy Gates

Status: Active governance law
Source: Extracted from root `AGENTS.md` §2 and §7 (committed version, 2026-04-09)
Referenced by: `AGENTS.md` §7 (working discipline)

---

## 1. Post-P0.5 autonomy rule

### Before P0.5 is complete and accepted

- **No** broad autonomous multi-packet team execution.
- **No** "open team from momentum."
- P0.5 does not self-authorize team autonomy while it is still the active packet being implemented.

### After P0.5 is complete, accepted, AND pushed

AND after a later `FOUNDATION-TEAM-GATE` packet is frozen and accepted:
- Later phases may use autonomous **packet-by-packet** team execution.
- Still only one frozen packet at a time.
- Owner, file boundary, acceptance gate, and blocker policy must still be frozen before team launch.

### Even after P0.5 — permanent restrictions

- Final destructive/cutover work remains **human-gated**.
- P7 is **never fully autonomous** for final cutover/delete transitions.
- "Destructive" includes, at minimum:
  - live cutover timing decisions
  - data/archive/delete transitions
  - irreversible migration/cutover switches
  - authority-surface deletion/demotion that changes the active law stack

---

## 2. Team mode entry conditions

You may enter `$team`, `omx team`, `/team`, or `omc team` only when:
- there is an approved packet
- work is parallelizable
- one owner remains accountable
- team members are not being asked to redefine authority

### Do not teamize

- `architecture/**`
- `docs/governance/**`
- migration cutover decisions
- `.claude/CLAUDE.md` compatibility policy
- supervisor/control-plane semantics
- packet-less exploratory rewrites

### Use advisory lanes instead

- `omx ask ...`
- `omc ask ...`
- `/ccg`
- read-only critique/review

### Phase gate

- Before P0.5 is complete, do **not** use team mode as a broad execution default for the foundation mainline.
- After P0.5, team mode becomes allowed for later phases only on one frozen packet at a time and only after `FOUNDATION-TEAM-GATE` is accepted.

---

## 3. Historical lesson

> A 2026-04-07 session lost multiple edits across 50+ files due to zero commits over 12+ hours of work.

This rule exists to prevent unbounded autonomous sessions from creating unrecoverable state loss.

---

## Related documents

- `docs/governance/zeus_packet_discipline.md` — Packet discipline and closure rules
- `docs/governance/team_policy.md` — Team mode usage rules (detailed)
- `AGENTS.md` §7 — Working discipline (summary + cross-reference)
- `docs/governance/zeus_autonomous_delivery_constitution.md` — Full delivery constitution
