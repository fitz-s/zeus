# Critic Review — Runtime Improvement Engineering Package

Reviewed: 2026-05-15
Reviewer: critic (fresh-context, opus tier)
Files in package: 22 (21 markdown + 1 YAML)
Verdict counts: SEV-1 = 3 ; SEV-2 = 7 ; SEV-3 = 5
Final verdict: REVISE

---

## PROBE CONTRACT VERDICTS

### Probe 1 — UNIVERSAL_TOPOLOGY_DESIGN.md project-agnostic at semantic layer

PASS. Lexical grep returns zero non-meta hits. Semantic scan for Zeus
domain concepts (calibration weighting, ENS member, money path, settlement
bin, K1 split, runtime daemon names, harvester, riskguard, CLOB,
nowcast, Kelly, Platt, Polymarket) returns zero hits in the universal
section. The only `src/` reference is a generic example at line 390
(`src/feature_loader.py`). The only `architecture/` references are
inside fenced-yaml schema templates that name a generic shape, not Zeus
files. Zeus identifiers are confined to `ZEUS_BINDING_LAYER.md` as
required.

### Probe 2 — §3 admission unit is structural replacement, not sidecar

CONDITIONAL PASS. UNIVERSAL_TOPOLOGY_DESIGN.md:86-88 explicitly contrasts
old `(lexical-task-phrase, file-path-list) → profile` against new
`(typed-intent, file-path-list, profile-hint?) → AdmissionDecision`,
and §4 step 5 demotes the hint to diagnostic-only ranking.
This IS structural replacement of phrase-as-routing-key.

Caveat (logged as Major #1): the §3 prose claim "intent — not phrase —
drives routing" is overstated relative to the §4 algorithm. Inspection
of §4 steps 1-4 shows FILES drive primary candidate selection via
the Coverage Map (step 2); intent only filters via cohort
`intent_classes` (§8) and Zeus high-fanout hints (binding §5). Phrase
is removed, intent is added as a secondary filter, but the new keying
function is `(file-path-list, intent-class)` not `(intent, files)`. The
structural change is real but the rhetoric drifts.

### Probe 3 — Per-pattern verdicts on 7 friction patterns

| Pattern | Verdict | Cite |
|---|---|---|
| LEXICAL_PROFILE_MISS | STRUCTURAL_FIX | §3 lines 99-101 (admission unit replacement) |
| UNION_SCOPE_EXPANSION | STRUCTURAL_FIX | §7 Composition Rules + §8 Cohort Admission |
| SLICING_PRESSURE | NOT_ADDRESSED | only named in §12 line 301; no §-mechanism eliminates the incentive structure that drives slicing |
| PHRASING_GAME_TAX | RENAMED_SIDECAR | §14 friction budget MEASURES it but is "not a gate" (line 361); §3 helps indirectly by removing phrase routing, but no first-class fix |
| INTENT_ENUM_TOO_NARROW | STRUCTURAL_FIX | §2.2 lines 50-52 extension mechanism with namespace prefix |
| CLOSED_PACKET_STILL_LOAD_BEARING | STRUCTURAL_FIX | §13 artifact_authority_status registry |
| ADVISORY_OUTPUT_INVISIBILITY | STRUCTURAL_FIX | §11 + G3 (line 372): caller MUST surface advisory; ok=True with non-empty issues is not clean pass |

Two patterns (SLICING_PRESSURE, PHRASING_GAME_TAX) lack structural
fixes despite the package's own claim that v_next "structurally
addresses" all 7. PLAN.md line 18-20 names these patterns explicitly
as the persistent failures across iterations. PHRASING_GAME_TAX is
plausibly resolved-by-implication from the intent-routing change, but
SLICING_PRESSURE has no mechanism — see Major #2.

### Probe 4 — SAFETY_CONTRACT.md gaps in hand-written validator

FAIL. A precondition validator built from the contract has at least
six pre-defense holes:

1. SAFETY_CONTRACT.md line 22 says "read-write or move" — but the
   forbidden category for credentials (line 51-55) is in the same list.
   It is not stated whether bare READ of a credential file (no
   mutation) is allowed. The validator signature in line 130 is
   `validate_action(path, operation)` with operation outcomes —
   READ outcomes are unspecified.
2. TOCTOU race: validator approves a path; pre-existing symlink in
   the filesystem (e.g., `~/Library/LaunchAgents/.archive/X →
   ~/Library/LaunchAgents/X`) routes the mutation back into forbidden
   space. Line 92 forbids the AGENT creating symlinks but says
   nothing about FOLLOWING pre-existing ones. No `realpath`/canonical
   path requirement in the validator spec.
3. Path normalization: a path like
   `~/Library/LaunchAgents/.archive/../com.zeus.live-trading.plist`
   resolves to the active plist. The contract does not require
   pre-normalization before glob match.
4. Hardlinks: a hardlink from a forbidden path to an allowed path
   bypasses pattern matching entirely. Not addressed.
5. `git mv` of a directory containing a forbidden-path subdir is
   one operation but multiple leaves; no per-leaf check requirement.
6. `git remote set-url` (not in forbidden actions) followed by
   allowed `gh pr create` could route maintenance PRs to an
   attacker-controlled remote. Line 95 forbids arbitrary network
   requests but `git push` to a redirected origin is allowed-by-omission.

These are realistic ways a hand-written validator built from this
contract slips. See Critical #1.

### Probe 5 — ARCHIVAL_RULES 8 exemption checks miss load-bearing cases

FAIL. The 8 checks (ARCHIVAL_RULES.md:43-64) scan: PLAN/README first
50 lines, `architecture/reference_replacement.yaml`,
`architecture/docs_registry.yaml`, `git grep` of
src/scripts/tests/architecture, packets modified ≤30d, open PRs,
`.claude/settings.json` + `.codex/hooks.json` +
`~/Library/LaunchAgents/com.zeus.*.plist`, worktree branch names.

NOT scanned (each is a realistic Zeus load-bearing surface):

- Root `AGENTS.md` and the CLAUDE.md chain (root, ~/.claude, ~/.openclaw).
  AUTHORITY_DOCS_INVENTORY shows AGENTS.md at 42 commits/30d as active
  doctrine.
- `~/.openclaw/cron/jobs.json` (the Zeus cron layer); CRON_INVENTORY
  shows 100+ jobs with full path arguments.
- `docs/operations/POLICY.md`, `docs/operations/INDEX.md`, and
  `docs/operations/current_*.md` (DRIFT_ASSESSMENT Cohort 7 lists
  these as authority-bearing surfaces NOT in the inventory).
- The new `artifact_authority_status` registry from
  ZEUS_BINDING_LAYER.md §8 — the universal design's STRUCTURAL fix
  for this exact friction pattern (UNIVERSAL §13) is NEVER consulted
  by the archival 8-check list. This is the most important gap; see
  Critical #2.
- `state/` ledgers and snapshot files (CSV/JSON manifests) that may
  cite packet paths — `git grep` only covers tracked text in
  src/scripts/tests/architecture, not state/.
- Memory feedback files under `~/.claude/projects/.../memory/**`
  (Fitz's MEMORY.md chain has feedback files referencing packets).
- Worktrees that are not currently checked out (tracked branches in
  other locations may still cite the slug in commit messages, not
  branch names).

A closed packet whose only live citation is in root `AGENTS.md` OR
`~/.openclaw/cron/jobs.json` OR the new authority-status registry
passes all 8 checks today and is silently archived.

### Probe 6 — PACKET_INDEX dependency graph acyclicity

CONDITIONAL FAIL. The diagram (PACKET_INDEX.md:18-30) shows P1+P2 in
parallel feeding P3 → P4. P3 depends on {P1, P2} per its text (line
122). But P2's "Dependency" text (line 95-96) says "P1 must be in
shadow mode so the new mechanism is exercised before it can block."
Shadow mode is P3's role (`topology_v_next_phase2_shadow`), not P1's
("additive parallel route"). On the strictest reading P2 → P3 and
P3 → P2: a cycle. On a charitable reading P2's text is a typo and
the graph is correct. Either way the spec is internally
inconsistent. Major #3.

P10 dependency similarly contradicts itself: graph (line 27-28) says
"opportunistic; runs in parallel after P3 ships" — text (line 306)
says "P4 cutover ships first." Minor #4.

### Probe 7 — DRIFT_ASSESSMENT disposes every row of AUTHORITY_DOCS_INVENTORY

FAIL. Inventory has 62 rows. DRIFT_ASSESSMENT cites 54 unique
inventory paths across Cohorts 1-6. The following 8 inventory rows
are NOT classified into any cohort:

```
architecture/artifact_lifecycle.yaml         (11 commits/30d, 2026-05-03)
architecture/city_truth_contract.yaml        (1 commit/30d,  2026-04-22)
architecture/context_budget.yaml             (10 commits/30d, 2026-04-23)
architecture/context_pack_profiles.yaml      (8 commits/30d, 2026-05-01)
architecture/core_claims.yaml                (4 commits/30d, 2026-05-04)
architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md (1, 2026-05-05)
architecture/negative_constraints.yaml       (12 commits/30d, 2026-05-01)
architecture/reference_replacement.yaml      (referenced from ARCHIVAL_RULES check #2 but absent from cohorts)
```

The package's own VERIFICATION_PLAN.md:153-154 asserts "DRIFT_ASSESSMENT.md
cohort assignments cover every row in AUTHORITY_DOCS_INVENTORY (62
rows) — no orphans." This assertion is false on the deliverable as
shipped. Critical #3.

(AGENTS.md is classified — line 56 + 120; the regex initially missed
`(root)` annotation. The 8 missing entries above are confirmed missing
after re-grep.)

### Probe 8 — 30-day dry-run mandate enforced as code, not honor

FAIL. Three contradictions across the deliverable:

- DRY_RUN_PROTOCOL.md:10: "first 30 days of operation should be 100%
  dry-run" — wording is "should be," honor-system.
- TASK_CATALOG.yaml:138: `zero_byte_state_cleanup` has
  `live_default: true` from day 0 — directly contradicts "100%
  dry-run." Same for `agent_self_evidence_archival` (line 187):
  `dry_run: false` `live_default: true`.
- PACKET_INDEX.md:328-329: the cross-packet invariant says "4-week
  dry-run mandate is non-negotiable" — but 4 weeks ≠ 30 days, and
  the only mechanism named is the human not flipping the
  `live_default` flag (the agent never enforces a 30/28-day floor).

There is no code-enforced gate. A human-edited TASK_CATALOG flip
turns on live execution day 1. See Major #4.

### Probe 9 — PURGE_CATEGORIES "Currently observed example" anchors

PARTIAL PASS. Categories 1, 2, 3, 4 cite real entries from
WORKSPACE_MESS_AUDIT (verified line-by-line). Category 5 says "None
at this snapshot beyond the active package directory itself" —
acceptable per the rule discipline. Category 6 (zero-byte cleanup)
cites NO observed example; PURGE_CATEGORIES.md:14 says verbatim "A
rule that cannot cite a current example must be removed." Category 6
violates the package's own discipline. Minor #5.

### Probe 10 — No implementation code in the 22 files

PASS. No Python/shell modules ship as implementation. The Python
helper inside VERIFICATION_PLAN.md:51-63 is a verification probe (a
short coherence check), not implementation logic; this is consistent
with PLAN.md non-goals which forbid `topology_doctor.py` code, not
all probe scripts. Acceptable.

---

## CRITICAL FINDINGS (block acceptance)

### Critical #1 — SAFETY_CONTRACT validator is unimplementable as written

ARCHIVAL_RULES.md and SAFETY_CONTRACT.md both refer to a precondition
validator (`validate_action(path, operation)` at SAFETY_CONTRACT:130)
that is "the LAST line of defense." But the contract under-specifies
the validator on six axes (see Probe 4): credential-read semantics,
TOCTOU symlink-following, path normalization, hardlinks, per-leaf
checks for directory ops, and `git remote set-url` then push. Any
single one of these would let a forbidden mutation land while the
agent is "compliant." For a worker that touches `~/Library/LaunchAgents/`
and opens PRs, this is a load-bearing safety surface — the contract
has to specify the validator's guarantees, not just its name.

Fix: add a §"Validator semantics" to SAFETY_CONTRACT.md specifying
(a) reads on forbidden paths are FORBIDDEN_PATH, (b) every path is
canonicalized via `realpath` before pattern match, (c) symlink and
hardlink resolution policy, (d) per-leaf decomposition of directory
operations, (e) git remote URL is checked against an allowlist
before any push. Make these checks part of the contract that the
implementation packet (P5) must satisfy.

### Critical #2 — ARCHIVAL_RULES does not consult the new authority-status registry

UNIVERSAL_TOPOLOGY_DESIGN §13 introduces `artifact_authority_status`
as the structural fix for `CLOSED_PACKET_STILL_LOAD_BEARING`.
ZEUS_BINDING_LAYER §8 populates it. But ARCHIVAL_RULES.md's 8
exemption checks (lines 43-64) never read the registry. The two
tracks ship the same insight twice and never wire them together.
Result: the daily maintenance agent's archival decision is
operationally blind to the very signal v_next was designed to expose.

This is also why Probe 5 fails — ARCHIVAL_RULES is doing 8 ad-hoc
greps where the design layer offers a single declarative source of
truth. The design seam between the topology track and the workspace
hygiene track is broken.

Fix: ARCHIVAL_RULES check #0 (priority): if the packet's path is
listed in the active `artifact_authority_status` registry with
status ≠ ARCHIVED and ≠ CURRENT_HISTORICAL with explicit
archival-OK, classify `LOAD_BEARING_DESPITE_AGE` immediately, no
further checks needed. Promote the registry to the source of truth
the package claims it is.

### Critical #3 — DRIFT_ASSESSMENT misses 8 of 62 inventory rows; package self-test asserts otherwise

Probe 7 evidence above. The deliverable ships an internal
verification gate
(VERIFICATION_PLAN.md:153-154 "62 rows — no orphans") that is
demonstrably false on the deliverable's own files at the moment of
delivery. This is a coherence failure: the package promises an
invariant it does not satisfy. Either the missing 8 rows must be
classified, or the verification claim must be relaxed and the
omission named explicitly.

Fix: classify the 8 missing rows (likely most belong to Cohort 3
MINOR_DRIFT or Cohort 5 DEMOTE based on their commit cadence). If
any are intentionally deferred, name them in DRIFT_ASSESSMENT.md
under a new "Cohort 0: Deferred to v2" with explicit reason.

---

## MAJOR FINDINGS (must address before P1 ships)

### Major #1 — §3 prose overstates what §4 algorithm does

UNIVERSAL_TOPOLOGY_DESIGN §3 line 101 claims "intent — not phrase —
drives routing." §4 algorithm has FILES driving candidate selection
(step 2 uses Coverage Map by file pattern); intent enters as a cohort
filter (§8 `intent_classes`) and via Zeus high-fanout hints. The
structural change is real (phrase is removed), but the new keying
function is `(files, intent-as-filter)` not `(intent, files)`. The
prose makes a stronger claim than the algorithm delivers.

Fix: rewrite §3 to "intent removes phrase as a routing key; files
drive candidate selection; intent gates cohort admission and
high-fanout disambiguation." This is what §4 actually says. Closing
the rhetoric-vs-algorithm gap matters because P1's acceptance is
"shadow agreement >95%"; if P1 implements §4 literally and the
critic measures against the §3 promise, the divergence will look
worse than it is.

### Major #2 — SLICING_PRESSURE has no structural mechanism

§12 line 301 names the pattern; no §-mechanism eliminates the
incentive that causes agents to ship sub-correct units. The
friction budget (§14) MEASURES the problem but explicitly is "not a
gate" (§14 line 361). Cohort admission (§8) only addresses
declared cohorts; arbitrary unit-of-work splitting is not addressed.

PLAN.md lines 18-20 explicitly names this pattern as one of the
persistent failures the package must absorb. The deliverable does
not.

Fix options: (a) add §X "Unit-of-Work Coherence Score" — admission
returns a score that flags artificially-narrow change sets; (b)
honestly downgrade the claim and say SLICING_PRESSURE is OUT OF
SCOPE for v_next with rationale; (c) tie SLICING_PRESSURE
remediation to the friction budget by adding a soft_block when
attempts spike on the same files within N minutes (revealing that
the agent is slicing under previous-rejection pressure).

### Major #3 — P2/P3 dependency relation is internally inconsistent

PACKET_INDEX P2 text (line 95-96) says "P1 must be in shadow mode"
— shadow mode is P3, not P1. P3 text says it depends on P1+P2. The
graph shows P2 in parallel with P1 feeding P3. Three statements,
mutually inconsistent. A reader cannot determine whether P2 should
ship before P3 or after.

Fix: rewrite P2 dependency to "P1 (additive parallel route) must
ship; P2 implements companion-required as part of v_next's
admission, and runs as additive logic alongside P1; P3 then runs
both P1 and P2 in shadow mode." Then update graph and P3 text to
match.

### Major #4 — 30-day dry-run "mandate" is honor-system

Probe 8 evidence. Two TASK_CATALOG entries (`zero_byte_state_cleanup`,
`agent_self_evidence_archival`) ship live from day 0. DESIGN.md
"30-day uninterrupted run" is an acceptance test, not a runtime
gate. PACKET_INDEX says "4-week dry-run mandate is non-negotiable"
without naming any code mechanism that enforces the floor. The
agent will execute live actions on tick 1 if the YAML says so.

Fix: introduce an `agent_install_date` field captured at first run
into `${STATE_DIR}/install_metadata.json` (immutable). The validator
refuses any `live_default: true` action while
`now - agent_install_date < dry_run_floor_days` (default 30) UNLESS
an explicit `${STATE_DIR}/dry_run_floor_override.ack` file is present
with human signature. This makes the mandate enforceable, not
exhortation.

### Major #5 — `zero_byte_state_cleanup` operates inside a forbidden surface

TASK_CATALOG.yaml:141 lists `target_dirs: ['state/', 'logs/',
'evidence/', 'proofs/']`. SAFETY_CONTRACT.md:45 forbids `state/*.db`,
`state/*.db-wal`, `state/*.db-shm`, `state/*.sqlite*`,
`state/calibration/**`, `state/forecasts/**`, `state/world/**`.
Category 6 cleanup (PURGE_CATEGORIES.md:140-151) checks `lsof` for
active handles and active SQLite ATTACH but the SQLite WAL files
(`*.db-wal`, `*.db-shm`) can be 0 bytes briefly between checkpoints
when no transactions are pending — `lsof` may not show a handle if
the connection is closed in that window. Deletion of a 0-byte WAL
file can cause SQLite to lose recovery state.

Fix: tighten Category 6 forbidden list to include `*.db*` and
`*.sqlite*` glob explicitly, regardless of size. Or restrict
`target_dirs` to exclude `state/` entirely and let the human run
state cleanup manually under a separate procedure.

### Major #6 — Authority status registry is hand-maintained with no freshness check

ZEUS_BINDING_LAYER.md §8 introduces `artifact_authority_status`
with `last_confirmed:` dates. There is no mechanism that (a) requires
the field to be re-touched on a cadence, (b) blocks admission if
`last_confirmed` is older than N days, (c) populates the registry
automatically from packet metadata. HIDDEN_BRANCH_LESSONS Iteration
1 lessons (line 36) explicitly call out "profile required_reads are
maintained by hand with no expiry or freshness check" as a recurring
failure mode — and v_next reproduces the same failure pattern in the
new registry.

Fix: add a `confirmation_ttl_days` field to each row; topology
v_next emits ADVISORY when an admission touches a packet whose
authority status is older than its TTL. This converts hand-maintenance
into surfaced-drift.

### Major #7 — Cross-iteration meta-finding leans on unverified history

README.md:38-46 and HIDDEN_BRANCH_LESSONS.md make a strong claim:
"Across all seven iterations, the admission decision unit was never
changed." The evidence is HIDDEN_BRANCH_LESSONS itself, which the
synthesis lane wrote based on packet PLAN.md content. The actual
admission code (`scripts/topology_doctor_digest.py` at 78kB) was not
read; iterations 4 and 6 both shipped real code that touches admission
directly. The "never changed admission unit" claim is plausible but
unverified against the implementation. If it turns out an iteration
DID partially change the unit, v_next's framing as "the structural
change no prior iteration made" weakens.

Fix: HIDDEN_BRANCH_LESSONS § Cross-Iteration Meta-Pattern should
cite specific symbols/functions in the current `topology_doctor_digest.py`
that demonstrate the keying function. Without code-grounded evidence
the meta-finding is rhetoric.

---

## MINOR FINDINGS

### Minor #1 — Pattern naming inconsistency

UNIVERSAL_TOPOLOGY_DESIGN.md uses both `CLOSED_ARTIFACT_STILL_LOAD_BEARING`
(§12 line 304) and `CLOSED_PACKET_STILL_LOAD_BEARING` (§13 lines
312, 314). The sibling audit packet uses the latter. Pick one;
change the other. The `friction_budget` and `friction_budget_alert`
also alternate. Fix: spell once in §1 glossary and use that
spelling everywhere.

### Minor #2 — §8 Cohort and §9 Companion-Loop-Break are circularly defined

§8 says "Cohort admission generalizes the Companion-Loop-Break …
to a first-class pattern." §9 says "Preserved from prior work as a
special case of Cohort Admission." This is internally consistent
but confusing. State once that §9 is the legacy compatibility shim
for §8 and remove the bidirectional reference.

### Minor #3 — VERIFICATION_PLAN cross-track coherence script is broken

VERIFICATION_PLAN.md:69-75 has a shell pipeline using a sed
substitution `s/.*\`([0-9]+_[a-z_]+/[A-Z_.]+\.(md|yaml))\`.*/\1/`
that contains an unbalanced character class (the `[A-Z_.]+` after
the `/` won't match what's intended; the alternation parens are not
escaped for sed BRE). The script will silently emit garbage rather
than catch real reference rot. Fix the regex or rewrite as Python.

### Minor #4 — P10 contradiction (graph vs text)

PACKET_INDEX.md:27-28 graph: "P10 — opportunistic; runs in parallel
after P3 ships." Line 306 text: "P4 cutover ships first." Pick one.

### Minor #5 — PURGE_CATEGORIES Category 6 violates own discipline

Probe 9 evidence. Either add a real example from WORKSPACE_MESS_AUDIT
(grep `state/` for 0-byte files) or remove the rule per the
package's own line 14 directive.

---

## WHAT'S MISSING (gaps not in the probes)

- **No mention of the Zeus session-mirror loop** (CLAUDE.md root says
  workspace-venus has bidirectional Discord ↔ session sync). The
  maintenance agent could collide with session-mirror writes; no
  guard for it in SAFETY_CONTRACT.
- **No backpressure on PR creation rate.** The agent can open one
  `[maintenance]` PR per tick. If the human ignores 30 PRs they
  stack indefinitely. DRY_RUN_PROTOCOL has stale-proposal cleanup
  but no stale-PR cleanup.
- **No spec for what happens during a long-running migration.**
  DESIGN.md "Refusal Modes" mentions
  `MAINTENANCE_PAUSED` flag but doesn't say who creates it during
  the multi-week P3 shadow window — if v_next migration is in
  progress and the agent runs daily, who blocks the agent during
  cutover days?
- **No statement of what `live_default: true` means for
  `agent_self_evidence_archival`** if disk-free is below threshold.
  The guard refuses the whole tick, including evidence archival,
  which compounds the disk problem.
- **No quarantine-restoration test in MW probes.** MW-1 through MW-10
  test refusal, archival, ack — but not "the human approves a
  rollback; the quarantined file returns to its original location."
  A reversibility claim with no test is just a slogan.
- **The MIGRATION_PATH "shadow log preserved 90 days post-cutover"
  is not in TASK_CATALOG.** Where does the agent learn that 90-day
  retention vs. its own evidence retention?
- **DRIFT_ASSESSMENT formula uses `covered_code_path` field that does
  not yet exist on any doc.** The frontmatter extension is mentioned
  (DRIFT_ASSESSMENT:163, REMEDIATION_PLAN:99) but no packet schedules
  the `covers:` migration. The drift score formula is unrunnable on
  day 1.

---

## VERDICT JUSTIFICATION

REVISE. The package is closer to ACCEPT than to REJECT — the
universal/binding split is clean, the topology v_next has real
structural change at §3 (not a sidecar disguised as one), the
maintenance agent design has correct shape (dry-run, kill switch,
evidence trail, refusal modes), and the verification surface is
genuine.

The reasons it cannot ship as-is:

1. Three CRITICAL findings each represent a deliverable that
   contradicts an internal claim or under-specifies a load-bearing
   safety surface (validator semantics, registry consultation, 8
   missing inventory rows).
2. Two structural friction patterns (SLICING_PRESSURE,
   PHRASING_GAME_TAX) are named but not fixed; the package should
   either fix them or honestly demote the claim.
3. The 30-day dry-run mandate is not enforced as code despite being
   called "non-negotiable" — Major #4 must convert this to a code
   gate before the agent ships.
4. The §3 rhetoric vs §4 algorithm gap (Major #1) is not fatal but
   will distort P1's acceptance signal if not closed first.

Realist Check applied. Critical #1 (validator gaps) was held at
CRITICAL because the surface is `~/Library/LaunchAgents/` and
credential paths — a single missed mutation has no easy rollback.
Critical #2 (registry not consulted) was held at CRITICAL because
the package builds two halves of the same insight that never meet;
this is exactly the kind of seam that produces silent failures.
Critical #3 (8 missing rows) was held at CRITICAL because the
deliverable's own self-test asserts coverage that is false; the
package cannot claim acceptance against a verification gate it does
not pass.

Major #5 (`state/` cleanup) was kept at MAJOR rather than escalated
because Category 6 has lsof + SQLite ATTACH guards that mitigate
most cases; the WAL window is real but narrow.

Adversarial mode escalation: triggered (3+ Major findings discovered
in §3 review). Adjacent surfaces re-checked: pattern naming
(Minor #1), §8/§9 circularity (Minor #2), broken verification regex
(Minor #3), P10 contradiction (Minor #4) — all surfaced after
escalation; would have been missed in thorough-only mode.

Mode: started THOROUGH, escalated to ADVERSARIAL on discovery of
Major #1 + Major #4 + finding pattern in PROBE 7.

---

## OPEN QUESTIONS (unscored)

- The HIDDEN_BRANCH_LESSONS Iteration 5 / `hook_redesign_v2`
  retraction is described as "the one deliberately subtractive
  iteration." Is this lesson absorbed structurally in v_next, or
  is it just stated? The "periodic audit of accumulated sidecars"
  governance pattern (line 234) is mentioned but not scheduled as a
  task in TASK_CATALOG.
- Does the maintenance agent need to know about Codex hooks
  (`.codex/hooks.json`) given Codex parity is "Out-Of-Index" per
  PACKET_INDEX line 338? If Codex agents bypass the topology
  layer, the structural fix only protects half the agent fleet.
- ARCHIVAL_RULES "Wave Packets" special case (line 117) treats
  wave families atomically — but the rule for grouping is "share
  family slug." How is family slug computed? Is `task_*_wave1` and
  `task_*_wave2` always the same family, or only if their full
  slug prefixes match?
- DRY_RUN_PROTOCOL "Bulk Acknowledge" (line 169-180) auto-honors N
  ticks even on changed proposals. This is the only auto-ack
  mechanism but is documented as "Recommended for
  zero_byte_state_cleanup only" — yet that task already has
  `live_default: true`, so the bulk-ack mechanism appears to have
  no consumer.
- Does the `companion_required` mechanism (REMEDIATION_PLAN:159-180)
  conflict with the Companion-Loop-Break (UNIVERSAL §9)? Both
  involve companion files but from opposite directions: §9
  auto-admits a companion that's already in --files;
  companion_required blocks if the companion is NOT in --files.
  The two should be one mechanism with two failure modes, not two
  mechanisms.

---

## REVISE PUNCH LIST

In order of fix difficulty:

1. (Critical #3) Classify the 8 missing inventory rows in
   DRIFT_ASSESSMENT.md or relax the VERIFICATION_PLAN.md "no
   orphans" assertion.
2. (Critical #2) Add ARCHIVAL_RULES check #0 that consults
   `artifact_authority_status` registry; promote registry to
   source of truth.
3. (Critical #1) Add §"Validator semantics" to SAFETY_CONTRACT.md
   covering reads, realpath, symlinks, hardlinks, per-leaf
   decomposition, git-remote allowlist.
4. (Major #4) Convert 30-day dry-run "mandate" into a code gate
   keyed on `agent_install_date` with explicit ack file for
   overrides.
5. (Major #2) Either add a SLICING_PRESSURE structural mechanism
   or honestly remove the claim that v_next addresses it.
6. (Major #1) Reword UNIVERSAL §3 to match what §4 algorithm
   delivers ("files drive candidate selection; intent gates
   cohort and high-fanout").
7. (Major #3) Resolve P2 dependency contradiction in PACKET_INDEX.
8. (Major #5) Tighten Category 6 to forbid `*.db*` / `*.sqlite*`
   regardless of size, or remove `state/` from `target_dirs`.
9. (Major #6) Add `confirmation_ttl_days` to authority status
   registry; admission emits ADVISORY when stale.
10. (Major #7) HIDDEN_BRANCH_LESSONS § Cross-Iteration cites a
    function/symbol in topology_doctor_digest.py that demonstrates
    the unchanged keying function.
11. (Minors 1–5) pattern naming, §8/§9 circularity, broken regex,
    P10 contradiction, Category 6 example.

After REVISE, re-submit for ACCEPT. The skeleton is sound; the
gaps are specific and addressable.
