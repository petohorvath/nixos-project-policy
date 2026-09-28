# Development

## Host prerequisites

Install Nix with `nix-command` and `flakes` enabled. CI uses Nix 2.34.6 to enter the pinned environment.

For automatic shell activation, install direnv with `use flake` support. Enable direnv in the interactive shell. [Nix-direnv](https://github.com/nix-community/nix-direnv#installation) can supply flake support if needed. The repository's `.envrc` reports missing prerequisites.

Review `.envrc`, then run `direnv allow`. Alternatively, run `nix develop --no-update-lock-file`. Host Nix is required for both methods; the development shell then supplies its pinned Nix executable.

## Tools and checks

The root lock supplies Nix, nil, nixfmt, statix, deadnix, treefmt, shfmt, Prettier, Git, jq, Python/PyYAML, Ruff, and actionlint. `data/pins.json` separately holds the stable and unstable pins that the checker bundles.

```bash
nix fmt --no-update-lock-file
nix flake check --no-update-lock-file --print-build-logs
```

`nix flake check` runs checker tests, bundled data validation, formatting, Nix lint, Python lint, and workflow validation for the host architecture. CI runs these checks on both supported Linux architectures. This repository has no VM tests.

Development shells, formatter, and check outputs cover `x86_64-linux` and `aarch64-linux`. The default app and the `default` and `policy-check` packages expose `nixos-project-policy` for every system in the pinned nixpkgs package sets. Those additional package outputs do not imply CI coverage on every system.

The formatter covers Nix, shell, Markdown, YAML, JSON, and Python. It preserves Markdown wrapping. Formatting checks use writable source copies without Git metadata.

### Focused tests

```bash
nix develop --no-update-lock-file --command python -m unittest discover -s tests -v
nix fmt --no-update-lock-file -- --ci
nix run --no-update-lock-file .# -- validate
nix run --no-update-lock-file .# -- --version
```

### Host tests

These tests run outside Nix build sandboxes because they invoke Nix themselves. The CI workflow runs them in one development-shell invocation on each supported architecture:

```bash
nix develop --no-update-lock-file --command python -m unittest tests.nix_compatibility tests.nix_vm tests.packaged_policy -v
```

`tests.nix_compatibility` runs `test` against real fixture flakes in every nixpkgs mode. It checks that the overrides replace root `nixpkgs`, lock preservation, rejection of required lock updates, nonempty host checks, and the explicit default development-shell requirement. It also runs `check` on a real fixture flake with a release tag to prove public-output evaluation, empty-namespace notices, and the removal comparison.

`tests.nix_vm` checks that `vm` discovers and builds `legacyPackages.<system>.vmTests`, reports failing entries by name, returns `not-applicable` without VM tests, and that `nix flake check` does not build them.

`tests.packaged_policy` builds the checker from a controlled clean repository whose bundled pins differ from the committed ones. It runs the packaged commands without any data checkout and asserts that reports use the bundled pins.

Test adapters supply Nix process results. Git operations, bundled data loading, checker code, and packaged commands execute normally. Use the real-Nix test to verify native override behavior.

CI also runs the checker against this repository, which starts its default development shell and evaluates its formatter:

```bash
nix run --no-update-lock-file .# -- check .
```

## Checking another repo

Run this checkout's checker against a repo beside it:

```bash
nix run --no-update-lock-file .# -- check ../REPO
nix run --no-update-lock-file .# -- test ../REPO --nixpkgs stable
nix run --no-update-lock-file .# -- survey ..
```

`survey` runs `check` on every Git repo directly under the workspace directory, marks the repos listed in `data/repos.json`, and prints a table of rule results per repo to standard error. The commands do not update the checked repo's sources or lock, though `check` and `test` execute its code.

## Nix conventions

Root `flake.nix` declares `systems`, inputs, and public outputs. Expressions in `nix/` supply the package and formatter through `pkgs.callPackage`. The check constructor takes named arguments; its callers appear above its implementation.

The `shell` and `formatter` rules use `nix eval --impure --expr builtins.currentSystem` to identify the host before checking its default development shell and formatter. Dependency and build evaluation use the locked flake. Review command usage, module dependencies, and names as well as lint results.

Probes that must reject lock updates pass `--no-update-lock-file` alone. Do not add `--no-write-lock-file`: in Nix 2.34.6, disabling writes bypasses the update rejection and permits an in-memory replacement lock. `test --nixpkgs stable|unstable` overrides root `nixpkgs` with `--override-input`, which implies `--no-write-lock-file`, so it tests the pins rather than the committed lock.
