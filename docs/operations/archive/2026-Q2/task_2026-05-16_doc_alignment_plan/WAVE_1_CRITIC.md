# WAVE 1 Critic Verdict — REVISE (2026-05-16)

Opus critic (fresh-context, agent a4cbd982f112ed51f) review of WAVE 1 commits 0630d88ae9..7d163d8daa. 8 probes; 2 PASS + 3 MAJOR + 4 MINOR.

## Verdict: REVISE

3 MAJOR findings (one downgraded from CRITICAL by realist check). All correctable in <30 LOC each.

## 3 MAJOR Findings (amendments required)

### M1: Ungated hardcoded-fallback in `_get_active_rules`
**File**: `maintenance_worker/core/validator.py:135-147`
**Evidence**: `try: load_forbidden_rules() except Exception: return _FORBIDDEN_RULES` — no env check. Test `test_env_var_unset_calls_loader` enshrines always-on fallback, but loader docstring says "HARD FAILURE — maintenance_worker cannot run safely without universal safety defaults" (forbidden_rules_loader.py:104-108). Test ↔ design contract contradicts itself.
**Impact**: WAVE 7 binding-only world → admin mis-mounts `BINDINGS_DIR` → silent fallback to stale hardcoded rules instead of crash.
**Fix**: gate fallback on `MW_FORBIDDEN_RULES_FROM_CODE=1` only. Re-raise `ConfigurationError` when `BINDINGS_DIR` is explicit OR env unset. Update conflicting test.

### M2: YAML safety_defaults missing 5 rules silently demote vs hardcoded
**File**: `bindings/universal/safety_defaults.yaml` vs `maintenance_worker/core/validator.py:171-244`
**Evidence**: Diff = 51 YAML rules vs 54 hardcoded. Missing: `('*/.openclaw/*', authority_surfaces)` + 4 `prefix=True` rules for `/etc`, `/private/etc`, `/usr/local/etc`, `/private/usr/local/etc`. YAML header claims openclaw moved to `bindings/zeus/safety_overrides.yaml` — verified NOT there.
**Impact**: Default production (env unset, YAML loaded, no exception) silently drops `*/.openclaw/*` rule. Currently masked by M1 fallback; once M1 fixed, becomes live regression on Group 2 authority-surface coverage.
**Fix**: add `*/.openclaw/*` to either `bindings/universal/safety_defaults.yaml` or `bindings/zeus/safety_overrides.yaml`. Add 4 root-path rules with `prefix: true` field (loader already supports per `_parse_entries`). Add regression test asserting YAML rule set ⊇ hardcoded rule set.

### M3: `~`-path silent 404 in `_path_matches_row`
**File**: `maintenance_worker/core/archival_check_0.py:111-135` + `architecture/artifact_authority_status.yaml:134` (`~/.openclaw/CLAUDE.md` entry)
**Evidence**: `_path_matches_row` does no `expanduser()` expansion. Empirically verified: `_path_matches_row(Path('/Users/leofitz/.openclaw/CLAUDE.md'), '~/.openclaw/CLAUDE.md')` returns `False`. Real-world archival walks produce resolved absolute paths.
**Impact**: Cross-repo registry entry silently 404s; promised Check #0 protection cannot deliver. Failure mode falls through to ARCHIVABLE (heuristic checks 1-8 still run) — not LOAD_BEARING bypass. Downgraded from CRITICAL.
**Fix**: add `os.path.expanduser(row_norm)` (and symmetrically `cand_norm`) in `_path_matches_row`. Add test for resolved-absolute-path lookup against literal-`~` registry entry.

## 4 MINOR Findings

- Dead code: `_resolve_candidate` defined never called (archival_check_0.py:96); `_ALWAYS_LOAD_BEARING_STATUSES` frozenset defined never read
- Redundant condition: archival_check_0._path_matches_row:130-131 — second clause already covered at line 127
- Unused import: `os` imported in forbidden_rules_loader.py:30 never used
- Mid-file `import logging as _logging` at validator.py:106 (aesthetic)

## Open Question / Deviation Verdicts

| Item | Verdict | Reason |
|------|---------|--------|
| Deviation #1 (6-value enum) | ACCEPT | design doc §13 + ZEUS_BINDING_LAYER §8 confirm |
| Deviation #2 (cross-repo entry) | REVISE per M3 | fix via expanduser |
| Deviation #3 (write_install_metadata direct) | OUT OF SCOPE | WAVE 1.7, not in 6-commit batch |
| OQ1 (cross-repo handling) | REVISE | expanduser fix; separate block over-engineered for 1 entry |
| OQ2 (`_FORBIDDEN_RULES` fallback gating) | REVISE per M1 | gate on MW_FORBIDDEN_RULES_FROM_CODE only |
| OQ3 (_WAVE_PATTERN regex) | ACCEPT | non-greedy + `$` anchor handles edge cases |

## Per-Probe Disposition

| # | Probe | Verdict |
|---|-------|---------|
| 1 | Schema match (registry ↔ reader) | PASS |
| 2 | Module manifest schema match | PASS |
| 3 | forbidden_rules_loader fail-closed | FAIL → M1 |
| 4 | wave_family regex edge cases | PASS |
| 5 | 35 tests are real (not True==True) | PASS |
| 6 | No INV-## edits | PASS |
| 7 | docs_registry dup-check + schema | PASS |
| 8 | Cross-cutting (commit msg, TODOs, skips) | PASS |

## What's Missing (test gaps)

- No integration test exercising `_get_active_rules` against shipped YAML (only synthetic in tmp_path) — would have caught M2
- No regression guard asserting YAML rule count ≥ hardcoded rule count — would have caught M2
- No test for realpath round-trip on registry entries — would have caught M3

## Provenance

WAVE 1 critic dispatched 2026-05-16 by orchestrator session 7f255122 (agent a4cbd982f112ed51f, opus, fresh-context). Pre-commitment predictions vs actuals: ~50% hit rate (predicted: missing fail-closed gating + schema mismatch + regex edge case + dups; actuals: 2 of 4 + 2 unpredicted-real defects). Critic stayed in THOROUGH mode (no escalation to ADVERSARIAL — defects bounded).
