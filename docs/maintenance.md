# Maintenance

This guide covers the prepared v0.4.0 contract. Publish the immutable release before activating member callers. Member migrations and live merge-setting changes require separate decisions.

## Records

Current records live on `main`. Use one trusted snapshot for each operation.

| Record                                                                                              | Purpose                                      |
| --------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| [members.json](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/members.json)   | Enrolled repository identities               |
| [pins.json](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/pins.json)         | Approved shared pins and pin update batches  |
| [support.json](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/support.json)   | Release retirements and migration periods    |
| [projects.json](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/projects.json) | Records required by supported older checkers |

The selected release supplies the rules, checker code, and [requirements](../policy/requirements.json). Each v0.4.0 member selects its release and settings in its policy caller. See [record formats](checker.md#member-declarations-and-central-records) for fields and validation rules. Keep approval and test evidence in PRs and CI results.

## Pin candidates and approval

Routine shared-pin updates use one central PR. The PR proposes an exact stable/unstable pair and registers every enrolled member's exact clean commit. Normal member CI keeps the current approval until human-reviewed merge.

1. Select the weekly candidate artifact or prepare an urgent pair. Candidate artifacts do not approve pins.
2. Capture clean member commits from the trusted roster. Keep integration projects at their intended committed dependency sets.
3. Resolve source failures and required lock updates through separate member PRs. Use the lock procedure below.
4. Add a new batch to `policy/pins.json`. Put the proposed pair in `approved`. For a routine update with no remaining member rollout, set the batch to `complete`. Record the current approved pair in `previous`.
5. Open the central PR. Keep enrollment, support, legacy settings, policy versions, and checker changes separate. Commit records as regular files; symlinks and uncommitted edits fail validation.
6. Review `Pin batch / Complete candidate` on the current PR head. Verify the baseline, all member outcomes, every required architecture, both compatibility results, ordinary checks, and applicable VM and additional gates. For integration projects, verify agreement and behavioral checks at the locked dependency revisions.
7. Retain the plan, worker artifacts, GitHub provenance, and complete summary with the PR. Retain evidence from failed workers too.
8. Obtain human merge approval with current complete evidence. Merge changes the approved pair for subsequent checks.

Retain completed batches. Do not reuse an approved or historical batch as a candidate. For an active rollout, use `approved`, `rolling`, or `paused` until the member changes are complete. State-only pause, resume, completion, and withdrawal changes use ordinary review; they do not require a new candidate.

The `Central pin proposal` workflow uses the current trusted default-branch workflow, coordinator, and baseline. It tests proposed records as candidate data. It does not execute proposed code or requirements. See [central PR checks](checker.md#central-pin-prs) for evidence and permissions.

For investigation, run `Complete candidate batch` or `Candidate member validation` from trusted `main`. Supply an exact committed proposal and batch ID. These manual results do not replace the central PR gate. See [local execution and replay](checker.md#complete-enrolled-batches).

### Candidate preparation

Maintenance prepares an unapproved artifact weekly and audits members daily. Urgent manual preparation accepts both `stable_revision` and `unstable_revision` as exact commits. Supplying only one fails. With neither input, it resolves the configured stable and unstable branches once.

Preparation creates no PR. Automation that creates member PRs requires a separately reviewed write identity and tests for targeted lock updates.

### Required member lock changes

Members on v0.2.0 or later can retain their selected root dependency during compatibility tests. Older releases still require member lock updates. Independently locked examples, additional inputs, and distinct transitive nixpkgs nodes also retain shared-pin requirements.

1. Resolve `follows` relationships before selecting the owning inputs.
2. Update those inputs with a targeted `nix flake lock` operation and exact candidate revisions.
3. Update relevant branch declarations for a stable release upgrade.
4. Compare the old and new lock graphs. Verify persisted revisions, branch declarations, and sharing. Reject unrelated dependency changes.
5. Run builds and checks with `--no-update-lock-file` and no overrides.
6. For migrated members, also run the policy runner against both candidate revisions.

Record each tested commit and result in the central PR. Test these lock properties on controlled fixtures when changing update automation. Preserve human review and rollout records for required member changes.

Keep `VERSION`, legacy policy selections, and member workflow references unchanged for pin updates. At completion, every enrolled member must meet its selected release's requirements against the new shared pins. During active rollouts, compatibility tests use the central approved pair. Temporary old/new lock allowances do not change that test target.

## Renewing PR evidence

Renew validation when the candidate pair, proposal head, registered source, settings, integration dependencies, checker, enrollment, support, or captured records change. Start a fresh attempt. An unrelated member branch update does not change an exact registration. Replay checks captured evidence; it cannot renew a live PR check.

Maintenance invalidates stale proposal checks after pushes to `main`, scheduled runs, and manual dispatch. It checks baseline changes and effective retirements. After a baseline change, update/rebase the proposal or reopen the PR to start a fresh target event. Rerun an older workflow only while its original workflow revision and baseline remain current.

Before activating the central gate:

1. Verify that repository Actions settings allow `pull_request_target`.
2. Verify that `Pin batch / Complete candidate` appears on the reviewed head.
3. Require the intended trusted workflow and issuer in merge protection.
4. Require an up-to-date branch or equivalent validation at merge time.
5. Verify current release support and human approval.

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

Before a new identity participates in pin batches, add its [legacy compatibility entry](checker.md#member-declarations-and-central-records). Retain prior entries and historical batch references after migration or roster removal. Older releases still use their original readiness and adoption process.

Run `audit WORKSPACE --fetch --github` for all enrolled identities. Retain its JSON report. See [audit behavior](checker.md#enrollment-audits) for release discovery and inspection failures.

## Releases

`VERSION` contains the prepared version without `v`. Its release tag adds that prefix: `0.4.0` becomes `v0.4.0`. Releases contain rules, checker code, and workflows. Their record copies are historical snapshots.

1. Prepare a release PR with `VERSION`, the changelog, and migration notes.
2. Review compatibility of rules, commands, reports, workflow inputs, and record schemas. At 0.x, incompatible changes require a minor bump. Compatible changes can use a patch bump.
3. Preserve the records required by supported older checkers. Follow [retirement and cleanup](#retirement-and-legacy-cleanup) before removing legacy data.
4. Merge the release PR with human approval.
5. Run required checks on the exact release commit on both supported Linux architectures.
6. Verify the required controls on `main` and enable [immutable releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes).
7. Prepare a draft release at the checked commit. Include the reviewed changelog and migration notes.
8. Publish with the exact version tag.
9. Verify that GitHub reports an immutable release and that the tag resolves to the checked commit.

Do not reuse a release tag. Ordinary PR merges and candidate generation do not authorize publication. Member upgrades use separate reviewed PRs. New releases do not retire older selections.

## Retirement and legacy cleanup

Record each reviewed retirement in `policy/support.json`. Specify the exact release, decision reference, reason, migration start, and effective retirement time. Allow enough time for the agreed migrations; the checker requires a positive period but sets no universal duration.

Members migrate through their own reviewed PRs. After the deadline, new-contract commands reject the retired selection and audits retain it as a failed enrolled assessment. Immutable older checkers keep their original behavior. See the [support contract](checker.md#release-support-and-retirement).

Preserve legacy records until all affected releases have retired and their consumers have migrated. This includes complete check lists on adopted entries and references in completed or withdrawn batches. Roster removal and a future retirement date do not establish cleanup safety.

For a separately reviewed cleanup proposal, run:

```bash
nix run .# -- --policy-root PROPOSED validate \
  --previous-policy-root TRUSTED_BASE --workspace MEMBERS
```

Use the actual trusted prior snapshot. The checker verifies published legacy releases, including patches, and exact clean migrated checkouts from both rosters. Plain `validate` checks structure only. Keep snapshot identities and migration evidence with the cleanup PR. A successful review deletes nothing and grants no merge authority.

## Integration project agreement

Creating or enrolling an integration project requires a separate decision. Keep behavioral tests in its root flake.

1. Adopt the [integration caller template](../templates/integration-caller.yml) after release publication.
2. Declare `Integration / Policy agreement` as an additional required check.
3. Keep both callers on the same release. Preserve the template's source and record output bindings.
4. Run `ci PATH --project NAME` to inspect required gates.
5. Run `agreement PATH --project NAME` with the selected checker and trusted records.
6. Verify the actual GitHub status. Review merge-setting changes separately.

Upgrade the integration selection and its complete matching dependency set together. Member branches can upgrade independently while the integration lock retains older supported revisions. Review reported lock scopes, source revisions, selections, and support decisions.

Policy agreement does not prove functional compatibility. Preserve committed-lock tests, both compatibility revisions on each required architecture, and applicable VM tests. See the [agreement contract](checker.md#integration-policy-agreement).

## Migration to v0.4.0 (prepared)

Publish the immutable release before activating callers. Select each member migration separately.

1. Update policy documentation links, the workflow reference, and `policy_version` in one member PR.
2. Copy the intended architectures into `required_architectures` as a literal JSON string.
3. Copy VM targets and additional gates into `vm_targets` and `additional_required_checks`. Omit these optional inputs when empty.
4. Run `ci PATH --project NAME`. Verify generated names against PR statuses and merge gates.
5. Run compliance/shell, committed-lock, both compatibility revisions, and applicable VM checks.

Do not add a settings file or pin override inputs. Preserve required coverage unless review explicitly justifies a reduction. The remaining policy gates retain v0.3.0 status names. The policy no longer requires the formatting/lint gate; members own any replacement enforcement. VM gates stay on x86_64 even when ordinary jobs use ARM only. Additional status names require jobs that produce them; see [integration setup](#integration-project-agreement).

Retain the member's selected root nixpkgs revision. Leave central legacy selections, settings, and adoption fields unchanged. Retain their complete check lists and historical batch references until [cleanup review](#retirement-and-legacy-cleanup) permits removal. This upgrade neither enrolls a member nor approves pins or retires releases.

## Migration to v0.3.0

Use the published v0.3.0 checker for this older contract. Update policy links, the workflow reference, `policy_version`, and central `policyVersion` together.

Move project-specific gates to `additionalRequiredChecks`. Omit that field when empty. Generate the complete set with `ci --project NAME`. Verify actual statuses and merge gates. Equivalent settings retain v0.2.0 names and matrices; project records retain schema version 2.

Retain full `requiredChecks` on adopted legacy entries. A retained v0.3.0 list must match its generated requirements. During preparation, keep `adopted: false` if the list needs regeneration; pending entries can omit it. Current records remain subject to [retirement and cleanup](#retirement-and-legacy-cleanup), even after all active members upgrade.

## Migration to v0.2.0

Use the published v0.2.0 checker for this older contract. Select each migration separately. Update policy links, the workflow reference, `policy_version`, and central `policyVersion` together.

Keep the committed root lock. Name its selected dependency `nixpkgs`, regardless of update branch. Additional inputs, transitive selections, and independently locked examples retain shared-pin requirements. No extra input or compatibility flake is required solely for testing.

Use caller name `Policy` and record `requiredArchitectures`. Generate required names with `ci --project NAME`. Run both compatibility revisions on every recorded architecture, plus ordinary and applicable VM checks. Verify actual PR statuses before recording `requiredChecks` or activating merge gates.

Retain the runner's metadata and results with the PR. Capture project, checker, and record commits and the record digest for [replay](checker.md#compatibility-execution-and-evidence). Project and pin record schemas remain unchanged. Members on v0.1.x keep their selected contract until separately migrated.
