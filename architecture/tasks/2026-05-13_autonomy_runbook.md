# Zeus Autonomy Runbook — 2026-05-13 / Active Mission

Created: 2026-05-13
Last reused or audited: 2026-05-13
Authority basis: User directive "好好plan一个task.md作为autonomy运行指南" — operator wants the AI loop to drive itself through the queued work without per-step approvals.

---

## Mission

Restore ECMWF flow → verify live trading chain (data → forecast → calibration → edge → execution → monitor → settlement → learning) → open PR for K1/K2/K3/D1/promote/antibody bundle work → support live flip.

User trades on Polymarket weather markets. Pipeline must produce calibrated probabilities and sized orders. Any silent failure in the chain blocks revenue. The runbook below is what the AI should do without asking.

---

## Decision authority matrix

| Action | Auto-OK | Ask first |
|---|---|---|
| Read code / git log / grep | Yes | — |
| Run `topology_doctor --navigation` | Yes | — |
| Run pytest on a single file or directory | Yes | — |
| Edit code in `src/` / `scripts/` / `tests/` | Yes if topology_doctor pre-edit gate passes AND change is scoped to the active task | If touching `config/settings.json`, on-chain wallet adapters, or trading parameters |
| `git commit` | Yes | — |
| `git push` to **feature branch** with `--force-with-lease` | Yes IF local is strict superset of remote (re-verify with `git diff --stat HEAD origin/<branch>`) | Otherwise ask |
| `git push` to `main` | NO | Always |
| `gh pr create` | NO | Always — PR open triggers paid auto-reviewers (memory: "Accumulate changes before opening a PR") |
| `gh pr merge` | NO | Always |
| `gh pr <comment|review>` | NO | Always |
| Spawn executor (Task tool) | Yes for focused, scoped, ≤300-word brief | — |
| `launchctl unload`+`load com.zeus.data-ingest.plist` | Yes when needed for ECMWF/ingest fix verification | — |
| `launchctl unload`+`load com.zeus.live-trading.plist` (src.main) | Conditional: yes only when collateral_ledger / authority-side code lands AND user is informed in advance | Otherwise ask |
| `launchctl unload`+`load com.zeus.riskguard-live.plist` | NO | Always — riskguard owns kill-switch |
| `sqlite3 ... CREATE INDEX` on PROD DB | Yes for missing-index fixes when daemons are unloaded or PRAGMA busy_timeout used | Heavy DDL: ask |
| `sqlite3 ... DELETE / DROP TABLE` on PROD DB | NO | Always |
| `sudo <anything>` | NO | Always |
| `rm -rf` outside `/tmp/` | NO | Always |
| Create new files under `architecture/`, `tmp/`, `architecture/tasks/` | Yes | — |
| Run `/ultrareview` | NO (user-only billing) | — |

---

## Monitoring cadence (no polling — event-driven)

| Source | Mechanism | Action on event |
|---|---|---|
| Background executor completion | Auto task-notification | Read result; advance phase |
| `Monitor` tool on log file | Persistent tail-grep | Each match = one event |
| `ScheduleWakeup` | Cadence checkpoints | Run sanity probe; brief if drift |
| Hourly cron health check | Existing `159e2a65` | Brief user only on RED FLAG that is NOT already known |

**Cache discipline**: long waits → `delaySeconds ≥ 1200` (20 min) to amortize cache miss. Short waits → `≤ 270s` (in-cache). Never 300s.

---

## Phase DAG (current mission)

```
A. ECMWF flow restored  ──┐
                          ├── B. Pre-PR verification ── C. Git reconciliation ── D. PR open ── E. PR review
B. Pre-PR verification ───┘                                                       (ASK USER)    (ASK USER)
                                                                                                    │
                                                                                                    ▼
                                                                                            F. Phase 3 LIVE readiness gates
                                                                                                    │
                                                                                                    ▼ (ASK USER for LIVE flip)
                                                                                            G. First-trade observation chain
                                                                                                    │
                                                                                                    ▼
                                                                                            H. First settlement → harvester → learning
```

---

## Phase A — ECMWF flow restoration

**Status**: in_progress. Opus executor `a23445483b0b87b66` running.

### Hypothesis
Promote script (`659ca79637 perf(promote): skip post-DELETE VACUUM + drop indexes`) dropped `calibration_pairs_v2` indexes pre-INSERT. Sample stack (100% in `sqlite3VdbeExec → moveToChild → readDbPage → unixRead`) shows daemon is running a slow B-tree scan, not hung. STAGE has 5 indexes; PROD now has 4 (missing `idx_calibration_pairs_v2_refit_scope`).

### Auto-actions
- A1. Wait for executor `a23445483b0b87b66` completion.
- A2. On completion, read `state/zeus-forecasts.db` for ensemble_snapshots_v2 max(fetch_time). If advanced past `2026-05-12T02:57` → A3. Else → A5.
- A3. Wait 30 min after first advance. Re-read max(fetch_time) — should advance by ≥1 cycle.
- A4. Mark task #28 completed in TaskUpdate. Proceed to Phase B.
- A5. If ECMWF still stale after fix landed: spawn second opus executor with deeper diagnostic (py-spy with sudo if needed, EXPLAIN QUERY PLAN on every SQL the ingest path runs, identify any other missing infrastructure).

### Escalation thresholds (Phase A)
- Executor `a23445483b0b87b66` not returning within 90 min → spawn new fresh executor with same brief + current state delta.
- ensemble max(fetch_time) not advancing after fix → escalate to user with EXPLAIN QUERY PLAN evidence.
- WAL grows but no ensemble row → DB write succeeding but contract validation rejecting; surface error log.

---

## Phase B — Pre-PR verification

### Auto-actions
- B1. `git status` clean? If untracked files belong in commit, stage + commit with appropriate message.
- B2. Run pytest only on files touched in branch:
  ```
  CHANGED=$(git diff --name-only origin/main...HEAD | grep "^tests/" | tr '\n' ' ')
  python -m pytest $CHANGED -x -q
  ```
- B3. Run topology_doctor on every changed source file:
  ```
  python3 scripts/topology_doctor.py --navigation --task "K1/K2/K3 + D1 + ECMWF antibody" --files <list>
  ```
- B4. Re-read `tmp/PR_DESCRIPTION_draft.md`. Verify empirical claims match current state (ensemble freshness, daemon PIDs, row counts, antibody boundary log evidence). Update if drift.
- B5. Gate: B2 green AND B3 ok AND B4 fresh → proceed to Phase C.

### Escalation thresholds (Phase B)
- Any test fail in suite related to our changes → revert offending commit, retry. Three reverts in a row → escalate.
- topology_doctor block → produce evidence the change is scoped correctly OR open a separate planning ticket.

---

## Phase C — Git reconciliation

### Auto-actions
- C1. `git fetch origin`
- C2. `git rev-list --left-right --count HEAD...origin/<branch>` — count divergence.
- C3. `git diff --stat HEAD origin/<branch>` — confirm LOCAL is strict superset (insertions ≤ deletions on local→remote diff direction = remote is missing local content).
- C4. **ASK USER**: "Force-push --force-with-lease to overwrite origin/<branch>'s 39 commits? Local is strict superset (4674 deletions vs 134 insertions on local→remote diff). User said earlier they want to avoid force-push but Path D (cherry-pick) is refuted by hash-equal check."

### When user approves
- C5. `git push --force-with-lease origin <branch>` — single attempt. On failure, stop + brief.
- C6. Tag the pre-push state for rollback: `git tag backup/pre-force-push-2026-05-13`.

---

## Phase D — PR open

### Auto-actions before asking
- D1. Read latest `tmp/PR_DESCRIPTION_draft.md` — verify empirical proof section is honest (ECMWF freshness, daemon PIDs, antibody bundle empirical status).
- D2. Run `gh repo view` to confirm right repo + base branch.
- D3. **ASK USER**: "Open PR? Title: `<title>`. Body: tmp/PR_DESCRIPTION_draft.md ready. After approval I'll run gh pr create."

### When user approves
- D4. `gh pr create --base main --title "<title>" --body-file tmp/PR_DESCRIPTION_draft.md`
- D5. Capture PR URL. Update task #51 with URL.

---

## Phase E — PR review monitoring

### Auto-actions
- E1. Spawn `Monitor` on `gh api repos/<owner>/<repo>/pulls/<n>/comments --paginate --jq '.[] | "\(.user.login):\(.created_at):\(.body)"'` polled every 60s.
- E2. For each new comment, classify per memory rule (BUG / STYLE_NIT / MISUNDERSTANDING / NOISE / OUT_OF_SCOPE).
- E3. BUG → spawn focused executor to fix; commit with `fix(pr-N-comment): ...`. Resolve thread via `gh api ... resolveReviewThread`. Original-executor continuity per memory.
- E4. STYLE_NIT → fix inline if 1-line, else queue.
- E5. MISUNDERSTANDING → write a clarifying reply in PR description update (not thread reply per memory "no thread replies").
- E6. NOISE / OUT_OF_SCOPE → resolve thread with a single explanatory commit comment.
- E7. SUBSTANTIVE issue → ask user.

---

## Phase F — Phase 3 LIVE readiness gates (Task #50)

**Blocked until PR merged.**

### Gates (in order)
- F1. Verify executable_forecast bundle quality (Task #10):
  - Probe: `sqlite3 -readonly state/zeus-forecasts.db "SELECT COUNT(*) FROM executable_forecast WHERE ..."` (find live writer)
  - Expect: ≥1 bundle within last cycle interval (30 min)
- F2. Verify probability chain (Task #11):
  - Probe: forecast → calibration → edge → posterior trace
  - Expect: every cycle produces a candidate with `P_raw`, `P_cal`, `P_posterior`, `edge`, `CI_low`, `CI_high`, `position_size`
- F3. Verify lifecycle_funnel (Task #12):
  - Probe: `status_summary.json::lifecycle_funnel` shows transitions
  - Expect: no stage = 0 longer than reasonable interval
- F4. Verify execution_capability gate ALLOWED (Task #13):
  - Probe: `status_summary.json::execution_capability.gate == "ALLOWED"`
  - Expect: gate evaluates to ALLOWED post-collateral-fix daemon load

### Auto-actions
- F1–F4 are READ-ONLY probes. Auto-collect evidence, write summary report `architecture/tasks/2026-05-14_phase3_readiness_evidence.md`.
- No code changes. No daemon restarts.

### Escalation
- Gate fails → diagnose root cause, brief user, do not auto-fix unless trivial (config edit ≤1 line and clearly identified).

---

## Phase G — First-trade observation chain (Tasks #14–17)

**ALL OBSERVE-ONLY. NEVER TRIGGER.**

### G1. First candidate selection (Task #14)
- Watch `status_summary.json::cycle.selected` > 0
- Capture full decision-chain trace for that selection
- Write `architecture/tasks/2026-05-14_first_candidate.md` with evidence

### G2. First order placement (Task #15)
- Watch `state/orders.db` for new INSERT
- Capture `execution_envelope` decision metadata
- Verify on-chain submission via wallet logs

### G3. First fill (Task #16)
- Watch for `state/positions.db` write
- Verify position size matches Kelly recommendation

### G4. Monitor + exit triggers (Task #17)
- Watch monitor_refresh writing position revaluation
- If exit fires, capture full trigger chain

### Escalation
- First trade goes wrong → halt immediately, brief user, do not let next trade proceed
- Risk level shifts from GREEN → user notified within 30s via PushNotification

---

## Phase H — First settlement → harvester → learning (Task #20)

**ALL OBSERVE-ONLY.**

- Watch for market settlement event
- Verify harvester records P&L
- Verify learning loop ingests outcome
- Write `architecture/tasks/2026-05-14_first_settlement.md` with full chain trace

---

## Concurrent tasks (independent workstreams)

| Task | Owner | Status | Action |
|---|---|---|---|
| #3 tigge-runner monitoring | passive | pending | continue cron monitor; no action |
| #4 oracle_kelly evidence rebuild | separate workstream | pending | await critic R6; do not touch |
| #53 subdir-dict refactor | follow-up PR | doc done | scheduled after current PR merges |

---

## Failure procedures

### Daemon crashes
- src.ingest_main dies → auto `launchctl load`; if dies twice in 10 min, halt and brief
- src.main dies → auto `launchctl load`; if dies twice in 30 min, halt and brief
- riskguard dies → halt all entries, brief user immediately

### Disk space
- df shows <20 GB on state volume → halt all writes, brief user
- df shows <5 GB → halt EVERYTHING including reads (might trigger swap thrash)

### Data corruption
- Any sqlite3 PRAGMA integrity_check fails → halt writes, brief user
- ensemble_snapshots_v2 max(fetch_time) goes BACKWARDS → halt writes, brief user

### Test regressions
- Pytest run on changed files fails → revert offending commit, retry once
- Two reverts on same code path → halt, brief user

### Live trading anomalies
- Position size > 2× Kelly recommendation → halt immediately, brief
- Order placed for wrong contract → halt, attempt to cancel via venue API, brief
- Fill price worse than envelope → record, alert user, do not halt unless repeated

### Authentication failures
- gh CLI auth fails → halt, brief user (must re-authenticate)
- on-chain wallet signature fails → halt all entries, brief user

---

## Background executor registry

Active as of 2026-05-13 / late evening:

| Agent ID | Description | Model | Status |
|---|---|---|---|
| `a23445483b0b87b66` | ECMWF slow-SQL fix | opus | running |
| `a117d1ee59748a1bc` | (predecessor: run_with_timeout deadlock fix + boundary logs) | opus | completed → landed `0d4a1cd22b`, `405ad3508e` |
| `ae1037496b853547b` | collateral_ledger persistent conn fix | opus | completed → landed `1795cd8723` |
| `a822453732f99f258` | #53 subdir-dict race doc | sonnet | completed |
| `af6f4b95319efccda` | PR draft empirical correction | sonnet | completed |
| `adc13948b27c0938c` | promote pipeline (5-step) | opus | completed → landed `1bda53b84d` + perf series + `36c999d127` |

Never spawn duplicates of running executors. Use `SendMessage` to continue if context preserved.

---

## Key state paths (snapshot)

- Live daemon (src.main) PID 38087 — leave alone, don't restart without ask
- Ingest daemon (src.ingest_main) PID 17451 — restart auto OK
- Riskguard PID 90763 — never restart
- Forecasts DB: `state/zeus-forecasts.db` (35 GB, 91M-row calibration_pairs_v2)
- World DB: `state/zeus-world.db` (36 GB)
- STAGE DB: `state/tigge_stage_20260511T175548Z.db` (68 GB, source for promote)
- Pipeline status log: `/tmp/zd_pipeline_status.log`
- Promote stdout log: `/tmp/zd_promote.out`
- Step 3 daemon load script: `/tmp/zd_step3_daemon_load.sh`
- Path D reconcile (refuted, kept as reference): `/tmp/zd_path_d_reconcile.sh`
- PR open script: `/tmp/zd_pr_open.sh`
- PR description: `tmp/PR_DESCRIPTION_draft.md`

---

## Commit-batching discipline

Per memory rule "Accumulate changes before opening a PR": batch related work onto one branch and one push. The current branch already has 54+ commits since merge base; further fixes should land on the SAME branch until PR opens.

---

## Per-turn discipline (when AI re-enters)

1. Read this runbook from top.
2. Check the active phase (Phase A → H tracker).
3. Check executor registry — any completed since last turn?
4. Read 1–2 relevant log tails (don't poll, just spot-check).
5. Advance to next phase IF gate passes, ELSE wait for executor or user.
6. Brief user only on (a) phase transitions, (b) escalations, (c) user-approval-required gates.

**Don't ask user every step.** The runbook IS the approval. New ambiguities → propose, decide if reversible, act if so.

---

## Open ambiguities (decide unilaterally if encountered)

| Ambiguity | Default decision |
|---|---|
| Should daemon restart count as user-action? | Ingest = no (auto); live = yes (ask) |
| Force-push needs re-confirmation each push? | Yes (each push) |
| Comment on PR resolveReviewThread before fix lands? | No — fix-commit IS the response per memory |
| Spawn sonnet executor that hits 1M context limit? | Switch to haiku for broad grep tasks per CLAUDE.md tier overlay |

---

## When to ask the user

1. **PR open** — always
2. **Push to main** — always
3. **force-push to feature branch** — always (each occurrence)
4. **Daemon restart of src.main** — yes
5. **Edit to config/settings.json** — yes
6. **Cancel/halt live trade** — no (act immediately, brief after)
7. **Anything that touches on-chain wallet keys** — yes
8. **Halt mission** — auto-halt on RED escalation; brief afterward

---

## Success criteria for this mission

- [ ] ECMWF flow restored — ensemble_snapshots_v2 advancing every cycle
- [ ] Test suite green on touched files
- [ ] PR opened with honest empirical proof
- [ ] PR merged
- [ ] LIVE readiness gates F1–F4 all pass
- [ ] First candidate selection observed and recorded
- [ ] First trade executed (if applicable to current market window)
- [ ] First settlement → harvester → learning loop closed
- [ ] All P10–P17 tasks marked completed with evidence

When all checkboxes are met, this runbook is retired. Mark task #51 completed and brief user with final summary.
