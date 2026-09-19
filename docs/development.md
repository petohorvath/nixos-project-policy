# Development

## Host prerequisites

Install Nix with `nix-command` and `flakes` enabled. Install direnv with `use flake` support and enable its integration in the interactive shell. Direnv can provide the integration directly. Nix-direnv is an optional alternative; follow its [installation instructions](https://github.com/nix-community/nix-direnv#installation) when using it. The repository's `.envrc` checks for Nix and flake integration and reports a missing prerequisite.

Run `direnv allow` after reviewing `.envrc`. Alternatively, enter the same shell with `nix develop`. Nix is required before entering a shell even though the shell also supplies a pinned Nix executable. Host Nix 2.34.6 is the bootstrap version used by the CI definitions.

## Tools and checks

The stable lock supplies Nix, nil, nixfmt, statix, deadnix, treefmt, shfmt, Prettier, Git, jq, Python/PyYAML, Ruff, and actionlint. Python, Ruff, jq, and actionlint support this project's checker and workflow tests. No unstable tool override is currently needed. `flake.lock` records exact bootstrap revisions; `policy/pins.json` separately records family approval.

```bash
nix fmt
nix flake check --print-build-logs
```

The complete command runs checker tests, policy-record validation, formatting, Nix lint, Python lint, and workflow validation for the host architecture. No VM execution is included. CI definitions run these checks on both supported Linux architectures. A separate host-level fixture executes real Nix metadata and full root checks with both exact overrides, verifies lock preservation, and verifies default-lock update rejection; nested Nix execution keeps this fixture outside build-sandbox checks.

For a focused test run:

```bash
nix develop --command python -m unittest discover -s tests -v
nix develop --command python -m unittest tests.nix_compatibility -v
nix fmt -- --ci
nix run .# -- validate
nix run .# -- --version
```

The formatter covers Nix, shell, Markdown, YAML, JSON, and Python present in this repository. Go formatting belongs in projects containing first-party Go. Markdown uses preserved wrapping so focused edits do not reflow existing paragraphs. Formatting checks operate on writable source copies without Git metadata.

Inspect a project with `nix run .# -- --policy-root . check ../PROJECT --project PROJECT`, using a trusted checkout of current central records. Add `--shell` only when executing that project's development environment is intended. The default check reads files; neither it nor a local audit changes member sources or lockfiles. See [checker commands](checker.md).

## Nix conventions

The [shared Nix rules](../POLICY.md#nix-code-and-tests) apply to this repository's expressions, scripts, workflows, and documentation. Use modern `nix` subcommands and the root flake entrypoints. The check constructor takes named arguments, with its callers above its implementation. Package and formatter expressions declare their dependencies as arguments and are instantiated through `pkgs.callPackage`.

Root `flake.nix` declares `systems`, the `nixpkgs` and `nixpkgs-unstable` inputs, and each public output. Local package and formatter expressions provide the implementation through plain Nix composition.

The shell probe uses `nix eval --impure --expr builtins.currentSystem` only to identify the host platform before selecting its formatter output. Dependency and build evaluation still use the locked flake. Command usage, module boundaries, and naming require source review in addition to formatting and lint checks.
