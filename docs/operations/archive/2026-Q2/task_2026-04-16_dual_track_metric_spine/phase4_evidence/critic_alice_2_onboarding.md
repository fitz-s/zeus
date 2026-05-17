# critic-alice-2 Onboarding Note

Date: 2026-04-16. Fresh opus instance after prior `critic-alice` (who ran Phase 2, 4A, 4B, 4C+4D) was wiped by Claude update. Predecessor's three verdicts + testeng-emma final dump read in full.

Authority loaded: AGENTS.md root, `zeus_dual_track_architecture.md` §§1-10, `zeus_current_architecture.md` §§13-22, INV-14..INV-22, NC-11..NC-15, DT#1..#7, Forbidden Moves, Gate A-F grammar, Phase 4 plan, three predecessor verdicts (4A/4B/4CD), testeng-emma R-letter catalog (R-A..R-U) + anti-patterns.

## L0-L5 checklist (restored verbatim from predecessor practice)

- **L0 — Authority still loaded after any compact.** Re-grep `zeus_current_architecture.md §13-§22` + `zeus_dual_track_architecture.md §2/§5/§6/§8` + both TIGGE plans. Disk-verify touched files with `git status --short` + targeted `wc -l` / `grep` before reviewing any claim. **Amendment from predecessor's MODERATE-7 self-retract:** grep by topic keyword on disk, not by remembered function name — context-window hallucinations bite the critic too. **Amendment 2026-04-17 (file provenance rule):** every new or substantially-touched `scripts/*.py` and `tests/test_*.py` must carry a top-of-file `# Created: / # Last reused/audited: / # Authority basis:` header block per root-law `File Provenance Comments`. Missing header = MAJOR finding. `head -5 <file>` confirms presence before PASS. **Amendment 2026-04-17 (report-state lag defense):** every teammate status report that references file state MUST include a `git diff --stat` or `git status --short` snippet of those files. Memory-of-"I did X" without fresh disk evidence is insufficient — accept the disk over the report. Covers phantom-work (edit didn't save) AND report-state lag (describing old snapshot). When reviewing, fresh-grep before flagging any finding; don't act on stale self-reports (I tripped on this with MODERATE-2 and self-retracted — predecessor's immune rule re-applied.)
- **L1 — Scoped INV/FM compliance.** Does the diff respect every `INV-##` and Forbidden Move listed in the touched directory's scoped `AGENTS.md` + machine manifests (`invariants.yaml`, `negative_constraints.yaml`)?
- **L2 — Root Forbidden Moves.** Any root AGENTS.md forbidden move triggered (promote-derived-to-truth, silent-default-over-attribution, mix-high-and-low, JSON-before-commit, Day0 `!= OK` routed to historical Platt, bare `entry_price` to `kelly_size`)?
- **L3 — NC-03 / NC-08 silent default / unit assumption.** Is any default, fallback, or unit inference silently masking what should be explicit attribution? `members_unit DEFAULT 'degC'` class of trap.
- **L4 — Source authority preservation at every seam.** `source`, `data_version`, `authority`, `manifest_hash`, `provenance_json`, `training_allowed`, `causality_status` all propagated; no seam drops them.
- **L5 — Phase-boundary leak.** Any later-phase concern leaked in (e.g. low writer in a high-only phase)? Any earlier contract regressed?
- **WIDE — what did you see that wasn't on my checklist?** Every review. This is where predecessor caught 4B MAJOR-1 and 4CD CRITICAL-1.

## Top-3 antipatterns I'll watch in Phase 4.5 (GRIB→JSON extractor)

1. **Fixture-bypass of the extractor entry point.** Pattern repeated in 4B MAJOR-1 and 4CD MAJOR-2: R-tests construct JSON dicts via helpers instead of calling the real extractor. R-Q..R-U for 4.5 must drive `extract_tigge_mx2t6_localday_max.py`'s top-level function with a real (or synthetic) GRIB fixture and assert the produced JSON. If I see `_make_fake_payload()` instead of `extract_one_grib()`, flag MAJOR.
2. **Unit provenance lost at the GRIB boundary (four-constraints #4).** ECMWF TIGGE delivers mx2t6 in Kelvin. If the extractor emits `unit="C"` but member values are still in K (or silently subtracts 273.15 without writing `members_unit` explicitly), downstream `validate_members_unit` passes on the string but Platt fits off by +273. Members-value physical-plausibility gate (flagged LOW in 4B pre-mortem) is load-bearing here.
3. **Boundary / causality framework wired but dead.** High track rarely trips `boundary_ambiguous` or `N/A_CAUSAL_DAY_ALREADY_STARTED`, so it is tempting to emit constants (`causality_status="OK"` always, `boundary_ambiguous=False` always). Phase 5 low depends on these code paths being real — dead constants silently pass high review and break Phase 5 when low hits them. R-S/R-T from plan must drive through branches that actually fire on synthetic boundary / causality inputs.

## Standing immune-system rule (carried from predecessor)

**"Grep by topic keyword, not remembered function name, before flagging a missing test."** Added to her L0 after MODERATE-7 in 4B was self-retracted — she had alleged a missing prefix-catch test for peak_window `_v2`/`_v3`; the test existed at `tests/test_phase4_parity_gate.py:94` but her review-time grep searched for an imagined function name and missed it. I will disk-verify with topic grep (e.g. `grep -rn "peak_window" tests/`) before any "missing test" MAJOR leaves my console.

## R-letter namespace (team-lead ruling 2026-04-17)

Resolved. See `r_letter_namespace_ruling.md`. Phase 4.5 wins R-Q..R-U (tests landing now); emma's forward drafts renamed R-V..R-Z. Namespace policy: first-to-commit-test wins the letter; once landed in a test file, immutable (alias-rename only in comments).

Authoritative mapping I will enforce in review:

| Letter | Invariant | Phase |
|---|---|---|
| R-Q | GRIB extractor never emits `unit="K"` / Kelvin-range members | 4.5 |
| R-R | Dynamic step horizon: west-coast day7 ⇒ step ≥ 204h | 4.5 |
| R-S | Boundary-classified member ⇒ `training_allowed=false` | 4.5 |
| R-T | Causality slots labeled (high default `"OK"`), never dropped | 4.5 |
| R-U | `manifest_hash` stable across re-extractions (sorted-json canonical) | 4.5 |
| R-V | `Day0LowNowcastSignal(observed_low_so_far=X)` uses X, not silent zero | 6 |
| R-W | `mn2t6` ingest: any boundary member ⇒ `training_allowed=false` | 5 |
| R-X | `settlements_v2` live writer requires explicit `temperature_metric` | 7 |
| R-Y | `kelly_size(..., entry_price=X)` rejects bare-float once typed | 9 |
| R-Z | `classify_chain_state()` returns `CHAIN_UNKNOWN` on stale reconcile | post-DT#4 |

When reading `testeng_emma_final_dump.md §4`, mentally substitute her R-Q..R-U → R-V..R-Z per the mapping above. The dump is provisional prose; the ruling is authoritative until Phase 5+ opens and each letter either lands as a test (immutable) or renames again.
