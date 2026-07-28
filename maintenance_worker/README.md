# maintenance_worker

A second top-level Python package, separate from `src/`, because it is a
different kind of agent doing a different job: a standalone repo-hygiene
worker (config load → guard checks → candidate enumeration → dry-run
proposal → apply → summary report), not a component of the trading
system itself.

Its modules (`cli/`, `core/`, `rules/`, `types/`) are stdlib-only and
carry zero Zeus-specific identifiers or directory assumptions — Zeus
wiring (paths, install metadata, launchd plist) is injected from
`bindings/` rather than hardcoded here. That separation is deliberate:
`maintenance_worker` can be reasoned about, tested, and audited on its
own, without pulling in the trading machine's state or risk posture, and
its own guard/kill-switch layer (`core/guards.py`, `core/kill_switch.py`)
is the thing that decides whether a proposed change is safe to apply —
not `src/`'s invariants.

It lives beside `src/`, not under it, so that boundary stays visible in
the tree.
