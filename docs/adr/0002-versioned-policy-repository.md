# Keep policy in a versioned repository

Local skills and project-specific configuration do not provide a common, portable maintenance contract. Keep shared requirements, approved dependency revisions, and validation tools in the dedicated versioned `nixos-project-policy` repository, accepting coordination work in exchange for one authoritative policy source. Member projects remain responsible for their own development and build configuration; external enforcement is defined in [ADR 0005](0005-external-policy-enforcement.md).

Publish shared rules and checker code as immutable policy releases. Keep shared pins and member enrollment in current central records, so a nixpkgs update does not require a new policy release. Each check combines the selected policy release with one recorded snapshot of the current records; the policy version alone does not identify the required nixpkgs revisions.
