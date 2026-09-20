# Project policy design

The project family consists of independently usable Nix and NixOS projects. Each member owns its public interfaces, releases, development tools, tests, and lockfiles. The policy repository provides the shared contract and external checks. [POLICY.md](../POLICY.md) is authoritative for requirements; [CONTEXT.md](../CONTEXT.md) defines the shared terms.

## Dependency boundaries

Member flakes do not depend on the policy repository. The policy flake does not depend on members: its checker accepts their checkouts as test subjects. This keeps development and builds independent of shared maintenance infrastructure. Member dependencies remain acyclic, with integration tests owned by a consumer or a separate integration project.

A member's small GitHub Actions workflow calls an exact policy release tag. That CI reference supplies common enforcement without adding policy code to member development shells or Nix builds. See [ADR 0001](adr/0001-independent-projects.md), [ADR 0002](adr/0002-versioned-policy-repository.md), and [ADR 0005](adr/0005-external-policy-enforcement.md).

## Development interface

Each root flake declares its supported `systems` and applicable outputs explicitly. The selected root input is `nixpkgs`, regardless of its update branch. Additional shared unstable selections use `nixpkgs-unstable`. Local helpers implement tools and checks, while the root remains the visible public interface. The default shell, root `.envrc`, formatter, and ordinary checks provide consistent development commands across projects.

Projects select tools for the languages and tasks they maintain. A pure library can retain a plain-import interface while exposing development outputs lazily. Separate VM targets let ordinary local checks run on hosts without VM capabilities. Benchmarks serve project-specific performance needs and are optional. See [ADR 0004](adr/0004-root-flake-development.md).

## Pins and enforcement

Member lockfiles select actual dependencies. Central pin records describe the approved pair and coordinated update batches; changing a record does not change member locks. A batch identifies exact candidate revisions and tested member commits, so approval can follow validation across the affected projects. The policy runner tests both shared revisions through root input overrides while ordinary checks test the committed lock. See [ADR 0006](adr/0006-independent-selection-and-compatibility.md) and the [maintenance procedure](maintenance.md).

CI verifies that the selected policy release is published and immutable, then captures its exact commit, one member source commit, and one current record commit for all jobs. The release supplies rules and checker code; current records supply shared pins, pin update batches, and member enrollment. A shared-pin update changes current records and requires new compatibility runs; migrated members can retain their root locks. Older-policy members and other checked lock scopes may still need lock updates. Neither path changes the selected policy release or caller. Member-owned release selection and settings live in the existing workflow caller; one validated interpretation supplies local checks, matrices, gates, and execution targets. Checks can run before enrollment, which remains a separate central decision. Retained legacy records preserve supported immutable older checker contracts. Passing project tests alone establishes neither enrollment nor pin approval. See [ADR 0007](adr/0007-member-owned-policy-selection.md) and [ADR 0008](adr/0008-validate-pin-updates-before-approval.md).

Mechanical checks cover reliable structural properties, lock graphs, shell tools, callers, and configured merge gates. Human review covers architecture, compatibility, prose, and the meaning of test coverage. Required status names do not establish which workflow implementation ran, so caller review and drift audits remain necessary. The [checker reference](checker.md) defines the implemented boundary.

## Documentation and releases

Documentation explains current usage, structure, design, and architectural decisions. Git commits, issues, PRs, and CI retain implementation and validation history. Changelogs summarize release-facing changes and migration steps. Independent release PRs review versions and compatibility; human approval remains required for merges and release publication.
