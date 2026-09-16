# Changelog

## Unreleased

- Record the shared project policy and accepted design decisions.
- Provide root development tools, formatting, checker tests, and CI definitions.
- Inspect lock graphs, pin batches, documentation structure, CI callers, and adoption drift.
- Prepare weekly candidate artifacts without approving pins or migrating members.
- Prepare `nixos-cross-config` as the first selected member migration, with a pinned policy reference and an unapproved candidate batch.
- Require conventional `nixpkgs` and `nixpkgs-unstable` input names, a visible root `systems` binding, and explicit flake outputs.
- Check first-party nixpkgs input names, including resolved `follows` and independently locked examples.
- Limit language tooling to applicable sources and clarify that benchmarks are optional.
- Keep changelogs and current design documentation while removing duplicate implementation, review, and validation histories.
