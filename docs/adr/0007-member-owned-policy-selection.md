---
status: accepted
---

# Let members own policy release selection

Central records that repeat each member's selection require changes in two repositories for one upgrade. Let a reviewed member PR select a published immutable release. Members can upgrade independently while other members retain supported releases.

This refines [ADR 0002](0002-versioned-policy-repository.md). Shared requirements, pins, enrollment, and audits remain central. The roster contains enrolled repository identities only. Enrollment means a member must meet its selected policy; it does not assert compliance or require the newest release. Add or remove identities through reviewed central PRs. Keep enrollment plans in issues.

The member's existing workflow caller owns its selection and settings. Its immutable release reference and `policy_version` must agree. Required architectures use a literal JSON string. VM targets and additional checks use the same format and default to empty lists. Local checks, CI, and audits validate the same declaration. Review must justify reductions in coverage.

Removing central selection copies requires audits to inspect exact member revisions. Missing enforcement must remain a failure. Checks before enrollment use the same requirements, but passing checks do not enroll a member or approve pins.

An integration project requires its exact locked member revisions to select its own supported release. Current member branches cannot describe those historical selections. This permits independent member upgrades while the integration project retains a matching dependency set. Behavioral tests must still prove that the members work together.

Support starts at v0.4.0 now that all supported members use that contract. Current tooling and records have one member declaration contract; compatibility records and adapters for pre-v0.4.0 checkers are removed.

[ADR 0008](0008-validate-pin-updates-before-approval.md) defines pin approval independently of member policy selection. The v0.4.0 contract implements this decision. See [enrollment](../maintenance.md#enrollment) for new members.
