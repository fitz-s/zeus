# Adversarial review — safety-gate hook cleanup (2026-06-13)

Scope: diff of .claude/hooks/dispatch.py, registry.yaml, settings.json
Method: handlers invoked LIVE via _run_advisory_check and end-to-end via main_multi
(real subprocess). Each attack string built inside Python (not on the shell line) so the
live PreToolUse gate does not block the harness. OLD bare-substring regex cloned locally
and diffed against the NEW anchor to isolate regressions introduced by THIS change.

## VERDICT: REVISE — 1 CRITICAL (under-block), 0 HIGH

The anchoring change fixes a real false-DENY but introduces a real false-ALLOW in TWO
BLOCKING gates. Consolidation (--multi), deletion, and boot-self-test relocation are all
correct.

---

## CRITICAL-1 — command-position anchor under-blocks EXECUTING git in both BLOCKING gates

Files / lines:
- dispatch.py:1659  maintree_git_state_guard (BLOCKING) new anchor
- dispatch.py:236   cotenant_staging_guard  (BLOCKING) new anchor
- shared lead-in: (?:^|[;&|]\s*)(?:[A-Za-z_]\w*=\S+\s+)*(?:/\S*/)?git

The anchor treats git as command-position only after start, a ;/&/| separator,
env-assignments, or an absolute path. It does NOT recognize command substitution, child
shell, redirect, brace/subshell groups, or leading wrapper words. The OLD bare \bgit
matched all of them. So forms the OLD gate BLOCKED, the NEW gate ALLOWS (exit 0), and they
all EXECUTE a real branch-mutating git checkout / broad git add -A on the LIVE MAIN tree.

Verified live (cwd=_MAIN_TREE, _run_advisory_check -> exit code):

  maintree_git_state_guard:
    git checkout main                          -> 2 BLOCK   (control, OK)
    $(git checkout main)                       -> 0 ALLOW   *** under-block (cmd-subst)
    `git checkout main`                        -> 0 ALLOW   *** under-block (backtick)
    bash -c "git checkout main"                -> 0 ALLOW   *** under-block (child shell)
    >f git checkout main                       -> 0 ALLOW   *** under-block (redirect)
    { git checkout main; }                   -> 0 ALLOW   *** under-block (brace group)
    if true; then git checkout main; fi        -> 0 ALLOW   *** under-block (then kw)
    ( git checkout main )                      -> 0 ALLOW   *** under-block (subshell parens)
    time git checkout main                     -> 0 ALLOW   *** under-block (wrapper word)
    command git checkout main                  -> 0 ALLOW   *** under-block
    nice git checkout main                     -> 0 ALLOW   *** under-block
    for d in a b; do git checkout $d; done     -> 0 ALLOW   *** under-block (do kw)

  cotenant_staging_guard (git add -A): the same wrapped forms -> 0 ALLOW.

End-to-end through the real `dispatch.py --multi ...` subprocess:
    ( git checkout main )           -> rc=0   (reaches the caller as ALLOW)
    bash -c "git checkout main"     -> rc=0
    git checkout main               -> rc=2   (plain still blocks)

Each ALLOWED form is a command the shell actually runs: $(...)/backticks in a subshell,
bash -c in a child shell, ( ... ) in a subshell, >f cmd runs cmd, { ...; } and
if/then + for/do in the current shell. Every one mutates the main tree HEAD/index — the
exact 2026-06-12 incident class the gate exists to prevent. The && incident shape itself
still blocks (& is in the lead-in), but ( git checkout main ) and time git checkout main are one
trivial token away and are forms agents emit routinely, non-adversarially.

Why CRITICAL not MAJOR (Realist Check):
- Confirmed REGRESSION vs the prior committed gate, not a pre-existing gap: OLD \bgit
  caught all 12 forms; NEW catches 6, misses the 12 listed.
- Blast radius = live trading daemons' checkout (operator top-severity surface). No
  upstream mitigation — this hook IS the only gate; on ALLOW the command runs.
- Detection is silent (daemon branch moves, no error surfaced).
- NEVER-downgrade rule applies (state corruption of the live tree).

Root cause: the anchor was lifted from pre_checkout_uncommitted_overlap (dispatch.py:293),
an ADVISORY hook where a miss is harmless. Promoting an advisory-grade matcher to gate a
BLOCKING decision elevates the same evasion class from "advisory miss" to "safety bypass."
The false-fire fix and the bypass-surface widening are coupled in one regex; decouple them.

Minimal fix (keep the false-fire fix, restore coverage): widen the command-position
lead-in to include shell openers and wrapper words and treat $(/backtick as command
position, e.g.:

    (?:^|[;&|(){}]\s*|\b(?:do|then|else|time|command|nice|env|builtin|exec|sudo)\s+|\$\(|`)
    (?:[A-Za-z_]\w*=\S+\s+)*(?:/\S*/)?git

Re-run the 12-form matrix; all must BLOCK except the 3 false-fire controls
(echo git checkout main ; grep -r 'git checkout' ; git log --grep=checkout) which must stay ALLOW —
they remain ALLOW under the widened pattern (echo/grep/log are not openers and the buried
git is not at a command position). Apply the identical lead-in to BOTH BLOCKING handlers
and to the advisory source it was copied from.

---

## Confirmed-correct (no finding)

1. --multi consolidation is faithful: ANY blocking constituent -> exit 2 (verified plain
   maintree + cotenant via real subprocess); runs ALL hooks, no early stop; every reason
   on stderr; an exception in an EARLY hook does NOT skip a LATER blocking hook
   (monkeypatched invariant_test to raise; main_multi still returned 2). All 4 BLOCKING
   ids present and reachable. main_multi skips main's registry missing_spec lookup, but an
   unknown id falls open via _ADVISORY_HANDLERS -> same net behavior; no regression for the
   21 valid ids.
2. maintenance_worker_dry_run_floor deletion complete: gone from registry.yaml, both
   settings.json matchers, and _ADVISORY_HANDLERS. Active settings id-set = 21, all 21
   have handlers (zero fall-open). No other hook dropped; matchers unchanged.
3. _boot_self_test moved off module import; `dispatch.py boot_self_test_only` prints
   "OK: all 21 registry hooks have handlers"; SessionStart entry added; normal dispatch no
   longer pays it; integrity check reachable.
4. The two UNCHANGED BLOCKING handlers (pr_create_loc_accumulation ^-anchored;
   pre_merge_comment_check (?:^|\s)-anchored) were not touched; out of scope for this
   regression.

## LOW (optional)

- dispatch.py:1337 _run_advisory_check_maintenance_worker_dry_run_floor is now orphaned
  dead code (no registry/settings/dict ref). Harmless; delete for hygiene.
- settings.json.bak-worktree-selfmerge-20260613-182037 still references the deleted id
  (inert backup).


---

## Re-review 2 — CRITICAL-1 fix (2026-06-13)

Fix: regex anchor replaced by command-segment tokenizer
`_git_subcmd_at_command_position` (dispatch.py:157), used by both BLOCKING handlers
(maintree dispatch.py:1687, cotenant dispatch.py:270). Splits on
`; & | newline ( ) { } backtick $( do then else (bash|sh|zsh) -c`, then per segment
strips env-assignments + wrappers (time|command|builtin|exec|nice|env|sudo|xargs) +
redirects, then matches `git <subcmd>` at segment HEAD.

### VERDICT: REVISE — 0 CRITICAL, 2 HIGH

Big improvement: ALL 12 round-1 under-blocks now BLOCK (verified live):
$( ), backtick, bash -c, ( ), { }, if/then, for/do, >redirect, env-prefix, pipe,
abspath, the && incident shape, plus tab, process-subst <(, here-string-in-subst,
nested $( $( ) ), $'...', no-space ( )/{}, `env`, `xargs` (no-flag). The common-case
agent forms are all closed. No CRITICAL remains.

But the wrapper-strip set is an ENUMERABLE DENYLIST and the residual under-blocks are
real, executing git mutations on the live MAIN tree (verified rc=2-expected but got
0 ALLOW via _run_advisory_check on the loaded module; identical on cotenant):

HIGH-1 — wrapper words OUTSIDE the strip list pass an executing git through (under-block).
  ALLOWED (should BLOCK), maintree AND cotenant:
    timeout 30 git checkout main           -> 0 ALLOW   (timeout: very common agent wrapper)
    stdbuf -oL git add -A             -> 0 ALLOW
    eval "git checkout main"               -> 0 ALLOW   (eval re-parses+runs; NOT in strip set)
    eval git checkout main                 -> 0 ALLOW
    script -c "git checkout main" /dev/null-> 0 ALLOW
    watch git checkout main                -> 0 ALLOW
    flock /tmp/l git checkout main         -> 0 ALLOW
    ionice git checkout main               -> 0 ALLOW
  Root cause (structural): strip set = time|command|builtin|exec|nice|env|sudo|xargs is a
  fixed allowlist; ANY wrapper not enumerated (timeout/stdbuf/eval/script/watch/flock/
  ionice/chrt/setsid/nohup/...) defeats the gate. `eval` is the sharp one — it re-parses
  an arbitrary string, so even a perfect wrapper list cannot see git inside
  `eval "$cmd"`. `timeout` is the LIKELY one — agents routinely wrap git in timeout.
  Realist: realistic worst case = silent live-tree HEAD/index mutation, no upstream
  mitigation (this hook is the only gate); probability lower than the round-1 ubiquitous
  forms but `timeout`/`eval` are ordinary, not exotic -> HIGH, not MINOR. Cannot ACCEPT.
  Fix: (a) add eval (and the body of `eval '...'`) as a SPLIT separator like bash -c, so
  the quoted payload becomes its own segment; (b) make the wrapper strip a GENERIC
  "leading non-git words + their flags" consume — i.e. after env/redirect stripping,
  skip any run of `(?:[\w./-]+(?:\s+-\S+)*\s+)*` simple-command words until the first
  token is git — OR invert to a small ALLOWLIST of safe heads and treat everything-else-
  then-git as command position. A denylist of wrapper names will keep losing this race.

HIGH-2 — `xargs <flags> git` and backslash-newline line-continuation under-block.
    echo x | xargs -I@ git checkout @      -> 0 ALLOW   (xargs IS in strip set, but `-I@`/`-n1`
                                                     between xargs and git defeats the
                                                     `xargs\s+` strip — claimed-covered
                                                     path actually open)
    echo main | xargs -n1 git checkout     -> 0 ALLOW
    git \<newline>checkout main           -> 0 ALLOW   (\n split severs `git \` from
                                                     `checkout main`; line-continuation joins
                                                     them at runtime into one git command)
  Fix: consume wrapper FLAGS after the wrapper name (`(?:-\S+|\S+)` runs) for xargs/env/
  sudo/nice/timeout/etc.; strip backslash-newline (`\\\n`) before tokenizing.

### New false-fire check (question 2): essentially clean
  Only one new over-block: `cat <<EOF\ngit checkout main\nEOF` -> BLOCK (heredoc BODY is data
  to cat, not a command; the \n split mis-reads it as a segment). MINOR — over-block on a
  rare form, fails SAFE (benign command blocked, documented bypass MAINTREE_GIT_BYPASS=1).
  Acceptable for a BLOCKING gate's bias. All real false-fire controls still ALLOW:
  echo/grep/printf git, git status, git branch --show-current, git log --grep, comment
  `# checkout`, path /home/git/checkout, `git add file1 file2` (explicit pathspec), and the
  TRUE positive `git add -A # then push` correctly blocks (comment, not a 2nd command).

### Advisories (question 3): acceptable to leave on the weaker anchor — with one caveat
  invariant/secrets/post_merge/pre_branch/pr_thread stay on `(?:^|[;&|]...)`. For pure
  warnings, under-detect = a missed reminder, not a security bypass -> fine to defer.
  CAVEAT: pre_branch_create_in_primary's intent (catch branch creation in the primary
  worktree) overlaps the BLOCKING maintree concern; if it is ever meant as a guard rather
  than a nudge it should move to the tokenizer too. Recommend: route ALL git-command
  detectors through _git_subcmd_at_command_position for consistency once HIGH-1/2 land, so
  there is ONE tokenizer to harden, not two matchers to drift (single-authority).

### Confirmed still-correct
  --multi exit-2 propagation, exception isolation, deletion completeness, boot self-test
  relocation — unchanged since Re-review 1; not re-litigated.


---

## Re-review 3 — HIGH-1/HIGH-2 fix (denylist -> inverted token-scan) (2026-06-13)

Fix: `_git_subcmd_at_command_position` (dispatch.py:157) now joins backslash-newline,
splits on `eval` + prior separators, flattens quotes, then per segment scans tokens L->R
consuming any leading word NOT in a DATA_OR_REMOTE set until it hits `git <subcmd>` (BLOCK)
or a data/remote command (ALLOW). No wrapper enumeration remains.

### VERDICT: REVISE — 0 CRITICAL, 0 HIGH(under-block), 1 HIGH(over-block / false-fire)

UNDER-BLOCK: fully closed. 0/many. Every round-1 and round-2 attack BLOCKs, plus all of
this round's probes (verified live, maintree AND cotenant):
  command eval "git checkout", find -execdir git checkout, find -execdir git reset --hard,
  parallel git checkout, flock /tmp/l -c "git checkout", nohup, setsid, chrt -b 0, env -i,
  xargs -P4 -I@ git checkout, timeout, stdbuf, eval, script -c, watch, ionice, backslash-nl,
  and the 2nd-segment cases (echo x; git checkout  /  ssh host ls; git checkout). The security
  objective of this entire review is now MET — the dangerous failure mode is gone.

HIGH-OB-1 (over-block / false-fire, ABOVE minor) — "unknown leading word => wrapper" mis-
classifies a PROGRAM that takes `git <subcmd>` as ARGUMENTS as a command-position git.
Verified live BLOCK (should ALLOW; none runs a local git mutation), maintree AND cotenant:
    python script.py git checkout main          -> BLOCK   (argv to the tool)
    .venv/bin/python scripts/x.py git checkout   -> BLOCK   (Zeus-idiomatic invocation!)
    python3 -m mytool git checkout main          -> BLOCK
    python3 -c "git checkout"                     -> BLOCK
    node tool.js git checkout main               -> BLOCK
    uv run mytool git checkout / poetry run app   -> BLOCK
    mycli run git checkout                        -> BLOCK
    man git checkout  /  tldr git checkout             -> BLOCK   (help lookups)
    type git checkout                             -> BLOCK   (builtin, runs nothing)
    alias gco="git checkout"                      -> BLOCK   (defines alias, runs nothing)
    (cotenant) python x.py git add -A, mytool git add -A, alias ga="git add -A" -> BLOCK
  Controls that DO correctly ALLOW: echo/printf "...git checkout...", grep "git checkout",
  history|grep, pytest -k "checkout", `git status`. Real dangers still BLOCK
  (git checkout main, time git checkout main).

  Why above MINOR (Realist Check): (a) frequency is HIGH — `python <tool> ... git <x> ...`
  argv, `man git <sub>`, `alias`, `type` are everyday, and `.venv/bin/python scripts/*.py`
  is the dominant Zeus invocation shape; (b) the block message misattributes intent
  ("branch-mutating git on MAIN tree") to a command that mutates nothing, which is
  confusing; (c) bypass-habituation risk — the codebase's own law is that agents route
  AROUND obstacles; if `MAINTREE_GIT_BYPASS=1` becomes a reflex to clear these false
  accusations, the gate is defeated for the REAL cases too. This is a usability/trust
  regression in a BLOCKING gate, not a security hole — it fails SAFE for security but
  fails NOISY for workflow. NOT rated HIGH-under-block (there are none); rated HIGH on the
  false-fire axis.

  Root cause (structural): the inversion over-corrected. You cannot distinguish
  `time git checkout` (exec-wrapper; danger) from `man git checkout` / `python t.py git checkout`
  (program takes git as data; safe) by "unknown => wrapper" — that assumption IS the bug.
  The discriminator is whether the leading word is an EXEC-WRAPPER (execs its tail as a
  command) vs a program that consumes args.

  Fix (a bounded allowlist is correct HERE, unlike round 2): consume-as-wrapper ONLY for a
  curated EXEC_WRAPPER set + their flags — time|timeout|eval|stdbuf|nice|ionice|nohup|
  setsid|chrt|sudo|env|command|builtin|exec|xargs|parallel|flock|script|watch — and treat
  ANY OTHER unknown leading word as a program that makes git its ARGUMENT => ALLOW (git not
  at command position). The exec-wrapper SET is small, bounded, and well-known (round 2's
  problem was DENYLISTING wrappers — open-ended; an ALLOWLIST of exec-wrappers is closed).
  This keeps every under-block closed (all the dangerous forms above use exec-wrappers or
  real separators) while ALLOWing python/node/man/tldr/type/alias + git-as-arg. Re-run BOTH
  matrices after: the 12 over-block rows must flip to ALLOW; the under-block matrix must
  stay all-BLOCK. Note find -execdir/-exec and parallel DO run git locally and SHOULD stay
  BLOCK — keep those in the exec-wrapper set (find handled via its -exec/-execdir token).

  Residual MINOR (carry, do not block on): heredoc body `cat <<EOF\ngit checkout\nEOF` still
  over-blocks (round 2); same fail-safe direction. The HIGH-OB-1 fix should also special-
  case the heredoc body if cheap, else leave as documented MINOR.

### Confirmed still-correct
  --multi exit-2 propagation, exception isolation, deletion completeness, boot self-test
  relocation — unchanged. Advisories still on weaker anchor (acceptable, q3 round 2).


---

## Re-review 4 — HIGH-OB-1 fix (exec-wrapper allowlist) (2026-06-13)

Fix: `_git_subcmd_at_command_position` (dispatch.py:157) replaced "unknown=>wrapper" with a
bounded EXEC_WRAPPERS allowlist {time,timeout,eval,stdbuf,nice,ionice,nohup,setsid,chrt,
sudo,doas,env,command,builtin,exec,xargs,parallel,flock,script,watch,find}. Per segment:
strip leading env-assignments/redirects; head IS `git <subcmd>` -> BLOCK; head is an
exec-wrapper -> block-biased search of remainder; else (program head) -> git is an
ARGUMENT -> ALLOW.

### VERDICT: ACCEPT (1 MINOR residual, documented + accepted)

UNDER-BLOCK: still fully closed. 24/24 dangerous forms BLOCK, ZERO regressions from the
allowlist (verified live, maintree+cotenant): plain/subshell/bash -c/&&/sudo/timeout/eval/
stdbuf/script -c/watch/flock/ionice/nice reset --hard/xargs -I@/xargs -n1/env/
find -execdir/backslash-newline/abspath/FOO=x/nohup/setsid/chrt/doas. The security
objective of the whole review is MET and STABLE across the allowlist change.

HIGH-OB-1: fully fixed. 11/11 former over-blocks now ALLOW (verified live): python
script.py git checkout, .venv/bin/python scripts/x.py git checkout (Zeus-idiomatic), python3 -m,
node, uv run, poetry run, man git checkout, tldr, type git checkout, alias gco="git checkout", mycli run.

MINOR (accepted, not blocking) — wrapper THEN non-wrapper-program THEN git-as-arg over-
blocks: env [VAR=x] python tool.py git checkout; sudo python deploy.py git checkout; sudo -u ci node
x.js git checkout; timeout 300 python train.py git checkout; xargs python run.py git checkout;
find -execdir python x.py git checkout; nohup python svc.py git checkout; (cotenant) sudo/env python
x.py git add -A; timeout 5 echo "git checkout". All BLOCK though no local git mutation runs.

  Independently assessed as genuinely MINOR (not a frequent case) for four reasons:
  1. It is a 3-way CONJUNCTION (exec-wrapper prefix + intervening non-wrapper program +
     that program literally receiving a mutating `git <subcmd>` token as an arg) — narrow.
  2. The DOMINANT inline-env idiom is correctly ALLOWED and does NOT hit this path:
     verified live `FOO=bar python tool.py git checkout`, `A=1 B=2 python x.py git checkout`,
     `PYTHONPATH=. python run.py git add -A` all ALLOW (the leading-strip loop eats bare
     VAR=val and exposes `python`). Only the explicit `env`/`sudo`/`timeout` KEYWORD
     variant over-blocks, which is materially rarer than bare-assignment. And the real
     danger `FOO=bar git checkout main` still BLOCKS.
  3. It fails SAFE: over-block on a rare form with a documented bypass
     (MAINTREE_GIT_BYPASS=1 / COTENANT_GUARD_BYPASS=1), zero security exposure.
  4. Tightening it would RE-RISK the dangerous direction. Arity inside an exec-wrapper is
     unknowable without a real shell parser (`sudo X` may be `sudo git` OR
     `sudo prog ... git`). The block-biased remainder search is exactly what keeps
     `xargs -I@ git checkout` and `find -execdir git checkout` blocked; narrowing it to dodge the
     over-block would reopen those under-blocks — a strictly worse trade for a safety gate.
  No frequent case found that escapes to above-MINOR. The author's accept rationale is
  correct.

  Residual carry (also MINOR, prior round): heredoc body `cat <<EOF\ngit checkout\nEOF` over-
  blocks; same fail-safe direction.

### Final disposition
  CRITICAL-1 (round 1), HIGH-1/HIGH-2 (round 2), HIGH-OB-1 (round 3) — all fixed and
  verified live. 0 CRITICAL, 0 HIGH remain. Under-block surface fully closed and stable;
  the only residuals are MINOR fail-safe over-blocks with documented bypasses. --multi
  exit-2 propagation, exception isolation, deletion completeness, boot self-test relocation
  confirmed across all rounds. Advisories left on weaker anchor (acceptable; under-detect =
  missed warning, not a gate bypass). Recommend (non-blocking) routing the advisory git
  detectors through this same tokenizer eventually for single-authority. APPROVED to ship.
