# Contributing

Read [POLICY.md](POLICY.md) for the common coding, documentation, PR, and release rules. Read [development instructions](docs/development.md) for this repository's tools and commands.

Implement changes on a branch and open a PR with a Conventional Commit title. Describe the behavior change, relevant validation, and any compatibility effect. Squash merge only after required checks and human approval.

For checker changes, add tests that demonstrate the policy boundary being enforced. Include malformed records, unsupported inputs, or transition states when relevant. Run `nix fmt` and `nix flake check`. Policy records must distinguish proposed revisions, approved pins, and adoption status; an unapproved configuration must never become a passing compliance result.

For a new requirement, document its scope, exceptions, mechanical checks, and review criteria. Follow [maintenance](docs/maintenance.md) to prepare affected projects before activation. Changes to this repository do not authorize edits in member repositories.

Keep documentation about current behavior and design. Put implementation and validation history in commits, PRs, and CI results, and release-facing changes in [CHANGELOG.md](CHANGELOG.md).

For a policy release, set `VERSION` to the intended SemVer version and prepare a PR with the changelog and migration notes. Public contracts include the rules, checker CLI and report meanings, reusable-workflow inputs, and record schemas. Human merge authorizes publication after the release commit passes checks; follow [release maintenance](docs/maintenance.md#releases). Pin updates and enrollment changes update current records without a policy version bump.
