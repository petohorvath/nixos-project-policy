# nixos-project-policy

Shared development and maintenance rules for independently released Nix and NixOS projects. Each member owns its flake, tools, tests, and dependency locks. This repository provides written standards and checks that run outside member flakes.

## Support

The development shell and CI definitions target x86_64 Linux and aarch64 Linux. The policy repository has no VM suites. Member VM suites run separately on suitable x86_64 Linux builders.

The current [member roster](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/members.json) identifies enrolled repository identities, and current [pin records](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/pins.json) identify the approved baseline and update batches. The root lockfile pins this repository's tooling; it does not approve those revisions for the family.

Policy releases version the shared rules and checker code. Members select an exact release tag such as `v0.1.0`; checks read shared pins and enrollment from current records on `main`. A nixpkgs update does not require a policy release. [VERSION](VERSION) identifies the prepared version; publication follows the [release procedure](docs/maintenance.md#releases).

## Quickstart

With host Nix, direnv with flake support, and shell integration configured:

```bash
direnv allow
nix flake check
nix run .# -- validate
```

`nix develop` is the explicit shell entrypoint. To audit exact clean sibling checkouts using their published immutable selected checkers:

```bash
nix run .# -- --policy-root . audit ..
```

Prepared v0.4.0 member checks read release selection and settings from the policy workflow and work before enrollment against approved shared pins. Reports distinguish enrollment, static check results, and compatibility execution. Older immutable releases retain their central selection and adoption contracts.

After publication, an ordinary policy upgrade needs one reviewed member PR; the central enrollment and legacy selections stay unchanged. A routine root-only shared-pin update uses one [central pin PR](docs/maintenance.md#pin-candidates-and-approval), with complete enrolled-member validation before human approval and merge. Source repairs and locks still governed by older contracts can require coordinated member PRs.

## Development

Use root `nix fmt` and `nix flake check`. The latter runs checker tests, formatting, Nix lint, Python lint, and workflow validation without VM execution. [Development instructions](docs/development.md) explain prerequisites, tools, and focused checks.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) describes the local workflow. [POLICY.md](POLICY.md) is the shared normative contract. Changes use PRs and Conventional Commit titles; every merge initially requires human approval.

## Documentation

- [Shared policy](POLICY.md): requirements and their scope.
- [Checker reference](docs/checker.md): commands, records, coverage, and limitations.
- [Maintenance](docs/maintenance.md): pin candidates, approval, recovery, and enrollment.
- [Changelog](CHANGELOG.md): release-facing changes.
- [Glossary](CONTEXT.md): project-family terminology.
- [Design decisions](docs/normalization-design.md) and [ADRs](docs/adr/0001-independent-projects.md): accepted choices and rationale.

Original code is licensed under [MIT](LICENSE).
