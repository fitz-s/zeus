# Security Policy

Zeus is a closed, operator-run live trading engine. There is no public
deployment, public API, or external support channel.

## Reporting a vulnerability

Do not open a public GitHub issue for any security matter.

If you discover a potential vulnerability — in the source code, the
settlement logic, credential handling, or any other surface — report it
privately to the repository owner. Include:

- A clear description of the issue
- The affected file(s) or component
- A proof of concept or reasoning, if available
- A suggested remediation (optional)

Contact: via the GitHub profile of the repository owner, or the email
address associated with the account.

## Credential and secret handling

- Live trading credentials (API keys, wallet private keys, venue tokens)
  are sourced exclusively from environment variables or the system keychain.
- `.gitleaks.toml`, enforced by `.github/workflows/secrets-scan.yml` on every
  push and pull request to `live`, is the server-side control against new
  secrets being committed.
- `config/settings.json` is operator-local and not tracked in version
  control. See `config/settings.example.json` for the safe template.

### Disclosed incident

A Weather Underground API key (fingerprint `6532d645…`, 32 hex chars) was
committed to this repository starting 2026-03-30, and by 2026-04-16 was
present across multiple tracked files, including `docs/zeus-system-constitution.md`
and files under `src/data/`. It was removed from the tracked tree on
2026-05-23 (commit `98708567e`). History was not rewritten, so the value
remains retrievable from public git history by anyone who already holds
it — confirmable with `git log -S` against the full value, which is
deliberately not reproduced here. This is a free-tier Weather Underground
API key that grants read access to public weather data only; it confers
no access to funds, trading accounts, or private data. The operator has
assessed this exposure as low severity and treats the key as burned — it
is no longer relied on as a secret.

## Scope

This repository contains the source of a live trading system operating
on real capital. Any discovered exposure of credentials, wallet addresses,
or venue authentication tokens should be treated as high severity and
reported immediately.
