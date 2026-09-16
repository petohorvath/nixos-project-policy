# Maintenance

## Current boundary

The 2026-09-16 member-selection instruction authorizes `nixos-cross-config` as the first migration, extending Q37's earlier preparation boundary. All seven members remain pending adoption, and no shared pair is approved. The [migration record](cross-config-adoption.md) tracks its candidate and remaining hosted setup. The other six members remain outside this migration.

## Pin candidates and approval

The maintenance workflow prepares an unapproved candidate artifact weekly, with manual runs available. It resolves the configured stable branch and unstable once and records their exact commits. Daily drift jobs inspect public checkouts and, once members enroll, their configured merge gates. These workflow definitions become operational only after publication and configuration on GitHub.

For a selected candidate, create a batch record and prepare member lockfile PRs against the same pair. Include independently locked examples, preserve `follows` relationships, and reject unrelated lock changes. A stable release upgrade also changes the relevant branch declarations. The [lock update audit](normalization-audit.md#targeted-lock-update-mechanics) documents source-reviewed commands and the controlled-fixture validation needed before implementing the updater.

Record each exact tested project revision, required CI results, and the approval evidence in the batch's review. Candidate CI uses the registered pair and the proposed committed locks. Checker code stays pinned while CI captures one current central-record commit for all jobs. Register the candidate merge commits in those records, then rerun the member checks without changing their workflow references. Approve only after every affected project passes. Update the central approved pair through human-reviewed policy changes and track the individual member merges until all projects converge. Recheck after source changes or a change to either pin.

The initial automation emits artifacts; it does not create cross-repository PRs or approve batches. Implement that updater after member selection, with tests for targeted lock updates and a separately reviewed write identity. Preparation is not authority to update or merge member repositories.

## Audit access

Before enrolling the first member, configure the policy repository's `MEMBER_AUDIT_TOKEN` Actions secret with a fine-grained personal access token restricted to the enrolled member repositories. Grant only Administration read, Contents read, and Metadata read. Administration read permits [classic branch-protection inspection](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection); Contents read permits [branch metadata inspection](https://docs.github.com/en/rest/branches/branches#get-a-branch); Metadata read permits [active branch-rule inspection](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch). Add newly enrolled repositories to the token's selection and renew it before expiry. No write permission is needed.

The maintenance audit passes that secret as `GH_TOKEN`; it does not fall back to the workflow's automatic token. The automatic [`GITHUB_TOKEN`](https://docs.github.com/en/actions/concepts/security/github_token) is limited to the policy repository. For a local audit, supply a credential with the same member access through `GH_TOKEN` or `GITHUB_TOKEN`; `GH_TOKEN` takes precedence. A GitHub App installation token with the same read permissions can also be supplied locally; hosted token issuance would need separate workflow setup.

While every member is pending, the audit needs no member credential and performs no merge-gate API requests. Once any member is adopted, a missing or inaccessible credential fails the audit with an inspection error. The JSON artifact identifies unknown settings separately from verified missing gates; do not interpret an HTTP 404 as absent protection. Verify hosted access before activation. Credential provisioning and hosted execution remain deferred setup work.

## Recovery

Pause further update merges when a regression appears. Keep the new pair when the repair is understood and testable, add a regression test, and validate the revised rollout before resuming. Roll back through tested PRs when impact is unacceptable or the repair is uncertain. A prior pair must still pass against current code. Either path needs human merge approval and tracked temporary differences; historical releases remain unchanged.

If a repair needs another nixpkgs commit, treat that pair as a revised candidate and rerun all affected project checks. Mark the batch complete only when adoption has converged; withdraw superseded candidates explicitly.

## Later enrollment

Select a member explicitly before preparing its migration. Give it the agreed root shell, tools, formatting, documentation, pins, and local check interface. Resolve lint findings and preserve its public contracts and specialized requirements. Add the pinned caller only after the policy repository is published at a reviewable immutable revision. Record that full commit in `policyRevision` while keeping `adopted: false`, so the checker can validate the proposed references before enrollment.

Run `check PATH --project NAME --readiness --shell` against approved pins, or register the exact clean candidate commit for initial baseline validation. Readiness reports `ready` or `candidate-ready`; normal compliance checks still fail for pending members. The reusable workflow requests readiness automatically, using the trusted central adoption state. Approve candidate pins through the batch procedure before recording adoption.

Verify both Linux architectures and applicable VM suites on suitable CI hosts. Confirm that local default checks work without VM execution. Determine the actual required GitHub status names from the reviewable PR, then record them in `requiredChecks` and verify the merge gates and audit credential access. Record any necessary technical exceptions and integration commits. Only then set the member's adoption status through a policy PR. A normal `check` must now report `pass` against the approved baseline.

Prepare new requirements against the existing approved policy, and activate them after all affected members are ready. The policy repository keeps test subjects outside its Nix dependency graph.

## Releases

Prepare independently versioned release PRs with a changelog and migration notes. Human merge authorizes publication after the release commit passes the required checks. The release workflow must bind publication to that checked commit and its reviewed version; ordinary PR merges must not publish. Release publishing automation is part of later hosted setup, not the initial artifact-only maintenance workflow.
