# Project policy

Each repo in devnix-labs that follows this policy makes three guarantees: about its inputs, its public outputs, and its tests. Each policy release bundles the checker with the stable pin and the unstable pin.

## Inputs

- Root `nixpkgs` is a locked `NixOS/nixpkgs` revision that the repo chooses. A second nixpkgs input is permitted when `flake.nix` states the reason; review checks the reason.
- An input that is another repo references a release tag, appears at one revision in the lock graph, and follows root `nixpkgs`. Another repo is any repository under the GitHub owner of the listed repos, currently `petohorvath`, except the policy repository. A commit reference is permitted temporarily during a change that spans repos; state the reason in the PR.
- The policy repository is never an input.
- Third-party inputs and independently locked examples are unrestricted.

## Public outputs

- Every public output evaluates. Avoid empty output namespaces; the check reports them without failing.
- After the first release tag, removing a public output requires a minor or major version bump over the last release tag: the highest `vMAJOR.MINOR.PATCH` tag reachable from the checked commit. The new version is the topmost release heading in `CHANGELOG.md`, such as `## [0.5.0] - 2026-10-01`; an `Unreleased` heading does not count. The check fails on a shallow clone, which can lack the tag.
- Without a release tag, the removal comparison is skipped. Tag a first release, such as `v0.1.0`, to turn it on.

## Tests

- `nix flake check` passes on each system with the locked nixpkgs, the stable pin, and the unstable pin. `checks.<system>` is not empty, and test runs leave `flake.lock` unchanged.
- VM tests live in `legacyPackages.<system>.vmTests`, outside `checks`, and pass on x86_64-linux with KVM and the locked nixpkgs.

## Caller

A job named `Policy` calls `petohorvath/nixos-project-policy/.github/workflows/check.yml@v0.5`, as [templates/policy-caller.yml](templates/policy-caller.yml) does. Require `Policy / Check (<system>)`, which also starts the default development shell and evaluates the formatter, and `Policy / Tests (locked|stable|unstable, <system>)` on each system, and `Policy / VM tests` when the repo provides `vmTests`. Without `vmTests`, `Policy / VM tests` appears as a skipped job that is not required; GitHub cannot omit a job of a reusable workflow. Patch releases carry pin bumps and fixes and move `v0.5`; breaking rule changes start a new minor series.

## Conventions

- Change `main` through PRs, squash-merged as one Conventional Commit. A human approves every merge.
- Tag releases with SemVer `vMAJOR.MINOR.PATCH`. Never move or reuse a version tag. At 0.x, a minor bump marks a breaking change.
- Keep a `CHANGELOG.md`.
- License original code under MIT.
