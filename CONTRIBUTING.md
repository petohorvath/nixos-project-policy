# Contributing

Follow [POLICY.md](POLICY.md) for shared requirements. See [development](docs/development.md) for tools and test commands.

1. Make changes on a branch.
2. Add behavioral tests for checker changes. Include invalid records, unsupported inputs, and transition states where applicable.
3. Run `nix fmt` and `nix flake check`.
4. Open a PR with a Conventional Commit title. Describe the change, test results, validation limits, and compatibility effects.
5. Squash merge after required checks pass and a human approves the merge.

For a new requirement, document its scope, exceptions, automatic checks, and review criteria. Follow [maintenance](docs/maintenance.md) to prepare affected projects. Changes here do not authorize member migrations.

Keep usage and design in the docs. Keep implementation history and validation evidence in commits, PRs, and CI results. Record release changes in [CHANGELOG.md](CHANGELOG.md).

For a release, follow the [release procedure](docs/maintenance.md#releases). Pin updates and enrollment changes do not require a policy version change.
