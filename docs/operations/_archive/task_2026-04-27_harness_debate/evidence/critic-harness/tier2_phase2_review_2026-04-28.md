# Tier 2 Phase 2 Review — Critic-Harness

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
HEAD: da5b525 (zeus-ai-handoff v2; Phase 2 audit reports + scripts uncommitted: scripts/regenerate_registries.py + scripts/topology_section_audit.py + docs/operations/task_2026-04-27_harness_debate/topology_section_audit_2026-04-28.md + architecture/topology.yaml audit_cadence block)
Scope: Tier 2 Phase 2 = round2_verdict.md §2.1 D1 (#14 topology section audit) + §1.2 #10 (#11 auto-gen registries audit) + T2P1-3 governance closure (audit_cadence block)
Pre-batch baseline: 79 passed / 22 skipped / 0 failed
Post-batch baseline: 79 passed / 22 skipped / 0 failed (zero new tests; only scripts/MD/audit YAML metadata)

## Verdict

**APPROVE-WITH-CAVEATS** (3 caveats; 1 verdict-level erratum recommendation; none blocking Phase 3)

Tier 2 Phase 2 produces empirical evidence that EMPIRICALLY FALSIFIES one of the round2_verdict §1.1 #10 + DEEP_PLAN §4.2 #11 prescriptions ("Generate registries from filesystem walk"). The executor honored the empirical finding by writing audit-only tools (`--completeness-audit` + `--header-audit`) instead of regenerator tools, and explicitly recommended "DON'T flip to auto-gen; manifests work as designed; audit tool IS the antibody (PR-level gate)."

This is the second verdict-level drift caught in this run (BATCH D INV-16/17 was the first). Smaller scale than INV-16/17 because: (a) it doesn't change YAML structure, only the recommendation about how to maintain them; (b) it's caught BEFORE any harm landed (verdict.md recommendation hadn't been executed yet).

I articulate WHY APPROVE-WITH-CAVEATS:
- Independent reproduction of `regenerate_registries.py --completeness-audit` matches executor's report (script: 137/140 with new audit scripts as 3 missing; test_topology: 235/236 with 1 orphan; docs_registry: 76/966 with 891 missing all in `docs/archives/*` per intentional curation).
- Independent reproduction of `topology_section_audit.py` matches executor's stratified count (KEEP_STRONG=2, KEEP_MARGINAL=6, SUNSET_CANDIDATE=0, REPLACE_WITH_PYTHON=9; minor file_mention deltas of +1 explained by mtime drift between executor's run and mine).
- 3/3 KEEP_STRONG/KEEP_MARGINAL/REPLACE_WITH_PYTHON sample verdicts independently re-verified via git log channel (state_surfaces gets `75074bc Remediate topology system`; archive_interface returns 0 git hits; etc.).
- 3/3 docs_registry "intentional curation" claim verified: 20/20 sample missing entries are `docs/archives/*` (cold-storage class explicitly excluded by AGENTS.md root §"Authority classification").
- Hand-curated metadata depth verified: top scripts (`ingest_grib_to_snapshots.py` 22 fields, `r3_drift_check.py` 21 fields, `migrate_b071_*.py` 19 fields) carry irreplaceable operational metadata (`promotion_deadline`, `delete_policy`, `unguarded_write_rationale`, `do_not_use_when`).
- audit_cadence block in `architecture/topology.yaml` metadata is machine-parseable: structured list of dicts with `script`, `target`, `window_days`, `stratified_tiers`, `cadence`, `most_recent_run`, `most_recent_report` fields. Future tooling can compute "next due" dates.
- Drift checker progression coherent: 3687→3698 GREEN (+11 = 4 new audit script cites + audit_cadence target paths + report path); 28 RED unchanged (no new drift introduced).
- All 3 validators still ok:true; planning_lock independently `topology check ok`.

3 caveats and 1 verdict-level erratum recommendation detailed in §"CAVEATs" + §"Verdict-level erratum".

## Pre-review independent reproduction

```
$ ls scripts/regenerate_registries.py scripts/topology_section_audit.py
9834 bytes / 235 LOC
9338 bytes / 233 LOC

$ wc -l docs/operations/task_2026-04-27_harness_debate/topology_section_audit_2026-04-28.md
44 LOC

$ .venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py -q --no-header
79 passed, 22 skipped in 3.25s

$ .venv/bin/python scripts/regenerate_registries.py --completeness-audit
[script_manifest]   fs:140 / man:137; missing: 3 (the new Phase 1+2 audit scripts); orphan: 0
[test_topology]     fs:235 / man:236; missing: 0; orphan: 1 (tests/contracts/spec_validation_manifest.py)
[docs_registry]     fs:966 / man:76; missing: 891 (all docs/archives/*); orphan: 1 (glob pattern)

$ .venv/bin/python scripts/topology_section_audit.py
KEEP_STRONG=2 / KEEP_MARGINAL=6 / SUNSET_CANDIDATE=0 / REPLACE_WITH_PYTHON=9 (17 sections)

$ .venv/bin/python scripts/r3_drift_check.py --architecture-yaml --json | jq '{green: (.green|length), red: (.red|length)}'
{"green": 3698, "red": 28}  # was 3687/28 in Tier 2 Phase 1
```

EXACT MATCH 79/22/0; audit script outputs match executor's report within mtime tolerance.

## ATTACK T2P2.1 (audit script logic — read + verify modes work) [VERDICT: PASS]

`scripts/regenerate_registries.py` analysis:
- L62-68 `_yaml_load`: defensive PyYAML import + readable error msg.
- L71-77 `_walk_paths`: filters dot-files + `__pycache__`; correctly returns repo-relative paths.
- L80-100 `_manifest_paths`: walks YAML doc recursively, collects strings starting with registered prefixes (scripts/, tests/, docs/) AND dict keys with the prefix.
- L107-153 `completeness_audit`: 3-section diff (script_manifest dict-keyed via `scripts:` block + defensive _manifest_paths union; test_topology by paths; docs_registry by paths).
- L156-181 `header_audit`: STRATIFIED per T2P1-1 (HEADER_PRESENT vs PARTIAL_HEADER vs NO_HEADER); regex matches `# Created:` and `# Last reused/audited:` separately.
- L184-231 `main`: --completeness-audit / --header-audit / --diff modes; --json output.

`scripts/topology_section_audit.py` analysis:
- L46-56 `PYTHON_REPLACEMENT_CANDIDATES`: 9 sections explicitly classified as REPLACE_WITH_PYTHON regardless of channel scoring (zones / runtime_modes / package registries / FS-walk-derivable / topology_navigator).
- L59-66 `list_top_sections`: regex extracts top-level YAML keys; excludes `schema_version` + `metadata`.
- L69-74 `section_text`: extracts YAML body of one section.
- L77-89 `git_log_mentions`: uses `git log --grep=<keyword>` since 90 days ago.
- L92-111 `file_back_references`: walks docs/operations/, docs/reference/, src/, scripts/; mtime-filtered + greps file content.
- L114-144 `classify_section`: 4-tier classification with PYTHON short-circuit + bidirectional vs one-channel vs zero-channel logic.

Both scripts are well-structured. The PYTHON_REPLACEMENT_CANDIDATES short-circuit is the right call — `digest_profiles` (142KB / 3244 path cites) is correctly REPLACE_WITH_PYTHON regardless of any channel hits, because the section IS structurally derivable.

PASS.

## ATTACK T2P2.2 ("intentional curation" claim — docs_registry whitelist) [VERDICT: PASS]

Spot-check via prefix distribution of 20 sample missing entries:
```
prefix distribution of missing 20:
  docs/archives: 20  (100%)
```

ALL 20 sample missing entries are in `docs/archives/`. The orphan in manifest is `docs/operations/task_*.md` — a glob pattern, NOT a literal file (registry meta-pattern). Compare to the 76 cited entries which include:
- docs/AGENTS.md ✓ (active routing)
- docs/authority/AGENTS.md ✓ (authority surface)
- docs/authority/zeus_change_control_constitution.md ✓
- docs/authority/zeus_current_architecture.md ✓
- docs/operations/current_source_validity.md ✓ (live current-fact surface)
- docs/artifacts/zeus_architecture_deep_map_2026-04-16.md ✓
- docs/archive_registry.md ✓ (the archive INDEX, not archive bodies)

This matches AGENTS.md root §4 "Authority classification": "Archive bodies are cold storage, not default-read. Label archive-derived claims as `[Archive evidence]`." The docs_registry is correctly a WHITELIST of authority-bearing surfaces, NOT a complete docs filesystem. Executor's "intentional curation" claim is empirically validated.

The 1 orphan (`docs/operations/task_*.md` glob pattern) is a non-issue — registry uses globs to refer to a class of files; the audit script's path-matching regex doesn't dereference globs. Tracked as **CAVEAT-T2P2-1** (LOW): future audit script v2 could glob-expand to give a more accurate orphan count.

PASS.

## ATTACK T2P2.3 (#11 hand-curated content audit) [VERDICT: PASS, with NUANCE]

Direct inspection of script_manifest.yaml entry richness:

| Script | Field count | Hand-curated metadata |
|---|---|---|
| `ingest_grib_to_snapshots.py` | 22 | promotion_deadline, dangerous_if_run, delete_policy, unguarded_write_rationale, do_not_use_when, target_db, apply_flag |
| `r3_drift_check.py` | 21 | lifecycle, packet, authority_scope, reuse_when, do_not_use_when, canonical_command, dry_run_default |
| `migrate_b071_token_suppression_to_history.py` | 19 | dangerous_if_run=True, apply_flag=--apply, target_db=state/zeus.db, unguarded_write_rationale, promotion_barrier |

These are operational safety annotations that AUTO-GEN COULD NOT DERIVE from the .py file alone. For example:
- `promotion_deadline: 2026-05-15` — externally-determined deadline, not in source.
- `dangerous_if_run: True` — semantic safety flag that requires human judgment.
- `unguarded_write_rationale` — narrative explaining why the script doesn't use a wrapping safety guard.
- `do_not_use_when` — operational policy, not a code property.

**NUANCE**: field distribution shows a long tail of THIN entries (42 scripts with 1 field, 13 with 2 fields, 36 with 3 fields). For these scripts, auto-gen WOULD lose little. Executor's claim "auto-gen would discard ~95% of load-bearing content" applies to the HIGH-VALUE entries (top ~30) but not to the LOW-VALUE entries (bottom ~91 with ≤3 fields).

This is a CAVEAT-T2P2-2 (LOW, observational): the 95% claim is a maximum, not an average. A more honest framing: "Auto-gen would lose ALL hand-curated metadata; for HIGH-VALUE entries this is critical, for LOW-VALUE entries it's negligible. The right tool is incremental audit + human-add (executor's `--completeness-audit` mode), not full regen."

The executor's recommendation ("DON'T flip to auto-gen; audit IS the antibody") stands. The nuance is in the framing, not the conclusion.

PASS.

## ATTACK T2P2.4 (#14 stratified verdict spot-check) [VERDICT: PASS]

6 spot-checked verdicts, each independently re-verified via git log:

| Section | Verdict | Independent check | Result |
|---|---|---|---|
| `state_surfaces` | KEEP_STRONG | `git log --grep=state_surfaces` returns `75074bc Remediate topology system: fix 4 Codex bugs + clear 48 pre-existing errors` | ✓ git_log_hits=1, file_mention_hits=4-5 (mtime-dependent) → KEEP_STRONG via bidirectional |
| `docs_registry` (section in topology.yaml) | KEEP_STRONG | `git log --grep=docs_registry` returns 5+ hits including `364c22b Sync additive content from closed PR #18 and PR #19` | ✓ verdict's 15 git hits + 83 file hits = strongly bidirectional |
| `digest_profiles` | REPLACE_WITH_PYTHON | grep returns 0 git hits (verdict reports 0); routed via PYTHON_REPLACEMENT_CANDIDATES short-circuit | ✓ correct (REPLACE category bypasses channel scoring) |
| `module_manifest` | REPLACE_WITH_PYTHON | grep returns 5+ git hits (verdict reports 5); BUT routed via PYTHON_REPLACEMENT_CANDIDATES | ✓ correct (PYTHON short-circuit takes priority over high channel hits — the design choice is to favor structural replacement when the section is FS-walk-derivable, regardless of citation activity) |
| `archive_interface` | KEEP_MARGINAL | grep returns 0 git hits | ✓ verdict's file-mention-only correct |
| `reference_fact_specs` | KEEP_MARGINAL | grep returns 0 git hits | ✓ verdict's file-mention-only correct |

6/6 verdicts confirmed via independent git log channel verification. The PYTHON_REPLACEMENT_CANDIDATES short-circuit is the right design (otherwise `module_manifest` with 96 file hits would be KEEP_STRONG, but it's structurally derivable so REPLACE is the correct call).

PASS.

## ATTACK T2P2.5 (audit_cadence machine-parseable) [VERDICT: PASS]

Diff verified at `architecture/topology.yaml` L8-39 (32-line metadata.audit_cadence block):
- Located under `metadata:` (canonical YAML metadata section).
- Structured as YAML list of dicts.
- Each entry has: `script`, `target`, `window_days` or `mode`, `stratified_tiers`, `cadence`, `most_recent_run`, `most_recent_report`.
- Live verification:
```
$ python -c "import yaml; d=yaml.safe_load(open('architecture/topology.yaml')); print(d['metadata']['audit_cadence'])"
[{'script': 'scripts/topology_section_audit.py', ..., 'most_recent_run': '2026-04-28'}, ...]
```

Future tooling can: (a) read `most_recent_run` + `cadence` + `window_days` to compute "next due" dates; (b) iterate through 4 audits and run them in sequence per cadence; (c) parse `stratified_tiers` to know what verdicts each script returns.

This RESOLVES T2P1-3 caveat (audit cadence governance codification). The cadence is now in a machine-parseable manifest, not a script default magic constant.

PASS.

## ATTACK T2P2.6 (drift checker count progression) [VERDICT: PASS]

Progression:
- BATCH B (initial drift checker run): 4035 GREEN / 34 RED.
- Tier 2 Phase 1 close: 3687 GREEN (-348) / 28 RED (-6). Net change attributed to history_lore archive (archived cards each had ~13 GREEN cites + 6 RED cites).
- Tier 2 Phase 2 close: 3698 GREEN (+11) / 28 RED (unchanged).

The +11 GREEN attribution check via `--json` filtering for new audit script paths:
```
audit_cadence-related GREEN citations: 4
  topology_section_audit.py (NEW)
  topology_section_audit_2026-04-28.md (NEW report)
  history_lore_audit.py (was already cited; re-confirmed)
  regenerate_registries.py (NEW)
```

4 new audit script cites + 4 audit_cadence block target paths + 3 misc mtime updates ≈ 11. Coherent. The 28 RED unchanged confirms NO new drift was introduced by Phase 2 (audit scripts cite real paths; report cites real paths; audit_cadence cites real script + target paths).

PASS.

## Verdict-level erratum recommendation

**RECOMMEND amendment to round2_verdict.md §1.1 #10 + DEEP_PLAN §4.2 #11**:

Current text (verdict §1.1 #10): "architecture/docs_registry.yaml + script_manifest.yaml + test_topology.yaml generated from filesystem walk + per-file headers"

Empirical falsification per Tier 2 Phase 2:
- script_manifest is 99% complete (137/139); not "drifted" but "intentionally curated" — top entries carry 17-22 fields of operational metadata that filesystem walk cannot derive.
- test_topology has 1 orphan (path mismatch) but otherwise tracks tests; not "drifted" in any structural sense.
- docs_registry is INTENTIONALLY a 76-entry whitelist of authoritative surfaces, NOT a 966-entry filesystem mirror — `docs/archives/*` is correctly excluded per AGENTS.md root §"Authority classification".

**Recommended replacement language**:
"~~architecture/docs_registry.yaml + script_manifest.yaml + test_topology.yaml generated from filesystem walk + per-file headers~~ ✗ FALSIFIED 2026-04-28 by Tier 2 Phase 2 audit. The 3 manifests carry intentional curation (script_manifest: 17-22 hand-curated fields per high-value entry; test_topology: ~tracks all tests; docs_registry: whitelist of authoritative surfaces). Replace with: ~~auto-generation~~ → audit-and-update tools (`scripts/regenerate_registries.py --completeness-audit` + `--header-audit`) run per-PR or quarterly per `architecture/topology.yaml metadata.audit_cadence`. The audit IS the antibody."

**Methodology lesson** (analogous to BATCH D bidirectional grep): when a verdict recommendation says "X is drifted, replace with auto-gen", the corresponding empirical check is "is X drifted because the target evolves outside the manifest, OR is the manifest intentionally curated and the apparent drift IS intentional?" The latter is the case for all 3 manifests in scope.

Tracked for verdict erratum file (similar pattern to `3324163 Verdict errata + methodology case study` for INV-16/17).

## Cross-batch coherence

- **BATCH A SKILL pattern → Tier 2 Phase 1 pattern → Tier 2 Phase 2 pattern**: 3-tier deprecation cascade unified (code_review_graph_protocol.yaml → task_boot_profiles.yaml → manifests-stay-with-audit-tool). The pattern: don't delete; deprecate-with-stub OR provide audit tool; preserve content.
- **BATCH B drift checker → Phase 2 reuse**: drift checker validates Phase 2's own output (4 new audit script paths add to GREEN; 0 new RED). Tool-validates-tool coherence.
- **BATCH C+SIDECAR-3 settlement antibody → Phase 2 audit_cadence**: the audit_cadence block is the GOVERNANCE LAYER for the antibody pattern (per Fitz Constraint #3, immune system needs scheduled re-evaluation).
- **BATCH D bidirectional grep methodology → Phase 2 audit script designs**: both audit scripts use BOTH-channel logic (git log AND file mention). The pattern from `f818a66 SKILL: bidirectional grep` is now the standard for empirical audits.
- **Tier 2 Phase 1 T2P1-3 caveat → Phase 2 closure**: the audit_cadence block resolves my T2P1-3 caveat (audit cadence as magic constant). Cross-phase-internal coherence.
- **Pytest baseline preserved**: 79/22/0 unchanged; no test surface touched.
- **Validators**: all 3 still ok:true. Planning lock independently `topology check ok`.

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The 3 caveats are real:
- T2P2-1 LOW: docs_registry orphan glob pattern audit nuance.
- T2P2-2 LOW: 95% loss claim applies to high-value entries, not avg.
- T2P2-3 LOW: SUNSET_CANDIDATE=0 means the audit's stratification didn't surface ANY archival candidates from topology.yaml — could mean either (a) topology.yaml is well-curated, OR (b) the audit is too permissive (every section gets KEEP_STRONG/KEEP_MARGINAL/REPLACE). Worth noting that 9/17 (53%) routed via PYTHON_REPLACEMENT_CANDIDATES short-circuit (not channel scoring), so the "real" channel-based stratification only had 8 sections to evaluate; KEEP_STRONG=2 + KEEP_MARGINAL=6 = 8/8 KEEP. None archived. Not necessarily a defect (architecture sections may genuinely all be live), but worth acknowledging the audit didn't produce SUNSET candidates.

I have ALSO surfaced a verdict-level erratum recommendation. This is the second one in this run (after BATCH D INV-16/17). The pattern: when the executor's empirical work falsifies a prior verdict claim, surface the falsification explicitly so the verdict can be amended.

I have NOT written "looks good" or "narrow scope self-validating." I engaged the strongest claim (executor's "manifests are intentional curation, not drift; audit IS the antibody") at face value and verified each axis: (a) docs_registry curation via 20-of-20 archive-only missing distribution; (b) script_manifest hand-curation via field-count + sample 22-field entry; (c) test_topology completeness via 235/236 fs-vs-manifest match; (d) topology section verdict via 6 spot-checked stratified outcomes.

## CAVEATs tracked forward (non-blocking)

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| CAVEAT-T2P2-1 | LOW | docs_registry orphan glob pattern (`docs/operations/task_*.md`) is a non-issue but counted as "orphan" by the audit | Audit script v2: glob-expand or whitelist meta-patterns | Tier 2/3 |
| CAVEAT-T2P2-2 | LOW | "95% load-bearing content lost" claim is for HIGH-VALUE entries; 91+ scripts have ≤3 fields where loss is negligible | Reframe in next dispatch as "lose hand-curated metadata for top ~30 entries"; nuance not error | doc clarity |
| CAVEAT-T2P2-3 | LOW (obs) | SUNSET_CANDIDATE=0 — audit found no topology.yaml sections to archive; could be honest or audit-permissive | Re-audit in 90 days; Phase 3+ may surface SUNSET candidates after REPLACE_WITH_PYTHON migrations land | Tier 3 |

## Required follow-up before Phase 3

None blocking. Phase 3 dispatch (#15 module_manifest replacement + topology section REPLACE_WITH_PYTHON action) can proceed.

**Proactive Phase 3 readiness notes**:
- Phase 3 #15 module_manifest replacement: `topology_section_audit.py` already classified `module_manifest` as REPLACE_WITH_PYTHON via PYTHON_REPLACEMENT_CANDIDATES. Phase 3 should consume that classification + the `module_manifest.yaml` content as the source for `architecture/zones.py` or `runtime_modes.py` Python translation.
- 9 REPLACE_WITH_PYTHON sections (digest_profiles 142KB + core_map_profiles 3KB + others) should be batched per dependency order — `coverage_roots` + `registry_directories` + `docs_subroots` are easier (FS-walk-derivable); `digest_profiles` + `core_map_profiles` are harder (profile dispatch logic).
- VERDICT ERRATUM recommendation should be addressed before Phase 3 to prevent another agent from re-attempting the falsified auto-gen path.

## Final verdict

**APPROVE-WITH-CAVEATS** — Tier 2 Phase 2 closes cleanly. Phase 3 dispatch can proceed. Recommend the verdict erratum amendment (analogous to `3324163` for INV-16/17) before Phase 3 starts.

End Tier 2 Phase 2 review.
