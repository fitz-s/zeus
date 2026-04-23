# Data Readiness Remediation — Execution Work Log

Live record of packet-by-packet execution. Every packet appends a section below; no packet closes without critic-opus APPROVE.

Created: 2026-04-23 by team-lead (during P-0 closure request cycle).

Reference docs:
- `first_principles.md` — decision framework, all packets anchor here
- `plan_v6.md` — backlog and v5→v6 correction log (historical; no longer the source of truth; frozen)
- `.claude/teams/zeus-data-readiness/config.json` — team config; members are `team-lead` + `critic-opus`

Convention: timestamps UTC ISO8601. SQL output reproduced inline (no "see elsewhere"). Every self-verify AC must show actual output, not "pass".

---

## P-0: First Principles Framework — 2026-04-23

### Pre-packet (Q1-Q10 answers)
- Q1 (invariant): INV-FP-1 through INV-FP-10 are the packet's output; no pre-existing invariant enforced
- Q2 (fatal_misread): all 7 listed in fatal_misreads.yaml explicitly mapped in §2 of first_principles.md
- Q3 (single-source-of-truth for numbers): INV count (20) via grep -c; plan count (6) via ls; corruption count (1562/1562/1562/1) via SQL
- Q4 (first-failure): N/A — no DB changes, no code changes; only doc write
- Q5 (commit boundary): N/A — doc, not code
- Q6 (verification): critic-opus independent re-read + re-run of cross-checks
- Q7 (new hazards): risk that framework itself introduces ambiguity — checked via critic review
- Q8 (closure requirement): critic-opus APPROVE on first_principles.md after independent verification
- Q9 (decision reversal): N/A — no prior decision being reversed
- Q10 (rollback boundary): doc-only; `git checkout -- first_principles.md` reverses; no downstream state

### Execution log
- 2026-04-23T12:25:00Z — Created `.claude/teams/zeus-data-readiness/config.json` via TeamCreate
- 2026-04-23T12:27:00Z — Spawned `critic-opus@zeus-data-readiness` (Opus model, run_in_background=true)
- 2026-04-23T12:30:00Z — Wrote `first_principles.md` (draft)
- 2026-04-23T12:33:00Z — SendMessage P-0 closure request to critic-opus
- 2026-04-23T12:36:43Z — critic-opus confirmed readiness + idle
- 2026-04-23T12:45:00Z — critic-opus REJECT verdict with 6 findings (F1 F2 CRITICAL; F3 F4 F5 F6 MAJOR; F7 F8 MINOR)
- 2026-04-23T13:00:00Z — Applied fixes F1 through F8 + 3 secondary findings (hourly_downsample, graph-where-not-what, rollback-boundary Q10)
  - §1: Added "BASELINE FACT 100% corruption" box with SQL proof
  - §2: Fixed INV count to 20; added scope-exclusion list
  - §2 mappings: INV-FP-1 += INV-03; INV-FP-5 corrected INV-10→INV-03+NC-02; INV-FP-6 += INV-03+INV-08; INV-FP-7 += fatal_misreads references; INV-FP-10 += INV-06
  - §3: Added AP-16 (zone laundering) + AP-17 (graph-as-truth)
  - §4: Added Q9 (decision reversal) + Q10 (rollback boundary)
  - §6: Rewrote dependency graph as single-topology (P-0 → {P-A, P-D, P-C} parallel → P-G → P-B → P-F → {P-E, P-H} parallel)
  - §7: Updated item 7 language to "rebuilt from scratch, NOT patched"
  - §8: Added rules 9, 10, 11 (no zone laundering; no UPDATE on corrupt rows; no graph-as-truth)
  - §9: Added mutation authority boundary + escalation path + stats tolerance spec
  - App-A: Added plan filename footnote
  - App-C: Added full R3 TODO → AP → Packet traceability table (24 rows)
  - App-D: Added work_log skeleton reference
- 2026-04-23T13:05:00Z — Created this work_log.md

### Self-verify
- AC-Foundation-1: `grep -c '^## Section' first_principles.md` → 9 sections (§1-§9)
- AC-Foundation-2: `grep -c '^## Appendix' first_principles.md` → 4 appendices (A, B, C, D)
- AC-Foundation-3: `grep -oE 'INV-[0-9]+' first_principles.md | sort -u | wc -l` → includes INV-03 ✓, INV-06 ✓, INV-08 ✓, INV-09 ✓, INV-10 ✓, INV-14 ✓, INV-15 ✓, INV-17 ✓, INV-22 ✓ = 9 INVs anchored
- AC-Foundation-4: `grep -c '^| AP-' first_principles.md` → 17 anti-patterns (AP-1..AP-17)
- AC-Foundation-5: work_log.md exists ✓ (this file)

### Closure request → critic-opus (second attempt)
- Deliverables: `first_principles.md` (revised), `work_log.md` (new skeleton)
- Evidence: this log + inline SQL/grep outputs above
- Findings addressed: F1 ✓, F2 ✓ (Appendix C), F3 ✓, F4 ✓, F5 ✓ (AP-16), F6 ✓ (single-topology graph), F7 ✓ (§9 stats tolerance), F8 ✓ (App-A footnote)
- Secondary: `hourly_downsample_preserves_extrema` added to INV-FP-7; `code_review_graph_answers_where_not_what_settles` added as AP-17 + §8 rule 11; rollback boundary as Q10
- Authority boundary clarified in §9: critic-opus is read-only + arbitration-triggered; mutations require team-lead execution

### critic-opus verdict (final, revision 2)
- **APPROVE** — all 9 findings (F1-F9) fixed + all 3 secondary addressed
- Evidence verified independently: MD5 `29715433...`, 512 lines, 9 sections, 4 appendices, 17 APs, 24 R3 rows, 10 Qs, 0 contradictory arrows
- All 9 scope-relevant INVs anchored; all 7 fatal_misreads addressed
- Non-blocking hazards flagged:
  - NH-1: §7 "1,562 rows" is provisional universe; P-E must re-snapshot (city,target_date) pairs before execution
  - NH-2: R3-15 training_cutoff is governance not schema; P-B may defer policy decision
  - NH-3: AP-1 wording "1,562 rows" vs "all 1,562" is stylistic only

### Closure action
- 2026-04-23T12:55:00Z — P-0 closed. TaskUpdate #1 status='completed'. Move to P-A.

---

## P-A: Bulk Writer RCA — 2026-04-23

### Pre-packet (Q1-Q10 answers)

**Q1 (invariant enforced/preserved)**:
- **INV-03** (canonical authority is append-first and projection-backed): the bulk writer bypassed the canonical append path. Identifying the writer is precondition to proving future writers respect INV-03.
- **INV-FP-1** (provenance chain unbroken): produces the provenance record for the 1,562 rows.
- **INV-FP-6** (write routes registered): proves there IS a ghost writer (or isn't, and writer is found in the registry).
- **AP-1** (ghost bulk writer): this packet IS the AP-1 investigation.

**Q2 (fatal_misread not collapsed)**:
- `api_returns_data_not_settlement_correct_source`: not risked; this packet is forensic, doesn't create new settlement data.
- `code_review_graph_answers_where_not_what_settles`: code-review-graph used for WHERE (impact/callers) only, NOT for WHAT settled.

**Q3 (single-source-of-truth for numbers/names)**:
- Row counts: SQL against `state/zeus-world.db`
- File existence: `ls`, `find`, `stat`
- Git history: `git log`, `git reflog`, `git show`
- Code references: `grep -rn`

**Q4 (first-failure policy)**:
- If investigation finds a **registered writer** in `architecture/source_rationale.yaml::write_routes` that matches the signature → record + move on
- If investigation finds **unregistered writer** (deleted script, REPL, cron) with identifiable artifact → record + move on
- If investigation is **exhaustive yet unidentifiable** → accept as "unidentifiable with negative evidence"; produces `bulk_writer_rca_final.md` documenting every search lane that returned null

**Q5 (commit boundary)**:
- N/A — investigation only, no DB writes. Evidence files are written, but they are audit artifacts, not canonical state.

**Q6 (verification)**:
- critic-opus independently re-runs the key forensic commands against current repo state
- Each hypothesis in the evidence doc has citable grep/git/ls output
- No "possibly..." conclusions without evidence

**Q7 (new hazards introduced)**:
- Risk: investigation might pursue false leads. Mitigation: every line of the evidence doc cites a command output; no speculation.
- Risk: investigation's negative evidence might be incomplete (some search lane missed). Mitigation: §3 of evidence doc enumerates every search lane attempted, with exit status.

**Q8 (closure requirement)**:
- Deliverable: `evidence/bulk_writer_rca_final.md` with per-hypothesis verdict + supporting command output
- critic-opus approves if: (a) every hypothesis has supporting evidence or documented negative evidence; (b) the "unidentifiable" verdict (if reached) is exhaustively justified; (c) downstream implications for P-E quarantine protocol are documented

**Q9 (decision being reversed)**:
- N/A. This is investigation, not pattern revival.

**Q10 (rollback boundary)**:
- Packet writes only evidence files (no DB mutations).
- `git checkout -- .` fully reverses.
- Forward-compatible: P-D, P-C, P-E can all proceed from the evidence doc regardless of whether the writer is identified.

### Execution log

- 2026-04-23T12:55:00Z — Phase 1 (signature census) SQL run on state/zeus-world.db: 1562 rows, single microsecond settled_at, 933 point-bin rows with settlement_value=pm_bin_lo exact match, 59 low-shoulder NULL_lo, 210 high-shoulder NULL_hi, 0 sentinels (-999/999) in DB
- 2026-04-23T12:58:00Z — Phase 2 (current code INSERT sites): only `src/execution/harvester.py:560` and `scripts/onboard_cities.py:383` — neither matches signature
- 2026-04-23T13:00:00Z — Phase 3 (git history): commit d99273a (2026-04-16 09:22 CDT) deleted 3 scripts + created _build_pm_truth.py. Timing: bulk write at 07:39:58 CDT is BEFORE d99273a at 09:22 CDT. Reflog checked — no orphan commits.
- 2026-04-23T13:03:00Z — Phase 4 (.omc/.claude sessions): 12 .omc session files exist but content not auditable for bulk-write signature; no .ipynb files in repo
- 2026-04-23T13:05:00Z — Phase 5 (launchd/cron): 3 plists active (live-trading, riskguard, heartbeat) + cron `city-settlement-audit` weekly. None fired 07:39 CDT on 2026-04-16
- 2026-04-23T13:07:00Z — Phase 6 (shell history): only SELECT queries, no INSERT — REPL ruled out
- 2026-04-23T13:09:00Z — Phase 7 (_build_pm_truth.py): grep confirms 0 sqlite/INSERT/conn.execute — JSON-only
- 2026-04-23T13:12:00Z — Rule out rebuild_settlements.py: `SELECT COUNT(*) FROM settlements WHERE settlement_source LIKE '%_rebuild%'` → 0
- 2026-04-23T13:15:00Z — Writer logic decoded: reads pm_settlement_truth.json, maps sentinels to NULL, sets settlement_value=pm_bin_lo for point bins only
- 2026-04-23T13:18:00Z — JSON vs DB cross-check confirms match:
  - 936 JSON point bins ≈ 933 DB point bins
  - 360 JSON finite = 360 DB finite (exact)
  - 59 JSON low-shoulders = 59 DB null_lo (exact)
  - 211 JSON high-shoulders ≈ 210 DB null_hi
  - 5 missing = 5 JSON duplicates for 2026-04-15 (scientist R3-D4)
  - 1 extra DB row = Denver 2026-04-15 (scientist R3-D7)
- 2026-04-23T13:22:00Z — IPython history queried (fixed schema — sessions/history tables): 0 rows with INSERT settlements or pm_bin_lo. REPL ruled out.
- 2026-04-23T13:25:00Z — Archived refactor package v2 inspected: schema tests only, no bulk writer
- 2026-04-23T13:30:00Z — Wrote `evidence/bulk_writer_rca_final.md` (8 sections, Hypotheses H1..H12 evaluated, H12 "unregistered local script" probable but unverifiable)

### Self-verify

Key ACs (all pass):
- AC-P-A-1: Writer logic fully decoded and reproducible via SQL → ✓ (Section 2.4 pseudocode + §2.3 SQL verification 933/933 exact)
- AC-P-A-2: 11 writer hypotheses enumerated and ruled out with supporting evidence → ✓ (§3 H1-H11 each have grep/SQL/file evidence)
- AC-P-A-3: Verdict "unidentifiable but logic decoded" with clear implications for downstream packets → ✓ (§4 + §6 implications table)
- AC-P-A-4: Zero DB mutations made by this packet → ✓ (only evidence doc written; `git diff state/` shows no change to state/zeus-world.db beyond WAL)
- AC-P-A-5: INV-03, INV-FP-1, INV-FP-5, INV-FP-6, NC-02 citations in §4 verdict → ✓

### Closure request → critic-opus

- 2026-04-23T13:35:00Z — First closure request sent (pre-session-boundary)
- Session-boundary recovery: team dir `~/.claude/teams/zeus-data-readiness/` was deleted at session boundary (OMC session-end patch appears to have been overridden, possibly by OMC update to v4.13.2 staged in prompt). All on-disk deliverables preserved:
  - `first_principles.md` MD5 `e238cf80f418818f695b77e29926f8b8` (518 lines, post-§5 step 6 addendum)
  - `evidence/bulk_writer_rca_final.md` 15236 bytes unchanged
  - `work_log.md` this file
- 2026-04-23T16:30:00Z (11:30 CDT session resume) — TeamCreate zeus-data-readiness; respawned critic-opus with bootstrap prompt referencing all 9 context docs + first_principles.md at new MD5 + P-A deliverables. Team config at `~/.claude/teams/zeus-data-readiness/config.json`.
- 2026-04-23T16:31:00Z — P-A closure request re-queued via respawn's inbox (P-A context delivered in startup prompt; no second SendMessage needed until critic issues verdict)

### critic-opus verdict (respawned critic, 2026-04-23)

**APPROVE** with 3 MINOR fix-in-place items (all applied post-verdict) + 3 non-blocking hazards (documented in evidence §7.1) + 1 scope correction:

- F1 MINOR: line-number drift L562→L563 for `'VERIFIED'` stamp — fixed in §1 and H5 of evidence doc
- F2 MINOR: `.omc/sessions` lane was stated as "not auditable"; critic asked for actual grep. Result: 66 sessions × 0 matches. Appended as H14 + NH-A1.
- F3 MINOR (optional): bin-shape classification of 5 JSON dupes — not applied (optional per critic)
- NH-A1 `.omc/sessions` audit: CLOSED (0 matches; added to §7.1)
- NH-A2 Time Machine lane: TOMBSTONED (operator decision, not blocker)
- NH-A3 Source-file identification confidence: ~90% (acknowledged in §7.1)
- H13 `git fsck --lost-found`: critic-opus ran independently; 2 dangling commits ruled out
- **Scope correction**: R3-05 (AP-6 caller-count for append_many_and_project) is NOT closed by P-A — "AST-level caller evidence is P-H territory". Only R3-03 closes.

Evidence independently re-verified by critic-opus:
- 17 SQL/grep claims all exact match
- Additional insight (orthogonal split): F markets NEVER use point bins (0/458); C markets EXCLUSIVELY use point bins (936/1108). Writer's `settlement_value = pm_bin_lo if lo==hi` rule therefore produces value ONLY for C markets — matches DB 933 C-populated / 458 F-NULL exactly.

Invariants checked:
- INV-03 VIOLATED by bulk writer (correctly cited)
- INV-06 IMPLICATED (correctly deferred to P-E via INV-FP-10)
- INV-09 SATISFIED
- INV-14 PROVISIONAL (correctly deferred to P-B)
- INV-FP-1/5/6 CITED correctly
- NC-02 CITED correctly (spirit violation, literal surfaces are JSON-related)

Fatal misreads all preserved (no collapse).

### Closure action

- 2026-04-23T16:35:00Z — Applied F1 (L562→L563 in §1 + H5), F2 (.omc/sessions grep result as H14 + §7.1 NH-A1), added §7.1 NH-A2/A3, added §7.2 git fsck H13
- 2026-04-23T16:37:00Z — Updated App-C: R3-03 Status = `CLOSED-BY-P-A` (2026-04-23; critic-opus APPROVE); R3-05 Status remains PENDING, note changed to "P-H territory per critic-opus"
- 2026-04-23T16:38:00Z — TaskUpdate #2 P-A → status=completed
- 2026-04-23T16:39:00Z — P-A formally closed. Per P-0 §6 dependency graph, {P-D, P-C} are the next parallel packets (P-G waits for both + P-A investigations complete; P-A is now complete)

---

## P-D: Harvester Gamma Probe — started 2026-04-23T16:45:00Z; closure requested 2026-04-23T17:15:00Z

### Pre-packet (Q1-Q10 answers)

**Context & scope**: read-only probe of live Polymarket Gamma API on closed weather markets. Goal: determine whether `src/execution/harvester.py::_find_winning_bin` (L486-503) write-path is structurally viable (i.e., does Gamma actually populate `winningOutcome='yes'` for closed markets?). Result shapes later decisions about harvester fix (DR-33) and fallback signals (R3-09 outcomePrices issue).

**Q1 (invariant enforced/preserved)**:
- **INV-FP-1** (provenance chain): we want to prove the Gamma API response shape is a real provenance anchor, not a hoped-for field
- **INV-FP-7** (source role boundaries): we're probing the settlement-related market-metadata stream, which is a distinct source role from settlement-daily-observation source (WU/HKO/NOAA)
- No architectural invariants violated by a read probe.

**Q2 (fatal_misread not collapsed)**:
- `code_review_graph_answers_where_not_what_settles` — graph is irrelevant here; probe uses actual HTTP API, not graph inference
- `api_returns_data_not_settlement_correct_source` — even if Gamma returns data, we're NOT treating it as settlement-source; we're checking whether the FIELDS exist to let harvester write settlement rows from it. Separate question from settlement-source validity.

**Q3 (single-source-of-truth for numbers/names)**:
- API URL: `https://gamma-api.polymarket.com` (per existing `scripts/_build_pm_truth.py:13`)
- Tag ID for daily-temperature: `103040` (per `scripts/_build_pm_truth.py` constant)
- Sample size: probe ~50 known-settled events (same as prior scientist round-2 suggestion + critic round-2 methodology)
- All claims cite actual HTTP response payload excerpts

**Q4 (first-failure policy)**:
- Gamma API down / rate-limited → retry with backoff; if persistent, document timestamp + retry count + tombstone the lane pending reconnect
- Response shape unexpected (e.g., no `winningOutcome` field anywhere) → that IS the finding; document shape + recommend fallback signal (non-price, per R3-09)
- Gamma returns OUR data but with different schema than existing `_build_pm_truth.py` assumes → flag as R3-XX schema-drift issue

**Q5 (commit boundary)**:
- N/A. Read-only probe; no DB writes; no file writes to tracked paths (only to `evidence/`).

**Q6 (verification)**:
- critic-opus independently re-runs the probe against same endpoint
- Every claim about response shape cites actual JSON keys + sample values
- Per-field population rate claimed = verifiable by field-count over N samples

**Q7 (new hazards introduced)**:
- Risk: Gamma API rate-limits our probe and affects other workflows (scrape jobs)
  - Mitigation: probe <50 requests over ~2 minutes; within normal API usage bounds
- Risk: probe's `curl` calls leak credentials or proxy settings
  - Mitigation: use same env-strip pattern as existing `scripts/_build_pm_truth.py` (strips HTTP_PROXY/HTTPS_PROXY per existing code)

**Q8 (closure requirement)**:
- Deliverable: `evidence/harvester_gamma_probe.md` documenting:
  - Probe methodology (URL, params, sample size, env handling)
  - Response shape findings (which fields present, which NULL, % populated)
  - `winningOutcome` field population rate across samples
  - Alternative signals enumerated (`outcomePrices`, `resolvedBy`, `closedTime`, `series` metadata) with per-field availability
  - Verdict: harvester L486-503 viable / needs-fallback / needs-rewrite
  - Explicit note: **do NOT revive `outcomePrices >= 0.95` price fallback** per R3-09 (removed by documented decision in `src/execution/harvester.py:490-491`)
- critic-opus APPROVE if: probe evidence is reproducible; shape findings cite actual JSON; fallback recommendation does not revive deleted price-fallback; scope respects R3-09

**Q9 (decision being reversed)**:
- **POTENTIAL reversal**: v6 DR-43 proposed reviving `outcomePrices 1.0` fallback. This packet confirms/rejects that. Per harvester.py:490-491 documented rationale, price signals are NOT settlement authority.
- P-D will NOT revive price fallback. If probe shows `winningOutcome` unreliable, we look at non-price signals (resolvedBy, closedTime, series). If ALL non-price signals also unreliable, P-D concludes with "harvester write path structurally non-viable; requires separate packet on Gamma API evolution or alternative source".

**Q10 (rollback boundary)**:
- No state changes. Only evidence doc + work_log entry. `git checkout -- .` fully reverses.

### Execution log

- 2026-04-23T16:50:00Z — Initial 50-event probe: `winningOutcome` absent in 412/412 markets (100%). Harvester L486-503 path confirmed structurally unreachable.
- 2026-04-23T16:55:00Z — Detailed sample probe (3 events, 3 markets each): `outcomePrices=["0","1"]` and `["1","0"]` patterns observed with `umaResolutionStatus='resolved'` and 100%-populated `resolvedBy`, `closedTime`. Dallas 2025-12-30 showed the winning bin: "between 52-53°F" market has `outcomePrices=["1","0"]`.
- 2026-04-23T17:00:00Z — Systematic tally: 142 markets with `umaResolutionStatus='resolved'` (122 NO_won + 20 YES_won). Distribution consistent with Polymarket binary question resolution.
- 2026-04-23T17:05:00Z — Drafted minimal fix for `_find_winning_bin` (§6.1 of evidence doc). Gated by `umaResolutionStatus='resolved'`, reads `outcomePrices[0]=='1'` as UMA-oracle-output. Documented as NOT reviving R3-09 deleted pattern (pre-resolution price fallback); post-resolution reading is oracle-vote extraction.
- 2026-04-23T17:10:00Z — Extended probe (R3-23 closure): scanned 250 closed events; 0 Denver matches; Denver 2026-04-15 confirmed as synthetic orphan from pm_settlements_full.json speculative entries. P-G DELETE disposition recommended.
- 2026-04-23T17:15:00Z — Wrote `evidence/harvester_gamma_probe.md` (11 sections), updated App-C R3-09 partial-close + R3-23 CLOSED-BY-P-D.

### Self-verify

- AC-P-D-1: 50 events fetched from live Gamma → ✓
- AC-P-D-2: `winningOutcome` absent in 412/412 markets (100%) → ✓ headline finding stands
- AC-P-D-3: `umaResolutionStatus='resolved'` detected as authoritative gate → ✓ (142 resolved markets)
- AC-P-D-4: `outcomePrices=["1","0"]` correlates with YES-won question text → ✓ (Dallas 52-53°F sample)
- AC-P-D-5: proposed fix does NOT revive R3-09 deleted pattern → ✓ (§5.3 + §8 explicit distinction)
- AC-P-D-6: no DB mutations → ✓ (only evidence doc + work_log + App-C status update)
- AC-P-D-7: R3-23 Denver orphan closed with 250-event scan → ✓ (0 matches)

### Closure request → critic-opus

- 2026-04-23T17:15:00Z — P-D closure request sent with reproducible API probe commands and §8 Q9 non-reversal attestation

### critic-opus verdict (2026-04-23)

**APPROVE** with 3 MINOR findings + 3 NH-D* test-coverage notes + R3-## guidance:

- F1 MINOR: §4 field lists were representative subset, not full API schema. Fixed: added "representative, not exhaustive" language; referenced critic's 44 event-fields / 76 market-fields extended enumeration.
- F2 MINOR: §5.2 reported subsample tally (142 resolved / 87%). critic's full 412-scan: 412 resolved / 100% / clean 1-winner-per-event topology (50 YES-won + 362 NO-won). Fixed: replaced §5.2 with full-scan numbers; noted subsample was early-probe artifact.
- F3 MINOR: §6.1 docstring now cites `scripts/_build_pm_truth.py:137-139` precedent (same outcomePrices[0]=="1" pattern WITHOUT umaResolutionStatus gate → P-D fix is STRICTER than existing production). Strengthens non-reversal argument.
- NH-D1 outcomes-order invariant → added to §11.1 test-fixture requirements
- NH-D2 string-representation fragility → added to §11.1
- NH-D3 umaResolutionStatus state-machine coverage → added to §11.1
- **Non-reversal attestation accepted**: §5.3 + §8 prove semantic distinction is sound. R3-09 AP-11 concern is closed at review-gate level.
- Added insight (critic §R3-23 nuance): pm_settlements_full.json HAS Denver 2026-04-15 with pm_bin_lo=68, pm_bin_hi=69, pm_exact_value=None. Bulk writer rule `settlement_value = pm_bin_lo if lo==hi` produces NULL for 68≠69 → MATCHES current DB (Denver settlement_value=NULL). Writer likely read BOTH JSONs with pm_settlement_truth precedence, NOT just pm_settlement_truth alone. Consistent with P-A NH-A3 (90% confidence on single source).

App-C status updates (per critic-opus confirmation):
- R3-09: PENDING → **CLOSED-BY-P-D** (critic's marginal preference; AP-11 review-gate resolved; DR-33 impl is separate downstream task)
- R3-21: stays PENDING; routed to **P-G** (reconciliation scope, not signal scope)
- R3-23: PENDING → **CLOSED-BY-P-D** (conclusive 250-event Gamma scan + 3-way cross-reference)

### Closure action

- 2026-04-23T17:30:00Z — Applied F1 (§4 representative language), F2 (full-412 tally in §5.2), F3 (§6.1 _build_pm_truth.py precedent citation)
- 2026-04-23T17:32:00Z — Added §11.1 NH-D1/D2/D3 test-fixture requirements for future DR-33 implementation
- 2026-04-23T17:34:00Z — Updated App-C: R3-09 → CLOSED-BY-P-D; R3-21 routing clarified to P-G; R3-23 remains CLOSED-BY-P-D (confirmed)
- 2026-04-23T17:35:00Z — P-D formally closed. Per P-0 §6 dependency graph, **P-C (WU product audit) is now the remaining parallel investigation** before the gate to P-G. P-A + P-D complete; P-G blocks on P-C.

---

## P-C: WU Product Identity Audit — started 2026-04-23T17:40:00Z

### Pre-packet (Q1-Q10 answers)

**Context & scope**: audit whether `observations.wu_icao_history` (WU private API v1 hourly-aggregate, per `src/data/wu_hourly_client.py:7`) matches the WU-website-daily-summary product that Polymarket resolution references. Fatal-misread `wu_website_daily_summary_not_wu_api_hourly_max` directly applies. Goal: per-city go/no-go decision informing P-E reconstruction scope.

**Q1 (invariant enforced/preserved)**:
- **INV-FP-7** (source role boundaries): the core question this audit answers
- **INV-FP-1** (provenance chain): result drives provenance reasoning for re-derived rows (WU-native confident vs pending-audit)

**Q2 (fatal_misread not collapsed)**:
- `wu_website_daily_summary_not_wu_api_hourly_max` — THIS packet exists to test this antibody empirically
- `api_returns_data_not_settlement_correct_source` — preserved; even if API returns data, that doesn't make it settlement-correct; audit determines correctness
- `airport_station_not_city_settlement_station` — preserved; audit covers per-city station verification implicitly

**Q3 (single-source-of-truth for numbers/names)**:
- WU ICAO stations per city: `config/cities.json::<city>.wu_station`
- Cities needing audit: 47 WU-settled cities per `docs/operations/current_source_validity.md` (2026-04-21 audit)
- Statistical tolerances: per critic-opus R2 P1-5: ≥10 samples/month per city, ≥60 samples/city total, bounds ±<1°F delta and ≥95% exact-integer-match
- All counts via SQL against observations / city_truth_contract / settlements_pm_bin

**Q4 (first-failure policy)**:
- WU website archive pages unreachable for a city → document with retry timestamps; if persistent for specific city, QUARANTINE that city's audit with reason `WU_WEBSITE_ARCHIVE_UNAVAILABLE`
- WU website structure changed (scrape fails) → document schema drift; recommend separate packet
- Per-city match rate <95% → that city's rows go to UNVERIFIED in P-E, not VERIFIED; log to per-city decision table

**Q5 (commit boundary)**:
- N/A. Read-only audit; no DB writes.

**Q6 (verification)**:
- critic-opus re-runs sample audit on ≥2 randomly-chosen cities to confirm methodology
- Per-city match rate cites actual (wu_icao_history.high_temp, wu_website_scrape.daily_max) tuple comparisons
- Binomial CI reported per city

**Q7 (new hazards introduced)**:
- Risk: WU website rate-limits → extended probe time → could delay P-C by hours
  - Mitigation: ~60-100 requests/city × 47 cities = 2820-4700 total; batch by city with 1s sleep; estimate 2 hours audit time
- Risk: WU website returns stale/cached data not matching our probe window
  - Mitigation: compare obs.fetched_at to website archive date; flag if mismatch

**Q8 (closure requirement)**:
- Deliverable: `evidence/wu_product_identity_audit.md` with:
  - Methodology (URL pattern, sample selection, comparison rule)
  - Per-city match rate + CI
  - List of cities <95% match (quarantine candidates for P-E)
  - List of cities ≥95% match (VERIFIED candidates for P-E)
  - Explicit handling of DST-day obs (known quarantined in DB)
- critic-opus APPROVE if: methodology statistically sound; per-city decisions reproducible; no WU city silently greenlit without evidence

**Q9 (decision being reversed)**:
- N/A — this is a NEW empirical audit; no prior architectural decision is being reversed.
- BUT: result may reverse v6 DR-44's assumption that "95% threshold alone suffices". If audit shows ANY city with systematic bias, we need tighter threshold or per-city station re-mapping.

**Q10 (rollback boundary)**:
- No state changes. Only evidence doc + work_log entry.
- Forward-compatible: per-city decisions in evidence doc are inputs to P-E; P-E consumes the document without modifying it.

### Execution log

[to be populated during audit]

