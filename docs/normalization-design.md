# Project policy design

[POLICY.md](../POLICY.md) defines requirements. [CONTEXT.md](../CONTEXT.md) defines shared terms. This page explains the structure and the decisions behind it.

## Dependency boundaries

Each member owns its flake, tools, tests, lockfiles, and releases. Member flakes do not import the policy repository. The policy flake accepts member checkouts as test subjects without depending on them.

A member's GitHub Actions caller selects a published immutable policy release. This CI reference keeps policy execution separate from member builds. Member dependencies remain acyclic; consumers or integration projects own tests that span members.

## Development interface

Each root flake exposes its systems and applicable outputs. Local helpers supply the implementation. This gives projects common development commands without a shared build framework. VM tests stay separate so ordinary checks can run without VM support.

## Pins and enforcement

| Source                  | Owns                                                                        |
| ----------------------- | --------------------------------------------------------------------------- |
| Policy release          | Rules, checker code, workflows, and required checks                         |
| Member caller           | Policy selection, required architectures, VM targets, and additional checks |
| Member lockfiles        | Selected dependencies                                                       |
| Current central records | Shared pins, the stable update branch, and enrollment                       |

Each CI run captures one member commit, checker commit, and record commit. Compatibility checks test shared pins through root input overrides. Separate checks test the committed lock. Candidate validation supplies evidence; human-reviewed merge approves pins.

Checks before enrollment use the same requirements. Enrollment and policy compliance remain separate. Supported selections start at v0.4.0.

See [checker coverage](checker.md#implemented-coverage) for automatic checks and [review responsibilities](checker.md#review-responsibilities) for their limits. See [maintenance](maintenance.md) for procedures.

## Decisions

- [0001: Independent projects and releases](adr/0001-independent-projects.md).
- [0002: Versioned policy repository](adr/0002-versioned-policy-repository.md).
- [0003: Shared revisions across project uses](adr/0003-shared-nixpkgs-pins.md); root selection changed in 0006.
- [0004: Root flake development](adr/0004-root-flake-development.md).
- [0005: External policy checks](adr/0005-external-policy-enforcement.md).
- [0006: Separate selected dependencies from compatibility coverage](adr/0006-independent-selection-and-compatibility.md).
- [0007: Member-owned policy selection](adr/0007-member-owned-policy-selection.md).
- [0008: Review pin updates without member bookkeeping](adr/0008-validate-pin-updates-before-approval.md).
