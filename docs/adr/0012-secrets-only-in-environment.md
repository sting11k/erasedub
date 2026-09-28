# 0012 — Secrets only in environment variables

- Status: Accepted
- Date: 2026-09-26

## Context

Config files get shared in issues, committed to repos and baked into images. API keys in them leak.

## Decision

- API keys and tokens are read only from environment variables (or a service's own credential store, such
  as Modal's `~/.modal.toml`).
- The config loader rejects `erasedub.toml` files whose keys look like credentials or whose values look like
  well-known key formats, and never echoes the offending values in error messages.
- `erasedub doctor` reports only whether each known key is set, never its value.

## Alternatives considered

- **Allow keys in the config with a warning**: convenient, and exactly how keys end up in bug reports.

## Consequences

Some option names that merely look like secrets are refused; the loader keeps an allow-list for legitimate
names such as `max_tokens`.
