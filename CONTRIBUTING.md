# Contributing

Follow [POLICY.md](POLICY.md) for shared requirements. See [development](docs/development.md) for tools and test commands.

1. Make changes on a branch.
2. Add behavioral tests for checker changes. Include malformed bundled data, unsupported inputs, and transition states where applicable.
3. Run the formatter and flake checks listed under [tools and checks](docs/development.md#tools-and-checks).
4. Open a PR. Describe the change, test results, validation limits, and compatibility effects.
5. Squash merge after required checks pass and a human approves the merge.

For a new requirement, document its scope, exceptions, automatic checks, and review criteria. Changes here do not authorize migrating other repos.

Keep usage and design in the docs. Keep implementation history and validation evidence in commits, PRs, and CI results. Describe release changes in [CHANGELOG.md](CHANGELOG.md).

For a release, follow [releases](README.md#releases). Pin bumps are automated patch releases; see [pin bumps](README.md#pin-bumps).
