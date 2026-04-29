# Fix Plan — Execution-State Truth P0 Hardening

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: PR #18 (`Refresh execution-state truth operations package`), `docs/operations/task_2026-04-19_execution_state_truth_upgrade/{project_brief,implementation_plan}.md`, current `main` HEAD `7507a21`.

This plan does **not** authorize code mutation. It locks the surface for the next narrow implementation packet that will land P0 hardening once operator freezes it via `current_state.md`.

## 0. Scope contract

- This is a **planning packet only**. Allowed write-targets within this packet: `pr18_audit.md`, `fix_plan.md`, `task_packet.md` (downstream), `receipt.json` (downstream), `work_log.md` (when implementation starts).
- No source mutation, no test mutation, no schema change, no production DB write, no `current_state.md` promotion. Operator owns those gates.
- Companion artifact: [pr18_audit.md](docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/pr18_audit.md) — every PR claim verified against current code.

## 1. Structural-decision summary (Fitz Constraint #1)

The 13 cited remaining defects in PR #18 collapse to **5 structural decisions** (K=5, N=13):

| # | Structural decision | Symptoms it dissolves |
|---|---------------------|------------------------|
| K1 | Authority labels must be a function of (source × freshness × confidence), not a static map | C5 (degraded→VERIFIED), C6/C7 partially (truth-shape gap) |
| K2 | Live placement must be gated by a single command boundary; `place_limit_order` becomes private | C7 (submit-before-persist), C11 (direct calls), C13 (RED sweep not durable command) |
| K3 | Capability claims must be removable when not implemented (no decorative labels) | C8 (iceberg/dynamic_peg/liquidity_guard) |
| K4 | UNKNOWN must compose: chain × command × portfolio. Single layer's UNKNOWN does not certify a flat downstream | C9 (chain UNKNOWN not command-aware), C10 (fabricated `unknown_entered_at`) |
| K5 | Venue-version (V1/V2) must be an `ExternalParameter[T]` with explicit preflight, not implied by base URL | C12 (no V2 preflight) |

P0 hardens four of these five at the *symptom* layer (no schema change). K2 and K4 require P1/P2 to make the categories impossible (Fitz Constraint #1: "make the category impossible, not just the instance"). P0 buys safety; P1/P2 buy permanence.

## 2. P0 surface lock (concrete file:line targets)

Each row defines: WHAT changes, WHERE, the structural-decision K-bucket, and whether it is symptom-suppression or category-impossible.

| ID | Target | Change shape | K-bucket | Posture |
|----|--------|--------------|----------|---------|
| P0.1 | [src/state/portfolio.py:59](src/state/portfolio.py:59) `_TRUTH_AUTHORITY_MAP` | Map `"degraded"` to a new `"DEGRADED_PROJECTION"` value (or a similar non-VERIFIED label). Update consumers that branch on `VERIFIED` to treat `DEGRADED_PROJECTION` as non-authoritative. | K1 | Symptom-suppression (P1 will replace the map with computed authority) |
| P0.2 | [src/state/portfolio.py:1364](src/state/portfolio.py:1364) consumer paths | Audit every `_TRUTH_AUTHORITY_MAP.get(...)` reader. Confirm `DEGRADED_PROJECTION` does not flow into entry-allow checks or operator status as VERIFIED. | K1 | Symptom-suppression |
| P0.3 | [src/engine/cycle_runner.py](src/engine/cycle_runner.py) entry path (degraded loader branches at L1226, L1271) | Add an explicit *entry-block* gate: if loader is degraded OR portfolio authority is `DEGRADED_PROJECTION`, return entry-block reason `LOADER_DEGRADED`. Monitor / exit / reconciliation continue. | K1 | Symptom-suppression |
| P0.4 | [src/data/polymarket_client.py:131](src/data/polymarket_client.py:131) and constructor | Add `_v2_preflight()` called at client init or before first `place_limit_order`. Failure raises `V2PreflightError`; `_live_order` callers must catch and surface as `entry_block_reason="v2_preflight_failed"` without falling back. Date and endpoint live in config (G4 from audit), not literal. | K5 | Category-impossible (V2 endpoint mismatch becomes unconstructable) |
| P0.5 | [src/execution/executor.py:291](src/execution/executor.py:291), [src/execution/executor.py:422](src/execution/executor.py:422) | No code change at this stage. Add a static guard (P0.6) that prevents *new* call sites being added outside `executor`. The boundary tightening to "gateway-only" lands in P1 with `command_bus`. | K2 | Symptom-suppression (full move in P1) |
| P0.6 | new `architecture/ast_rules/forbidden_patterns.md` rule + matching semgrep id (e.g. `zeus-place-limit-order-gateway-only`) | Static rule: any file other than `src/execution/executor.py` and approved gateway location may not call `client.place_limit_order(...)`. CI fails on violation. | K2 | Category-impossible at the static layer |
| P0.7 | [src/execution/executor.py:133-135](src/execution/executor.py:133) `create_execution_intent` | Either (a) drop `slice_policy`/`reprice_policy`/`liquidity_guard` fields entirely, or (b) keep the fields but emit constant `"unimplemented"` with an explicit `# CAPABILITY_NOT_IMPLEMENTED` comment and an INV-backed assertion that downstream code does not branch on the value. Recommend (a) — fewer surfaces. | K3 | Category-impossible (deletion) |
| P0.8 | new `architecture/runtime_posture.yaml` + `state/runtime_posture.json` (read-only at runtime), enforced in `cycle_runner` entry decision | Lock branch posture flag (`NO_NEW_ENTRIES` / `EXIT_ONLY` / `MONITOR_ONLY` / `NORMAL`). Default on `data-improve` and `midstream_remediation` is `NO_NEW_ENTRIES` until P0/P1/P2 close. | K1+K2 cross-cut | Category-impossible at runtime (no entry can pass without posture flag check) |
| P0.9 | `architecture/invariants.yaml` | Pre-allocate `INV-23` ("degraded projection cannot export as VERIFIED"), `INV-24` ("place_limit_order is gateway-only"), `INV-25` ("V2 preflight failure blocks placement"), `INV-26` ("runtime posture flag is read-only at runtime; entry path must consult it"). Anchor each to an enforcing test or rule id. | All K-buckets | Category-impossible at the law layer |
| P0.10 | `architecture/negative_constraints.yaml` | Add `NC-XX`: "no direct `place_limit_order` call outside gateway" pointing at the new semgrep rule from P0.6. Add `NC-YY`: "no decorative capability labels in `ExecutionIntent`" pointing at the test from P0.13. | K2, K3 | Category-impossible at the law layer |
| P0.11 | [docs/operations/current_state.md](docs/operations/current_state.md) | Operator-only: when freezing, name this packet as the active execution packet, set `Active program → P0 hardening`, and stamp `branch posture: NO_NEW_ENTRIES`. Not an agent action. | — | Operator gate |
| P0.12 | new `tests/test_p0_hardening.py` | Tests:<br>(a) `test_degraded_export_never_verified` — exercise the degraded loader path, assert the exported authority label is not `VERIFIED`.<br>(b) `test_v2_preflight_blocks_placement` — patched preflight raises; `_live_order` returns block, never reaches `place_limit_order`.<br>(c) `test_runtime_posture_blocks_new_entry` — posture set to `NO_NEW_ENTRIES`; entry path returns block reason `posture_blocked` even when all other gates pass.<br>(d) `test_red_sweep_emits_exit_intent_for_active` — confirms current behavior (regression guard before P2 changes it). | All K-buckets | Antibodies |
| P0.13 | extend `tests/test_executor_typed_boundary.py` | Assert that `ExecutionIntent.slice_policy` / `reprice_policy` / `liquidity_guard` either do not exist (option-a) or are constant `"unimplemented"` and not consumed by any executor branch (option-b). | K3 | Antibody |
| P0.14 | `tests/test_architecture_contracts.py` (existing) | Add assertions that `INV-23..26` exist with non-empty `enforced_by`, and that `NC` ids point at semgrep rule ids that exist. | All K-buckets | Antibody |
| P0.15 | tests under `tests/test_phase5a_truth_authority.py` | Demote / correct any present-tense claim that asserts a behavior P0 changes (e.g. degraded → VERIFIED). Mark claims that no longer hold with explicit fail-active assertions. | K1 | Cleanup |

## 3. Acceptance gates (must all hold before P0 closes)

1. All P0.12, P0.13, P0.14 tests new and passing on the implementation branch.
2. Semgrep rule from P0.6 fails on a synthetic violation and passes the existing tree.
3. `architecture/invariants.yaml` and `architecture/negative_constraints.yaml` carry the new INV/NC ids with `enforced_by` populated.
4. `state/runtime_posture.json` exists with `NO_NEW_ENTRIES` and a startup gate refuses to run without it.
5. `topology_doctor.py --navigation` and `--map-maintenance --map-maintenance-mode advisory` clean over the changed files.
6. `git diff --check` clean.
7. Demoted/corrected stale tests no longer encode false present-tense authority claims.
8. PR doc-level gaps G1, G3, G5, G6, G7, G8 (from `pr18_audit.md`) reconciled in the same PR or a fast follow-up. G2 and G4 may be resolved separately if blocking.

## 4. Stop conditions (defer to P1 if any of these arise)

- A P0 fix would require new DB schema (`venue_commands`, `venue_command_events`, etc.).
- The static guard cannot be expressed in semgrep / `architecture/ast_rules/` without a real command-bus extraction (i.e. the boundary itself has to move first).
- V2 preflight requires negotiated authentication or a new SDK version that is not yet on the approved list — **escalate to operator with vendor evidence URL**, do not literal-encode the cutover.
- `runtime_posture.yaml` design needs lifecycle ownership (who can flip it, where audit lives) — escalate.
- Removing `slice_policy`/`reprice_policy`/`liquidity_guard` (P0.7 option-a) breaks an unforeseen consumer found by grep — fall back to option-b.

## 5. Risks (explicit, with mitigations)

| ID | Risk | Mitigation |
|----|------|-----------|
| R1 | Renaming `_TRUTH_AUTHORITY_MAP` value silently flips behavior in operator status / tracker JSON. | P0.2 audit + golden status snapshot test; do not ship without a diff review of every reader. |
| R2 | V2 preflight false-positive blocks the live path mid-cutover. | Preflight result must be advisory until P0.8 posture is `NORMAL`; while posture is `NO_NEW_ENTRIES`, V2 preflight failure is logged as evidence, not entry-blocking, because no entries are allowed anyway. |
| R3 | Static `place_limit_order` guard misses indirect call paths (e.g. via `getattr`). | Tests in P0.12 must include a *negative* fixture that intentionally violates and confirms semgrep flags it. |
| R4 | INV/NC additions collide with existing `INV-21`/`INV-22`/`INV-13` numbering (the manifest order is non-monotonic). | Pre-merge step: read `architecture/invariants.yaml` first, allocate the next free integer ids, and reserve them in the same commit that lands the law text. |
| R5 | `current_state.md` promotion freezes other in-flight midstream work. | Operator chooses freeze timing; agent never auto-promotes. |
| R6 | Posture flag becomes a "second authority plane" duplicating `risk_level`. | INV-26 must explicitly state posture is *additive* with `risk_level`, not a replacement; entry path consults both. |

## 6. Open operator decisions (required before promotion)

| ID | Decision | Why it blocks |
|----|----------|---------------|
| O1 | Approve V2 SDK / package / version pin. | P0.4 cannot be implemented without a known good preflight signature. |
| O2 | Approve `runtime_posture.yaml` lifecycle ownership and edit policy. | P0.8 needs an authority owner; otherwise the posture flag becomes a third authority plane. |
| O3 | Confirm CLOB V2 cutover date and source URL with retrieval timestamp. | G4 in audit; otherwise the date is encoded as folklore. |
| O4 | Choose P0.7 option (a) drop labels, or (b) constant + INV. | Determines downstream consumer migration scope. |
| O5 | Allocate `INV-23..26` and one or two `NC-XX` ids in `architecture/invariants.yaml` / `architecture/negative_constraints.yaml`. | All P0 acceptance gates depend on the allocations. |

## 7. Test relationships (Fitz: "test relationships, not just functions")

These cross-module invariants must be expressible as `pytest` assertions before the implementation packet is allowed to start (Fitz: "If you can't express the cross-module invariant as a pytest assertion, you don't understand the relationship yet"):

- **R-1 (degraded × export)**: When portfolio authority is `degraded`, the exported status payload's `authority` field is *not* `VERIFIED`.
- **R-2 (preflight × placement)**: When `_v2_preflight()` raises, no `client.place_limit_order(...)` call ever fires within `_live_order` for the same cycle.
- **R-3 (posture × entry)**: When `runtime_posture` is `NO_NEW_ENTRIES`, every entry-decision path returns block, irrespective of risk_level / chain_state / family budget.
- **R-4 (capability × consumption)**: For every field in `ExecutionIntent` that names an unimplemented capability, no executor branch reads that field. (Encoded as an introspection test over `executor` source AST.)
- **R-5 (RED × command-emission)**: P0 retains the regression guard that RED still marks `exit_reason="red_force_exit"`. P2 will replace this with command emission; the regression guard prevents accidental loss of the existing safety in transit.

The implementation packet's first artifact is these 5 relationship tests. Implementation only starts after they fail correctly (red bar) against pre-fix code.

## 8. Sequencing

```
[operator freezes packet via current_state.md]
        ↓
[allocate INV-23..26, NC-XX, NC-YY in manifests]   (P0.9, P0.10) — law first
        ↓
[write 5 relationship tests R-1..R-5]              (P0.12, P0.13) — antibodies first, must fail red
        ↓
[implement P0.1, P0.2, P0.3, P0.4, P0.7]           — symptom suppression
        ↓
[implement P0.6 semgrep rule + P0.8 posture]       — category-impossible at static + runtime layer
        ↓
[demote/correct stale tests P0.15]
        ↓
[acceptance gate run: all 8 gates in §3]
        ↓
[critic + verifier review per OMC team protocol]
        ↓
[commit + push; update current_state.md to point to next packet (P1)]
```

The order is mandatory. Code before law violates the authority order. Tests after implementation produce confirmation bias, not antibodies.

## 9. What this fix plan does NOT do

Per Fitz Constraint #1 ("Make the category impossible, not just the instance"), the following remain **deliberately deferred** to P1/P2:

- Durable `venue_commands` / `venue_command_events` schema and append-first events (P1).
- Pre-side-effect command persistence (P1).
- Chain × command UNKNOWN composition (P2).
- Removal of fabricated `unknown_entered_at` from temporal authority (P2).
- RED → durable `CANCEL`/`DERISK`/`EXIT` command emission (P2).
- Persistent alpha budget, market eligibility, station-finalization contract (P3).

These are not regressions. They are P0's stop conditions made explicit.

## 10. Provenance and authority

This fix plan was produced by reading PR #18 head (`76a2f42`) end-to-end, grep-verifying every claim against `main` HEAD `7507a21`, and reconciling against `AGENTS.md`, `docs/operations/AGENTS.md`, `docs/operations/current_state.md`, `architecture/invariants.yaml`, `architecture/negative_constraints.yaml`. No code or production state was mutated.

Authority order followed: runtime code/tests/manifests > operations pointer > official venue docs > review evidence > historical docs.

Branch: `claude/pr18-execution-state-truth-fix-plan-2026-04-26`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-pr18-fix-plan-20260426`
