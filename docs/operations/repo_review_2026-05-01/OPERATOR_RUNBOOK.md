# Operator Runbook — Remaining P0/P1 Items

**Date**: 2026-05-01 (rev 2 — branch-fork discovery + B2/B3/D1 collapse)
**Audience**: Operator (Fitz). Single source of truth for actions Claude could not take.
**Read first**: `SYNTHESIS.md` for full context. This runbook is the action checklist.

---

## ⚡ rev 2 update — most "remaining work" was already authored on a parallel branch

Mid-session discovery: `ultrareview25-remediation-2026-05-01` has 6 commits NOT on the current branch `live-prep-2026-05-01`. Three of them collapse what were listed as P0/P1 items into a single cherry-pick:

| Commit | Subject | Closes |
|---|---|---|
| `21cff1ec` | P2a: remove silent DEFAULT 'high' from temperature_metric | **B3 site #1** (kernel SQL line 129) |
| `4e89d00f` | P3-A: manifest cross-ref cleanups (F7/F11/F15/F16) | **B2** invariant cite drift |
| `7743f692` | P3-B: inv_prototype idempotency + INV-23↔NC-17 (F5/F10/F12) | **D1** + F5/F10 |
| `92bd0aaa` | P2b: narrow zeus-no-json-authority-write (F6) | F6 |
| `51f4c686` | P3-C: durable OVERRIDE log + tightened verdict anchor (F17/F13) | F13/F17 |
| `a5e5c779` | PLAN.md final status update | docs |

**All 6 dry-run cleanly via `git merge-tree`** (zero conflicts). The single operator action that closes B2 + B3-site-1 + D1 is bringing these commits onto live-prep-2026-05-01.

**Site #4 of B3 is closed-as-false-alarm** (operator review 2026-05-01: `ensemble_snapshots` is write-frozen, all existing rows are genuinely high-track, harvester already assumes `'high'` as fallback — see `P1_2_DEFAULT_HIGH_REPAIR.md` Site #4 section).

---

## TL;DR — what's left (rev 3 — 2026-05-01 17:00)

| Category | Status |
|---|---|
| A1 hooks active | ✅ DONE |
| A2 gitleaks installed | ✅ DONE |
| **B1 AGENTS.md DATA_DEGRADED** | ✅ DONE — operator commit f10ff845 applied option (a) clarification |
| **B2 invariant cite drift** | ✅ DONE — operator commit f10ff845 fixed all 6 cites + cleared KNOWN_BROKEN |
| **B3 DEFAULT 'high' cleanup** | ✅ DONE — sites #1/#2/#3/#5 repaired via cherry-pick of 21cff1ec; site #4 closed-as-false-alarm (write-frozen ensemble_snapshots) |
| **B4 CI gate triage** | 🟡 IN PROGRESS — Phase 1+2+1-D landed (149 → ~121 failures); Phase 3+4 in flight |
| **C1 launchd creds** | ✅ DONE (POLYMARKET_API_KEY/SECRET/PASSPHRASE injected via PlistBuddy) |
| **D1 F12 (INV-23↔NC-17 anchor)** | ✅ DONE — cherry-picked 7743f692 (also F5/F10 inv_prototype) |
| **P2-1 contract source-field baseline** | ✅ DONE — scanner + test landed at `aecd6cf5` (13 fields locked across 5 files) |
| **P2-3 FM-08 semgrep rule** | ✅ DONE via cherry-pick 4e89d00f (P3-A) |
| **P3-pip-audit** | ✅ DONE — wired into pre-commit-secrets hook |

**The cleanest single action that closes B2 + B3-site-1 + D1 + F5/F10 + F6 + F13/F17 in one shot**: cherry-pick the 6 commits below onto `live-prep-2026-05-01`. See §B-cherry-pick.

---

## A. One-line operator commands

### A1. Activate dual-channel git hooks (REQUIRED if not already done)

Per the gitleaks chain check earlier today, this MAY already be active. Verify and re-run if not:

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
git config --get core.hooksPath        # expect: .claude/hooks
# If nothing prints OR prints something else:
bash scripts/install_hooks.sh
```

What it gives you: every operator-direct `git commit` now runs (a) the pytest baseline check (currently 241 passing) and (b) gitleaks against `.gitleaks.toml` allowlist. Without this, only agent-channel `git commit` is gated.

### A2. (Optional) Verify gitleaks binary

If gitleaks is missing, the secrets-scan hook gracefully skips with an advisory. To make it active:

```bash
brew install gitleaks
# Verify:
gitleaks version    # expect: 8.x.x
```

Already confirmed installed and wired (8.30.1) earlier today, but listed for completeness when you re-clone or set up another machine.

---

## B-cherry-pick — single-action closure for B2 / B3-site-1 / D1 / F5/F6/F10/F13/F17

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
git status --short                          # confirm what's uncommitted

# Option (i): commit current review-session work first, then cherry-pick clean
git add -A
git commit -m "ultrareview25 remediation: P0/P1 antibodies + scanners + helpers"
git cherry-pick 21cff1ec 4e89d00f 7743f692 92bd0aaa 51f4c686 a5e5c779

# Option (ii): stash + cherry-pick + unstash (faster but stash-pop may need merging)
git stash --include-untracked
git cherry-pick 21cff1ec 4e89d00f 7743f692 92bd0aaa 51f4c686 a5e5c779
git stash pop                               # resolve any conflicts

# After either: verify
.venv/bin/python -m pytest $(grep "^TEST_FILES=" .claude/hooks/pre-commit-invariant-test.sh | sed 's/TEST_FILES="//;s/"$//') -q
```

Pre-flight via `git merge-tree --write-tree` confirms all 6 commits would apply cleanly (zero CONFLICT markers). Real cherry-pick may still need a small reconciliation if my session-added antibodies overlap with the cited tests — diff inspection will surface it.

After cherry-picking, also shrink the baselines that were forward-looking before the integration:
- `tests/test_invariant_citations.py:KNOWN_BROKEN` — empty out (0 entries)
- `scripts/check_identity_column_defaults.py:_BASELINE_KNOWN_DEFAULTS` — already correct (only site #4)

---

## B. Filed proposals waiting your decision

Each of these has a dedicated doc with the full reasoning + recommended action. Skim each, decide, apply.

### B1. AGENTS.md "Risk levels" clarification — DATA_DEGRADED semantics

**File**: `docs/operations/repo_review_2026-05-01/AGENTS_MD_DATA_DEGRADED_clarification.md`
**Type**: Doc-vs-code drift; code is correct, AGENTS.md over-stated
**Decision**: Choose one of three:
- **(a)** Amend AGENTS.md to clarify (recommended; docstring + lock test already landed)
- **(b)** Reverse the design (route missing/stale truth → RED instead of DATA_DEGRADED) — costly, not recommended
- **(c)** Defer (file in `architecture/governance_queue/` like F12)

**To apply (a)**:
```bash
export ARCH_PLAN_EVIDENCE=docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md
$EDITOR AGENTS.md   # apply the proposed paragraph from the proposal doc
git add AGENTS.md
git commit -m "AGENTS.md: clarify DATA_DEGRADED is YELLOW-equivalent (per P1_3 audit)"
```

The relationship test (`tests/test_dual_track_law_stubs.py::test_data_degraded_does_not_trigger_force_exit_sweep`) already locks this semantic; the AGENTS.md edit just brings the law into line with the code.

### B2. Invariant citation drift — 6 broken cites in `invariants.yaml`

**File**: `docs/operations/repo_review_2026-05-01/INVARIANT_CITATION_DRIFT_REPAIR.md`
**Type**: 6 cites in `architecture/invariants.yaml` don't resolve to real tests (INV-13, INV-30 ×2, INV-32 ×3)
**Why this matters**: 2 of those (INV-13, INV-32) have ALL their cites broken — same shape as the INV-05 doc-only finding that 3 reviewers independently surfaced.

**To apply**:
```bash
export ARCH_PLAN_EVIDENCE=docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md

# 1. Open architecture/invariants.yaml. Find each broken cite per the proposal doc.
$EDITOR architecture/invariants.yaml

# 2. Re-run the resolver to confirm zero remaining
.venv/bin/python scripts/check_invariant_test_citations.py
# Expect: "OK — every cited test resolves."

# 3. Shrink KNOWN_BROKEN baseline in the test file
$EDITOR tests/test_invariant_citations.py
# Remove the matching tuples from KNOWN_BROKEN. The test fails until you do
# (it complains: "P1-8 housekeeping: ... once known broken now resolve").

# 4. Verify
.venv/bin/python -m pytest tests/test_invariant_citations.py -v

# 5. Commit
git add architecture/invariants.yaml tests/test_invariant_citations.py
git commit -m "invariants.yaml: fix 6 broken test citations (P1-8 baseline → 0)"
```

### B3. Identity-column DEFAULT repair — 5 sites

**File**: `docs/operations/repo_review_2026-05-01/P1_2_DEFAULT_HIGH_REPAIR.md`
**Type**: 4× `DEFAULT 'high'` (temperature_metric) + 1× `DEFAULT 'v1'` (data_version) on identity columns
**Why this matters**: silently routes missing-value INSERTs to one half of bivalent identity (INV-14 violation). Most dangerous: site #4 (`src/state/db.py:~1581`, no row-count guard).

**To apply** (ordered easiest→hardest):
```bash
export ARCH_PLAN_EVIDENCE=docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md

# Sites #1, #2, #3, #5 are mechanical (drop DEFAULT keyword; rely on
# NOT NULL constraint + INSERT-side discipline). Site #4 needs a backfill
# decision FIRST — see proposal doc §"Site #4" for the open question.

$EDITOR architecture/2026_04_02_architecture_kernel.sql   # site #1 (line ~129)
$EDITOR src/state/db.py                                    # sites #2, #3, #5

# Re-run the scanner after each batch
.venv/bin/python scripts/check_identity_column_defaults.py

# Update the baseline in the script (see proposal doc step 5 + 6)
$EDITOR scripts/check_identity_column_defaults.py
# Remove repaired (column, file) entries from _BASELINE_KNOWN_DEFAULTS
# Decrement the matching count in _BASELINE_OCCURRENCE_COUNTS

# Verify
.venv/bin/python -m pytest tests/test_identity_column_defaults.py -v

# Commit
```

**Site #4 has an open question**: what `temperature_metric` should legacy `ensemble_snapshots` rows actually be? The proposal recommends backfilling from per-row evidence before dropping the DEFAULT. R3 lead (you) likely knows the answer.

### B4. CI gate triage — 120-failure cleanup

**File**: `docs/operations/repo_review_2026-05-01/CI_GATE_TRIAGE_PROPOSAL.md`
**Type**: Schedule decision (not an in-session edit)
**Effort**: 3-5 days dedicated slice, separate from this review

**Why deferred**: turning on the full pytest gate today would block every operator commit because there are 120 failures behind it. Each failure needs per-test triage:
- 10 governance violations in production (`test_structural_linter`) — REAL_BUGS
- 17 stale-stub regressions (`test_pnl_flow_and_audit`) — STALE_FIXTURES
- 4 INV-25/26 enforcement gaps (`test_p0_hardening`) — REAL_BUGS
- 16 `live_topology` marker excluded — separate concern
- ~73 unclassified — needs per-file investigation

**Operator action**: pick a week and dispatch the triage. Until then, the existing pre-commit hook (baseline 241) protects the 17-file law-gate surface; the rest is unchanged.

---

## C. Operator-machine-only tasks

### C1. launchd credential injection — **DONE 2026-05-01**

**What was actually done** (corrects original P1-1 framing):

1. **Polymarket L2 WS creds** — `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE` added to `~/Library/LaunchAgents/com.zeus.live-trading.plist` `EnvironmentVariables` via PlistBuddy (matches the existing `WU_API_KEY` plaintext pattern). Values pulled from macOS Keychain entries `openclaw-polymarket-api-{key,secret,passphrase}` which already exist.

2. **`ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET`** — **NOT injected**. Audit revealed:
   - The HMAC-token validation path (`src/control/cutover_guard.py:_validate_operator_token`) has NO daemon caller (`grep cutover_transition` in daemon entrypoints returned empty).
   - `scripts/arm_live_mode.sh` (the actual operator cutover workflow) writes `state/cutover_guard.json` directly, bypassing the HMAC path.
   - The env var is dormant — adding it to the plist would be plaintext-secret-on-disk with no current consumer.

3. **`ZEUS_USER_CHANNEL_WS_ENABLED`** — **NOT set**. The WS user channel feature is gated by this flag (`src/main.py:200`); with the flag unset (default 0), `WSAuth.from_env()` is never called, so the new creds wait until the operator explicitly turns the flag on. This is the safe default.

**Operator's next step (when ready to enable WS user channel)**:

```bash
# Verify creds are in plist
/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables" ~/Library/LaunchAgents/com.zeus.live-trading.plist | grep POLYMARKET

# Turn on the flag
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:ZEUS_USER_CHANNEL_WS_ENABLED string 1" ~/Library/LaunchAgents/com.zeus.live-trading.plist

# Set the condition_ids (required when flag is on, per src/main.py:209-210)
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:POLYMARKET_USER_WS_CONDITION_IDS string 'cond_xxx,cond_yyy'" ~/Library/LaunchAgents/com.zeus.live-trading.plist

# Load (or reload if already loaded)
launchctl unload ~/Library/LaunchAgents/com.zeus.live-trading.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.zeus.live-trading.plist

# Verify daemon ingestor started
log show --predicate 'eventMessage CONTAINS "user-channel ingestor"' --info --last 5m
```

**Why the original RUNBOOK code block was wrong**:
- Used `<name>` placeholder instead of explicit `live-trading.plist`
- Listed `ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET` as required (it isn't, per audit)
- Didn't specify the keychain-to-plist value-retrieval mechanism
- Didn't mention the `ZEUS_USER_CHANNEL_WS_ENABLED` flag gating

---

## D. Independent slice deferrals

### D1. F12 (INV-23 ↔ NC-17 anchor) operator ruling — P1-10

**Status**: Has been operator-deferred since 2026-04-26 (≥1 week). Listed in `architecture/invariants.yaml:233-241`.
**Type**: Architectural ambiguity in the law layer — needs your call. Cannot be automated.

**What's needed**: rule on whether INV-23's "DEGRADED_PROJECTION distinct non-VERIFIED" claim anchors to NC-17 (the negative constraint) or stands alone. This is a yaml-level cleanup once you decide; the proposal lives in the existing PLAN.md F12 section.

---

## Summary table — every remaining action

| # | Action | Effort | Blocker |
|---|---|---|---|
| A1 | `bash scripts/install_hooks.sh` (verify with `git config --get core.hooksPath`) | 1 min | — |
| A2 | `brew install gitleaks` (only on un-set-up machines) | 1 min | — |
| B1 | Apply AGENTS.md DATA_DEGRADED clarification | 5 min | Read the proposal doc |
| B2 | Repair 6 invariant citations in `architecture/invariants.yaml` + shrink KNOWN_BROKEN | 15 min | `ARCH_PLAN_EVIDENCE` env |
| B3 | Drop `DEFAULT 'high'` ×4 + `DEFAULT 'v1'` ×1 in DDL + shrink scanner baseline | 30 min | Site #4 backfill decision |
| B4 | Schedule the 120-failure triage slice (3-5 days) | Pick a week | — |
| C1 | Inject WS L2 + cutover-token creds into launchd plist | 30 min | Your machine creds |
| D1 | Rule on F12 (INV-23 ↔ NC-17 anchor) | 10-30 min | Your judgement |

**Order recommendation**: A → B1 → B2 → B3 → C1 → D1 → schedule B4.

---

## Provided artifacts (everything Claude built this session)

```
docs/operations/repo_review_2026-05-01/
├── SYNTHESIS.md                                   # main synthesis + action log
├── architecture.md                                # architect lane report
├── live_running.md                                # verifier lane report
├── adversarial.md                                 # critic-opus lane report
├── test_topology.md                               # test-engineer lane report
├── security.md                                    # security-reviewer lane report
├── FILE_INDEX.md                                  # entry-point map
├── AGENTS_MD_DATA_DEGRADED_clarification.md       # B1 proposal
├── INVARIANT_CITATION_DRIFT_REPAIR.md             # B2 proposal
├── P1_2_DEFAULT_HIGH_REPAIR.md                    # B3 proposal
├── CI_GATE_TRIAGE_PROPOSAL.md                     # B4 proposal
├── P1_3_TRUTH_AUTHORITY_AUDIT.md                  # P1-3 deep-impact audit
└── OPERATOR_RUNBOOK.md                            # this file

SECURITY-FALSE-POSITIVES.md                        # root: durable false-positive index
.gitleaks.toml                                     # root: gitleaks config + WU allowlist
.claude/hooks/pre-commit                           # git-channel orchestrator
.claude/hooks/pre-commit-secrets.sh                # gitleaks runner
.claude/hooks/pre-merge-commit                     # symlink → contamination check
scripts/install_hooks.sh                           # one-time per-clone setup
scripts/check_invariant_test_citations.py          # P1-8 citation gate
scripts/check_identity_column_defaults.py          # P1-2 default scanner
src/types/truth_authority.py                       # P1-3 closed StrEnum + helpers
```

Pre-commit baseline locked at **241 passed / 22 skipped**.

If you do nothing else: **A1** (`bash scripts/install_hooks.sh`) is the single highest-leverage action. It activates every other regression gate this review built.
