# PLAN_V3_CRITIC — round-3 fresh-context opus convergence audit (2026-05-16)

**Verdict:** **PASS_WITH_FOLLOWUPS — START EXECUTION**

**Method:** delta-only review of V3-A/B/C/D vs PLAN_V2_CRITIC.md. Fresh opus subagent. Did not re-run v1/v2 verified amendments (A2/A3/A4/A5 carry forward as PASS). Verified Q1 (A1' fix), Q2 (§8.5 substance), Q3 (residual) via grep + sed on PLAN.md commit `e19b7ca922` + footnote `ee1e544b9d`.

---

## Q1 — A1' regression fixed? **YES**

| Probe | Finding | Disposition |
|---|---|---|
| grep `dry-run --config bindings/zeus` in PLAN body | 2 hits, both NEGATIVE-CITES (§1 changelog audit entry + §12 row 4 documenting empirical fail) | PASS — no surviving prescription |
| grep `SCOUT-0B-determined`/`SCOUT 0B determines` | 7 hits across §5 WAVE 0/2, §6 gate table, §7 risk register | PASS — defer-to-scout is the operative pattern |
| Plan unambiguously says "do NOT pin command in plan; SCOUT 0B derives + records" | §5 line 197 verbatim: "**SCOUT 0B determines command per V3-A** (V2's cited command empirically fails)"; §12 row 15 verbatim: "no command pre-committed in PLAN body" | PASS |

A1' is structurally fixed. No regression class remaining for `bindings/zeus/config.yaml`.

---

## Q2 — §8.5 substantive or boilerplate? **SUBSTANTIVE (with one caveat)**

| Probe | Finding | Disposition |
|---|---|---|
| 1. Surgical edit operational threshold | Rule 1: "diff line count ≤ 3× lines of cited drift" — measurable | PASS |
| 2. Essence-vs-bloat threshold concrete | Rule 2: ">10 net LOC requires explicit justification (what insight, why not distilled to ≤5 LOC, why this doc not sibling)" — 3-part test | PASS |
| 3. Atomic claim example present | Rule 3: explicit compound `X AND Y AND Z` notation cited as failure pattern + "split into 3 separate atomic statements" — example present | PASS |
| 4. Provenance format precise | Rule 4: literal template `(a) REPLACES verbatim quote / (b) WHY: one-of-six-enum / (c) VERIFIED-AT: file::symbol-or-SHA` — precise | PASS |
| 5. Stop condition has verification steps | Rule 5: 4-item per-doc checklist (citations grep-verifiable + Rule 4 triple sourced + zero orphan refs + zero unjustified net-new LOC) — concrete | PASS |
| 6. §8.5 reference count ≥ 4 | 28 hits across PLAN (planner claim 23 — undercounted; actual 28) | PASS |
| 7. §12 commands have RECORDED exit codes | Rows 4-9 enumerate exit codes; rows 4-6 record empirical FAIL exit codes (POSITIVE-FAILURE records — anti-rubber-stamp pattern) | PASS |
| Enforcement mechanism | §5 WAVE 1/2/3 each contain `Critic dispatch MUST probe compliance with §8.5 Rules 1-5 in every wave-close review`; §6 gate table row 5 mandates `§8.5 Rules 1-5 probed at every wave-close critic` | PASS |

**Caveat (followup-grade, not block-grade):** §8.5 lacks a single fully-worked GOOD-vs-BAD edit example. Rule 4 is a literal template; Rule 3 names the failure pattern (`X AND Y AND Z`). But no "Rule 1 surgical example: BAD = entire-section-rewrite of `task_boot_profiles.yaml::semantics`; GOOD = 2-line replacement of the stale loader path." Executors must induce surgical-vs-rewrite criterion from the 3× threshold + commit-justification rule. Acceptable: the operational thresholds are concrete enough that critic can flag violations objectively.

§8.5 has TEETH. Not boilerplate.

---

## Q3 — Path-citation residual deferral adequate? **CONDITIONALLY YES**

| Probe | Finding | Disposition |
|---|---|---|
| WAVE 0 SCOUT 0A scope explicitly covers path-citation pre-flight of WAVE 1/2/3 file refs | §5 SCOUT 0A scoped to "TIER 0A 8 docs findings + INV-NN runtime caller cross-check" — does NOT structurally absorb plan-internal path-citation audit | GAP |
| §14 FCI4 mandates re-verify all paths within 10 min of WAVE 0 dispatch | YES — §14 line 430: "Re-verify within 10 min of WAVE 0 dispatch" applies to "22 TIER 0 + 40 TIER 1 paths + CLI surfaces + loaders" | PARTIAL MITIGATION |
| Planner self-discloses residual + assigns v4 critic responsibility | Footnote in commit `ee1e544b9d` explicitly says next critic should "audit every other pytest/test-path citation in PLAN.md" + V3-D lesson expanded | MITIGATION |
| Risk if `tests/test_invariants.py` cite reaches WAVE 1 unfixed | WAVE 1 step 3 currently mandates a pytest command that will exit-nonzero immediately. Executor will hit the wall in ~30 seconds. Caught on first WAVE 1 dispatch, not silent | LOW BLAST RADIUS |

**Disposition:** Q3 is a structural gap (SCOUT 0A scope doesn't formally absorb path-citation pre-flight), BUT:
- §14 FCI4 mandate covers it implicitly
- Planner has self-disclosed the residual + assigned v4 critic
- Failure mode is FAIL-FAST (executor cannot proceed past WAVE 1 step 3 with wrong path) not silent-drift

Not a block. Becomes a FOLLOWUP: orchestrator MUST run a path-citation grep sweep on PLAN §5 WAVE 1/2/3 immediately before WAVE 0 dispatch (5-min check) — listed in followups below.

---

## Pre-committed probes — full disposition

| # | Probe | Disposition |
|---|---|---|
| 1 | Surgical edit operational threshold | PASS |
| 2 | Essence-vs-bloat threshold | PASS |
| 3 | Atomic claim example | PASS (named `X AND Y AND Z` failure pattern) |
| 4 | Provenance format precise | PASS (literal `(a)(b)(c)` template) |
| 5 | Stop condition verification | PASS (4-item per-doc checklist) |
| 6 | §8.5 references ≥ 4 | PASS (28 hits) |
| 7 | §12 commands have exit codes | PASS (rows 4-9 enumerate; rows 4-6 are POSITIVE-FAILURE records) |
| 8 | WAVE 0 SCOUT 0A path-citation pre-flight | GAP (mitigated by §14 + planner self-disclosure) |
| 9 | Baseline test failures acknowledged | PARTIAL — §12 rows 13/14 mandate baseline capture pre-WAVE-0; no inline acknowledgment of 4 known invariant fails on main. Acceptable: baseline capture happens AT WAVE 0 dispatch |
| 10 | 3-PR split rationale post-amendments | PASS — §4 alternative-considered section intact; sizing per V3 amendments still aligned (PR-A 300-600 / PR-B 500-1000 / PR-C 500-900) |

---

## Mandatory followups before WAVE 0 dispatch

1. **Path-citation grep sweep** (5 min): orchestrator greps every `tests/test_*.py` + `scripts/*.py` + `src/**/*.py` reference in §5 WAVE 1/2/3, `ls`-verifies. Fix `tests/test_invariants.py` cite in WAVE 1 step 3 to one of (a) `python -m pytest tests/ -q -k invariant`, (b) SCOUT-0A-derived per-finding subset, (c) full `tests/` baseline. Operator picks.
2. **§12 row 13/14 execution** (baseline capture): mandatory per §12 — not a critic followup, a §12 prereq.
3. **§12 row 16 execution** (this critic dispatch): this verdict satisfies it.

No further critic round required. V3 is converged.

---

## Realist check on residual (§8.5 missing worked examples)

- Realistic worst case: 1-2 executor edits in WAVE 1 violate Rule 1 (over-rewrite) before critic catches it. Cost: 1 revise round per critic = ~30 min.
- Mitigating factors: per-wave critic probes §8.5 Rules 1-5 explicitly; commit-header prefix is mechanical (impossible to forget); Rule 4 provenance triple is template-form.
- Detection: FAIL-FAST at critic, not silent.
- Severity: MINOR. Followup-grade not block-grade.

---

## Per-axis convergence verdict

| Axis | v1 | v2 | v3 |
|---|---|---|---|
| A1 phantom CLI | REVISE | REGRESSED | **FIXED (V3-A)** |
| A2 topology_doctor module form | REVISE | PASS | PASS |
| A3 reality_contracts TIER 0B | REVISE | PASS | PASS |
| A4 actual `wc -l` | REVISE | PASS | PASS |
| A5 TIER 1 = 40 | REVISE | PASS | PASS |
| B6 surgical edit | n/a | ABSENT | **PASS (Rule 1)** |
| B7 essence-over-bloat | n/a | ABSENT | **PASS (Rule 2)** |
| B8 atomicity | n/a | ABSENT | **PASS (Rule 3)** |
| B9 per-statement provenance | n/a | PARTIAL | **PASS (Rule 4 + WAVE 2/3 extension via V3-C)** |
| B10 stop-condition | n/a | ABSENT | **PASS (Rule 5)** |
| C1 path-citation rot | n/a | n/a | RESIDUAL (followup) |

10 of 11 axes PASS. 1 residual is followup-grade with fail-fast detection.

---

## Verdict gate

- All 3 convergence questions PASS (Q1 fully, Q2 substantively, Q3 conditionally with mitigations).
- §8.5 has operational thresholds + enforcement mechanism in every wave-close critic. Not boilerplate.
- A1' regression structurally eliminated by defer-to-SCOUT-0B contract.
- Residual `tests/test_invariants.py` cite is fail-fast (~30s detection in WAVE 1), self-disclosed, and absorbed by §14 FCI4 + mandatory path-citation grep sweep followup.
- No structural defect blocks execution.

**Verdict: PASS_WITH_FOLLOWUPS — START EXECUTION**

Operator: dispatch WAVE 0 after the 3 followups above. Plan is converged.

---

## Sign-off

- v1 critic verdict: REVISE (5 amendments). v2 folded all 5; A1 regressed.
- v2 critic verdict: REVISE_ROUND_2 (A1' + AXIS B missing). v3 folded V3-A/B/C/D.
- v3 critic verdict: **PASS_WITH_FOLLOWUPS** — A1' structurally fixed; §8.5 substantive; Q3 residual is followup-grade with fail-fast detection.

3-round critic convergence achieved. WAVE 0 cleared to dispatch after path-citation grep sweep + baseline capture.
