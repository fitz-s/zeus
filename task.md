# Zeus Mission Dashboard

As of: **2026-05-14 ~06:30 UTC** · Branch `fix/calibration-vs-obs-relative-tolerance-2026-05-12` @ `14ace08836` · 56 commits ahead of origin
Owners: Fitz (operator) · AI (autonomous loop) · launchd (daemons)

---

## 🔴 Active blocker (VACUUM IN FLIGHT)

**ECMWF flow** — REINDEX didn't solve. Lane C deeper sample shows daemon was in MIXED workload (33 libsystem_kernel, 10 _ssl, 7 _zoneinfo, 6 _cyutility, 2 _sqlite3, 2 pandas), not single-SQL bottleneck. Root cause is compounded slowness across SSL fetch + datetime parse + pandas + SQL dedup checks per-file.

**Actions in flight (07:07 UTC)**:
- ✅ Live + ingest both unloaded (live unload approved as part of "并行做a和c")
- ✅ WAL TRUNCATE checkpoint complete (0|0|0)
- ⏳ VACUUM INTO `state/zeus-forecasts.vacuumed.db` running (PID **5899**, log `/tmp/zd_vacuum_status.log`)
- ⏳ Estimated 1–3h for 48 GB DB. Will atomic swap on success.
- ⏳ Cleanup executor `ab4363d978b1617ad` running (workspace temp files)
- ⏳ pytest executor `a7e16b8521bea5635` running (PR-scope tests)

**LIVE TRADING IS HALTED for VACUUM duration.** This was implicit in user's "并行做a" approval.

---

## 4 parallel lanes

| Lane | Theme | State | Blocker | Doable now? |
|---|---|---|---|---|
| **A** | ECMWF flow unblock | running | REINDEX in flight | auto, no |
| **B** | PR prep + open | ready | A | yes (B1-B4 read-only) |
| **C** | LIVE verification | queued | PR merged | no (downstream) |
| **D** | Tech debt / follow-up | drain | none | yes (D1-D3 anytime) |

---

## Lane A — ECMWF flow unblock (CURRENT)

### A1. REINDEX ensemble_snapshots_v2 [RUNNING]
- **Owner**: AI inline (bj53x7bgj background bash)
- **Why**: 100% B-tree page-read in sample = autoindex fragmentation suspected
- **Next**: monitor `b9ka4apn9` will fire on completion
- **Success**: REINDEX done < 60s + ANALYZE done < 30s + integrity_check ok + ingest re-loaded
- **Fail path**: → A2 VACUUM INTO (1-3h, needs user nod)

### A2. VACUUM INTO new_file + atomic swap [FALLBACK]
- **Owner**: ASK USER (35 GB temp space, 1-3h)
- **Why**: If REINDEX doesn't fix → page interleaving (promote DELETE 46M + INSERT 84M scattered free pages across the shared 51 GB DB file)
- **Pre-reqs**: disk has 50 GB free, ingest daemon down
- **Risk**: live daemon reads forecasts.db; VACUUM blocks readers — must unload src.main too

### A3. Verify ECMWF flow restored [POST-A1 or POST-A2]
- **Probe**: `sqlite3 -readonly state/zeus-forecasts.db "SELECT MAX(fetch_time) FROM ensemble_snapshots_v2"` advances within 30 min of daemon load
- **Probe**: `logs/zeus-ingest.err | grep -E "loop_progress|loop_end|commit_done"` non-empty
- **Probe**: WAL grows from 0
- **Success → Task #28 completed → unlock Lane B**

---

## Lane B — PR prep + open

### B1. Branch hygiene [AUTO-OK ANYTIME]
- `git diff --name-only origin/main...HEAD` → list of files in PR
- `git log --oneline origin/main..HEAD | wc -l` → commit count
- topology_doctor on each changed file (read-only)

### B2. Pre-merge test pass [AUTO-OK ANYTIME]
- `python -m pytest <changed-test-files> -x -q`
- All new tests since last green: `test_collateral_ledger_global_persistent_conn.py`, `test_promote_calibration_pairs_v2_attach_path.py`, `test_ecmwf_open_data_hang_antibodies_2026_05_13.py`, etc.

### B3. PR draft sanity refresh [AUTO-OK ANYTIME]
- Re-read `tmp/PR_DESCRIPTION_draft.md`
- Verify ECMWF empirical claims match current state (after Lane A succeeds)
- Update commits list with: 0d4a1cd22b, 405ad3508e, 1795cd8723, 14ace08836

### B4. Git reconciliation decision [ASK USER]
- Local is strict superset of origin (134/4674 diff)
- Options: `git push --force-with-lease` (overwrites 39 remote SHAs) vs branch-rename + new PR
- ASK before push

### B5. PR open [ASK USER]
- `gh pr create --base main --title "<title>" --body-file tmp/PR_DESCRIPTION_draft.md`
- Triggers paid auto-reviewers (memory)
- ASK before

---

## Lane C — LIVE verification (downstream — queued)

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

## 📍 Live state (snapshot)

| Path / Variable | Value | Note |
|---|---|---|
| Forecasts DB | `state/zeus-forecasts.db` (35 GB, calibration_pairs_v2 = 91 M rows) | post-promote |
| World DB | `state/zeus-world.db` (36 GB) | unchanged |
| STAGE DB | `state/tigge_stage_20260511T175548Z.db` (68 GB) | source of promote |
| Forecasts WAL size | `0 B` since 19:16 PDT | wedge symptom |
| `ensemble_snapshots_v2.MAX(fetch_time)` | `2026-05-12T02:57` (52h+ stale) | wedge symptom |
| `src.main` PID | 38087 | LIVE daemon — don't restart |
| `src.ingest_main` PID | **unloaded** at A1 start | will reload after REINDEX |
| `riskguard` PID | 90763 | don't restart |
| Disk free (state vol) | ~120 GB | healthy |
| Branch | `fix/calibration-vs-obs-relative-tolerance-2026-05-12` | 56 commits ahead origin |
| `git diff HEAD origin/HEAD --stat` | 134/4674 (local = strict superset) | force-push safe |

---

## 🔄 Background workers (running)

| ID | Kind | Description | Status |
|---|---|---|---|
| `a7e16b8521bea5635` | Executor sonnet | Lane B2 pytest on PR-scope tests | running |
| `ScheduleWakeup ~07:06 UTC` | wake | 20-min ECMWF advance check | armed |
| (cron `159e2a65`) | hourly | data daemon HC | persistent — 7-day expiry |

**Last completed**:
- `bj53x7bgj` Bash REINDEX + reload — done (REINDEX 9s, ANALYZE 0s, daemon PID 77932 alive)
- `a23445483b0b87b66` Opus ECMWF diag — committed `14ace08836` (mmap+timing logs)

## 📊 Lane B1 — branch scope (read just now)

- **58 commits** ahead of main since merge base `0ecb3ab6`
- **19 new test files** in PR scope (the antibody bundle + ATTACH path + collateral persistent conn + K3 yield + parallel fetch + schema invariants)
- **PR draft** 89 lines at `tmp/PR_DESCRIPTION_draft.md` (already empirical-corrected)

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
