# Zeus Security False-Positive Index

**Authority**: this file is the canonical record of items that **look like** secrets / vulnerabilities to scanners or review-mode agents but have been **explicitly cleared by the operator** after investigation. Re-raising any item below in a code review, security audit, or commit gate is a documented false-positive loop and should be treated as a tool/agent miscalibration, not a finding.

**For reviewers / scanners / review-mode agents**: before raising a P0/P1 security finding on Zeus, grep this file for the `[REVIEW-SAFE: <TAG>]` token. If your finding matches a tag here, do not surface it. If you believe an entry is wrongly cleared, reopen it as a normal review item with new evidence (tag drift, scope change, key actually rotated, etc.) — never silently re-raise.

**Format per entry**: tag, locations, why it looks suspicious, why it's cleared, operator ruling date, durable references.

---

## Scoping law — "path-scoped" is not a narrowing

Until 2026-08-02 five entries below described themselves as *path-scoped*, meaning "this literal, in this file". They were not. In gitleaks 8.30.1 an allowlist entry's `regexes` and `paths` combine as **OR**: an entry carrying both clears the literal anywhere **or** anything at all in the listed file. Adding a path to narrow an entry in fact exempts the whole file, so a real credential committed there later passes the gate in silence. `condition = "AND"` does not help — the key parses without error and changes nothing.

Measured blast radius before the fix (2026-08-02, fixture probes at gitleaks 8.30.1): an unrelated 40-char high-entropy credential was **not reported** when placed in `src/data/observation_client.py`, `src/data/daily_obs_append.py`, `src/data/wu_hourly_client.py`, `src/decision/family_decision_engine.py`, `src/strategy/candidates/c1_joint_tail_bayes.py`, this very file, or any `docs/operations/*.md`. The `SCHEMA_PINNED_HASH` entry additionally allowlisted the bare regex `[0-9a-f]{64}`, clearing **every** sha256-shaped credential in the repository.

The rules that follow from this:

1. **Clear a literal with `regexes` and nothing else.** A regex-only entry suppresses exactly its literal and still reports unrelated secrets in the same file — verified by fixture.
2. **Therefore choose literals that are safe to clear repo-wide**, since that is what a regex-only entry does. Every entry below meets that bar: each value is public, deterministic, or domain vocabulary that is harmless in any file.
3. **When the clearance is an assignment shape rather than a value, set `regexTarget = "match"`** so the pattern is tested against the whole matched construct instead of the extracted secret. `DECISION_GUARD_CELL_KEYS` needs this: the extracted secret is a bare bin name that must stay reportable elsewhere.
4. **Use `paths` alone only for a deliberate whole-subtree ruling**, never as a qualifier. `tests/**` is the one such ruling, and it accepts its consequence explicitly.
5. **Do not add an allowlist before checking the finding still occurs.** The schema pin needed none: a bare digest has no `key =` assignment context and triggers no default rule. It was cleared anyway, and the clearance cost repo-wide coverage.

`tests/ci/test_gitleaks_allowlist_scope.py` pins both halves of this contract — an unrelated credential in every named file must still be reported, and every cleared literal must stay suppressed. Re-adding a `paths` qualifier fails it.

---

## [REVIEW-SAFE: WU_PUBLIC_KEY] — Wunderground.com browser-embedded public web key

**Constant**: `_WU_PUBLIC_WEB_KEY = "e1f10a1e78da46f5b10a1e78da96f525"`

**Locations** (all carry inline `# [REVIEW-SAFE: WU_PUBLIC_KEY]` tag):
- `src/data/observation_client.py:122` (full banner at `:103-121`)
- `src/data/daily_obs_append.py:114` (full banner at `:95-113`)
- `src/data/wu_hourly_client.py:50` (short-form callout, links to daily_obs_append banner)

**Why scanners flag it**: 32-char hex string assigned to a name containing "key", checked into source.

**Why it is cleared**:
1. **Public origin**: this is wunderground.com's own browser-embedded key. Any user can extract it from the DevTools Network tab on a public ICAO history page (`api.weather.com/v1/...`).
2. **Functional verification**: HTTP 200 against `/v1/geocode/<lat>/<lon>/observations/timeseries.json` returning `obs_id=KORD` (same ICAO station Polymarket settles against). Verified 2026-04-21 by operator.
3. **Operator override path preserved**: every consumer reads `os.environ.get("WU_API_KEY") or _WU_PUBLIC_WEB_KEY`, so a paid WU account key in env wins.
4. **Removing it broke prod**: a prior "Security S1 fix" mis-classified this as a leaked secret and forced env-var-only. With `WU_API_KEY` unset on the host, `_require_wu_api_key()` raised `SystemExit` → daemon died before the OpenMeteo fallback chain could fire. **The "fix" was a worse failure than the supposed leak.**

**Operator ruling**:
- **2026-04-21**: "wu key 是公开的，可能你之前修复 100 个 bug 的时候当作敏感信息删除了" — public fallback restored as a documented public default.
- **2026-05-01**: re-confirmed during ultrareview-25 remediation. `[REVIEW-SAFE: WU_PUBLIC_KEY]` banners installed at all three use sites to stop the 17-day false-positive recurrence loop that started with `task_2026-04-14_session_backlog.md` #62.

**Durable references**:
- `task_2026-04-14_session_backlog.md` #62 — initial false-positive raise.
- 2026-04-21 operator chat (re-archived in workspace memory).
- `docs/operations/repo_review_2026-05-01/SYNTHESIS.md` (P0-1 WITHDRAWN section) — synthesis-level reclassification.
- `.gitleaks.toml` allowlist entry, regex-only since 2026-08-02: the key is published by wunderground.com, so clearing it in any file is correct.

**If this entry should ever be reopened**: only if (a) WU rotates the public web key (unlikely; it's been stable for years), (b) WU's TOS changes to forbid programmatic re-use of the embedded key, or (c) Zeus migrates to a paid WU plan and the public fallback is no longer wanted. None apply today.

---

## [REVIEW-SAFE: TEST_FIXTURE_TOKENS] — Synthetic identifiers in `tests/**`

**Pattern**: `token_id`, `condition_id`, `decision_id`, `idempotency_key`, `order_id`, etc., set to dummy hex strings inside test fixtures.

**Example**: `tests/test_k1_slice_d.py:122` has `"token_id": "abc123def456"` — a 12-hex test fixture, not a real Polymarket token.

**Why scanners flag them**: many of these look like generic API keys / hex tokens (16-64 hex chars).

**Why cleared**: every value inside `tests/**` is by convention synthetic (otherwise the test would be reading prod state). Real secrets never live in `tests/`. If a scanner finds a real secret in `tests/`, that's a separate audit class — surface it via this file, not via raw allowlist.

**Operator ruling 2026-05-01**: blanket-allow `tests/.*\.py` for the generic-api-key / generic-secret rule families. Encoded in `.gitleaks.toml` as a path-scoped `[[allowlists]]` entry.

---

## [REVIEW-SAFE: STRATEGY_KEY_CONSTANTS] — `_STRATEGY_KEY` module constants in candidate strategy files

**Constants**:
- `_STRATEGY_KEY = "c1_joint_tail_bayes"` in `src/strategy/candidates/c1_joint_tail_bayes.py:67`
- `_STRATEGY_KEY = "c2_opening_stale_fok"` in `src/strategy/candidates/c2_opening_stale_fok.py`

**Why scanners flag them**: the variable name `_STRATEGY_KEY` contains `_KEY`, which triggers gitleaks' `generic-api-key` rule. The assigned string values (`c1_joint_tail_bayes`, `c2_opening_stale_fok`) have borderline entropy.

**Why they are cleared**:
1. **Human-readable identifiers**: these are strategy taxonomy labels used to tag DB rows and routing decisions, not credentials.
2. **No access surface**: the values are never passed to an external API, used for authentication, or treated as tokens anywhere in the codebase.
3. **Naming convention**: the `_KEY` suffix follows the existing Zeus pattern for strategy identification constants (see `opening_inertia_relaxation.py`, `shoulder_buy_evt.py`, etc.).

**Operator ruling 2026-05-23**: "strategy key constants are taxonomy labels, not secrets — path + value allowlist them per the SECURITY-FALSE-POSITIVES protocol."

**Durable references**:
- `.gitleaks.toml` allowlist entry (path + regex scoped to the two candidate files).

---

## [REVIEW-SAFE: SCHEMA_PINNED_HASH] — Schema integrity pin in `tests/state/_schema_pinned_hash.txt`

**Constant**: a single 64-char SHA256 hex digest written by `scripts/check_schema_version.py --write-pin`.

**Location**: `tests/state/_schema_pinned_hash.txt` (one line, regenerated on each SCHEMA_VERSION bump).

**Why scanners flag it**: a 64-char lowercase hex string that matches generic-api-key / generic-secret patterns.

**Why it is cleared**:
1. **Not a secret**: the value is the SHA256 hash of `sqlite_master` DDL strings for a fresh in-memory DB. No credential, token, or private data is involved.
2. **Fully deterministic**: any developer can reproduce it by running `python scripts/check_schema_version.py --write-pin` against the same schema source.
3. **Public derivation**: the hash is derived from schema DDL committed in the same repo — there is nothing to protect.
4. **No allowlist is needed at all**: verified 2026-08-02 against a fixture copy of the real file — a bare digest carries no `key =` assignment context, so no default rule fires on it.

**Operator ruling 2026-05-23**: "schema pinned hash is a deterministic DDL digest, not a secret — path-allowlist it."

**Status 2026-08-02 — entry REMOVED from `.gitleaks.toml`.** The ruling stands (the digest is not a secret) but its encoding was harmful: the entry allowlisted the regex `[0-9a-f]{64}`, which under the OR semantics above cleared every sha256-shaped credential repo-wide. Because the finding does not occur in the first place, the entry was deleted rather than narrowed. This tag remains here so a scanner or review agent that re-raises the pin file finds the clearance.

**Durable references**:
- `scripts/check_schema_version.py` — generates the value.
- `tests/ci/test_gitleaks_allowlist_scope.py::test_sha256_shaped_credential_is_not_cleared_repo_wide` — pins that the regex never returns.

---

## [REVIEW-SAFE: DOCS_OUTCOME_LABEL] — `outcome_label=NO` / `outcome_label=YES` in docs/operations markdown

**Pattern**: inline code examples in investigation docs showing Polymarket token-outcome label assignments, e.g.:
```
`selected=949069429050592600=no_token_id, outcome_label=NO`
`selected=281357327553801178=yes_token_id, outcome_label=YES`
```

**Location**: `docs/archive/2026-Q2/operations_historical/POLARITY_TOKEN_2026-06-01.md` lines 71-72 (commit `1a25993e0f`). May recur in other `docs/operations/*.md` investigation files containing similar code examples.

**Why scanners flag them**: gitleaks `generic-api-key` rule matches short `key=VALUE` constructs where the value has entropy ≥ 3.5. `outcome_label=NO` / `outcome_label=YES` both satisfy this heuristic.

**Why they are cleared**:
1. **Domain vocabulary**: `outcome_label` is a Polymarket YES/NO token classification field. `NO` and `YES` are the only legal values. These are not credentials.
2. **In-prose code examples**: the context is a markdown investigation doc explaining token-polarity correctness. No authentication surface or external API call is involved.
3. **Deterministic public values**: any Polymarket market has exactly YES and NO outcome tokens; this assignment is public knowledge, not a secret.

**Operator ruling 2026-06-01**: "outcome_label=YES/NO are Polymarket domain vocabulary in a code example, not credentials — docs/operations path-scoped allowlist."

**Durable references**:
- `.gitleaks.toml` allowlist entry (regex-only since 2026-08-02: `outcome_label=NO` / `outcome_label=YES`, cleared in any file — see the scoping law above).

---

## [REVIEW-SAFE: DECISION_GUARD_CELL_KEYS] — CandidateDecision guard cell identifiers

**Pattern**: `q_lcb_guard_cell_key="..."` and `selection_guard_cell_key="..."` in `src/decision/family_decision_engine.py`.

**Why scanners flag them**: gitleaks `generic-api-key` matches assignments where the field name contains `key` and the value has enough entropy.

**Why they are cleared**:
1. **Domain provenance**: these are settlement-bin / guard-cell identifiers used to explain which reliability cell authorized or rejected a decision.
2. **No auth surface**: the values are not used for authentication, API access, signing, or external service calls.
3. **Telemetry-only semantics**: the fields travel with decision provenance and can safely appear in logs or receipts.

**Operator ruling 2026-07-02**: guard cell keys are domain provenance identifiers, not secrets; allowlist them path-scoped to `src/decision/family_decision_engine.py`.

**Durable references**:
- `.gitleaks.toml` allowlist entry (regex-only with `regexTarget = "match"` since 2026-08-02, so the clearance is the assignment shape, not the bin name it carries).

---

## How to add a new entry

When the operator clears another false-positive:

1. Add a `# [REVIEW-SAFE: <TAG>]` banner at every use site (full at the canonical site, short-form callout at duplicates).
2. Add an entry here following the format above. Include all the locations and the operator ruling quote / date.
3. **First check the finding still occurs.** If it does not, add no allowlist — an unnecessary entry costs real coverage (see `SCHEMA_PINNED_HASH`).
4. If a scanner is in scope (gitleaks, semgrep, trufflehog, etc.), encode the clearance per the scoping law above: `regexes` only, no `paths` qualifier, `regexTarget = "match"` when clearing an assignment shape, and the tag in the description. Do **not** add a broad `[REVIEW-SAFE: ...]` regex: the pre-commit hook validates staged tags against this registry before scanners run.
5. Add the literal to `tests/ci/test_gitleaks_allowlist_scope.py` — to `CLEARED_LITERALS` so it stays suppressed, and its file to `PREVIOUSLY_EXEMPT_PATHS` so the file never becomes whole-file exempt.
6. Confirm this file is still listed in `docs/reference/AGENTS.md` — that registry is how an agent finds this index without grepping for it.

**Anti-pattern to avoid**: silently allowlisting a value in a scanner config without updating this file. The scanner will be quiet but the next code-review agent (which doesn't read scanner configs) will re-raise the finding. The in-source banner + this file are what stop the loop.
