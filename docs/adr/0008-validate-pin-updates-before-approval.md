---
status: accepted
---

# Validate shared-pin updates before approval

Registering candidates solely to run CI requires a registration PR before approval. Use one central PR to propose and test both revisions across all enrolled members, including integration projects. Human-reviewed merge approves the pair. Routine compatibility updates need no member PR or policy-version change.

Prepare candidates weekly or urgently when needed. Capture exact clean member commits, selected immutable checker releases, settings, and records. Test both revisions on every required architecture. For integration projects, use the actual locked dependency set. Preserve committed-lock checks and applicable VM gates. Keep evidence in the PR and CI results.

Proposed records are test data. Trusted enrollment and published checkers define coverage; a proposal cannot omit members or replace checking code. Report candidate success separately from approved-pin compliance. Normal member CI uses current approval until merge. Changing either candidate revision requires renewed checks across the batch. Changed member sources require renewed evidence for affected projects.

[ADR 0006](0006-independent-selection-and-compatibility.md) still applies. Members can retain selected root dependencies during compatibility tests. Source repairs and pin-bound lock scopes still require member PRs. These include older releases, independently locked examples, additional inputs, and distinct transitive nixpkgs nodes. Rollout and recovery obligations remain.

The central PR workflow and candidate coordinator implement this decision in the prepared v0.4.0 contract. Publication and live merge-gate activation remain separate requirements. Follow [pin maintenance](../maintenance.md#pin-candidates-and-approval) and [activation](../maintenance.md#renewing-pr-evidence).
