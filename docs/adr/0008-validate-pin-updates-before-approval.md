---
status: accepted
---

# Validate shared-pin updates before approval

Publishing a pin candidate to central records solely to run member CI requires a registration PR before the approval PR. Use one central PR to propose and test a stable and unstable revision pair across enrolled members, including integration projects, before human approval. The approved shared pins change only when that PR merges; routine compatibility updates need no member PR or policy-version change.

Keep weekly candidate preparation and allow urgent candidates when needed. Capture exact clean member commits, their selected immutable checker releases and settings, and the record snapshots used for validation. Test both candidate channels on every required architecture, including each integration project's actual locked member dependency set. Preserve the separate committed-lock checks and applicable VM gates, and retain validation evidence with the PR and CI results. A change to either candidate revision requires renewed checks across the batch; changed member sources require renewed evidence for the affected projects.

The policy PR's proposed records are test data, not approval. Validation must use trusted enrollment information and published checker releases, without allowing the proposal to omit required members or substitute its own checker code. Report candidate success separately from approved-pin compliance, and keep normal member CI on the current approved pair until the human-reviewed merge. Automation must establish complete required coverage; record structure alone does not establish test results or approval.

The checker can already test a candidate from an explicitly trusted, unmerged record checkout. Central automation will coordinate those runs so candidate registration and approval can share one PR. The current artifact-generation workflow does not provide that coordination.

[ADR 0006](0006-independent-selection-and-compatibility.md) remains in force: a member's selected root dependency can remain unchanged during compatibility testing. Member PRs remain necessary for source fixes or lock scopes that still require shared pins, including older-policy members, independently locked examples, additional inputs, and distinct transitive nixpkgs nodes. Existing rollout and recovery obligations remain when these changes are needed. The one-PR process removes registration overhead for routine updates; it does not remove required source changes or their approval.

This decision complements [member-owned policy selection](0007-member-owned-policy-selection.md). The central automation must be implemented before this process replaces the current [maintenance procedure](../maintenance.md#pin-candidates-and-approval).
