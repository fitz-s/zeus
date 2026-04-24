# Zeus Autonomy Gates

Status: historical report evidence; superseded by `docs/authority/zeus_current_delivery.md`
Supersedes: former `zeus_autonomy_gates.md` + `team_policy.md` (merged 2026-04-10)
Referenced by: `AGENTS.md` §7 (working discipline)

---

## 1. Destructive operations are always human-gated

The following actions require explicit human approval regardless of autonomy level:

- Live cutover timing decisions
- Data/archive/delete transitions
- Irreversible migration or cutover switches
- Authority-surface deletion or demotion that changes the active law stack

**Why**: A 2026-04-07 session lost multiple edits across 50+ files due to zero commits over 12+ hours of unbounded autonomous work. Destructive operations amplify the damage from state loss.

---

## 2. Team mode entry conditions

You may enter team mode (`$team`, `omx team`, `/team`, `omc team`) only when:
- There is an approved work packet
- Work is parallelizable
- One owner remains accountable
- Team members are not being asked to redefine authority

### Do NOT teamize

- `architecture/**`
- `docs/authority/**`
- Migration cutover decisions
- `.claude/CLAUDE.md` compatibility policy
- Supervisor/control-plane semantics
- Packet-less exploratory rewrites

### Use advisory lanes instead

For read-only consultation without team mode:
- `omx ask ...` / `omc ask ...`
- `/ccg`
- Read-only critique/review

---

## 3. One frozen packet at a time

Autonomous multi-packet team execution is not allowed. Team mode operates on exactly one frozen packet at a time. Before team launch, the following must be frozen:
- Owner
- File boundary
- Acceptance gate
- Blocker policy

---

## 4. Serialization rule

When multiple agents edit files in parallel, serialize writes to the same file — one agent per file, or coordinate via SendMessage.

---

## Related documents

- `docs/authority/zeus_packet_discipline.md` — Packet discipline and closure rules
- `docs/authority/zeus_current_delivery.md` — Current delivery entrypoint and authority order
- `AGENTS.md` §7 — Working discipline (summary + cross-reference)
