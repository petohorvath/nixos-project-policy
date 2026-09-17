# Maintenance

## Records

The current [project records](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/projects.json) identify adoption state and selected policy releases. The current [pin records](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/pins.json) identify shared pins, candidates, and rollout state. Both live on `main`. The selected policy release supplies the rules and checker code, including [development requirements](../policy/requirements.json). Keep approval and validation evidence on the corresponding PRs and CI runs.

## Pin candidates and approval

The maintenance workflow prepares an unapproved candidate artifact weekly, with manual runs available. It resolves the configured stable branch and unstable once and records their exact commits. Daily drift jobs inspect public checkouts and, once members enroll, their configured merge gates. These workflow definitions become operational only after publication and configuration on GitHub.

For a selected candidate, create a batch record and prepare member lockfile PRs against the same pair. Include independently locked examples, preserve `follows` relationships, and reject unrelated lock changes. A stable release upgrade also changes the relevant branch declarations.

Use a targeted `nix flake lock` operation with exact candidate overrides on the owning inputs. Resolve `follows` before choosing override paths. Compare the resulting lock graph with its previous state: the intended revisions must be persisted, branch declarations and sharing must remain correct, and unrelated dependencies must be unchanged. Build and check with `--no-update-lock-file` so an in-memory override cannot substitute for the committed candidate locks. Test these properties on controlled fixtures when changing update automation.

Record each exact tested project revision, required CI results, and the approval evidence in the batch's review. Candidate CI uses the registered pair and the proposed committed locks. Checker code stays pinned while CI captures one current central-record commit for all jobs. Register the candidate merge commits in those records, then rerun the member checks without changing their workflow references. Approve only after every affected project passes. Update the central approved pair through human-reviewed policy changes and track the individual member merges until all projects converge. Recheck after source changes or a change to either pin.

A pin update changes current records and member lockfiles. Keep `VERSION`, each member's `policyVersion`, and member workflow references unchanged. All enrolled members must use the new shared pins when the pin update batch is complete; old policy releases do not grant access to old pins.

The maintenance workflow emits candidate artifacts. Cross-repository PR creation requires a separately reviewed write identity and targeted-lock tests. Candidate generation does not approve batches or authorize member merges.

## Audit access

Before enrolling the first member, configure the policy repository's `MEMBER_AUDIT_TOKEN` Actions secret with a fine-grained personal access token restricted to the enrolled member repositories. Grant only Administration read, Contents read, and Metadata read. Administration read permits [classic branch-protection inspection](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection); Contents read permits [branch metadata inspection](https://docs.github.com/en/rest/branches/branches#get-a-branch); Metadata read permits [active branch-rule inspection](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch). Add newly enrolled repositories to the token's selection and renew it before expiry. No write permission is needed.

The maintenance audit passes that secret as `GH_TOKEN`; it does not fall back to the workflow's automatic token. The automatic [`GITHUB_TOKEN`](https://docs.github.com/en/actions/concepts/security/github_token) is limited to the policy repository. For a local audit, supply a credential with the same member access through `GH_TOKEN` or `GITHUB_TOKEN`; `GH_TOKEN` takes precedence. A GitHub App installation token with the same read permissions can also be supplied locally; hosted token issuance would need separate workflow setup.

While every member is pending, the audit needs no member credential and performs no merge-gate API requests. Once any member is adopted, a missing or inaccessible credential fails the audit with an inspection error. The JSON artifact identifies unknown settings separately from verified missing gates; do not interpret an HTTP 404 as absent protection. Verify hosted access before activation.

## Recovery

Pause further update merges when a regression appears. Keep the new pair when the repair is understood and testable, add a regression test, and validate the revised rollout before resuming. Roll back through tested PRs when impact is unacceptable or the repair is uncertain. A prior pair must still pass against current code. Either path needs human merge approval and tracked temporary differences; historical releases remain unchanged.

If a repair needs another nixpkgs commit, treat that pair as a revised candidate and rerun all affected project checks. Mark the batch complete only when adoption has converged; withdraw superseded candidates explicitly.

## Enrollment

Select a member explicitly before preparing its migration. Give it the agreed root shell, tools, formatting, documentation, pins, and local check interface. Resolve lint findings and preserve its public contracts and specialized requirements. Select an exact policy release such as `v0.1.0`, record it in `policyVersion`, and use the same tag in member policy links, the caller's `uses` reference, and its `policy_version` input. Keep `adopted: false` during preparation. A draft migration may target the planned first release, but its hosted checks cannot run until that immutable release is published.

The reusable workflow loads central records from the policy repository's `main` branch. Establish that branch and its human and CI merge controls before activating member CI.

Run `nix run .# -- --policy-root . check PATH --project NAME --readiness --shell` from a trusted current records checkout against approved pins, or register the exact clean candidate commit for initial baseline validation. Readiness reports `ready` or `candidate-ready`; normal compliance checks still fail for pending members. Local checks validate release references without contacting GitHub; hosted checks also require a published, immutable, non-prerelease GitHub release. The reusable workflow requests readiness automatically, using the trusted central adoption state. Approve candidate pins through the batch procedure before recording adoption.

Verify both Linux architectures and applicable VM suites on suitable CI hosts. Confirm that local default checks work without VM execution. Determine the actual required GitHub status names from the reviewable PR, then record them in `requiredChecks` and verify the merge gates and audit credential access. Record any necessary technical exceptions and integration commits. Only then set the member's adoption status through a policy PR. A normal `check` must now report `pass` against the approved baseline.

Prepare new requirements against the existing approved policy, and activate them after all affected members are ready. The policy repository keeps test subjects outside its Nix dependency graph.

## Releases

`VERSION` contains the prepared policy version without the `v` prefix. The release tag adds that prefix, for example `0.1.0` becomes `v0.1.0`. A policy release includes shared rules, development requirements, checker code, and workflows. Shared pins and enrollment remain current operational records; their copies in a release do not establish current approval.

Prepare a release PR with `VERSION`, the changelog, and migration notes. Review compatibility of the rules, checker CLI and reports, workflow inputs, and record schemas. At 0.x, incompatible changes require a minor bump; compatible changes may use a patch bump. Coordinate an incompatible record schema change with all enrolled members before activating it on `main`.

Human merge authorizes publication after the exact release commit passes checks on both Linux architectures. Establish `main` and its required merge gates before the first release. Enable [GitHub immutable releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes) before publication. Prepare a draft release targeting the checked commit, include the reviewed changelog and migration notes, then publish it with the exact version tag. Verify that GitHub reports the release as immutable and that its tag resolves to the checked commit. Do not reuse a tag or publish from an unchecked branch head. Ordinary PR merges and the candidate-artifact workflow do not publish releases.

Member upgrades change policy links, the workflow reference, `policy_version`, and the current `policyVersion` record together. Member lockfiles need changes only if they do not match the current shared pins. Publish the policy release before activating those member callers; their first hosted run verifies release immutability and records the resolved release and central-record commits.
