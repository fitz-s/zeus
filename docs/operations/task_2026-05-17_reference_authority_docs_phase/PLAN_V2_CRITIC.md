# PLAN_V2_CRITIC — fresh-context opus audit-of-audit (2026-05-16)

**Verdict:** **REVISE_ROUND_2**

**Method:** independent re-execution of all 5 v1 amendments (AXIS A), exhaustive essence-discipline keyword sweep (AXIS B), cross-section consistency probe (AXIS C). Fresh opus subagent, did not re-execute v1 probes; verified v2's claimed fixes from primary sources (file:bytes, CLI `--help`, `wc -l`, `git ls-files`).

**Headline:** A2/A3/A4/A5 land cleanly. **A1 is a fresh CRITICAL — the v2 "fix" cites a command that does not run.** AXIS B essence discipline is **entirely absent** from PLAN v2 (zero keyword hits across `surgical`, `essence`, `atomic claim`, `smallest diff`, `stop iterating`, `done when`).

---

## AXIS A — Amendment compliance

| Amendment | Disposition | Evidence |
|---|---|---|
| **A1 (CRITICAL — phantom CLI subcommand)** | **NOT FIXED — regressed to a different broken command** | See A1 detail below. |
| A2 (CRITICAL — topology_doctor module form) | **PASS** | `grep -E "scripts/topology_doctor\.py"` PLAN.md → zero hits. All 9 invocations use `PYTHONPATH=. python -m scripts.topology_doctor`. Live test `python -m scripts.topology_doctor --help` exits 0. |
| A3 (MAJOR — reality_contracts to TIER 0B) | **PASS** | §3 TIER 0B has 10 rows (was 6); 4 reality_contracts paths present with LOC 205/89/48/46. TIER 3 no longer lists them (§3 line 109). §11 WAVE 2 shows "5-6 hr / 10 docs". §6 gate table reflects TIER 0B count. §4 PR-B row reflects "10 docs incl. 4 reality_contracts". §7 risk row added for contract-loader breakage. |
| A4 (MAJOR — actual `wc -l`) | **PASS** | Independent `wc -l` on 8 TIER 0A files → 1,895 LOC total, matches §3 to the line. TIER 0B (5,669) and TIER 0C (1,485) also match to the line. |
| A5 (MAJOR — 40 TIER 1 paths enumerated) | **PASS** | `git ls-files '**/AGENTS.md' '*AGENTS.md' \| wc -l` = 46. Subtract 5 obs + 1 archive = 40. Path enumeration in §3 lines 94-99 expands (via braces) to exactly 40 unique paths: 3+4+2+10+18+3 = 40. §5 WAVE 3 sizing references "40 AGENTS.md" + "≤5 parallel sonnet × 8 docs". §11 row 4 shows "44 docs" for TIER 0C+1 (= 4+40, consistent). |

### A1 — CRITICAL detail

PLAN v2 line 184 and line 150 cite (verbatim):

```
python -m maintenance_worker.cli.entry dry-run --config bindings/zeus/config.yaml
```

Empirical: `python -m maintenance_worker.cli.entry dry-run --config bindings/zeus/config.yaml` returns:

```
maintenance_worker: error: unrecognized arguments: --config bindings/zeus/config.yaml
```

Two distinct defects in the cited command:

1. **Argument position.** `--config PATH` is a **parent-level** option on `maintenance_worker.cli.entry`, NOT a subcommand option. Correct form: `python -m maintenance_worker.cli.entry --config PATH dry-run`. The PLAN places it after the subcommand, which argparse rejects (`dry-run --help` shows only `-h`).
2. **Wrong file format/path.** The CLI loads a **JSON** config file (`maintenance_worker/cli/entry.py` imports `json`; default per `--help` is `MAINTENANCE_WORKER_CONFIG` env or `./maintenance_worker_config.json`). `bindings/zeus/config.yaml` is a YAML file consumed by a different layer (Zeus binding for main + maintenance_worker bootstrap, not directly by `cli/entry.py`). Even after fixing argument position, running `python -m maintenance_worker.cli.entry --config bindings/zeus/config.yaml dry-run` returns `ERROR: config parse error: Expecting value: line 1 column 1 (char 0)` — argparse's `--config` slot expects a JSON file.

Net: the WAVE 2 "loader-test" for `bindings/zeus/config.yaml` is the **same class of error v1 critic flagged as A1** — a fabricated command that doesn't run. Repeating the failure mode after one critic round = high systemic risk.

**Compounding:** §4 PR-B gate row (line 130), §6 gate table (line 236), §7 row "Touching bindings/zeus/config.yaml" (line 247), §12 checklist item (line 331), Risk register row (line 254), and `feedback_grep_gate_before_contract_lock` discipline all converge on this command. WAVE 2 has **no operative loader-test for the Zeus binding** until A1' (the new A1 fix) is corrected.

**Required fix (A1'):**
- Replace every cite of `python -m maintenance_worker.cli.entry dry-run --config bindings/zeus/config.yaml` with a verified path. Two candidates:
  - (a) If the intent is to exercise the daemon CLI against a real maintenance config: locate the JSON file (`find . -name maintenance_worker_config.json`) and cite `python -m maintenance_worker.cli.entry --config <that_json_path> dry-run`. SCOUT 0B must include this locate step.
  - (b) If the intent is to validate `bindings/zeus/config.yaml` schema, the loader is **not** `maintenance_worker.cli.entry`; find the Zeus binding loader (`grep -rn "bindings/zeus/config.yaml" src/ maintenance_worker/`) and cite that. SCOUT 0B finds the real loader; the command goes into the Loader Command Table.

The plan's current strategy of "SCOUT 0B records exact loader-test per binding" was designed to AVOID this exact failure — but PLAN v2 pre-commits to a specific wrong command in WAVE 2 step 2, bypassing its own scout step.

---

## AXIS B — Essence discipline

Operator principle: "NOT blind delete-and-rewrite, NOT mass-add statements without distilling essence."

| Probe | Disposition | Evidence |
|---|---|---|
| 6. Surgical edit rule (smallest diff) | **ABSENT** | `grep -niE "surgical\|smallest\|delete-and-rewrite"` PLAN.md → 0 hits. |
| 7. Essence-over-bloat rule | **ABSENT** | `grep -niE "essence\|distill\|bloat\|mass-add\|append-only"` → 0 hits. |
| 8. Atomicity rule for authoritative statements | **ABSENT** | `grep -niE "atomic claim\|atomic statement\|one claim\|one fact\|verifiable in isolation"` → 0 hits. |
| 9. Per-statement provenance (REPLACES / WHY / WHERE) | **PARTIAL** | §5 WAVE 1 step 1 mandates commit-header `AMENDMENT: <doc>::<id> [REASON: ...]` (REPLACES + WHY). WHERE-verified is implicit in FCI4 §14 but not contractually required per-statement. TIER 0B + TIER 0C+1 (WAVE 2/3) have NO equivalent per-statement commit-header rule — only WAVE 1 enjoys this. |
| 10. Stop-condition definition | **ABSENT** | `grep -niE "stop iterating\|done when\|exit criteria\|finish when"` → 0 hits. Gate tables (§6) describe wave-exit but not per-doc stop. Operator-stated stop ("done = all citations verifiable + all claims sourced + no orphan refs") not codified. |

**Net AXIS B:** the essence discipline operator named is the binding operating rule the plan was supposed to embed. It is absent. Without it, executor workers default to whichever edit pattern minimizes apparent risk — empirically that's append-and-explain (bloat) or wholesale rewrite (churn). Plan v2 has gate mechanisms (FCI4, opus critic, scout audit-of-audit) but no surface-level edit discipline contract.

The 4-for-4 opus-critic-on-SCAFFOLD ROI memory says architectural plans without explicit anti-bloat / atomicity rules systematically ship SEV-1 defects. Plan v2 currently has the critic but no rule for the critic to enforce.

---

## AXIS C — Pattern callouts

- **Mechanical-fold-only risk for A3 — CLEAR.** A3 promotion did update §3, §4, §5, §6, §7, §11 consistently. Spot-check passes.
- **LOC verification (A4) — CLEAR.** Re-running `wc -l` on 22 TIER 0 paths matches PLAN v2 to the line. Planner did not repeat the FCI4 LOC error mode.
- **A1 mechanical-fold failure.** The v2 author swapped `validate` → `dry-run` but did not run the resulting command. `feedback_one_failed_test_is_not_a_diagnosis` was applied; `feedback_verify_paths_before_prompts` was NOT applied to the FIXED command. Same class of error as v1.
- **`paris_station_resolution_2026-05-01.yaml` carry-forward (§9, line 283).** Routes through SCOUT 0B / WAVE 2 with no specific intervention contract. Acceptable to defer to SCOUT findings, but flag for visibility.
- **Per-statement provenance (probe 9) PARTIAL — only WAVE 1 enforces it.** TIER 0B (loader-coupled, highest blast radius) lacks the commit-header rule. Inconsistent enforcement across waves.

---

## Concrete amendments for v3

### V3-A (CRITICAL) — fix A1' contradiction in `bindings/zeus/config.yaml` loader-test

Two-step:

1. **Defer the command to SCOUT 0B output, do not pre-commit in §5 WAVE 2.** Rewrite line 184 to: `bindings/zeus/config.yaml` → SCOUT 0B identifies the exact loader and records command in Loader Command Table; DO NOT use `maintenance_worker.cli.entry --config <yaml>` (verified to fail: argparse rejects YAML at `--config` slot expecting JSON).
2. **Update §4 PR-B gate row, §6, §7, §12 to remove the specific `dry-run --config bindings/zeus/config.yaml` cite.** Replace with "SCOUT-0B-determined loader-test for Zeus binding."
3. **§12 checklist add:** `[ ] locate maintenance_worker_config.json or document its absence` (NB: `find . -name maintenance_worker_config.json` returns zero results in current worktree).

### V3-B (MAJOR) — embed essence discipline as §-level binding rules

Add a new section between §8 (anti-patterns) and §9 (carry-forward), titled "§8.5 Edit discipline contract":

- **Rule 1 — Surgical:** Every doc edit must be the smallest diff that fixes the surfaced drift. No reformatting, no reordering, no re-wording unless drift requires it. Diff line count ≤ 3× lines of cited drift; otherwise rewrite is justified per-commit.
- **Rule 2 — Atomic:** Every CHANGED authoritative statement is one claim, one fact, verifiable in isolation. Compound statements (`X AND Y AND Z`) must be split.
- **Rule 3 — Provenance triple:** Every CHANGED authoritative statement carries (a) REPLACES: <prior text or "new claim">, (b) WHY: <evidence current text is wrong / new>, (c) VERIFIED-AT: <file::symbol or command>. Stored in commit message footer, NOT inline in the doc (avoids bloat).
- **Rule 4 — No mass-add:** Adding >10 LOC to a doc requires explicit justification in commit body (what insight, why not distilled to ≤5 LOC).
- **Rule 5 — Stop-condition:** A doc is "done" when (a) all citations verifiable within 10-min FCI4 window, (b) all claims sourced via Rule 3 triple, (c) no orphan cross-refs to deleted symbols. WAVE-close gate checks all three per touched doc.

Mention rule numbers explicitly in WAVE 1/2/3 critic briefs ("probe whether Rules 1-5 hold per edit").

### V3-C (MAJOR) — apply Rule 3 (provenance) to WAVE 2 and WAVE 3, not WAVE 1 only

Currently §5 WAVE 1 step 1 has the only `AMENDMENT:` commit-header rule. Extend to WAVE 2 (`LOADER-COUPLED:` prefix) and WAVE 3 (`AGENTS-NAV:` prefix). Per-wave critic probes the prefix is present.

### V3-D (MINOR) — §12 checklist self-audit hardening

Item `[x] python -m maintenance_worker.cli.entry --help` confirmed surface — that's necessary but NOT sufficient. Add `[ ] every cited dry-run/loader-test command actually executes to success (not just --help)`. This catches the A1 → A1' regression class.

---

## Per-axis disposition table

| Axis | Disposition | Blocker for execute |
|---|---|---|
| A1 phantom CLI | **REGRESSION (different broken cmd)** | YES — WAVE 2 has no operative loader-test for `bindings/zeus/config.yaml` |
| A2 topology_doctor | PASS | no |
| A3 reality_contracts | PASS | no |
| A4 LOC | PASS | no |
| A5 TIER 1 enumeration | PASS | no |
| B6 surgical | ABSENT | softer — causes drift on execute |
| B7 essence-over-bloat | ABSENT | softer |
| B8 atomicity | ABSENT | softer |
| B9 per-statement provenance | PARTIAL (WAVE 1 only) | softer |
| B10 stop-condition | ABSENT | softer |

**Verdict gate:** A1 alone forces REVISE_ROUND_2 (verified-failing command is exactly the class v1 critic flagged). Compounded with absent essence discipline, v3 needs both fixes before WAVE 0 dispatch.

---

## Recommended v3 fold

- Fix A1' per V3-A (defer to SCOUT 0B + remove broken cmd cite from §4/§6/§7/§12).
- Add §8.5 Edit discipline contract per V3-B.
- Extend provenance triple to WAVE 2/3 per V3-C.
- Harden §12 checklist per V3-D.

**Time estimate to v3:** 30-40 min. v3 critic dispatch: fresh haiku, brief ≤20 lines, scoped only to V3-A through V3-D delta.

---

## Sign-off

- Audit-of-audit per `feedback_audit_of_audit_antibody_recursive` baseline (50% scout self-error). v2 author claimed pre-commit self-verification "all 4 PASS"; this re-critic finds 1 of 4 (A1 maintenance_worker dry-run) failed empirically — 25% self-error rate, consistent with baseline.
- v1 critic verdict: REVISE (5 amendments). v2 folded all 5 but A1's fix introduced a different defect of the same class.
- v2 critic verdict: **REVISE_ROUND_2** — A1' regression + AXIS B absence.
