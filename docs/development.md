# Development

## Host prerequisites

Install Nix with `nix-command` and `flakes` enabled. CI uses Nix 2.34.6 to enter the pinned environment.

For automatic shell activation, install direnv with `use flake` support. Enable direnv in the interactive shell. [Nix-direnv](https://github.com/nix-community/nix-direnv#installation) can supply flake support if needed. The repository's `.envrc` reports missing prerequisites.

Review `.envrc`, then run `direnv allow`. Alternatively, run `nix develop`. Host Nix is required for both methods; the development shell then supplies its pinned Nix executable.

## Tools and checks

The root lock supplies Nix, nil, nixfmt, statix, deadnix, treefmt, shfmt, Prettier, Git, jq, Python/PyYAML, Ruff, and actionlint. `policy/pins.json` separately records the approved shared pins.

```bash
nix fmt --no-update-lock-file
nix flake check --no-update-lock-file --print-build-logs
```

`nix flake check` runs checker tests, record validation, formatting, Nix lint, Python lint, and workflow validation for the host architecture. CI runs these checks on both supported Linux architectures. This repository has no VM tests.

Development shells, formatter, and check outputs cover `x86_64-linux` and `aarch64-linux`. The default app and the `default` and `policy-check` packages expose `nixos-project-policy` for every system in the pinned nixpkgs package sets. Those additional package outputs do not imply CI coverage on every system.

The formatter covers Nix, shell, Markdown, YAML, JSON, and Python. It preserves Markdown wrapping. Formatting checks use writable source copies without Git metadata.

### Focused tests

```bash
nix develop --command python -m unittest discover -s tests -v
nix fmt -- --ci
nix run .# -- --policy-root . validate
nix run .# -- --version
```

### Host tests

These tests run outside Nix build sandboxes because they invoke Nix themselves. The CI workflow runs both in one development-shell invocation on each supported architecture:

```bash
nix develop --no-update-lock-file --command python -m unittest tests.nix_compatibility tests.packaged_policy -v
```

`tests.nix_compatibility` runs real metadata queries and root checks with both exact nixpkgs overrides. It checks lock preservation, rejection of required default-lock updates, nonempty host checks under the committed lock, and the explicit default development-shell requirement.

`tests.packaged_policy` builds the checker from a controlled clean repository. It exercises member checks, checks before enrollment, audits, and integration agreement using the current release contract.

Test adapters supply unpublished release metadata, GitHub responses, and Nix process results. Git operations, record processing, checker code, and packaged commands execute normally. Use the real-Nix test to verify native override behavior. Neither host test verifies live GitHub merge protection.

CI also smoke-tests the default development shell and evaluates the formatter:

```bash
nix run --no-update-lock-file .# -- shell .
```

## Member checks

When the member selects the version in this checkout's `VERSION`, run the local checker with explicit trusted records:

```bash
nix run .# -- --policy-root . check ../PROJECT --project PROJECT
```

For another selected release, use that release's checker with a separate current record checkout, as shown in the [checker reference](checker.md#commands). Add `--shell` to smoke-test the member's development environment and evaluate its formatter. The default check reads files; compatibility execution uses the separate `compatibility` command. Checks and audits do not update member sources or lockfiles, though shell and compatibility commands execute member code. See the [checker reference](checker.md) for results and limits.

## Nix conventions

Follow the [shared Nix rules](../POLICY.md#nix-code-and-tests). Root `flake.nix` declares `systems`, inputs, and public outputs. Expressions in `nix/` supply the package and formatter through `pkgs.callPackage`. The check constructor takes named arguments; its callers appear above its implementation.

The shell probe uses `nix eval --impure --expr builtins.currentSystem` to identify the host before checking its default development shell and formatter. Dependency and build evaluation use the locked flake. Review command usage, module dependencies, and names as well as lint results.
