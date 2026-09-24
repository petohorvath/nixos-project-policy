# Maintenance

This guide covers v0.4.0 and later. Publish each immutable release before activating member callers. Member migrations and live merge-setting changes require separate decisions.

## Records

Current records live on `main`. Use one trusted snapshot for each operation.

| Record                                                                                            | Purpose                                                            |
| ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| [members.json](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/members.json) | Enrolled repository identities                                     |
| [pins.json](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/pins.json)       | Approved shared pins, stable update branch, and pin update batches |

The selected release supplies the rules, checker code, repository identity, and CI [requirements](../policy/requirements.json). Each v0.4.0 member selects its release and settings in its policy caller. See [record formats](checker.md#member-declarations-and-central-records) for fields and validation rules. Keep approval and test evidence in PRs and CI results.

## Pin candidates and approval

Routine shared-pin updates use one central PR. The PR proposes an exact stable/unstable pair and registers every enrolled member's exact clean commit. Normal member CI keeps the current approval until human-reviewed merge.

1. Select the weekly candidate artifact or prepare an urgent pair. Candidate artifacts do not approve pins.
2. Capture clean member commits from the trusted roster. Keep integration projects at their intended committed dependency sets.
3. Resolve source failures and required lock updates through separate member PRs. Use the lock procedure below.
4. Add a new batch to `policy/pins.json`. Put the proposed pair in `approved`. For a routine update with no remaining member rollout, set the batch to `complete`. Record the current approved pair in `previous`.
5. Open the central PR. Keep enrollment, the stable update branch, policy versions, and checker changes separate. Commit records as regular files; symlinks and uncommitted edits fail validation.
6. Review `Pin batch / Complete candidate` on the current PR head. Verify the baseline, all member outcomes, every required architecture, both compatibility results, ordinary checks, and applicable VM and additional gates. For integration projects, verify agreement and behavioral checks at the locked dependency revisions.
7. Retain the plan, worker artifacts, GitHub provenance, and complete summary with the PR. Retain evidence from failed workers too.
8. Obtain human merge approval with current complete evidence. Merge changes the approved pair for subsequent checks.

Retain active batches. Completed and withdrawn batches can be pruned in a separate reviewed cleanup; preserve their evidence in Git history and the original PR. Do not reuse an approved or historical batch as a candidate. For an active rollout, use `approved`, `rolling`, or `paused` until the member changes are complete. State-only pause, resume, completion, and withdrawal changes use ordinary review; they do not require a new candidate.

The `Central pin proposal` workflow uses the current trusted default-branch workflow, coordinator, and baseline. It tests proposed records as candidate data. It does not execute proposed code or requirements. See [central PR checks](checker.md#central-pin-prs) for evidence and permissions.

For investigation, run `Complete candidate batch` or `Candidate member validation` from trusted `main`. Supply an exact committed proposal and batch ID. These manual results do not replace the central PR gate. See [local execution and replay](checker.md#complete-enrolled-batches).

### Candidate preparation

Maintenance prepares an unapproved artifact weekly and audits members daily. Urgent manual preparation accepts both `stable_revision` and `unstable_revision` as exact commits. Supplying only one fails. With neither input, it resolves the configured stable and unstable branches once.

Preparation creates no PR. Automation that creates member PRs requires a separately reviewed write identity and tests for targeted lock updates.

### Required member lock changes

Members can retain their selected root dependency during compatibility tests. Independently locked examples, additional inputs, and distinct transitive nixpkgs nodes also retain shared-pin requirements.

1. Resolve `follows` relationships before selecting the owning inputs.
2. Update those inputs with a targeted `nix flake lock` operation and exact candidate revisions.
3. Update relevant branch declarations for a stable release upgrade.
4. Compare the old and new lock graphs. Verify persisted revisions, branch declarations, and sharing. Reject unrelated dependency changes.
5. Run builds and checks with `--no-update-lock-file` and no overrides.
6. Also run the policy runner against both candidate revisions.

Record each tested commit and result in the central PR. Test these lock properties on controlled fixtures when changing update automation. Preserve human review and rollout records for required member changes.

Keep `VERSION` and member workflow references unchanged for pin updates. At completion, every enrolled member must meet its selected release's requirements against the new shared pins. During active rollouts, compatibility tests use the central approved pair. Temporary old/new lock allowances do not change that test target.

## Renewing PR evidence

Renew validation when the candidate pair, proposal head, registered source, settings, integration dependencies, checker, enrollment, or captured records change. Start a fresh attempt. An unrelated member branch update does not change an exact registration. Replay checks captured evidence; it cannot renew a live PR check.

Maintenance invalidates stale proposal checks after pushes to `main`, scheduled runs, and manual dispatch. It checks baseline changes. After a baseline change, update/rebase the proposal or reopen the PR to start a fresh target event. Rerun an older workflow only while its original workflow revision and baseline remain current.

Before activating the central gate:

1. Verify that repository Actions settings allow `pull_request_target`.
2. Verify that `Pin batch / Complete candidate` appears on the reviewed head.
3. Require the intended trusted workflow and issuer in merge protection.
4. Require an up-to-date branch or equivalent validation at merge time.
5. Verify that selections are v0.4.0 or later and obtain human approval.

Final GitHub checks and periodic invalidation are not atomic with a later merge. Local fixtures cannot verify hosted permissions, native ARM execution, or live merge protection. See GitHub's [target-trigger guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target) and the [evidence contract](checker.md#central-pin-prs).

## Audit access

Configure the policy repository's `MEMBER_AUDIT_TOKEN` Actions secret for all enrolled repositories. Use a fine-grained personal access token with these read permissions:

| Permission     | Inspection                                                                                                                                                                                                                                |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Actions        | [Caller workflow state](https://docs.github.com/en/rest/actions/workflows#get-a-workflow)                                                                                                                                                 |
| Administration | [Actions settings](https://docs.github.com/en/rest/actions/permissions#get-github-actions-permissions-for-a-repository) and [classic branch protection](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection) |
| Contents       | [Branch metadata](https://docs.github.com/en/rest/branches/branches#get-a-branch)                                                                                                                                                         |
| Metadata       | [Active branch rules](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch)                                                                                                                                                 |

Add newly enrolled repositories to the token's selection. Renew the token before expiry. No write permission is required.

Maintenance passes this secret as `GH_TOKEN`. It does not use the automatic [`GITHUB_TOKEN`](https://docs.github.com/en/actions/concepts/security/github_token), which is limited to the policy repository. Local audits accept `GH_TOKEN` or `GITHUB_TOKEN`; `GH_TOKEN` takes precedence. A GitHub App installation token with the same read permissions also works locally. Hosted token issuance needs separate setup.

An empty roster requires no member credential. Otherwise, missing credentials or inaccessible metadata cause inspection errors. An HTTP 404 does not prove absent protection. Verify hosted access before activation. See [audit results](checker.md#enrollment-audits).

## Recovery

Pause further update merges when a regression appears. If the repair is understood and testable, keep the new pair. Add a regression test and validate the repair before resuming.

If the impact is unacceptable or the repair is uncertain, roll back through tested PRs. Test the prior pair against current code. Both paths require human approval and records of temporary differences. Historical releases remain unchanged.

A repair that changes either nixpkgs revision creates a revised candidate. Rerun all affected checks. Complete the batch only after rollout finishes. Mark superseded candidates as `withdrawn`.

## Enrollment

1. Select the member migration explicitly.
2. Prepare its shell, tools, formatter, documentation, dependency selection, and checks. Preserve public contracts and specialized host requirements.
3. Use the selected release's caller template. Match its release reference, `policy_version`, and documentation links. Declare [member settings](checker.md#member-declarations-and-central-records) in the caller.
4. Run `nix run .# -- --policy-root . check PATH --project NAME --shell` from a trusted current records checkout.
5. Run committed-lock checks, both compatibility revisions on every required architecture, and applicable VM suites.
6. Run `ci PATH --project NAME` with the selected checker. Compare the generated names with actual PR statuses and merge gates. Verify audit access.
7. Retain the evidence on the member PR. Add the repository identity to `policy/members.json` through a reviewed central PR after verification.

Drafts can prepare an unpublished release. Hosted checks require publication before activation. Checks before enrollment do not change membership or pin approval.

Removal also requires a reviewed central PR. Keep enrollment plans in issues. Ordinary policy upgrades do not change the roster.

Before a new identity participates in pin batches, enroll it in `policy/members.json`. Retained batches keep their tested member names and commits after roster removal.

Run `audit WORKSPACE --fetch --github` for all enrolled identities. Retain its JSON report. See [audit behavior](checker.md#enrollment-audits) for release discovery and inspection failures.

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

## Integration project agreement

Creating or enrolling an integration project requires a separate decision. Keep behavioral tests in its root flake.

1. Adopt the [integration caller template](../templates/integration-caller.yml) after release publication.
2. Declare `Integration / Policy agreement` as an additional required check.
3. Keep both callers on the same release. Preserve the template's source and record output bindings.
4. Run `ci PATH --project NAME` to inspect required gates.
5. Run `agreement PATH --project NAME` with the selected checker and trusted records.
6. Verify the actual GitHub status. Review merge-setting changes separately.

Upgrade the integration selection and its complete matching dependency set together. Member branches can upgrade independently while the integration lock retains older supported revisions. Review reported lock scopes, source revisions, and selections.

Policy agreement does not prove functional compatibility. Preserve committed-lock tests, both compatibility revisions on each required architecture, and applicable VM tests. See the [agreement contract](checker.md#integration-policy-agreement).
