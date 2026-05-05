# T0_D6_FIELD_LOCK — Planner Triage

**Created:** 2026-05-04
**Verdict:** REALITY_ANSWERED — the four D6 fields are unambiguously named in current source.
**Captured-by:** planner subagent

---

## 1. The plan's question

Per MASTER_PLAN_v2 §8 T0.6:
> Operator locks exact D6 field list → `T0_D6_FIELD_LOCK.md`; exact field names and source evidence from code/DRIFT/known gaps.

Per §10 T1BD plan body:
> If `position.corrected_executable_economics_eligible is True`, block all `entry_price`, `cost_basis_usd`, `size_usd`, and `shares` assignments from chain facts across every branch.

So the plan itself already names the four fields. The operator decision degenerates to "confirm these are the right four."

## 2. Reality verification (planner grep, 2026-05-04)

`grep -n "entry_price\|cost_basis_usd\|size_usd\|shares" src/state/chain_reconciliation.py` shows these **exact** field assignments from chain facts to position objects (rescued + corrected paths):

| Field | Chain-reconciliation assignment site (line) | Source |
|---|---|---|
| `entry_price` | `src/state/chain_reconciliation.py:531` `rescued.entry_price = chain.avg_price` | RESCUE branch |
| `entry_price` | `src/state/chain_reconciliation.py:574` `pos.entry_price = rescued.entry_price` | RESCUE commit-back |
| `entry_price` | `src/state/chain_reconciliation.py:621` `corrected.entry_price = chain.avg_price` | SIZE-MISMATCH branch |
| `entry_price` | `src/state/chain_reconciliation.py:649` `pos.entry_price = corrected.entry_price` | SIZE-MISMATCH commit-back |
| `cost_basis_usd` | `src/state/chain_reconciliation.py:533` `rescued.cost_basis_usd = chain.cost` | RESCUE branch |
| `cost_basis_usd` | `src/state/chain_reconciliation.py:575` `pos.cost_basis_usd = rescued.cost_basis_usd` | RESCUE commit-back |
| `cost_basis_usd` | `src/state/chain_reconciliation.py:623` `corrected.cost_basis_usd = chain.cost` | SIZE-MISMATCH branch |
| `cost_basis_usd` | `src/state/chain_reconciliation.py:650` `pos.cost_basis_usd = corrected.cost_basis_usd` | SIZE-MISMATCH commit-back |
| `cost_basis_usd` | `src/state/chain_reconciliation.py:692` `cost_basis_usd=chain.cost or (chain.size * chain.avg_price)` | QUARANTINE branch |
| `size_usd` | `src/state/chain_reconciliation.py:534` `rescued.size_usd = chain.cost` | RESCUE branch |
| `size_usd` | `src/state/chain_reconciliation.py:576` `pos.size_usd = rescued.size_usd` | RESCUE commit-back |
| `size_usd` | `src/state/chain_reconciliation.py:624` `corrected.size_usd = chain.cost` | SIZE-MISMATCH branch |
| `size_usd` | `src/state/chain_reconciliation.py:651` `pos.size_usd = corrected.size_usd` | SIZE-MISMATCH commit-back |
| `size_usd` | `src/state/chain_reconciliation.py:683` `size_usd=0.0` | QUARANTINE branch (zeros — still a mutation) |
| `shares` | `src/state/chain_reconciliation.py:536` `rescued.shares = chain.size` | RESCUE branch |
| `shares` | `src/state/chain_reconciliation.py:577` `pos.shares = rescued.shares` | RESCUE commit-back |
| `shares` | `src/state/chain_reconciliation.py:627` `corrected.shares = chain.size` | SIZE-MISMATCH branch (size-mismatch local-vs-chain) |
| `shares` | `src/state/chain_reconciliation.py:639` `corrected.shares = local_shares` | SIZE-MISMATCH preserved-local branch |
| `shares` | `src/state/chain_reconciliation.py:652` `pos.shares = corrected.shares` | SIZE-MISMATCH commit-back |
| `shares` | `src/state/chain_reconciliation.py:693` `shares=chain.size` | QUARANTINE branch |

`grep -n "corrected_executable_economics_eligible"`:

- `src/state/portfolio.py:286` — dataclass field declaration: `corrected_executable_economics_eligible: bool = False`
- `src/state/portfolio.py:1715` — projection write echoes it
- `src/execution/fill_tracker.py:452` — fill-time set-true site
- `src/engine/cycle_runtime.py:1061,1075,1116` — entry-time set/false sites

The eligibility flag exists, defaults False, and is set True only when `pricing_semantics_version == CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION`. So T1BD's guard predicate is materializable today.

## 3. Locked field list

```
LOCKED_D6_FIELDS = (
    "entry_price",
    "cost_basis_usd",
    "size_usd",
    "shares",
)
```

These four are confirmed by code grep. The MASTER_PLAN_v2 §10 T1BD body already names exactly these four. F5 ("more than one mutation branch") is also confirmed: there are 5 distinct mutation branches and 20 assignment sites across `chain_reconciliation.py`. T1BD's "every branch" requirement is justified.

## 4. T1BD eligibility predicate (no operator decision needed)

```python
if getattr(position, "corrected_executable_economics_eligible", False) is True:
    # Block assignment from chain facts to any of the four LOCKED_D6_FIELDS.
    # Increment cost_basis_chain_mutation_blocked_total{field}.
```

Predicate uses an existing dataclass field that already defaults to False, so legacy positions remain unguarded (preserving today's behavior, per F5/T1BD).

## 5. Projection/loader scope (T1BD second half)

`src/state/portfolio.py:1699-1716` shows the four fields are projected to durable rows:
- line 1699 `"entry_price": pos.entry_price`
- line 1700 `"size_usd": pos.size_usd`
- line 1707 `"shares_filled": pos.shares_filled` (note: `shares_filled` not `shares` — see §6 below)
- line 1708 `"shares_remaining": pos.shares_remaining`

`src/state/portfolio.py:1164` defines `_position_from_projection_row(row, *, current_mode)` (the loader). The T1BD counter `position_loader_field_defaulted_total` instruments default-firing for the four locked fields when reading rows back; planner did not exhaustively enumerate the loader defaults but the function exists and is the correct seam.

## 6. Subtle finding for T1BD planning (planner observation)

The plan's "shares" field is somewhat ambiguous: the dataclass `Position` has multiple share-flavored fields (`shares`, `shares_filled`, `shares_remaining`, `shares_submitted`, `chain_shares`). The chain-reconciliation mutation sites listed in §2 specifically write to `shares` (the legacy aggregate field) and `chain_shares`. T1BD's invariant should be:

> For corrected-eligible positions, `shares` and `chain_shares` may diverge from each other (chain-truth vs. fill-truth split), but neither may overwrite `shares_filled` or `shares_remaining` (the FillAuthority-derived fields). The four LOCKED_D6_FIELDS guard targets `entry_price`, `cost_basis_usd`, `size_usd`, `shares`.

The T1BD prompt should explicitly carry this distinction so the executor does not over-guard `shares_filled` / `shares_remaining`.

## 7. Source-evidence cite list (planner grep-verified within 10 minutes)

- `src/state/chain_reconciliation.py:531-695` — all D6-field mutation sites enumerated above
- `src/state/portfolio.py:286` — `corrected_executable_economics_eligible: bool = False`
- `src/state/portfolio.py:1699-1716` — projection write of D6 fields
- `src/state/portfolio.py:1164` — `_position_from_projection_row` loader
- `src/execution/fill_tracker.py:452` — eligibility set-true site
- `src/engine/cycle_runtime.py:1061,1075,1116` — eligibility entry-time gating
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:508` — plan body names exactly these four fields

---

**Verdict:** REALITY_ANSWERED. The four D6 fields are `entry_price`, `cost_basis_usd`, `size_usd`, `shares`. Operator may sign this triage as-is. T1BD prompt may use these four as locked.
