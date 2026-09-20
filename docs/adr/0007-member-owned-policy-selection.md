---
status: accepted
---

# Let members own policy release selection

Requiring central records to repeat each member's policy selection creates coordinated changes across repositories for a single upgrade. A human-reviewed member PR will authorize selection of a published immutable policy release, and members may upgrade independently while other members retain supported releases. The central roster will identify members expected to comply, and independent audits will inspect their actual policy selection, callers, and merge gates so missing enforcement remains a failure.

This direction refines the central enrollment model in [ADR 0002](0002-versioned-policy-repository.md): central membership and oversight remain, while migrated members will no longer need a central selected-version record or adoption flag for each upgrade. Shared requirements and approved pins remain central. Removing duplicate selection records accepts the need to inspect member revisions when reporting their policy selection; passing project tests alone does not establish policy compliance.

Roster membership will mean that a member is expected to follow its selected supported policy release. It will not assert compliance or require the latest release. Each member will still record its policy selection, and the policy audit will read that selection from the inspected member revision and report it with the assessment.

The central roster will contain enrolled members only. Plans for future member enrollment will live in issues. A central PR will add or remove a member; routine policy upgrades will leave the roster unchanged. Enrollment will follow preparation of the member's policy setup and verification of its required checks and merge gates.

Projects may run full policy checks before member enrollment, using their own declarations, the selected policy release, and approved shared pins. Passing these checks will not add a project to the roster or replace enrollment review.

Each member will own its required architectures, VM targets, and additional required checks. Review must confirm that these settings meet the selected policy release's rules and explicitly justify any reduction in required coverage.

Member settings will be literal inputs in the existing policy workflow caller, without a separate configuration file. The caller's immutable release reference will select the policy, and its `policy_version` input must agree. Required architectures will be an explicit JSON list passed as a string; VM targets and additional required checks will use the same format and default to empty lists. Local checks, CI, and audits will use the same validation of these declarations. The selected release will continue to define the mandatory checks.

An integration project will require its locked member dependency revisions to select its own supported policy release. The shared checker will inspect declarations at those exact revisions and reject missing or mismatched selections; current member branches and central records do not describe historical selections. Members may upgrade independently while the integration project retains a matching set, and integration tests must still establish that the members work together.

Publishing a new policy release will not automatically end support for an older release. Retirement will require an explicit decision and a migration period. Compatibility with supported older checkers must be maintained until retirement takes effect.

A new policy release will introduce member-owned declarations while preserving the central records required by supported older checkers. Members will migrate separately through their own PRs, without a central policy-selection update. The new checker will use member declarations; legacy records will remain available to old checkers. Remove those records after affected old releases have been explicitly retired and their members have migrated.

Shared-pin updates will use the central approval process in [ADR 0008](0008-validate-pin-updates-before-approval.md), independently of member policy selection.

Implementation and a new policy release are required before these rules replace the current [policy requirements](../../POLICY.md#enforcement-and-policy-changes).
