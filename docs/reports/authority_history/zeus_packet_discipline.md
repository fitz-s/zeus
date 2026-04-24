# Zeus Packet Discipline

Status: historical report evidence; superseded by `docs/authority/zeus_current_delivery.md`
Source: Extracted from root `AGENTS.md` §2 (committed version, 2026-04-09)
Referenced by: `AGENTS.md` §7 (working discipline)

---

## 1. Program / packet / slice hierarchy

- A **program phase** is larger than a packet. Examples: `FOUNDATION-MAINLINE`, `data-improve`.
- A **packet** is the atomic authority-bearing unit of execution. Examples: `DATAFIX-001 ensemble backfill`, `FEAT-002 hourly obs pipeline`.
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

## 8. Market-math packet requirements

Any packet touching market math or settlement semantics must include:

1. `domain_assumptions` — stated explicitly
2. Authority source for each assumption
3. Invalidation condition if the assumption is false

All review on market-math or calibration packets must verify:

1. Bin contract kind (point / finite_range / open_shoulder)
2. Settlement cardinality
3. Shoulder semantics
4. Whether the packet respects discrete support rather than only continuous intuition

See `docs/reference/zeus_domain_model.md` §2 (discrete settlement support) for the domain definitions.

---

## 9. Micro-event logging

### Separation of concerns

| Surface | Purpose | Who may write |
|---------|---------|---------------|
| Packet-level progress tracking | Durable state transitions, blockers, evidence | Packet owner only |
| `.omx/context/<packet>-worklog.md` | Micro-events, retries, scout findings, experiment breadcrumbs | Any agent |

Small events, retries, scout findings, timeout notes, and experiment breadcrumbs belong in the worklog, not in packet-level tracking. Promote a worklog fact only when it becomes a real state transition, blocker, or accepted evidence item.

### Preferred micro-event format

```md
## [timestamp local] <packet> <slice-or-event>
- Author:
- Lane:
- Type: scout | retry | timeout | evidence | blocker | note
- Files:
- Finding:
- Evidence:
- Suggested next slice:
```

| Field | Required | Description |
|-------|----------|-------------|
| `Author` | Yes | Agent identity or model name |
| `Lane` | Yes | Execution lane (main, scout-1, critic, etc.) |
| `Type` | Yes | One of: `scout`, `retry`, `timeout`, `evidence`, `blocker`, `note` |
| `Files` | If applicable | Files read or modified |
| `Finding` | Yes | What was discovered or attempted |
| `Evidence` | If applicable | Test output, grep result, error message |
| `Suggested next slice` | Optional | What should happen next |

---

## 10. Script disposal and promotion

Every packet closeout must include a **Script Disposal** section when the packet added, modified, ran, or deprecated a top-level script.

For each affected script, state one disposition:

- **Deleted** — one-off probe removed after its evidence was captured.
- **Promoted** — hardened into a `long_lived` script with stable naming, manifest metadata, reuse rules, and tests or a smoke gate.
- **Promotion candidate** — kept temporarily with `lifecycle: promotion_candidate`, an owner packet, and a dated promotion decision.
- **Packet-ephemeral** — still temporary with `lifecycle: packet_ephemeral`, `delete_by: YYYY-MM-DD`, and a reason it cannot be deleted yet. `delete_on_packet_close: true` is extra intent only; a dated deadline is still required.
- **Deprecated fail-closed** — retained only to make stale references fail visibly, with `status: deprecated`, `fail_closed: true`, and `canonical_command: DO_NOT_RUN`.

Do not close a packet with anonymous one-off scripts still living in `scripts/`. A script either becomes a reusable tool that future agents should discover and reuse, or it is deleted / fail-closed with an explicit disposition.

---

## Related documents

- `AGENTS.md` §7 — Working discipline (summary + cross-reference)
- `docs/authority/zeus_current_delivery.md` — Current delivery entrypoint and authority order
- `docs/authority/zeus_change_control_constitution.md` — Change control rules
- `docs/authority/zeus_autonomy_gates.md` — Autonomy gates and team mode restrictions
