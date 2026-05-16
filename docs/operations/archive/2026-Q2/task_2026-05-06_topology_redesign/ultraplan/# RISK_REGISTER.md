# RISK_REGISTER

## Sunset: quarterly review (next 2026-08-06)

Unmitigated risks at the quarterly cadence escalate to operator review.
Charter rule M3 (`ANTI_DRIFT_CHARTER.md §5`) governs this cadence.

## §1 Risk format

```yaml
id:                     # R-NN
title:                  # short
probability:            # L | M | H
impact:                 # L | M | H
structural_mitigation:  # not "we'll be careful" — a mechanism
detection_signal:       # what indicates the risk has fired
owner:                  # role from IMPLEMENTATION_PLAN §10
sunset:                 # when this risk row is re-evaluated
```

## §2 Risks

### R1 — Source-decorator coverage incomplete

| Field | Value |
|---|---|
| **probability** | H |
| **impact** | M |
| **structural mitigation** | (a) Phase 2 ships `tests/test_capability_decorator_coverage.py` that AST-walks every path in `capabilities.yaml :: hard_kernel_paths` and asserts at least one `@capability` decorator is present; (b) Phase 4 Gate 3 (commit-time diff verifier) catches missing decorators when an unguarded path is first touched and reports them; (c) Phase 5 cutover requires 100% coverage on guarded writers as a numeric exit criterion |
| **detection signal** | CI lint red on coverage test; commit-time gate 3 emits ritual_signal with capability=null on a hard-kernel path |
| **owner** | implementer (Phase 2); critic (Phase 5 sign-off) |
| **sunset** | 2026-09-06 (re-evaluate quarterly until coverage steady at 100% for 60d) |

### R2 — Replay-correctness gate non-determinism

Replaying historical events deterministically requires every projection
input to be bit-stable. Legacy events may carry timestamps, RNG seeds, or
source-system variability that defeats determinism.

| Field | Value |
|---|---|
| **probability** | M |
| **impact** | H |
| **structural mitigation** | Phase 0.G picks a *fixed seed window* (last 7 days of canonical events) rather than full history; Phase 4 Gate 4 uses the same seed window; ADR-5 explicitly confirms (briefing §10 decision #8) the Chronicler event types in scope; non-deterministic events are listed as exclusions |
| **detection signal** | Same input window produces different output snapshots across runs in CI; flake rate >0% over 24h |
| **owner** | implementer (Phase 0.G + Phase 4) |
| **sunset** | 2026-09-06 |

### R3 — `LiveAuthToken` phantom breaks an existing import

Adding a phantom-typed parameter to `submit()` will fail every existing
caller that does not produce a token. Over-broad rollout breaks
non-live test suites that import the same modules.

| Field | Value |
|---|---|
| **probability** | H |
| **impact** | M |
| **structural mitigation** | (a) `LiveExecutor` and `ShadowExecutor` are *separate* ABCs (researcher §3.2 #4); ShadowExecutor cannot construct a LiveAuthToken — therefore tests calling Shadow are unaffected by definition; (b) `@untyped_for_compat` 30-day escape hatch in Phase 4 §6 rollback table for legitimate refactors; (c) Phase 4 day-by-day rollout with mypy/pyright run in shadow first |
| **detection signal** | mypy / pyright errors in CI in test or non-live execution paths |
| **owner** | implementer (Phase 4 days 56-60) |
| **sunset** | 2026-09-06 |

### R4 — Shadow router agreement rate too low

If `route_function` and `topology_doctor` disagree on >10% of real
diffs, cutover is indefinitely deferred — and the team loses confidence
in the new design.

| Field | Value |
|---|---|
| **probability** | M |
| **impact** | H |
| **structural mitigation** | Phase 0.F sets the **floor at ≥90%** (not target); Phase 5 cutover requires **≥98%**. Each disagreement is auto-classified into "shadow correct" (legacy false-positive) vs "legacy correct" (shadow miss). Shadow misses identified during the 7d window become Phase 1 / Phase 2 fixes. Briefing §3.3 baseline shows 39% of legacy `forbidden_files` are prose stop-conditions — many disagreements should be **shadow correct**, validating the redesign |
| **detection signal** | Phase 0.H decision file shows agreement <90%, OR Phase 5 pre-cutover shows <98% |
| **owner** | implementer (Phase 0.F + Phase 5) |
| **sunset** | 2026-09-06 |

### R5 — Multi-agent lease service deadlocks

Lease graph cycles or wedged sqlite at `state/leases.sqlite` could block
all multi-agent capability work. New operational surface = new failure
mode.

| Field | Value |
|---|---|
| **probability** | L |
| **impact** | H |
| **structural mitigation** | (a) `lease_service.py` uses **TTL-based eviction** (default 600s) — no graph traversal, no cycle detection needed because no held lease lasts forever; (b) operator priority list in `state/lease_priority.yaml` resolves hot collisions deterministically; (c) CI cron sweep prunes expired leases hourly; (d) Phase 5 cutover monitoring includes lease-age histogram |
| **detection signal** | `list_active()` shows leases with `expires_at < now() - 1h`; agents report `LeaseConflict` indefinitely |
| **owner** | implementer (Phase 4 / Phase 5 telemetry); critic (monthly review) |
| **sunset** | 2026-09-06 |

### R6 — New design drifts back toward 禁书 within 6 months

Topology drifted in 12 months. zeus-ai-handoff drifted with an inline
warning. Without M1-M5 binding, the redesign would drift identically.

| Field | Value |
|---|---|
| **probability** | M (without M1-M5); H (without one of them) |
| **impact** | H |
| **structural mitigation** | (a) M1-M5 are binding (CHARTER §1: "all five or none — partial adoption recreates the ratchet"); (b) M5 ships as `tests/test_help_not_gate.py` with three concrete assertions (CHARTER §7); (c) Phase 3 + Phase 5 mid-implementation drift checks are *exit gates*, not advisories; (d) every artifact carries `sunset_date` (M3) so dead rules auto-demote |
| **detection signal** | Monthly critic review (CHARTER §8) shows any helper with `(fit_score < 0.5) / total > 0.20` over the prior 30d window AND `mandatory: true` AND no §4 evidence |
| **owner** | critic (monthly); operator (quarterly) |
| **sunset** | none — meta-risk; reviewed at every charter version bump |

### R7 — 20-hour replay fixture cannot be reconstructed

The original autonomous session was a real run; some inputs (web
fetches, model responses, external state) may not be reproducible. If
the replay fixture is weak, the acceptance test (briefing §9 row
"20-hour replay friction") loses meaning.

| Field | Value |
|---|---|
| **probability** | M |
| **impact** | H |
| **structural mitigation** | (a) Phase 0.A measures the *baseline* friction on the original session; the same fixture is re-run in Phase 5 — **delta** is the metric, not absolute reproduction; (b) if the original session cannot be replayed, Phase 5 substitutes a synthetic 5-task panel matching the original task-class distribution (≥80% topology operations); (c) ADR-5 documents the substitution policy |
| **detection signal** | Phase 0.A baseline file flagged "replay incomplete"; Phase 5 fixture deviates from original by >30% on input distribution |
| **owner** | planner (Phase 0.A); implementer (Phase 5) |
| **sunset** | 2026-09-06 |

### R8 — Operator decision fatigue on 6 ADRs

Six ADRs in 7 days. If signed without scrutiny, the redesign starts
with weak operator endorsement and Phase 0.H GO becomes pro forma.

| Field | Value |
|---|---|
| **probability** | M |
| **impact** | M |
| **structural mitigation** | (a) ADRs are reviewed in **two batches** of 3 (ADR-1, 2, 3 by day 4; ADR-4, 5, 6 by day 7) so the operator does not face 6 in one sitting; (b) each ADR is ≤500 words with the `accept | reject | revise` decision in the first paragraph; (c) ADR-4 (anti-drift binding) is the highest-leverage and is reviewed last — operator has the most context by then |
| **detection signal** | All 6 ADRs signed within 24h; or no annotations / questions logged |
| **owner** | architect (drafts); operator (signs) |
| **sunset** | one-time; closes when ADRs sign |

### R9 — `INV-11` / `INV-12` ID gaps cause ambiguous routing

The current `architecture/invariants.yaml` skips IDs 11 and 12 (verified
via grep). The briefing §8 references INV-12 for "contract semantic
violation" but no such invariant exists. Legacy code or docs may cite
INV-12 / INV-11 and resolve to nothing.

| Field | Value |
|---|---|
| **probability** | M |
| **impact** | M |
| **structural mitigation** | (a) Phase 1 audit of every invariant reference in `architecture/`, `src/`, `scripts/`, `tests/`, `docs/`; broken references logged; (b) ULTIMATE_DESIGN §2.1 records the policy decision: **leave the gaps** (compaction would rewrite history); (c) §8 of ULTIMATE_DESIGN cites the actual settlement-semantics invariants (INV-02, INV-14) instead of the broken INV-12 |
| **detection signal** | grep for `INV-11`, `INV-12` in repo returns hits; broken cross-reference list grows over time |
| **owner** | implementer (Phase 1 audit) |
| **sunset** | 2026-08-06 (one quarter post-Phase 1) |

### R10 — `[skip-invariant]` rate higher than briefing claimed

Briefing §2 cites ~50 `[skip-invariant]` commits / 60d (~1/day). Verified
count is **159 / 60d (~2.6/day)**. The agent population is already
trained to bypass; a sub-1/week target may be aggressive at cutover.

| Field | Value |
|---|---|
| **probability** | H (that the cutover target proves aggressive) |
| **impact** | M |
| **structural mitigation** | (a) Cutover GO requires **30 days of pre-cutover [skip-invariant] rate <2/week in shadow mode** — this is the *floor*; (b) post-cutover target remains <1/week, but the cutover does not block on it; (c) Phase 5 telemetry (CHARTER M1) measures the rate continuously so degradation is observable, not retrospective; (d) cutover sequence (CUTOVER_RUNBOOK §2) is gradual, so the bypass culture has time to recalibrate |
| **detection signal** | `git log --grep="skip-invariant" --since="<window>"` rate exceeds threshold |
| **owner** | critic (monthly review per CHARTER §8); implementer (Phase 5 telemetry wiring) |
| **sunset** | 2026-09-06 |

### R11 — Sum-of-files baseline (29,290) ≠ briefing baseline (39,800)

Reduction ratio (26× vs 19×) depends on the baseline framing. If the
team uses 26× as the success metric and reality delivers 19×, the
deliverable looks under-spec.

| Field | Value |
|---|---|
| **probability** | M |
| **impact** | L |
| **structural mitigation** | ULTIMATE_DESIGN §9.4 states **both** numbers transparently; the operator-decision in §0 row 4 cites both. The success metric is "topology infrastructure ≤1,500 LOC" (absolute, briefing §9), not a ratio. |
| **detection signal** | post-cutover communication uses 26× without 19× context, OR uses 19× as if it were a regression |
| **owner** | architect (ULTIMATE_DESIGN §9.4 wording) |
| **sunset** | 2026-08-06 (closes when cutover completes) |

## §3 Risk surfaces by phase

| Phase | Active risks |
|---|---|
| 0.A | R7 (replay fixture) |
| 0.B | R12 (existing-file disposition) |
| 0.C | R8 (decision fatigue) |
| 0.D | R10 (bypass culture observed) |
| 0.E | R1 (decorator coverage spike) |
| 0.F | R4 (shadow agreement) |
| 0.G | R2 (replay non-determinism) |
| 0.H | R8 closes; R4 + R7 + R10 floors evaluated |
| 1 | R9 (INV gap audit), R12 closes |
| 2 | R1 (full coverage push), R3 (phantom-type ramp begins via ABC split) |
| 3 | R6 (mid-drift check) |
| 4 | R3 (phantom-type fully ships), R2 (gate 4 promotes), R5 (lease service ships) |
| 5 | R6 (mid-drift check), R7 (replay re-run), R10 (cutover floor evaluated) |

## §4 Cross-risk dependencies

- R1 → R3: incomplete decorator coverage means phantom-type rollout
  cannot rely on capability metadata; mitigation is to land R1 fully
  before R3 reaches the submit boundary.
- R4 → R10: low shadow agreement means the new system cannot validate
  the bypass-rate floor; floor cannot be measured until shadow is
  trustworthy.
- R6 → all: anti-drift mechanisms guard the longevity of every other
  mitigation. R6 firing invalidates the durability of R1, R2, R3, R5.

## §5 Escalation policy

- Any risk row marked **H** that does not move to **M** within its
  sunset interval auto-escalates to operator at the next quarterly
  review.
- Any new risk discovered during a phase is added before phase
  exit; phases do not exit with newly observed risks unrecorded.
- Risk-register changes mid-phase emit `ritual_signal` so the audit
  trail captures the evolution.

## §6 Closed risks

### R12 — `topology_schema.yaml` and `inv_prototype.py` disposition undecided

These two files (537 LOC + 348 LOC) were not in briefing §2 inventory but
existed on disk and overlapped the redesign's stable layer. Silent retention
created duplicate authority.

| Field | Value |
|---|---|
| **probability** | M |
| **impact** | M |
| **structural mitigation** | ULTIMATE_DESIGN §9.1 lists both as removed in Phase 1 / Phase 3; Phase 1 audit retained both with documented blocker; Phase 4 Gate 1 carry-forward owns `topology_schema.yaml` deletion upon topology_doctor.py capability-schema refactor; `inv_prototype.py` deferred to Phase 4/5 migration |
| **detection signal** | Both files still present after Phase 3 close; or design document silently revised mid-phase |
| **owner** | architect (Phase 1 disposition decision); operator (signs ADR amendment if needed) |
| **sunset** | 2026-07-06 |
| **closed** | 2026-05-06 |
| **closure evidence** | evidence/r12_phase3_resolution.md; phase3_h_decision.md G-1/G-2; Phase 4.A DEV-1 delivery (topology_doctor_packet_prefill.py deleted, topology_doctor.py lines 1122-1211 removed); A-2 topology_schema.yaml deletion deferred to Phase 4 Gate 1 (13 active call sites — capability-schema refactor required first) |
| **closure note** | R12 closes on Phase 4.A delivery of DEV-1 (packet_prefill removal). topology_schema.yaml deletion is a Gate 1 precondition, not a blocker for R12 row closure — the disposition decision is made and the path is bounded. `inv_prototype.py` retention accepted per phase3_h_decision.md G-3 (F5+F10 antibodies load-bearing until Phase 4/5 migration). |
