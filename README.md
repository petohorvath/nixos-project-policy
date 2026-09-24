# nixos-project-policy

Shared development and maintenance rules for independent Nix and NixOS projects. Each member owns its flake, tools, tests, and lockfiles. This repository supplies the rules and checks that run outside member flakes.

## Support

Development and CI support `x86_64-linux` and `aarch64-linux`. This repository has no VM tests.

Support starts at v0.4.0. [VERSION](VERSION) identifies the current checker version. Publish new releases through the [release procedure](docs/maintenance.md#releases). Policy releases contain the rules and checker code. Current [records on `main`](docs/maintenance.md#records) contain shared pins, member enrollment, and release retirements.

## Quickstart

Install the [host prerequisites](docs/development.md#host-prerequisites), then run:

```bash
direnv allow
nix flake check
nix run .# -- validate
```

Use `nix develop` to enter the shell without direnv. `validate` checks record structure; it does not check member compliance.

For member checks and audits, use the [checker commands](docs/checker.md#commands).

## Development

Run `nix fmt` and `nix flake check` before submitting changes. See [development](docs/development.md) for tools and focused tests.

## Contributing

Follow [CONTRIBUTING.md](CONTRIBUTING.md) and the shared requirements in [POLICY.md](POLICY.md). Submit changes through PRs. Every merge requires human approval.

## Documentation

- [Policy](POLICY.md): shared requirements.
- [Checker](docs/checker.md): commands, records, results, and limits.
- [Maintenance](docs/maintenance.md): pins, enrollment, releases, and support.
- [Design](docs/normalization-design.md): structure and decisions.
- [Glossary](CONTEXT.md): shared terms.
- [Changelog](CHANGELOG.md): release changes.

Original code uses the [MIT license](LICENSE).
