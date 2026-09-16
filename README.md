# nixos-project-policy

Shared development and maintenance rules for independently released Nix and NixOS projects. Each member owns its flake, tools, tests, and dependency locks. This repository provides written standards and checks that run outside member flakes.

## Support

The development shell and CI definitions target x86_64 Linux and aarch64 Linux. The policy repository has no VM suites. Member VM suites run separately on suitable x86_64 Linux builders.

Status: `nixos-cross-config` is selected for the first member migration. No member is enrolled and no family pin pair is approved. The root lockfile pins this repository's bootstrap tooling; it does not approve those revisions for the family. See [preparation status](docs/preparation.md) and the [first migration record](docs/cross-config-adoption.md).

## Quickstart

With host Nix, direnv with flake support, and shell integration configured:

```bash
direnv allow
nix flake check
nix run .# -- validate
```

`nix develop` is the explicit shell entrypoint. To inspect existing sibling checkouts without changing them:

```bash
nix run .# -- audit ..
```

An audit distinguishes `pending-adoption` from passing compliance. A direct member `check` fails until the required baseline and adoption configuration exist.

## Development

Use root `nix fmt` and `nix flake check`. The latter runs checker tests, formatting, Nix lint, Python lint, and workflow validation without VM execution. [Development instructions](docs/development.md) explain prerequisites, tools, and focused checks.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) describes the local workflow. [POLICY.md](POLICY.md) is the shared normative contract. Changes use PRs and Conventional Commit titles; every merge initially requires human approval.

## Documentation

- [Shared policy](POLICY.md): requirements and their scope.
- [Checker reference](docs/checker.md): commands, records, coverage, and limitations.
- [Maintenance](docs/maintenance.md): pin candidates, approval, recovery, and later enrollment.
- [Preparation status](docs/preparation.md): completed work and deferred activation.
- [Glossary](CONTEXT.md): project-family terminology.
- [Design decisions](docs/normalization-design.md) and [ADRs](docs/adr/0001-independent-projects.md): accepted choices and rationale.

Original code is licensed under [MIT](LICENSE).
