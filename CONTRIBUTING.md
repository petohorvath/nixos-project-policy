# Contributing

Read [POLICY.md](POLICY.md) for the common coding, documentation, PR, and release rules. Read [development instructions](docs/development.md) for this repository's tools and commands.

Implement changes on a branch and open a PR with a Conventional Commit title. Describe the behavior change, relevant validation, and any compatibility effect. Squash merge only after required checks and human approval. This initial preparation has not configured remote merge gates.

For checker changes, add tests that demonstrate the policy boundary being enforced. Include malformed records, unsupported inputs, or transition states when relevant. Run `nix fmt` and `nix flake check`. Policy records must distinguish proposed revisions, approved pins, and adoption status; an unapproved configuration must never become a passing compliance result.

For a new requirement, document its scope, exceptions, mechanical checks, and review criteria. Follow [maintenance](docs/maintenance.md) to prepare affected projects before activation. Changes to this repository do not authorize edits in member repositories.

For a release, prepare a PR with the intended SemVer version, changelog, and migration notes. Human merge authorizes publication after the release commit passes checks. Initial publication and the release automation setup remain recorded in [preparation status](docs/preparation.md).
