# Zeus Packet Discipline

Status: Active governance law
Source: Extracted from root `AGENTS.md` §2 (committed version, 2026-04-09)
Referenced by: `AGENTS.md` §7 (working discipline)

---

## 1. Program / packet / slice hierarchy

- A **program phase** is larger than a packet. Examples: `FOUNDATION-MAINLINE`, `P0`, `P1`.
- A **packet** is the atomic authority-bearing unit of execution. Examples: `P0.2 attribution freeze`, `P0.1 exit semantics split`.
- An **execution slice** is a commit-sized step inside one still-open packet.

**Do not confuse** "one slice completed" with "packet completed."

### Autonomous continuation rule

If the active packet remains open, the next slice is clear, and no new authority/risk boundary is crossed, **continue autonomously** after commit/push instead of stopping for a human "continue."

**Stop only when:**
- the packet is actually complete,
- the next slice would widen scope,
- the next slice would change phase/packet,
- the next slice would cross into a higher-risk zone,
- or a real blocker / contradiction appears.

---

## 2. Closure / reopen rules

Packet acceptance and phase closure are always **defeasible by later repo-truth contradiction**.

If repo truth later disproves a prior acceptance or closure claim, control surfaces must **reopen explicitly** rather than patch quietly around the contradiction.

### Three-level distinction (do not collapse)

Distinguish clearly between:
1. **Packet family completed** — all packets in a family have evidence.
2. **Current targeted evidence passed** — the specific test/gate for this packet passed.
3. **Bottom-layer semantic convergence actually achieved** — the runtime code actually behaves as claimed.

Do not collapse those into one "done" claim unless the evidence truly covers all three.

---

## 3. Pre-closeout review rules

Before any packet-family or phase-closeout claim, run the broadest review that is still packet-bounded:

1. Targeted tests/gates for the packet.
2. Broader affected-file checks when closure is being claimed.
3. Explicit adversarial review.
4. At least one additional independent read-only review lane on the relevant bottom-layer surfaces for high-sensitivity runtime/governance work.

### Process failure signal

The point of critic/reviewer lanes before closeout is to surface blocker-level issues **before a human user does**.

If a human user can still trivially find additional blocker-level issues after an acceptance/closure claim, treat that as a **process failure signal**, not as normal "extra critic scope."

In that situation:
- Reopen the claim.
- Freeze an explicit repair or superseding packet.
- Update control surfaces to match repo truth.
- Only re-close after fresh evidence and review.

### Single repair packet preference

If multiple confirmed defects sit on the same bottom-layer truth boundary and the human explicitly directs one repair package, prefer one tightly bounded repair packet over artificial packet fragmentation.

---

## 4. Post-closeout gate rules

Packet closeout does **not** automatically authorize the next packet freeze.

**Before** marking a packet accepted or pushed:
- Finish the packet's pre-close critic/verifier review.

**After** a packet is marked accepted/pushed:
- Run one additional independent third-party critic review.
- Run one additional verifier pass on the accepted boundary.

If that post-close review finds a contradiction, stale control-surface snapshot, or evidence gap:
- Reopen or repair explicitly before advancing.
- Synchronize control surfaces (`architects_state_index.md`, `architects_task.md`, and the top-level `architects_progress.md` snapshot) to repo truth.
- Rerun the post-close gate until it passes.

Treat a passed post-close gate as a **separate advancement permission**, not as a byproduct of acceptance.

---

## 5. Evidence visibility rule

Every item listed in `evidence_required` must appear explicitly either:
- in the packet file itself, or
- in a clearly named paired ledger/evidence surface referenced by the packet.

**Do not** treat chat memory, reviewer intuition, or implied knowledge as sufficient evidence for packet closeout.

---

## 6. Capability-present / capability-absent proof rule

When a packet introduces behavior that depends on a capability or substrate being present (for example a table, contract, service, or bootstrap state), acceptance must explicitly cover:
- the **capability-present** behavior, and
- the **capability-absent** behavior.

If the absent-path behavior is advisory skip, fail-loud, or staged no-op, say so plainly and test or evidence it directly instead of silently overclaiming the present-path result.

---

## 7. Waiver rule (strict conditions)

A waived gate is acceptable **only when**:
- the gate is explicitly staged/advisory by current law, or
- the gate is unavailable for an external reason that is recorded as a blocker or limitation.

A waived gate is **not** acceptable when the real reason is convenience, impatience, or difficulty.

High-sensitivity architecture/governance/schema packets must not self-waive required gates by prose alone.

---

## Related documents

- `AGENTS.md` §7 — Working discipline (summary + cross-reference)
- `docs/governance/zeus_autonomous_delivery_constitution.md` — Full delivery constitution
- `docs/governance/zeus_change_control_constitution.md` — Change control rules
- `docs/governance/zeus_autonomy_gates.md` — Team mode and autonomy gate conditions
- `docs/governance/zeus_micro_event_logging.md` — Micro-event logging format and rules
