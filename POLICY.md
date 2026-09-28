# Project policy

Each repo in devnix-labs that follows this policy makes three guarantees: about its inputs, its public outputs, and its tests. Each policy release bundles the checker with the stable pin and the unstable pin.

## Inputs

- Root `nixpkgs` is a locked `NixOS/nixpkgs` revision that the repo chooses. A second nixpkgs input is permitted when `flake.nix` states the reason; review checks the reason.
- An input that is another repo references a release tag, appears at one revision in the lock graph, and follows root `nixpkgs`. A commit reference is permitted temporarily during a change that spans repos; state the reason in the PR.
- The policy repository is never an input.
- Third-party inputs and independently locked examples are unrestricted.

## Public outputs

- Every public output evaluates. Do not publish empty output namespaces.
- After the first release tag, removing a public output requires a minor or major version bump over the last release tag. The new version is the topmost release heading in `CHANGELOG.md`.
- Without a release tag, the removal comparison is skipped. Tag a first release, such as `v0.1.0`, to turn it on.

## Tests

- `nix flake check` passes on each system with the locked nixpkgs, the stable pin, and the unstable pin. `checks.<system>` is not empty, and test runs leave `flake.lock` unchanged.
- VM tests live in `legacyPackages.<system>.vmTests`, outside `checks`, and pass on x86_64-linux with KVM and the locked nixpkgs.

## Caller

Call the reusable workflow through the moving minor-series tag:

```yaml
jobs:
  policy:
    name: Policy
    uses: petohorvath/nixos-project-policy/.github/workflows/check.yml@v0.5
    # Optional; defaults to both Linux systems.
    # with:
    #   systems: '["x86_64-linux"]'
```

Patch releases carry pin bumps and fixes and move `v0.5`. Breaking rule changes start a new minor series.

Require these statuses:

- `Policy / Check (<system>)`: input rules, public outputs, the default development shell, and the formatter.
- `Policy / Tests (locked, <system>)`, `Policy / Tests (stable, <system>)`, and `Policy / Tests (unstable, <system>)`.
- `Policy / VM tests`, when the repo provides `vmTests`.

## Conventions

- Change `main` through PRs, squash-merged as one Conventional Commit. A human approves every merge.
- Tag releases with SemVer `vMAJOR.MINOR.PATCH`. Never move or reuse a version tag. At 0.x, a minor bump marks a breaking change.
- Keep a `CHANGELOG.md`.
- License original code under MIT.
