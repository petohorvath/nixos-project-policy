# Development

## Host prerequisites

Install Nix with `nix-command` and `flakes` enabled. CI uses Nix 2.34.6 to enter the pinned environment.

For automatic shell activation, install direnv with `use flake` support. Enable direnv in the interactive shell. [Nix-direnv](https://github.com/nix-community/nix-direnv#installation) can supply flake support if needed. The repository's `.envrc` reports missing prerequisites.

Review `.envrc`, then run `direnv allow`. Alternatively, run `nix develop`. Host Nix is required for both methods; the development shell then supplies its pinned Nix executable.

## Tools and checks

The root lock supplies Nix, nil, nixfmt, statix, deadnix, treefmt, shfmt, Prettier, Git, jq, Python/PyYAML, Ruff, and actionlint. `policy/pins.json` separately records the approved shared pins.

```bash
nix fmt
nix flake check --print-build-logs
```

`nix flake check` runs checker tests, record validation, formatting, Nix lint, Python lint, and workflow validation for the host architecture. CI runs these checks on both supported Linux architectures. This repository has no VM tests.

The formatter covers Nix, shell, Markdown, YAML, JSON, and Python. It preserves Markdown wrapping. Formatting checks use writable source copies without Git metadata.

### Focused tests

```bash
nix develop --command python -m unittest discover -s tests -v
nix fmt -- --ci
nix run .# -- validate
nix run .# -- --version
```

### Host tests

These tests run outside Nix build sandboxes because they invoke Nix themselves. CI runs both on each supported architecture:

```bash
nix develop --command python -m unittest tests.nix_compatibility -v
nix develop --command python -m unittest tests.packaged_transition -v
```

`tests.nix_compatibility` runs real metadata queries and root checks with both exact nixpkgs overrides. It checks lock preservation, rejection of required default-lock updates, nonempty host checks under the committed lock, and the explicit default development-shell requirement.

Fetch the repository's release tags before running `tests.packaged_transition`. This test builds the checker from a controlled clean repository. It exercises member upgrades, checks before enrollment, audits across releases, integration agreement, and the central PR workflow shell. It also checks retained records with the actual older release sources.

Test adapters supply unpublished release metadata, GitHub responses, and Nix process results. Git operations, record processing, checker code, and packaged commands execute normally. Use the real-Nix test to verify native override behavior. Neither host test verifies live GitHub merge protection.

## Member checks

Use a trusted checkout of current records:

```bash
nix run .# -- --policy-root . check ../PROJECT --project PROJECT
```

Add `--shell` to execute the member's development environment. The default check reads files. Checks and audits do not change member sources or lockfiles. See the [checker reference](checker.md) for results and limits.

## Nix conventions

Follow the [shared Nix rules](../POLICY.md#nix-code-and-tests). Root `flake.nix` declares `systems`, inputs, and public outputs. Expressions in `nix/` supply the package and formatter through `pkgs.callPackage`. The check constructor takes named arguments; its callers appear above its implementation.

The shell probe uses `nix eval --impure --expr builtins.currentSystem` to identify the host before checking its default development shell and formatter. Dependency and build evaluation use the locked flake. Review command usage, module dependencies, and names as well as lint results.
