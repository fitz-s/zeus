# The 24/7 improvement loop

In one sentence: this is scientific-method discipline — preregistration,
minimum sample sizes, refutation kept on the record — imposed on an
autonomous agent that proposes changes to a live trading system, so it
cannot p-hack its way to a false "it works."

## Mechanism

`tick.sh` is the entrypoint a launchd job fires hourly (`loop/INTERVAL`
sets the real cadence — the operator retunes it by editing one number,
never the plist). Each tick runs an OS-sandboxed (macOS Seatbelt) agent
process. The sandbox's writable roots are `loop/`, `docs/`, and `tests/`
only; `src/`, `.git`, and the network are denied at the kernel level, not
by convention. After the sandboxed run, an allowlist enforcer diffs the
tick's changes against an immutable pre-tick baseline and hard-reverts
anything outside the allowed scope; a tick touching more than 20 files or
600 lines trips a circuit breaker and gets rolled back wholesale.

Within that sandbox, `loop/prompts/l1.md` (see its permission-tiers
section, roughly lines 74–101) further splits what the agent may do by
target: **AUTO** — write directly under `loop/`/`docs/`/`tests/`.
**PREPARE** — for anything under `src/`, produce a `.patch` plus a failing
test, but never apply it; a human applies. **NEVER** — for config,
databases, LaunchAgents, or risk posture, write proposal text only.

`LEDGER.yaml` is the falsifiable-claim ledger: every claim about the
system's behavior gets a SQL query preregistered *before* the data is
looked at, statistical conclusions require `min_n=30` per cell, refuted
claims are never deleted, and "insufficient evidence" is treated as a
legitimate, required conclusion rather than a failure to report.

This README describes the mechanism as implemented in the scripts and
prompts below — it makes no claim about whether the launchd job is
currently installed or running on any host.
