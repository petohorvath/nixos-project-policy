# nixos-project-policy

Shared development and maintenance rules for independent Nix and NixOS projects. Each member owns its flake, tools, tests, and lockfiles. This repository supplies the rules and checks that run outside member flakes.

## Support

Development shells, formatting, and checks support `x86_64-linux` and `aarch64-linux`. The flake exposes the checker package and default app for every system in its pinned nixpkgs package sets. This repository has no VM tests.

Support starts at v0.4.0. [VERSION](VERSION) identifies the current checker version. Publish new releases through the [release procedure](docs/maintenance.md#releases). Policy releases contain the rules, the checker code, and the [checker data](docs/maintenance.md#records): the stable and unstable pins, the stable update branch, and the listed repos.

## Quickstart

Install the [host prerequisites](docs/development.md#host-prerequisites), then run:

```bash
direnv allow
nix flake check --no-update-lock-file --print-build-logs
nix run --no-update-lock-file .# -- validate
```

Use `nix develop --no-update-lock-file` to enter the shell without direnv. `nix flake check` runs checker tests, record validation, formatting, and lint checks for the host architecture. `validate` checks the checker's bundled pins and repo list and prints them; it does not check member compliance.

## Member projects

Use the [policy caller template](templates/policy-caller.yml) to select a published immutable policy release and declare the member name and required architectures. Optional settings select VM targets, their Linux architecture, and additional required checks. Keep the policy repository outside member flake inputs, shells, and builds. Follow the [enrollment procedure](docs/maintenance.md#enrollment) to add a member to the central roster; a passing check does not enroll it.

Members choose their root `nixpkgs` revision independently. Policy CI runs separate compliance, committed-lock project tests, and stable/unstable compatibility jobs on every required architecture. Compatibility jobs override the root input with the approved shared pins and run full root checks. Declared VM tests run separately.

For local checks, run the member's selected checker release. It reads the pins and repo list bundled with it, so no other checkout is needed:

```bash
nix run github:petohorvath/nixos-project-policy/v0.4.0 -- \
  check ../PROJECT
```

Replace the tag with the member's selected release and `PROJECT` with the repo's path. `check` applies the input rules to the root `flake.lock`, evaluates the public outputs and compares their names with the last release tag, starts the default development shell, and evaluates the formatter. Run the tests with `test ../PROJECT --nixpkgs locked`, `stable`, or `unstable`.

The checker also plans CI gates and builds the VM tests it discovers under `legacyPackages.<system>.vmTests`. See the [checker reference](docs/checker.md#commands) for commands, JSON results, and validation limits.

## Development

Run the formatter and flake checks listed under [tools and checks](docs/development.md#tools-and-checks) before submitting changes. CI also runs real-Nix and packaged-checker host tests on both supported Linux architectures and runs `check` against this repository. See [development](docs/development.md) for tools, focused tests, and host test commands.

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
