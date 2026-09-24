# nixos-project-policy

Shared development and maintenance rules for independent Nix and NixOS projects. Each member owns its flake, tools, tests, and lockfiles. This repository supplies the rules and checks that run outside member flakes.

## Support

Development shells, formatting, and checks support `x86_64-linux` and `aarch64-linux`. The flake exposes the checker package and default app for every system in its pinned nixpkgs package sets. This repository has no VM tests.

Support starts at v0.4.0. [VERSION](VERSION) identifies the current checker version. Publish new releases through the [release procedure](docs/maintenance.md#releases). Policy releases contain the rules and checker code. Current [records on `main`](docs/maintenance.md#records) contain shared pins, the stable update branch, and member enrollment.

## Quickstart

Install the [host prerequisites](docs/development.md#host-prerequisites), then run:

```bash
direnv allow
nix flake check
nix run .# -- --policy-root . validate
```

Use `nix develop` to enter the shell without direnv. `nix flake check` runs checker tests, record validation, formatting, and lint checks for the host architecture. `validate` checks the selected record checkout and reports whether it contains an approved pin pair; it does not check member compliance or approve pins.

## Member projects

Use the [policy caller template](templates/policy-caller.yml) to select a published immutable policy release and declare the member name and required architectures. Optional settings select VM targets, their Linux architecture, and additional required checks. Keep the policy repository outside member flake inputs, shells, and builds. Follow the [enrollment procedure](docs/maintenance.md#enrollment) to add a member to the central roster; a passing check does not enroll it.

Members choose their root `nixpkgs` revision independently. Policy CI runs separate compliance, committed-lock project tests, and stable/unstable compatibility jobs on every required architecture. Compatibility jobs override the root input with the approved shared pins and run full root checks. Other nixpkgs lock scopes remain subject to shared-pin requirements. Declared VM tests run separately.

For local checks, run the member's selected checker release with an explicit trusted checkout of current records:

```bash
nix run github:petohorvath/nixos-project-policy/v0.4.0 -- \
  --policy-root ../nixos-project-policy-records \
  check ../PROJECT --project PROJECT --shell
```

Replace the tag with the member's selected release and `PROJECT` with its name. Update the records checkout from `main` before checking current approval; the command does not fetch records. `check` inspects structure, locks, and the policy caller. `--shell` also smoke-tests the default development shell and evaluates the formatter. Compatibility execution requires separate `compatibility` commands for `stable` and `unstable`.

The checker also plans CI gates, audits enrolled members, checks integration policy agreement at locked member revisions, and executes declared VM targets. See the [checker reference](docs/checker.md#commands) for commands, JSON results, and validation limits.

## Development

Run `nix fmt` and `nix flake check` before submitting changes. CI also runs real-Nix and packaged-checker host tests on both supported Linux architectures and smoke-tests the development shell. See [development](docs/development.md) for tools, focused tests, and host test commands.

## Contributing

Follow [CONTRIBUTING.md](CONTRIBUTING.md) and the shared requirements in [POLICY.md](POLICY.md). Submit changes through PRs. Every merge requires human approval.

## Documentation

- [Policy](POLICY.md): shared requirements.
- [Checker](docs/checker.md): commands, records, results, and limits.
- [Maintenance](docs/maintenance.md): pins, enrollment, and releases.
- [Design](docs/normalization-design.md): structure and decisions.
- [Glossary](CONTEXT.md): shared terms.
- [Changelog](CHANGELOG.md): release changes.

Original code uses the [MIT license](LICENSE).
