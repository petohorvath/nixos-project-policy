# Project family maintenance

Shared language for maintaining the Nix and NixOS projects grouped in devnix-labs.

## Language

**Project family**:
The collection of independently usable Nix and NixOS projects that share development and maintenance conventions.
_Avoid_: Single product, release train

**Member project**:
An independently usable project within the project family, with its own public interface and release lifecycle.
_Avoid_: Subproject, product component

**Policy repository**:
The independently versioned `nixos-project-policy` project, which holds shared requirements, approved dependency revisions, and the implementation of policy checks.
_Avoid_: Global skills, shared project library

**Policy release**:
An immutable version of the shared rules and the implementation of policy checks, separate from the current shared pins and member enrollment state.

**Policy check**:
A check that a member project meets shared requirements, such as using approved dependency revisions and providing the required development tools.
_Avoid_: External check

**Integration project**:
A project that owns compatibility tests spanning member projects and depends on the projects it tests.
_Avoid_: Policy repository

**Shared pins**:
The approved pair of exact nixpkgs revisions, one stable and one unstable, used wherever member projects select these dependencies.
_Avoid_: Same channels, latest versions

**Pin update batch**:
A coordinated change to the shared pins, tracked across affected member projects from candidate evaluation through adoption or withdrawal.
_Avoid_: Independent pin updates
