# Agent instructions

Before changing code, documentation, bundled data, or workflows, read [POLICY.md](POLICY.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

For the caller workflow, pin bumps, or releases, read [README.md](README.md).

Complete changes with the relevant behavioral tests and by running the formatter and flake checks exactly as [docs/development.md](docs/development.md#tools-and-checks) writes them; their flags keep the committed `flake.lock` unchanged. Report validation limits explicitly. Migrating another repo to the policy requires a separate user decision; work in this repository does not authorize it.

## Agent skills

### Issue tracker

Track issues and specs in GitHub Issues for `petohorvath/nixos-project-policy`. Before tracker operations, read [issue tracker conventions](docs/agents/issue-tracker.md).

### Triage labels

Use the five default triage roles. Before assigning triage labels, read [the label mapping](docs/agents/triage-labels.md).

### Domain docs

Use the single-context layout with root `GLOSSARY.md` and `docs/adr/`. Before exploring the codebase, read [domain documentation rules](docs/agents/domain.md).
