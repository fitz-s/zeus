# Hook redesign v2 — universal critic adversarial review (2026-05-07)

**Reviewer:** universal critic (`~/.claude/agents/critic.md`), agent a38b44da7ed1ef8c6
**Target:** `docs/operations/task_2026-05-07_hook_redesign_v2/PLAN.md` (planner output, K=3, 480 LOC)
**Verdict:** **NO-GO** — operator architectural decision required on K3 authorization model before planner re-attempt.

> Note: critic agent's `agent_prompt` prohibits writing `.md` reports; findings were SendMessage'd to coordinator and recorded here by coordinator on critic's behalf.

## Findings (10 adversarial asks, full transcript)

### CRITICAL

**#3 — Phase-order claim is structurally false.**
PLAN §4 claims K1 → K3 → K2 with each phase unblocking the next inside the agent (no operator shell). Trace: after K1 lands (boot self-test + degraded-mode), `pre_edit_hooks_protected` (`dispatch.py:424` — `_run_blocking_check_pre_edit_hooks_protected`) is unchanged. It has zero awareness of any K1 sentinel/boot-fail signal. So Phase K3 still requires an Edit/Write to `.claude/hooks/**`, which is still gated, which still requires operator shell. The "each unblocks next" claim is fictional unless K1 *silently* includes K1b (modifying `pre_edit_hooks_protected` to honor a degraded-mode bypass), which the PLAN does not specify.

**#4 — K3 authorization model is empirically forgeable on this mac.**
Critic ran two probes on this mac under uid 501:
- `chflags uchg <file>` (immutable flag) is removable by file-owning user → not a security boundary, just an inconvenience flag
- `~/.gitconfig` is mode `0644` owned by `leofitz` → agent can `sed -i '' 's/email = .../email = operator@.../'` and forge any `user.email` commit attribution
Two of three K3 channels (immutable flag + commit author identity) are pure honor system on single-user mac. The third (timing window) is behavioral, not structural. PLAN's "honest single-user-mac authorization" framing is therefore dishonest about what the model actually achieves: it provides *deterrence against accidental agent edits*, not *authorization gating*. If that's the goal, the PLAN should say so.

### MAJOR

- **#1 K-compression integrity** — K1 bundles 3 sub-decisions (boot self-test + degraded-mode + handler binding). Could be K=4 or K=5 if recovery is genuinely orthogonal to schema closure.
- **#2 Recovery-loop deadlock** — PLAN §3 startup says fail-open degraded-mode if self-test fails. But what if degraded-mode itself crashes (e.g., advisory hook with bug)? No 2nd-level recovery specified. Could regress to fail-closed.
- **#5 Evidence file parser format hand-waved** — PLAN claims `fields_required` + `operator_signature` enforcement but does not specify markdown-frontmatter vs YAML vs regex-grep parser. Without a concrete format spec, the implementation is unbounded.
- **#6 Bash command-head regex coverage incomplete** — PLAN §3 anchors `gh pr create` to line head. Does NOT cover: `(cd /tmp && gh pr create ...)`, `bash -c 'gh pr create ...'`, `xargs gh pr create`, `$()` substitution where outer command differs.
- **#7 File-uniform gating overpromised** — PLAN §3 claims regex set catches Bash writes to protected paths. Holes: `python3 -c "shutil.copy('x', '.claude/hooks/dispatch.py')"`, `git checkout other-branch -- .claude/hooks/dispatch.py`, `mv /tmp/x .claude/hooks/dispatch.py`, `> .claude/hooks/dispatch.py`. The implementation surface is open-ended; regex pretends completeness.

### MINOR

- **#8 Migration transient** — §6 doesn't specify whether v1+v2 coexist or is big-bang; rollback path absent.
- **#9 GPG-skip test fiction** — §5 invariant "agent cannot bypass via Bash redirect" not actually expressible without simulating full Claude Code hook chain.
- **#10 Open-question punts** — §7 has 5 items; at least 2 have principled architectural answers planner didn't surface.

## Operator decision required before planner re-attempt

Critical finding #4 forces an architectural choice on the K3 authorization model. Three options:

**Option A — Drop K3 (acknowledge no real authorization).**
Stop pretending honor system is authorization. K3 becomes: protected paths simply log all edits to a tamper-evident audit trail (append-only jsonl with HMAC). Agent edits ARE allowed, but every edit is observable to operator post-hoc. No deterrence theater.

**Option B — GPG-only authorization.**
Operator GPG private key in macOS Keychain (agent cannot access without explicit user-approved Touch ID / password prompt). `evidence/<hook_id>__<override_id>.signed.asc` must be GPG-signed by operator key. Heavy infrastructure (~1 day to set up + Keychain access policy), but a real boundary.

**Option C — External trust anchor.**
Agent posts override request to a small local service (e.g., FastAPI on a port the agent shell can't reach, OR a webhook that requires operator click in browser/phone). Service signs the override. Requires operator running a sidecar process; high friction.

## Recommended path

**Drop K3 (Option A) for now**, scope v2 to K1 + K2 only, and treat protected-path edits as audit-trailed advisory rather than gated. Operator post-hoc review remains the actual control. This is honest about the single-user mac reality and unblocks K1+K2 implementation immediately. K3 (real authorization) becomes a separate future task once Touch ID / Keychain integration design is scoped.

## PLAN.md sections requiring revision

§2 (K-compression labels), §3.1 (startup self-test recovery), §3.2 (gating layer regex coverage), §3.3 (authorization model honesty), §4 (phase order — drop K3 from this round), §5 (test invariants — drop the unmeasurable ones), §7 (open-questions — reclassify 2 of 5 as architectural, not punts).
