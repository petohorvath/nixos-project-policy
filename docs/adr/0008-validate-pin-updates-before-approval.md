---
status: accepted
---

# Review pin updates without member bookkeeping

Store one approved stable/unstable pair and its stable update branch in central pin records. Recording member revisions and rollout states requires synchronization commits whenever member work changes. Keep test subjects and results in PRs and CI artifacts instead.

Prepare candidate pairs weekly or urgently when needed. Propose a pair through an ordinary central PR. Validate both revisions with enrolled members' selected immutable checkers on their required architectures. Preserve committed-lock checks and applicable VM gates. Integration projects test their actual locked dependency sets. Human review assesses this evidence and approves the pair through merge; normal member CI uses current approval until then.

The repository does not coordinate member update states or publish a separate pin-approval status. Member repairs use independently reviewed PRs. Changes to the proposed pair require renewed validation, and changes to tested sources require renewed evidence for the affected projects. Keep the exact tested commits with the results, outside central pin records.

[ADR 0006](0006-independent-selection-and-compatibility.md) still applies. Members retain their selected root dependencies, while other shared-pin scopes use the approved pair without temporary per-member allowances. Recovery restores a tested pair through a reviewed central PR or fixes the member. Follow [pin maintenance](../maintenance.md#pin-candidates-and-approval).
