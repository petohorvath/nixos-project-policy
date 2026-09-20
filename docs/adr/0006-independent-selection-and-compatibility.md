# Separate member dependency selection from compatibility coverage

Members selecting v0.2.0 or later can choose an immutable root `nixpkgs` revision independently of shared pins. ADR 0003 tied development choices to family compatibility coverage. Separate these choices so members can select a dependency while proving support for both shared revisions.

The policy runner reads trusted records and verifies the resolved root input. It runs full root `nix flake check` with an exact override for each shared revision and required architecture. In v0.2.0, central `requiredArchitectures` selects ordinary and compatibility architectures. Job generation and merge-gate validation use that same selection. [ADR 0007](0007-member-owned-policy-selection.md) later moves settings into the member caller.

A separate check tests the committed default configuration. Root overrides preserve normal flake evaluation and builds, including tools from the selected revision. They need no compatibility flake or import of the root `outputs` function. Cached builds can satisfy checks; metadata alone cannot.

The exception covers only the selected root lock node. Other nixpkgs nodes and independently locked examples retain shared-pin checks. Candidates require exact clean member commits. Active rollouts test the approved pair, even when other lock scopes temporarily permit old pins. Candidate success does not approve pins. Records remain compatible with supported older checkers.

[ADR 0005](0005-external-policy-enforcement.md) still applies: policy records and execution remain outside member flakes, shells, imports, and builds. Human review establishes test coverage and approves activation, merges, and publication.
