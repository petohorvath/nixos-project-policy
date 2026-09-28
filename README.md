# nixos-project-policy

Shared rules for the Nix flake repos in devnix-labs. [POLICY.md](POLICY.md) states what each repo guarantees about its inputs, public outputs, and tests, and [CONTEXT.md](CONTEXT.md) defines the terms. This repository holds the checker that enforces the rules, the reusable workflow that runs it, and the stable and unstable pins that each release bundles in [data/pins.json](data/pins.json).

## Caller workflow

Copy [templates/policy-caller.yml](templates/policy-caller.yml) into the repo's `.github/workflows/`. Its job calls `check.yml@v0.5` with one optional input, `systems`: a JSON list in a string that defaults to `'["x86_64-linux", "aarch64-linux"]'`.

```yaml
jobs:
  policy:
    name: Policy
    uses: petohorvath/nixos-project-policy/.github/workflows/check.yml@v0.5
    # Optional; defaults to both Linux systems.
    # with:
    #   systems: '["x86_64-linux"]'
```

[POLICY.md](POLICY.md#caller) lists the statuses to require; the workflow's `Plan` job also writes them to its step summary.

`v0.5` moves to every patch release, so pin bumps and fixes reach the repo without a change to its caller. Breaking rule changes start a new minor series, such as `v0.6`.

## Local check

Run the checker from the repo root:

```bash
nix run github:petohorvath/nixos-project-policy/v0.5 -- check .
```

`check` applies the input and public-output rules, starts the default development shell, and evaluates the formatter. CI also runs `test . --nixpkgs locked`, `stable`, and `unstable` on each system, and `vm .` when the flake has `legacyPackages.<system>.vmTests`; `--help` lists every command. Each command prints a JSON report and exits 0 on success, 1 when a rule or test fails, and 2 when the request cannot be processed.

Nix caches the `v0.5` reference for up to an hour. Add `--refresh` after `nix run` to use a patch release published within that time.

## Pin bumps

1. The [Propose pin bump](.github/workflows/pins.yml) workflow runs weekly, or on manual dispatch with both an exact `stable_revision` and `unstable_revision`. When the pins change, it opens the `automation/pin-bump` PR, which updates the pins, sets `VERSION` to the next patch release, and adds a changelog section.
2. The [Pin-bump tests](.github/workflows/pin-bump-tests.yml) workflow runs `test --nixpkgs stable` and `test --nixpkgs unstable` on each system, and `vm`, from the PR against the `main` branch of every repo in [data/repos.json](data/repos.json). Fix a failing repo through a PR in that repo, then re-run the failed jobs.
3. After a human merges the PR, the [Tag pin-bump release](.github/workflows/pin-bump-release.yml) workflow tags `vX.Y.Z` and moves `vX.Y` to it.

The proposal needs Settings → Actions → General → Workflow permissions → "Allow GitHub Actions to create and approve pull requests". Listed repos must be public. If rulesets are added, let GitHub Actions force-push `automation/pin-bump`, create `vX.Y.Z` tags, and force-update `vX.Y` tags.

## Releases

`VERSION` holds the current version; its release tag adds a `v` prefix. Patch releases carry pin bumps and fixes. A breaking change bumps `VERSION` to the next minor version in the PR that makes it, so every patch release from `main` stays compatible with its series.

For a release by hand, merge a PR that sets `VERSION` and moves the `Unreleased` changelog entries under the version heading. Tag the merge commit `vX.Y.Z` and create or move `vX.Y` to it. Pin-bump proposals fail until the tag for `VERSION` exists. Never move or reuse a `vX.Y.Z` tag.

Until nixos-registry and nixos-cross-config call `@v0.5`, keep `policy/pins.json` and `policy/members.json` on `main` for their v0.4.0 callers. Pin bumps update `policy/pins.json` as well.

## Development

Follow [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/development.md](docs/development.md). Design decisions are in [docs/adr/](docs/adr/), and release changes in [CHANGELOG.md](CHANGELOG.md). Original code uses the [MIT license](LICENSE).
