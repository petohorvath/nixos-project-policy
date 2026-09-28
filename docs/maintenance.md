# Maintenance

This guide covers v0.4.0 and later. Publish each immutable release before activating member callers. Member migrations and live merge-setting changes require separate decisions.

## Records

The checker carries its own data. Each release bundles the files below.

| File                                  | Purpose                                    |
| ------------------------------------- | ------------------------------------------ |
| [data/repos.json](../data/repos.json) | Listed repository identities               |
| [data/pins.json](../data/pins.json)   | Stable and unstable pins and update branch |

`policy/pins.json` and `policy/members.json` stay on `main` unchanged for v0.4.0 callers.

The selected release supplies the rules, checker code, repository identity, and CI [requirements](../policy/requirements.json). Each member selects a supported release and its settings in its policy caller. See [record formats](checker.md#member-declarations-and-central-records) for fields and validation rules. Keep approval and test evidence in PRs and CI results.

Run member commands with that selected checker. In the commands below, `./checker` is a checkout of the member's exact published release:

```bash
nix run --no-update-lock-file ./checker -- COMMAND
```

## Pin bumps

Pin bumps are automated patch releases. Each one needs a human merge.

1. The [Propose pin bump workflow](../.github/workflows/pins.yml) runs weekly and on manual dispatch. Without inputs, it resolves `stableBranch` from `data/pins.json` and `nixos-unstable`. Manual dispatch accepts both `stable_revision` and `unstable_revision` as exact commits; supplying only one fails.
2. When the pins change, `tools/pin_bump.py propose` writes `data/pins.json`, the transitional `policy/pins.json`, the next patch version in `VERSION`, and a `CHANGELOG.md` section. Unreleased changelog entries move into that section because the patch release ships them. The workflow force-pushes the `automation/pin-bump` branch and opens or updates its PR.
3. The [Pin-bump tests workflow](../.github/workflows/pin-bump-tests.yml) runs the checker from the PR head against the `main` branch of every repo in `data/repos.json`: `test --nixpkgs stable` and `test --nixpkgs unstable` on each system, and `vm` on `x86_64-linux`. `tools/pin_bump.py matrix` lists the jobs. The `Pin-bump tests` job summarizes them.
4. Fix a failing repo through a PR in that repo, then re-run the failed jobs.
5. After a human merges the PR, the [Tag pin-bump release workflow](../.github/workflows/pin-bump-release.yml) runs `tools/pin_bump.py release`. It creates the exact tag from `VERSION` and force-moves the minor-series tag, for example `v0.5`, to it. Other pushes to `main` create no tag.

The next patch version follows the highest `vX.Y.Z` tag in the minor series of `VERSION`. A proposal fails while `VERSION` itself has no tag, for example after a release PR for a new minor series. Tag that release by hand first. The tag workflow fails when `VERSION` is not that next patch or its tag exists on another commit. Exact version tags are never moved.

A patch release ships everything on `main`. Keep `main` releasable in the current series: a breaking change must bump `VERSION` to the next minor version in the same PR.

Pull requests and pushes from `GITHUB_TOKEN` start no `pull_request` runs. The proposal workflow therefore dispatches the Pin-bump tests and CI workflows on the PR branch. Their check runs appear on the PR head. Re-running them or pushing to the branch by hand also works.

The workflows need these repository settings:

- Actions may create pull requests (Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests").
- `GITHUB_TOKEN` may push the `automation/pin-bump` branch, `vX.Y.Z` tags, and forced updates of `vX.Y` tags. Exempt GitHub Actions from rulesets that block them.
- Listed repos are public, so the tests can check them out with `GITHUB_TOKEN`.

## Recovery

Pause further update merges when a regression appears. If the repair is understood and testable, keep the new pair. Add a regression test and validate the repair before resuming.

If the impact is unacceptable or the repair is uncertain, roll back through tested PRs. Test the prior pair against current code. Both paths require human approval and evidence in the relevant PRs. Historical releases remain unchanged.

A repair that changes either nixpkgs revision needs a new pin-bump PR. Rerun all affected checks.

## Enrollment

1. Select the member migration explicitly.
2. Prepare its shell, tools, formatter, documentation, dependency selection, and checks. Preserve public contracts and specialized host requirements.
3. Use the selected release's caller template. Match its release reference, `policy_version`, and documentation links. Declare [member settings](checker.md#member-declarations-and-central-records) in the caller.
4. Run `nix run --no-update-lock-file ./checker -- check PATH` with the member's selected checker.
5. Run committed-lock checks, both compatibility revisions on every required architecture, and `vm` on `x86_64-linux` for repos with `legacyPackages.<system>.vmTests`.
6. Run `ci PATH --project NAME` with the same checker prefix. Compare the generated names with actual PR statuses and merge gates.
7. Retain the evidence on the member PR. Add the repository identity to `data/repos.json` through a reviewed central PR after verification.

Drafts can prepare an unpublished release. Hosted checks require publication before activation. Checks before enrollment do not change membership or pin approval.

Removal also requires a reviewed central PR. Keep enrollment plans in issues. Ordinary policy upgrades do not change the roster.

Enroll new identities in `data/repos.json`; ordinary member changes do not update the roster or pin records.

## Releases

`VERSION` contains the current version without `v`. Its release tag adds that prefix: `0.4.0` becomes `v0.4.0`. Releases contain rules, checker code, and workflows. Their record copies are historical snapshots.

1. Prepare a release PR with `VERSION`, the changelog, and migration notes.
2. Review compatibility of rules, commands, reports, workflow inputs, and record schemas. At 0.x, incompatible changes require a minor bump. Compatible changes can use a patch bump.
3. Keep record schemas compatible with supported releases.
4. Merge the release PR with human approval.
5. Run required checks on the exact release commit on both supported Linux architectures.
6. Verify the required controls on `main` and enable [immutable releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes).
7. Prepare a draft release at the checked commit. Include the reviewed changelog and migration notes.
8. Publish with the exact version tag, then move the minor-series tag, for example `v0.5`, to it.
9. Verify that GitHub reports an immutable release and that the tag resolves to the checked commit.

Do not reuse a release tag. Ordinary PR merges and pin-bump proposals do not authorize publication; only a merged pin-bump PR is tagged automatically. Member upgrades use separate reviewed PRs. Selections must remain at v0.4.0 or later.
