---
status: accepted
---

# Reduce the policy to inputs, public outputs, and tests

Supersedes [ADR 0002](0002-versioned-policy-repository.md), [ADR 0007](0007-member-owned-policy-selection.md), and [ADR 0008](0008-validate-pin-updates-before-approval.md).

Most of the previous policy protected the checker's own trust model: immutable-release verification, record snapshots from `main`, digests, replay evidence, audits of GitHub settings, and integration agreement. It did not cover the real risk: a consumer can lock a combination of repos that the provider never tested, and nothing checks that a repo keeps the public outputs its consumers use. Reduce the policy to three guarantees for each repo: inputs, public outputs, and tests. Remove audits, agreement, GitHub-settings inspection, release-immutability checks, digests and replay, caller-shape validation, Markdown scanning, and style rules.

Bundle the stable pin and the unstable pin in each policy release instead of reading them from `main`. A local check is then one command with no data checkout. Repos call the reusable workflow through a moving minor-series tag such as `v0.5`. A merged pin-bump PR, after every listed repo passes with the new pins, tags the next patch release and moves the minor-series tag, so repos receive new pins without a change of their own. Breaking rule changes start a new minor series. Version tags are never moved or reused.

The moving tag means a repo commit that passed can fail later without a repo change. Testing every listed repo in the pin-bump PR before the tag moves limits this.

## Considered options

- Exact version tags in callers with pins in the release: every pin bump needs a PR in every repo.
- Pins on `main` read through a separate checkout: every repo had to document the checkout procedure, and those copies drifted.
- Declared public outputs or declared VM targets: deriving them from the flake needs no declaration to keep current.
