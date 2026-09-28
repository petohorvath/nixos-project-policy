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

## Pin candidates and approval

A pin update changes the approved pair through an ordinary central PR. Member commits and rollout states are not stored in this repository.

1. Select the weekly candidate artifact or prepare an urgent pair.
2. Change `stable` and `unstable` in `data/pins.json`. Keep enrollment and checker changes separate.
3. Build the checker from the proposed change and run it against each enrolled member. Run both compatibility revisions on its required architectures, committed-lock checks, and applicable VM and additional gates.
4. Retain exact tested source, checker, and record revisions with the results in the PR. Resolve required member fixes through their own reviewed PRs, then renew affected checks.
5. Obtain human approval and merge the central PR. Members receive the new pins with the next release that bundles them.

For example, test the proposed pins with a checkout `./proposed` of the proposed change:

```bash
nix run --no-update-lock-file ./proposed -- \
  compatibility ./member --project MEMBER --channel stable
nix run --no-update-lock-file ./proposed -- \
  compatibility ./member --project MEMBER --channel unstable
nix flake check ./member --no-update-lock-file --print-build-logs
```

Repeat on every required architecture and run the remaining member gates. These results establish behavior against the proposed pins; they do not approve them. No batch registration, central copy of member revisions, or separate pin-approval workflow is required.

### Candidate preparation

The [Prepare pin update workflow](../.github/workflows/pins.yml) produces `candidate.json` in the `pin-proposal` artifact weekly or on manual dispatch. Urgent manual preparation accepts both `stable_revision` and `unstable_revision` as exact commits. Supplying only one fails. With neither input, it resolves `stableBranch` from `data/pins.json` and the `nixos-unstable` branch once. Preparation does not run member compatibility checks; retain those results separately before approval.

Preparation creates no PR. Automation that creates member PRs requires a separately reviewed write identity and tests for targeted lock updates.

### Required member lock changes

Members can retain their selected root dependency during compatibility tests. Independently locked examples, additional inputs, and distinct transitive nixpkgs nodes also retain shared-pin requirements.

1. Resolve `follows` relationships before selecting the owning inputs.
2. Update those inputs with a targeted `nix flake lock` operation and exact candidate revisions.
3. Update relevant branch declarations for a stable release upgrade.
4. Compare the old and new lock graphs. Verify persisted revisions, branch declarations, and sharing. Reject unrelated dependency changes.
5. Run builds and checks with `--no-update-lock-file` and no overrides.
6. Also run the policy runner against both candidate revisions.

Record each tested commit and result in the central PR. Test these lock properties on controlled fixtures when changing update automation. Keep member changes and their review evidence in their own PRs.

Keep `VERSION` and member workflow references unchanged for pin updates. Validate enrolled members against the proposed pair before approval. Shared-pin lock scopes use one pair; no per-member old/new allowance is recorded.

## Renewing PR evidence

Renew validation when either proposed revision changes. Renew affected checks when member sources, settings, checker, or enrollment change. Review evidence against the final proposed pair before merge. Keep exact tested commits with the results; independent member changes require no central bookkeeping commit.

## Recovery

Pause further update merges when a regression appears. If the repair is understood and testable, keep the new pair. Add a regression test and validate the repair before resuming.

If the impact is unacceptable or the repair is uncertain, roll back through tested PRs. Test the prior pair against current code. Both paths require human approval and evidence in the relevant PRs. Historical releases remain unchanged.

A repair that changes either nixpkgs revision creates a revised candidate. Rerun all affected checks.

## Enrollment

1. Select the member migration explicitly.
2. Prepare its shell, tools, formatter, documentation, dependency selection, and checks. Preserve public contracts and specialized host requirements.
3. Use the selected release's caller template. Match its release reference, `policy_version`, and documentation links. Declare [member settings](checker.md#member-declarations-and-central-records) in the caller.
4. Run `nix run --no-update-lock-file ./checker -- check PATH --shell` with the member's selected checker.
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
8. Publish with the exact version tag.
9. Verify that GitHub reports an immutable release and that the tag resolves to the checked commit.

Do not reuse a release tag. Ordinary PR merges and candidate generation do not authorize publication. Member upgrades use separate reviewed PRs. Selections must remain at v0.4.0 or later.
