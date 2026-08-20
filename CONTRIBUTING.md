# Contributing

Zeus is closed to external contributions.

This repository is an audit-readable trace of a live, operator-run
trading system. It is not a project to fork, submit pull requests to,
or extend. The code ships as operational fixes on a continuous basis;
there is no roadmap, feature backlog, or issue tracker open to the
public.

## For readers and auditors

If you are here to understand the algorithm or verify the implementation:

- Start with `README.md` for the methodology and trade lifecycle.
- `AGENTS.md` documents the operational law and authority routing that
  governs how the system is changed — useful context for understanding
  why the codebase is structured the way it is.
- Source is under `src/`; tests are under `tests/`.

## Unresolvable authority citations

Many source files open with an `Authority basis:` comment naming the document a
change was derived from. Around 460 of those do not resolve in a clone, in two
groups:

- ~300 cite `docs/rebuild/` or `docs/evidence/` — local-only by design (see
  `.gitignore`): measurement runs, adversarial review rounds, rebuild specs.
- ~160 cite task plans that have since been archived or deleted. The packet
  they belonged to closed; the comment kept its original reference.

They are kept rather than stripped because the citation still answers "was this
reasoned about, and against what" even when the body is unavailable, and because
deleting the provenance reads cleaner while telling the reader less.

What does resolve is everything that constrains the running system: every
`architecture/**` citation in `src/` resolves, as do the invariant registry, the
authority docs, and the math spec. If a comment cites a rule you can act on, you
can open it.

## Security issues

See [`SECURITY.md`](SECURITY.md) for the private disclosure path.
Do not open public issues for security matters.
