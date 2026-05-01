# F12 Deep Audit — INV-23 ↔ NC-17 Anchor Decision

- **Created**: 2026-05-01
- **Last reused/audited**: 2026-05-01
- **Authority basis**: ultrareview-25 finding F12 (operator-deferred since 2026-04-26); INV-23 + NC-17 in architecture YAML; commit 3b627eca (re-anchor) + commit 7743f692 (orphaned P3-B cleanup); P1-3 audit at `docs/operations/repo_review_2026-05-01/P1_3_TRUTH_AUTHORITY_AUDIT.md`
- **Auditor**: Architect agent (READ-ONLY)
- **Method**: Grep-verified file:line citations within last 10 minutes; YAML statements quoted verbatim; commit-history reconstruction.

---

## TL;DR for the operator

**F12 has already been authored as a fix on a parallel commit chain that did NOT make it into HEAD.** The P3-B commit `7743f692 ultrareview-25 P3-B: inv_prototype idempotency + INV-23↔NC-17 cross-ref cleanup (F5/F10/F12)` exists on this branch but is not an ancestor of HEAD `0a33cbc1`. The on-disk YAML still has the cross-reference intact. The decision is **not** "what should F12 be" — it is **"do you want the already-authored P3-B fix, or a different one"**.

**Recommendation: cherry-pick `7743f692` (Option A) — minimal diff, removes the inert cross-reference both directions, preserves the theme connection via comment annotations, and tests still pass because the manifest tests only assert `enforced_by` is non-empty.**

---

## 1. What is F12 exactly?

### Original framing in `docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md`

Quoted verbatim from the table at line 25:

> | F12 | INV-23 ↔ NC-17 cross-reference describes "two unrelated rules" | `architecture/invariants.yaml:233` | **ARGUABLE — operator-deferred** (commit `3b627eca` 2026-04-26 deliberately re-anchored INV-23 from NC-16 to NC-17 under "no false certainty" theme; reviewer disagrees at mechanism layer) |

And from PLAN.md:77-79 (the dedicated F12 section, verbatim):

> ### F12 (operator-deferred)
>
> INV-23 ↔ NC-17 anchor: commit `3b627eca` deliberately set this anchor under "no false certainty" theme; reviewer claims they're "two unrelated rules" at mechanism layer. **Operator must rule** before any change. Plan does not mutate this without explicit go.

And PLAN.md:191 (verbatim):

> Before any packet: F12 INV-23↔NC-17 — affirm the 2026-04-26 anchor stays, or accept the reviewer's "unrelated rules" framing and re-anchor / re-statement.

### What the finding points at

- `architecture/invariants.yaml:233-241` (INV-23 declaration with `enforced_by.negative_constraints: [NC-17]` at line 238).
- `architecture/negative_constraints.yaml:109-115` (NC-17 declaration with `invariants: [INV-23]` at line 111).
- The PR-25 ultrareview reviewer (cloud session `019BMquSXQVXGafqBt6jWZ4E`) flagged this bidirectional cite as describing "two unrelated rules."

### Authoring history

The cite was deliberately set by commit `3b627eca5261642c2183f8035201c9e1a530179e` (2026-04-26 03:21:34 -0400, `Close P0 acceptance gates: semgrep rule + LOW/TRIVIAL polish`). The commit message explicitly says (verbatim from `git show 3b627eca`):

> INV-23 anchor correction:
> - Was incorrectly anchored to NC-16 (gateway). Re-anchor to NC-17
>   (decorative labels) — both invariants live under the broader "no false
>   certainty" theme; NC-16 is unrelated.

So the operator at 2026-04-26 was correcting an **earlier wrong anchor (NC-16)** and parking INV-23 next to NC-17 as the closest "no false certainty" sibling. The 2026-05-01 ultrareview disputes whether NC-17 is actually closer than NC-16.

---

## 2. What is INV-23 today?

Verbatim from `architecture/invariants.yaml:233-241` (grep-verified within the last 10 minutes):

```yaml
  - id: INV-23
    zones: [K2_runtime]
    statement: A degraded portfolio projection must never export authority="VERIFIED"; the export must use a distinct non-VERIFIED label.
    why: A degraded loader signals that DB authority was unreachable or the projection is non-canonical. Stamping that export as VERIFIED tells operator surfaces and downstream consumers that authority survived loss, which is false. Authority labels must reflect actual provenance, not load-success.
    enforced_by:
      negative_constraints: [NC-17]
      tests:
        - tests/test_p0_hardening.py::TestR1DegradedExportNeverVerified::test_truth_authority_map_does_not_collapse_degraded_to_verified
        - tests/test_p0_hardening.py::TestR1DegradedExportNeverVerified::test_save_portfolio_degraded_does_not_export_verified
```

### Precise scope

- **Domain**: portfolio-export `truth["authority"]` field on disk (positions JSON, status JSON).
- **Subject**: producer-side label semantics — what string a degraded loader is allowed to emit.
- **Mechanism**: `_TRUTH_AUTHORITY_MAP` at `src/state/portfolio.py:71-75` (working tree) plus the call at `src/state/portfolio.py:1457`.
- **Tests** anchoring it: `tests/test_p0_hardening.py:141` (map shape) and `:156` (end-to-end save).

### What INV-23 does NOT cover

- Any wire on `ExecutionIntent` (NC-17's domain).
- DB CHECK-constraint authority columns (per `P1_3_TRUTH_AUTHORITY_AUDIT.md` §5: those enumerate a 3-set without DEGRADED_PROJECTION).
- The other six "authority" grammars in the codebase (B/C/D/E/F/G per `P1_3_TRUTH_AUTHORITY_AUDIT.md` §0).
- Consumer-side branching on the JSON authority field (per `P1_3_TRUTH_AUTHORITY_AUDIT.md` §3.2: zero production consumers read `truth["authority"]` from JSON today).

---

## 3. What is NC-17?

Verbatim from `architecture/negative_constraints.yaml:109-115`:

```yaml
  - id: NC-17
    statement: ExecutionIntent must not carry decorative capability labels (slice_policy, reprice_policy, liquidity_guard, or any future label) without an enforcing executor branch that exhibits real capability behavior. Logging-only branches do not count.
    invariants: [INV-23]
    enforced_by:
      tests:
        - tests/test_p0_hardening.py::TestR4ExecutionIntentNoDecorativeLabels::test_execution_intent_has_no_decorative_fields
        - tests/test_p0_hardening.py::TestR4ExecutionIntentNoDecorativeLabels::test_executor_does_not_branch_on_decorative_labels
```

### Precise scope

- **Domain**: `src/contracts/execution_intent.py` dataclass fields and `src/execution/executor.py` branches.
- **Subject**: capability labels (`slice_policy`, `reprice_policy`, `liquidity_guard`) — fields that promise behavior the executor doesn't actually implement.
- **Mechanism**: introspection assertion at `tests/test_p0_hardening.py:203` checks `ExecutionIntent` dataclass field names; source-text assertion at `:216` checks `executor.py` doesn't reference `intent.slice_policy` etc.
- **Forbidden vocab list** (verbatim from `tests/test_p0_hardening.py:189`): `DECORATIVE_LABEL_FIELDS = ("slice_policy", "reprice_policy", "liquidity_guard")`.

### Precise forbidden action

NC-17 forbids the *literary* shape: declaring a capability field (a string label that suggests "this intent will be sliced/repriced/liquidity-guarded") without an executor branch that *acts* on that field beyond a `logger.info(...)` call. The K-A category-impossibility move was to **delete** the fields entirely (commit `6b48652d Land P0 hardening K1+K3: degraded label + capability removal`).

---

## 4. What does "anchor" mean in this context?

Reading PLAN.md:25, :77-79, :191 carefully, "anchor" means the **bidirectional cite in the YAML grammar**:

- `INV-23.enforced_by.negative_constraints: [NC-17]` (line 238 of `architecture/invariants.yaml`)
- `NC-17.invariants: [INV-23]` (line 111 of `architecture/negative_constraints.yaml`)

The **load-bearing claim** is INV-23 (the *positive* invariant: "degraded export must not stamp VERIFIED"). The *consequence* is NC-17 (the *negative* constraint: "no decorative labels"). The bidirectional cite says **NC-17 helps enforce INV-23**.

The reviewer's complaint is that **the cite mis-describes the enforcement relationship**:
- INV-23 is enforced by **TestR1DegradedExportNeverVerified** (`test_p0_hardening.py:141, :156`) — which exercises `_TRUTH_AUTHORITY_MAP` and `save_portfolio()`, both in `src/state/portfolio.py`.
- NC-17 is enforced by **TestR4ExecutionIntentNoDecorativeLabels** (`test_p0_hardening.py:203, :216`) — which inspects `ExecutionIntent` dataclass fields and `executor.py` branches.

These are mechanism-disjoint test suites that cover different code surfaces. NC-17's tests do not exercise INV-23's mechanism, and vice versa. The shared word is "no false certainty" — a *thematic* link, not a *mechanism* link.

So: the load-bearing direction is `INV-23 → NC-17`, the bidirectional cite implies "NC-17 is part of how INV-23 is enforced," and the reviewer says that implication is false because the test surfaces are disjoint.

---

## 5. Candidate anchoring decisions

Based on PLAN.md and the predecessor commit `3b627eca`, four framings are coherent:

### Option A — Remove the inert cross-ref both directions (P3-B, ALREADY AUTHORED)

**Already authored on commit `7743f692` (sibling commit chain on this branch, NOT ancestor of HEAD).** Reads (verbatim from `git show 7743f692 -- architecture/invariants.yaml`):

```yaml
  - id: INV-23
    zones: [K2_runtime]
    statement: A degraded portfolio projection must never export authority="VERIFIED"; the export must use a distinct non-VERIFIED label.
    why: A degraded loader signals that DB authority was unreachable or the projection is non-canonical. Stamping that export as VERIFIED tells operator surfaces and downstream consumers that authority survived loss, which is false. Authority labels must reflect actual provenance, not load-success.
    # ultrareview-25 F12: NC-17 was previously listed under enforced_by but
    # mechanically does not enforce INV-23 — NC-17 governs ExecutionIntent
    # decorative-capability labels (executor branches), whereas INV-23 governs
    # portfolio-export authority labels (state/portfolio.py truth_authority_map).
    # Both share a "no false certainty" theme but are mechanism-disjoint, so
    # the enforced_by.negative_constraints cite was removed 2026-05-01 to keep
    # that field reserved for actual mechanism enforcement. See commit 3b627eca
    # for the original anchor and PLAN.md F12 for the cleanup rationale.
    enforced_by:
      tests:
        - tests/test_p0_hardening.py::TestR1DegradedExportNeverVerified::test_truth_authority_map_does_not_collapse_degraded_to_verified
        - tests/test_p0_hardening.py::TestR1DegradedExportNeverVerified::test_save_portfolio_degraded_does_not_export_verified
```

And NC-17 loses its `invariants: [INV-23]` line, replaced with a parallel comment.

**Scope**: 2 files (`architecture/invariants.yaml`, `architecture/negative_constraints.yaml`); 14-line YAML diff total.
**Testable consequence**: Manifest tests at `tests/test_p0_hardening.py:54-60` (INV-23 registered) and `:119-125` (NC-17 registered) only check `enforced_by` is non-empty — both still pass after the cite removal because `tests:` array stays.

### Option B — Keep the cross-ref + restate the why field

Reframe the relationship as thematic, not mechanism. Edit `INV-23.why` to acknowledge the NC-17 link as a "thematic sibling under the no-false-certainty principle, not a mechanism-share."

**Scope**: 2 files; ~5-line YAML diff (one extended `why` field, optional symmetric note on NC-17).
**Testable consequence**: identical — manifest tests still pass; the change is descriptive only.

### Option C — Re-anchor INV-23 to a different (or new) NC

INV-23's mechanism-share would be with a constraint that governs **portfolio-export authority labels**. No such NC exists today. The closest neighbor is NC-15 (`make_family_id` canonical grammar at `architecture/negative_constraints.yaml:93-100`), which is also unrelated. So Option C requires **creating a new NC** — call it `NC-24: Truth-file authority labels must use the closed TruthAuthority enum` — anchored to `tests/test_truth_authority_enum.py` (working tree, untracked).

**Scope**: 3 files (`invariants.yaml`, `negative_constraints.yaml`, plus the new test in `test_p0_hardening.py` to register NC-24 per the manifest pattern at line 119); ~25-line YAML + ~10-line test diff.
**Testable consequence**: A new manifest assertion `test_nc24_truth_authority_enum_registered` lands; existing tests untouched.

### Option D — Keep the existing cite and do nothing

**Scope**: 0 files.
**Testable consequence**: identical (the cite is inert at the test layer today).

---

## 6. What changed today (2026-05-01) that may affect this decision?

P1-3 already landed in the working tree (NOT yet committed — `git status` shows `?? src/types/truth_authority.py` and `?? tests/test_truth_authority_enum.py`). The new module is `src/types/truth_authority.py:64-86` (verbatim):

```python
class TruthAuthority(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    QUARANTINED = "QUARANTINED"
    DEGRADED_PROJECTION = "DEGRADED_PROJECTION"
```

Plus helpers `is_authoritative()` (line 108) and `requires_human_review()` (line 131), each of which raises `TypeError` if a bare string is passed.

### Effect on F12 candidate options

- **Option A — STRENGTHENED.** Removing the inert NC-17 cite is now obviously correct because the *real* enforcement of INV-23 has shifted from "NC-17 thematic sibling" to "the closed TruthAuthority enum." The closed-enum mechanism is what actually makes "degraded → VERIFIED" unconstructable: `_TRUTH_AUTHORITY_MAP` now maps `"degraded" → TruthAuthority.DEGRADED_PROJECTION` (`src/state/portfolio.py:71-75`), and the relationship test in `tests/test_truth_authority_enum.py` locks the 4-member set + producer-side closure. A YAML cite to NC-17 is the wrong location to record that mechanism.
- **Option B — WEAKENED.** Restating the `why` to call out NC-17 as a "thematic sibling" duplicates what the (about-to-land) `TruthAuthority(StrEnum)` already does at the type layer. The YAML field becomes a worse-than-useless decoration.
- **Option C — NEWLY VIABLE.** A new `NC-24: Truth-file authority labels must use the closed TruthAuthority enum` is now possible because the mechanism exists in code. It would be the *correct* mechanism-anchor for INV-23.
- **Option D — STRICTLY WORSE than A or C.** Doing nothing leaves a YAML cite that was already wrong, and is now more obviously wrong because the real enforcement lives elsewhere.

So P1-3's landing collapses Option B and creates Option C as a new viable framing. Option D is dominated.

---

## 7. Why has it been deferred a week?

Reconstructed from on-disk evidence:

1. **The 2026-04-26 commit `3b627eca` was an emergency re-anchor**, not an architectural decision: it fixed an even worse mis-anchor (INV-23 → NC-16 gateway). The operator picked NC-17 as the "least wrong" neighbor under time pressure, with a comment in the commit body acknowledging "both ... live under the broader 'no false certainty' theme."
2. **The 2026-05-01 ultrareview disputed it** within a week. The reviewer's framing ("two unrelated rules") is true at the mechanism layer (per `P1_3_TRUTH_AUTHORITY_AUDIT.md` §0: 7 distinct authority grammars; INV-23 and NC-17 govern wholly different ones).
3. **PLAN.md:77-79 explicitly parks F12 as operator-deferred** because the planner did not have authority to resolve a doc-vs-doc dispute that touches *both* `architecture/invariants.yaml` (T3 governance, ARCH_PLAN_EVIDENCE-gated) and `architecture/negative_constraints.yaml` (same).
4. **A parallel commit chain authored the fix anyway** — commit `7743f692` (2026-05-01 08:20:23 -0500, P3-B) implements Option A (with explanatory comments) and was admitted under the topology profile `pricing semantics authority cutover`. That chain (`355bcfcb → 21cff1ec → 4e89d00f → 7743f692 → 92bd0aaa → 51f4c686 → a5e5c779`) **diverged** from the live-trading working chain at `9eb45d65` and never got merged. The branch HEAD `0a33cbc1` is on the live-trading chain (`355bcfcb → c701c8aa → 681a00b0 → 13cbf68c → 504dc6f0 → 0a33cbc1`).
5. **The deferral persists** because: (a) the operator never explicitly approved the framing in `7743f692`'s message; (b) the live-trading chain advanced past the merge point without picking up P3-B; (c) PLAN.md:191 still asks for an operator ruling.

So the block is **organizational, not informational**: the fix exists in commit form, was self-admitted, and passed pre-commit baseline at 220 (per the commit message), but the operator never flagged it as approved, and the parallel branch advanced without absorbing it.

---

## 8. Consequences of each candidate option

### Option A (cherry-pick `7743f692` or re-author the same diff)

| Surface | Cost |
|---|---|
| YAML edits | 14-line diff across `architecture/invariants.yaml` (drop `negative_constraints: [NC-17]`, add 8-line comment) and `architecture/negative_constraints.yaml` (drop `invariants: [INV-23]`, add 5-line comment). |
| Code/test edits | Zero. Manifest tests at `tests/test_p0_hardening.py:54-60`, `:119-125` only require `enforced_by` to be non-empty; the `tests:` array remains. |
| Consumer rewrite scope | Zero. Per `P1_3_TRUTH_AUTHORITY_AUDIT.md` §3.2: no production consumer reads `truth["authority"]` from JSON. NC-17 cite removal does not touch any code path. |
| Live-trading regression risk | **None.** The cite is inert. Removal is structurally invisible to runtime. Pre-commit baseline of 220 already verified by `7743f692`. |
| Operator effort | < 5 min: `git cherry-pick 7743f692` (which also pulls F5+F10 inv_prototype idempotency fix as a free bonus), or hand-edit the 14-line YAML diff. |

### Option B (keep the cite, restate why)

| Surface | Cost |
|---|---|
| YAML edits | ~5-line `why` field extension. |
| Code/test edits | Zero. |
| Consumer rewrite scope | Zero. |
| Live-trading regression risk | None (text-only). |
| Operator effort | < 10 min. |
| Hidden cost | Documents a mechanism link that doesn't exist. The next reviewer will re-flag it. |

### Option C (new NC-24)

| Surface | Cost |
|---|---|
| YAML edits | New NC-24 in `negative_constraints.yaml` (~10 lines); replace `[NC-17]` cite with `[NC-24]` in INV-23. |
| Code/test edits | New manifest registration test `test_nc24_truth_authority_enum_registered` in `tests/test_p0_hardening.py` (~10 lines); the existing antibody at `tests/test_truth_authority_enum.py` (working tree) becomes the enforcement target. |
| Consumer rewrite scope | Zero today. Forward-looking: future consumer-side `match` statements (per `P1_3_TRUTH_AUTHORITY_AUDIT.md` §3.4) would gain a single named NC to cite. |
| Live-trading regression risk | None (additive YAML + additive test). |
| Operator effort | ~30 min (write NC-24 statement, register test, decide bare-strings ban scope, document interaction with grammar B/C/D/E/F/G per `P1_3_TRUTH_AUTHORITY_AUDIT.md` §0). |
| Hidden cost | Creates a new NC the operator must defend in future reviews. Adds another entry to the `negative_constraints.yaml` growing list. |

### Option D (do nothing)

| Surface | Cost |
|---|---|
| YAML edits | Zero. |
| Code/test edits | Zero. |
| Consumer rewrite scope | Zero. |
| Live-trading regression risk | None today. |
| Operator effort | Zero today. |
| Hidden cost | The 2026-05-01 review *will* fire again on next ultrareview run. The cite-as-mechanism dishonesty propagates into operator memory and into agent training context. The "no false certainty" theme is itself violated by leaving a false-certainty cite in place. |

---

## 9. Recommendation

**Option A. Cherry-pick `7743f692` from the orphaned P3-B chain into HEAD.**

### Why

1. **The work is already authored.** `7743f692` is on the branch (just not in HEAD's lineage). Re-authoring would be wasted effort.
2. **It is the minimum behavior-neutral diff.** YAML cite removed, comment annotation preserves the thematic connection, manifest tests still pass, zero code touched.
3. **It is consistent with P1-3.** The TruthAuthority(StrEnum) work already moved INV-23's enforcement out of the YAML cross-reference plane and into the type plane. The YAML cite to NC-17 is now stranded — Option A acknowledges that.
4. **It is consistent with operator commit `3b627eca`'s spirit.** The 2026-04-26 commit message says "both invariants live under the broader 'no false certainty' theme." Option A preserves that theme connection in comments while fixing the false-certainty cite.
5. **Option C is better forward-looking** but requires more operator time (~30 min vs <5 min) and adds a new NC. **Best executed as a follow-up packet** after Option A lands, when P1-3 itself is committed and the TruthAuthority enum is the established authority anchor.

### Smallest concrete commit (operator can apply in < 5 minutes)

```bash
# From branch HEAD 0a33cbc1:
git cherry-pick 7743f692
# Resolve any merge conflicts (likely none since the parallel chain
# diverged before HEAD touched these files).
# Update PLAN.md F12 status from "operator-deferred" to "APPLIED".
```

Or, if cherry-pick is undesirable due to the F5+F10 (inv_prototype idempotency) work that 7743f692 also carries, hand-author the YAML diff alone:

**`architecture/invariants.yaml`** — at line 238, replace:
```yaml
    enforced_by:
      negative_constraints: [NC-17]
      tests:
```
with (verbatim from `7743f692`):
```yaml
    # ultrareview-25 F12: NC-17 was previously listed under enforced_by but
    # mechanically does not enforce INV-23 — NC-17 governs ExecutionIntent
    # decorative-capability labels (executor branches), whereas INV-23 governs
    # portfolio-export authority labels (state/portfolio.py truth_authority_map).
    # Both share a "no false certainty" theme but are mechanism-disjoint, so
    # the enforced_by.negative_constraints cite was removed 2026-05-01 to keep
    # that field reserved for actual mechanism enforcement. See commit 3b627eca
    # for the original anchor and PLAN.md F12 for the cleanup rationale.
    enforced_by:
      tests:
```

**`architecture/negative_constraints.yaml`** — at line 111, replace:
```yaml
    invariants: [INV-23]
```
with:
```yaml
    # ultrareview-25 F12: INV-23 was previously listed under invariants but the
    # mechanism is disjoint (NC-17 = ExecutionIntent capability labels;
    # INV-23 = portfolio-export authority labels). They share a "no false
    # certainty" theme only — the inert cross-reference was removed to keep
    # this field reserved for actual mechanism enforcement.
```

**Verification gate**: `python3 -m pytest tests/test_p0_hardening.py::test_inv23_degraded_export_law_registered tests/test_p0_hardening.py::test_nc17_no_decorative_capability_labels_registered -q` (both tests check `enforced_by` non-empty; both still pass after Option A lands).

---

## 10. Is F12 itself wrong-framed?

**Mostly correct, slightly drifted.** The 2026-04-26 framing was "INV-23 ↔ NC-17 anchor" because at that moment the operator's discrete decision was *which NC* INV-23 should point at. After P1-3 (today), that framing has drifted because **the question is no longer "which NC" but "should the YAML cross-reference plane carry mechanism information at all when the type system already does it?"**

### Re-frame

The right way to state the problem on 2026-05-01 is:

> **F12-revised**: INV-23 is now enforced by the closed `TruthAuthority(StrEnum)` type at `src/types/truth_authority.py` plus the relationship test at `tests/test_truth_authority_enum.py`. The legacy YAML cross-reference to NC-17 (introduced under time pressure on 2026-04-26 to fix an even-worse mis-anchor to NC-16) is now a stranded cite that should be removed. Decision: confirm the type-layer enforcement is canonical and remove the stranded cite — Option A from the audit.

### Tradeoff

The original framing ("which NC") is concrete and operator-actionable in 5 min. The re-framed version is more honest about today's state but drags the operator into reviewing P1-3 before they can rule on F12. **For the live-trading branch's pre-merge gate, the original framing + Option A is the right choice** — it closes F12 without coupling to P1-3's commit status, and the comment annotation (which references commit `3b627eca` and PLAN.md F12) carries the re-framed narrative for whoever audits next.

---

## Consensus Addendum

- **Antithesis (steelman for keeping the cite — Option D)**: "The cross-reference encodes a real semantic kinship. Both invariants are about the system avoiding false confidence claims. Stripping the YAML cite to mechanism-only purity throws away a thematic-coupling signal that has propaganda value for new agents reading the architecture for the first time. Comments are not searchable the same way YAML fields are." **Counter**: searchable propaganda value is real; commit `7743f692`'s comment annotations preserve it; the manifest tests (`test_inv23_degraded_export_law_registered`, `test_nc17_no_decorative_capability_labels_registered`) check `enforced_by` non-empty, not specific cross-refs, so the propaganda value lives equally well in comments as in YAML fields.
- **Tradeoff tension**: Option C is forward-better (creates a real mechanism-anchor NC-24 grounded in TruthAuthority enum), but couples F12 closure to P1-3's commit status. Option A is now-better (zero coupling, already-authored). The tension is: "fix it twice" (A now, C later when P1-3 is committed) versus "fix it once" (C now). Recommendation favors "fix it twice" because the live-trading branch should not block on P1-3's commit ordering.
- **Synthesis**: Option A now (in this commit cycle, < 5 min); Option C scheduled as a P2 follow-up after P1-3 is properly committed and the new NC-24 has a stable enforcement target.
- **Principle violations (deliberate-mode flag)**: Option D **violates Fitz Constraint #2 ("Translation loss is thermodynamic")** — leaving a stranded cite encodes false design intent into the YAML grammar and degrades next-session translation fidelity. Option B **violates "best-for-architecture means no false claims"** (per commit `3b627eca`'s own theme) — restating the `why` to call NC-17 a sibling does not make NC-17 a mechanism. Severity: LOW (text-only) but compounding.

---

## References

- `architecture/invariants.yaml:233-241` — INV-23 declaration with the disputed cite at :238.
- `architecture/negative_constraints.yaml:109-115` — NC-17 declaration with the symmetric cite at :111.
- `tests/test_p0_hardening.py:52-60` — `test_inv23_degraded_export_law_registered` (manifest assertion).
- `tests/test_p0_hardening.py:119-125` — `test_nc17_no_decorative_capability_labels_registered` (manifest assertion).
- `tests/test_p0_hardening.py:133-181` — `TestR1DegradedExportNeverVerified` (the actual INV-23 enforcement).
- `tests/test_p0_hardening.py:192-224` — `TestR4ExecutionIntentNoDecorativeLabels` (the actual NC-17 enforcement).
- `tests/test_p0_hardening.py:189` — `DECORATIVE_LABEL_FIELDS = ("slice_policy", "reprice_policy", "liquidity_guard")`.
- `src/state/portfolio.py:46` — `from src.types.truth_authority import TruthAuthority` (working tree, uncommitted).
- `src/state/portfolio.py:71-75` — `_TRUTH_AUTHORITY_MAP` with TruthAuthority enum members (working tree).
- `src/state/portfolio.py:1457` — `_TRUTH_AUTHORITY_MAP.get(state.authority, TruthAuthority.UNVERIFIED)` (working tree).
- `src/types/truth_authority.py:64-86` — `TruthAuthority(StrEnum)` declaration (UNTRACKED working tree file).
- `src/types/truth_authority.py:108-128` — `is_authoritative()` helper.
- `src/types/truth_authority.py:131-152` — `requires_human_review()` helper.
- `tests/test_truth_authority_enum.py` — UNTRACKED working tree test file (P1-3 antibodies).
- `docs/operations/repo_review_2026-05-01/P1_3_TRUTH_AUTHORITY_AUDIT.md:31-79` — 7-grammar authority surface inventory.
- `docs/operations/repo_review_2026-05-01/P1_3_TRUTH_AUTHORITY_AUDIT.md:140-202` — `truth["authority"]` consumer survey (zero production consumers).
- `docs/operations/repo_review_2026-05-01/P1_3_TRUTH_AUTHORITY_AUDIT.md:347-486` — Option (a) MINIMAL recommendation underlying the on-disk P1-3 working-tree state.
- `docs/operations/repo_review_2026-05-01/SYNTHESIS.md:111` — current state of P1-10 = F12.
- `docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md:25` — F12 finding row.
- `docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md:77-79` — F12 deferred section.
- `docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md:191` — F12 unblock question for operator.
- Commit `3b627eca5261642c2183f8035201c9e1a530179e` (2026-04-26 03:21:34 -0400) — original re-anchor of INV-23 from NC-16 to NC-17.
- Commit `7743f6927d55a7c78922e91a72c156de235d92a1` (2026-05-01 08:20:23 -0500) — `ultrareview-25 P3-B: inv_prototype idempotency + INV-23↔NC-17 cross-ref cleanup (F5/F10/F12)`. ON branch but NOT ancestor of HEAD `0a33cbc1`.
- Commit `a5e5c779e6c8e74876f5bc0c948e784a4ffaa65d` (2026-05-01 08:29:53 -0500) — `ultrareview-25 PLAN.md final status update`. Sibling commit on the same orphaned chain.

