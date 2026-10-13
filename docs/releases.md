# Releases and pin bumps

`VERSION` holds the current version; its release tag adds a `v` prefix. Patch releases carry pin bumps and fixes and move the series tag `vX.Y` to them. A breaking change bumps `VERSION` to the next minor version in the PR that makes it, so every patch release from `main` stays compatible with its series.

## Pin bumps

Pin bumps are automated patch releases:

1. The [Propose pin bump](../.github/workflows/pins.yml) workflow runs weekly, or on manual dispatch with both an exact `stable_revision` and `unstable_revision`. When the pins change, it opens the `automation/pin-bump` PR, which updates the pins, sets `VERSION` to the next patch release, and adds a changelog section.
2. The [Pin-bump tests](../.github/workflows/pin-bump-tests.yml) workflow runs `test` and `test --vm` with `--nixpkgs stable` and `--nixpkgs unstable` on each system from the PR against the `main` branch of every repo in [data/repos.json](../data/repos.json). Fix a failing repo through a PR in that repo, then re-run the failed jobs.
3. After a human merges the PR, the [Tag pin-bump release](../.github/workflows/pin-bump-release.yml) workflow tags `vX.Y.Z`, moves `vX.Y` to it, and publishes a GitHub release whose notes are the version's changelog section.

The proposal needs Settings → Actions → General → Workflow permissions → "Allow GitHub Actions to create and approve pull requests". Listed repos must be public. If rulesets are added, let GitHub Actions force-push `automation/pin-bump`, create `vX.Y.Z` tags, and force-update `vX.Y` tags.

## Manual releases

1. Merge a PR that sets `VERSION` and moves the `Unreleased` changelog entries under the version heading.
2. Tag the merge commit `vX.Y.Z`, and create or move `vX.Y` to it.
3. Publish a GitHub release for `vX.Y.Z` with the version's changelog section as its notes.

Pin-bump proposals fail until the tag for `VERSION` exists. Never move or reuse a `vX.Y.Z` tag.
