---
status: accepted
---

# Keep VM tests in the `checks` output and skip what the runner cannot build

VM tests are ordinary entries in the `checks` output, added on every system with no system condition. They are the entries that require the `nixos-test` system feature, which NixOS tests set. `test` runs `nix flake check --no-build` to evaluate every output, then builds either the other entries or, with `--vm`, the VM tests. It builds each entry whose `requiredSystemFeatures` the host's `system-features` provide and reports the others under `skipped`. An entry declares the features it needs itself, even when only a dependency needs them, such as an entry that reads the result of a shared NixOS test. Repos then name no system, and a runner with KVM runs the VM tests while one without KVM skips them in the report.

VM tests used to live in `legacyPackages.<system>.vmTests`, built by a separate `vm` command and job with the locked nixpkgs only. A stable or unstable pin could reach a release with no VM test against it. As entries of the `checks` output, VM tests run in the locked, stable, and unstable modes, and the `--override-input nixpkgs` of each mode reaches the `pkgs` they take from root `nixpkgs`. They keep their own jobs, `VM tests (<nixpkgs>, <system>)`, beside `Tests (<nixpkgs>, <system>)`. The `Plan` job evaluates the repo with the locked nixpkgs and plans VM jobs only for systems with VM tests, so a repo without VM tests gets no empty jobs. `vmTests` no longer counts as a public output.

## Consequences

- VM CI time grows up to threefold, one run per nixpkgs mode. A mode whose revision equals that of an earlier mode passes without building.
- A runner without KVM passes its VM jobs with every VM test under `skipped`.
- Plain `nix flake check` fails on a host without KVM. Build a subset with `nix build .#checks.<system>.<name>`, or give it a remote builder with the `kvm` feature. `test --vm` reads only the local `system-features`, so it skips the VM tests there.
- An entry that needs a feature only through a dependency, and does not declare it, fails on a runner without the feature instead of being skipped.

## Considered options

- A per-repo `system == "x86_64-linux"` gate: every repo hard-codes a system, and an aarch64 runner with KVM would never run the tests.
- `nix flake check --impure` with a `/dev/kvm` test in the flake: evaluation then depends on the host instead of the locked flake, and every Nix command on the flake needs `--impure`.
- A boolean flag input that turns VM tests on: every repo carries the input, and CI sets it per runner.
- A wrapper script outside the flake: the native Nix commands no longer see the VM tests.
- Reading the required features across each entry's build closure, for example with `nix derivation show --recursive`: it evaluates the whole derivation graph of every NixOS test, and a dependency that a substituter supplies does not need the feature.
