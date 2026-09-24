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

**Policy selection**:
The policy release a member chooses as its maintenance contract. Selection alone does not establish compliance.

**Member settings**:
The choices a member makes within its selected policy release, including required architectures, VM tests, and additional required checks.
_Avoid_: Policy overrides

**Member enrollment**:
The decision that a project is expected to follow its selected policy release and be included in family oversight. Enrollment alone establishes neither policy compliance nor selection of the latest release.

**Policy compliance**:
An assessment that a particular member revision satisfies the applicable requirements of its selected policy release, supported by validation evidence.
_Avoid_: Adoption flag, passing project tests

**Policy audit**:
An independent assessment of enrolled members' policy compliance and required enforcement.

**Policy check**:
A check that a member project meets shared requirements, such as providing the required development tools and compatibility coverage.
_Avoid_: External check

**Integration project**:
A project that owns compatibility tests spanning member projects and depends on the projects it tests.
_Avoid_: Policy repository

**Member dependency**:
A particular revision of a member project used by another project. Its policy selection is the one recorded at that revision.

**Shared pins**:
The approved pair of exact nixpkgs revisions, one stable and one unstable, required for member compatibility coverage. A member's selected root dependency can differ..
_Avoid_: Same channels, latest versions

**Selected dependency**:
The immutable nixpkgs revision a member chooses for its normal root development, checks, and builds, independently of required compatibility revisions.

**Pin candidate**:
An exact stable and unstable nixpkgs revision pair proposed for shared-pin approval. Successful checks provide evidence for approval but do not approve the pair.
