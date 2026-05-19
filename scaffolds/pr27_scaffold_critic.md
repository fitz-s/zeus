# SCAFFOLD Critic — PR 2+7 (B/27)

**Verdict**: APPROVE_WITH_MINOR
**Reviewer**: sonnet SCAFFOLD critic (`a35aedaf00e4937bd`)
**Reviewed**: 2026-05-19 against `b97fc1c0d7fa4d953a71fe2d9409761303fcf387`

---

## Required revision (BLOCKING for production phase)

**Finding #8 — `spread_observed_window_ms` is a dead field as scaffolded.** The SCAFFOLD states the field ships at default=0 with "future windowed-max implementations will populate this field." That means it ships permanently at 0 with no defined observer — schema pollution. The R-EE.5 antibody only tests roundtrip with `window_ms=0`, which passes trivially. Critic 2 P5 explicitly required spread observer semantics; this is not satisfied.

**Two acceptable fix paths (executor chooses):**
- (a) **Remove from PR 2 scope entirely.** Cleanest. Defer the field to a follow-up PR where the observer (windowed max-spread tracker in `market_scanner.py`) is implemented at the same time. Update the SCAFFOLD + tests to drop the field.
- (b) **Specify the observer concretely.** What counter/timer populates it (e.g., `time.monotonic_ns()` delta over a sliding window of `monitor_refresh` ticks). What event triggers the window roll. What table stores the windowed series. Update R-EE.5 to assert non-zero on at least one snapshot in the antibody fixture.

Pick one. Document the choice in the production commit message.

---

## Recommended (non-blocking; address during production if cheap)

1. **W1 variable name** (Finding #2): the SCAFFOLD says context is derived from `microstructure_sink` dict, but at `evaluator.py:3676` `microstructure_sink` is a callback, not a dict in scope. Identify the actual variable carrying `ask_sz` at that line (likely needs threading from earlier in `evaluate_candidate_edges`). Note in the production commit how the context is constructed at exactly W1.
2. **$0.05 boundary derivation** (Finding #4): one-line docstring in `effective_kelly_context.py` referencing `execution_price.py:130` convex fee erosion. Justifies the conservative midpoint over an asserted-but-undocumented value.
3. **"12 buckets" prose clarification** (Finding #6): strike "12 buckets" from prose; the design is a 6-row table with FOK/FAK as separate columns. Non-FOK/FAK order types default to FAK column. Prevents an executor from implementing a 12-row dict when 6×2 was intended.
4. **test_topology.yaml registration** (Finding #13): include the 8 test entry names + created dates inline in the production commit (don't omit under time pressure). Wave-A opus critic caught this miss for PR 4/PR 5; don't repeat.
5. **`db_table_ownership.yaml` line 1272** (Finding #15): UPDATE BOTH entries for `executable_market_snapshots` (lines 509 AND 1272). Partial update will be caught by critic and require a revision round.

---

## APPROVED items (no action needed)

- 7-site Kelly enumeration: exhaustive (K1, K2, W1-W5; 0 missed)
- W5 graceful degrade in replay: correct conservative behavior
- Depth bucket boundary (100 shares): operationally sound
- PR 2 storage migration pattern: matches Wave-A precedent; idempotent
- PR 2+7 wave atomicity: single commit-block, single branch ✓ (Critic 2 P7 mandate)
- Relationship tests BEFORE implementation: R-EE.1 through R-EE.8 specified as pytest signatures ✓ (Fitz methodology)
- B/36 non-collision: `raw_orderbook_hash` is attribute/dict/SQL-column access only, never line-number ✓
- File-header discipline: Lifecycle/Purpose/Reuse triplet present
- LOC realism: 660 well-calibrated

---

## Open question for PR description (not blocking SCAFFOLD-to-production)

Critic 2 P (Skeptic) flagged the Polymarket $0.10 UI-substitution threshold as needing URL evidence. When you open the PR, include a citation (Polymarket docs URL or screenshot) confirming $0.10 as the substitution boundary. If you can't substantiate, the WIDE bucket boundary is wrong and Critic 2's review will re-open.
