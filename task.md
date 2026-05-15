# Zeus Mission Dashboard

As of: **2026-05-15** · Branch `main` @ `8b3c3c2c59` · fully aligned
Owners: Fitz (operator) · AI (autonomous loop) · launchd (daemons)

---

## Current mainline state

**Main HEAD**: `8b3c3c2c59` merge: data daemon live verified
- K1 forecast DB split: COMPLETE (PR #114 + PR #116 merged)
- Data daemon authority chain: COMPLETE (PR #117 merged)
- INV-37 (cross-DB write seam audit): IN FORCE
- Operator script K1-broken paths: FIXED 2026-05-15 (this commit — healthcheck.py, verify_truth_surfaces.py, venus_sensing_report.py)

## Active milestones

| Task | Theme | Status |
|------|-------|--------|
| #49 | Phase 2 — LOW refits + promote stage→prod | IN PROGRESS |
| #50 | Phase 3 — LIVE readiness gates | PENDING (downstream of #49) |
| #51 | Phase 4 — Open PR + LIVE flip | PENDING (downstream of #50) |

---

## Phase 2 — LOW refits (Task #49)

- LOW_v1 + LOW_contract refits: done (2026-05-14)
- Promote stage → prod: pending operator go-ahead
- Pre-reqs: 30-min empirical proof complete; await operator confirmation

## Phase 3 — LIVE readiness gates (Task #50)

- F1 executable_forecast bundle quality (Task #10)
- F2 probability chain end-to-end (Task #11)
- F3 lifecycle_funnel transitions (Task #12)
- F4 execution_capability gate ALLOWED (Task #13)
- All read-only; auto-collect evidence

## Phase 4 — Open PR + LIVE flip (Task #51)

- ASK operator before PR open (memory: paid auto-reviewers on each push)
- LIVE flip: operator-controlled kill-switch via `config/settings.json`

---

## Lane C — LIVE verification (downstream of Phase 2)

### C1. Phase 3 readiness gates (Task #50)
- F1 executable_forecast bundle quality (Task #10)
- F2 probability chain end-to-end (Task #11)
- F3 lifecycle_funnel transitions (Task #12)
- F4 execution_capability gate ALLOWED (Task #13)
- **All read-only**: auto-collect evidence, write `architecture/tasks/2026-05-14_phase3_readiness_evidence.md`

### C2. First-trade observation (Tasks #14–17)
- Pure observe — never trigger
- Auto-write each `architecture/tasks/2026-05-14_first_{candidate,order,fill,exit}.md`

### C3. First settlement → learning loop (Task #20)
- Observe → harvester → learning evidence

---

## Lane D — Tech debt / follow-up (parallel-doable, no deps)

### D1. subdir-dict monkey-patch refactor (Task #53)
- Status: doc done `architecture/tasks/2026-05-13_subdir_dict_race_followup.md`
- Code change deferred to post-current-PR
- ~10 LOC across 2 files (proposal in doc)
- **Doable**: yes, anytime, as separate branch + PR

### D2. Remove deprecated `PolymarketClient.get_balance()` / `.redeem()` wrappers
- Source: collateral fix (commit 1795cd8723) executor follow-up note
- Pre-req: migrate `src/runtime/bankroll_provider._fetch_balance` to v2 adapter direct call
- **Doable**: yes, separate PR

### D3. Add `CollateralLedger.close()` daemon-shutdown hook
- Source: collateral fix executor follow-up note
- Non-correctness, just clean process exit
- **Doable**: yes, small

### D4. Oracle/Kelly evidence rebuild (Task #4)
- Independent workstream (A1-A8 commits already in history)
- Awaits critic R6 review — do NOT touch

### D5. Promote-script idempotency improvements
- If we needed `--skip-backup` flag this time, the script's backup logic likely needs a rework
- Defer to post-launch

---

## ⚙️ Decision authority cheat-sheet

| Action | AI auto | Ask |
|---|---|---|
| Read code / sqlite -readonly / git log | ✅ | — |
| pytest single file | ✅ | — |
| Edit src/scripts/tests + commit on feature branch | ✅ | — |
| Spawn focused executor (≤ 300-word brief) | ✅ | — |
| Unload+load `com.zeus.data-ingest.plist` | ✅ | — |
| `sqlite3 ... CREATE/REINDEX` with `busy_timeout` | ✅ | — |
| Unload+load `com.zeus.live-trading.plist` (src.main) | — | ⚠️ |
| Unload+load `com.zeus.riskguard-live.plist` | — | 🚫 |
| `git push --force-with-lease` (feature branch) | — | ⚠️ |
| `gh pr create / merge / review` | — | ⚠️ |
| `sqlite3 ... DELETE/DROP/VACUUM` | — | ⚠️ |
| `sudo *` | — | 🚫 |
| Edit `config/settings.json` | — | ⚠️ |

✅ no ask · ⚠️ ask first · 🚫 never

---

## 📍 Live state (snapshot as of 2026-05-15)

| Path / Variable | Value | Note |
|---|---|---|
| Main HEAD | `8b3c3c2c59` | merge: data daemon live verified |
| Forecasts DB | `state/zeus-forecasts.db` | K1 split; forecast-class tables |
| World DB | `state/zeus-world.db` | world-class tables |
| Branch | `main` | fully aligned, clean working tree |

---

## 📚 Authoritative docs (point here, not duplicate)

| Doc | What it is | When to read |
|---|---|---|
| `architecture/tasks/2026-05-13_autonomy_runbook.md` | Full runbook: phase DAG, escalation rules, failure procedures | Per-turn entry, dispute resolution |
| `architecture/tasks/2026-05-13_subdir_dict_race_followup.md` | Task #53 race analysis + 2 proposed fixes | When tackling D1 |
| `tmp/PR_DESCRIPTION_draft.md` | PR body — empirical proof, known incomplete | Before PR open / before each push |
| `~/.claude/CLAUDE.md` | Universal AI methodology | Always |
| `AGENTS.md` (project root) | Zeus architecture, money path, tier overlay | Always |
| **this file `task.md`** | Mission dashboard, what's parallel | Per-turn first read |

---

## ✅ Definition of done (this mission)

- [ ] Lane A: ECMWF advances within 30 min of daemon reload
- [ ] Lane B: PR opened, all changes pushed
- [ ] Lane B: PR merged into main
- [ ] Lane C1: All 4 Phase 3 readiness gates green
- [ ] Lane C2: First candidate→order→fill→exit observed
- [ ] Lane C3: First settlement→learning loop closed
- [ ] Lane D: D1, D2, D3 scheduled as follow-up branches

When all green → mission complete → retire this dashboard.

---

## Per-turn discipline (when AI re-enters)

1. **Read `task.md` first** — this file
2. Check Lane A — if blocker resolved → advance
3. Read 1 monitor / 1 log tail (don't poll)
4. Take next action per lane (auto or ask)
5. End-of-turn: 1-2 sentences (what changed / what's next)
