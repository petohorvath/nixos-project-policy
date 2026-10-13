# nixos-project-policy

nixos-project-policy is the shared policy for the Nix flake repos in devnix-labs, with the checker that enforces it and a reusable GitHub Actions workflow that runs the checker. A repo that calls the workflow gets the same checks of its inputs and public outputs, and the same tests, as every other repo, against the same nixpkgs pins, without keeping the rules in its own CI.

## Overview

[POLICY.md](POLICY.md) states the three guarantees each repo makes:

- **Inputs:** root `nixpkgs` is a locked revision, and inputs from other repos reference release tags and follow root `nixpkgs`.
- **Public outputs:** every public output evaluates, and removing one after the first release tag needs a minor or major version bump.
- **Tests:** every entry of the `checks` output builds with the locked nixpkgs, the stable pin, and the unstable pin.

Each policy release bundles the checker with its stable and unstable pins in [data/pins.json](data/pins.json): exact revisions of the stable NixOS branch and of `nixos-unstable`. [GLOSSARY.md](GLOSSARY.md) defines the terms, and [docs/adr/](docs/adr/) records the design decisions.

## Requirements

- Nix with the `nix-command` and `flakes` features enabled. The checker runs its own Nix 2.34 client from its pinned nixpkgs; CI installs Nix 2.34.6.
- A flake that provides `devShells.<system>.default`, `formatter.<system>`, and a nonempty `checks.<system>` on each checked system.
- GitHub Actions, for the reusable workflow.

## Add the policy to a repo

Copy [templates/policy-caller.yml](templates/policy-caller.yml) into the repo's `.github/workflows/`. Its `Policy` job calls the reusable `check.yml` workflow from the `v0.6` series:

```yaml
jobs:
  policy:
    name: Policy
    uses: petohorvath/nixos-project-policy/.github/workflows/check.yml@v0.6
    # Optional; defaults to both Linux systems.
    # with:
    #   systems: '["x86_64-linux"]'
```

The optional `systems` input is a JSON list of Nix systems in a string and defaults to `'["x86_64-linux", "aarch64-linux"]'`. Those two systems run on GitHub-hosted runners; any other system needs a self-hosted runner labelled with the system name.

Then require the policy's statuses on the repo's default branch. [POLICY.md](POLICY.md#caller) lists them, and the workflow's `Plan` job writes the exact names for the repo to its step summary.

## Check a repo locally

Run the checker from the repo's root:

```bash
nix run github:petohorvath/nixos-project-policy/v0.6 -- check .
```

The report goes to standard output and Nix's build output to standard error. For this repository at v0.6.1, the report is:

```json
{
  "checkerVersion": "v0.6.1",
  "issues": [],
  "notices": [],
  "release": {
    "tag": "v0.6.1",
    "version": "0.6.1"
  },
  "revision": "a312749162280eb9b157c5841c938242cab38065",
  "rules": {
    "extra-nixpkgs": "pass",
    "formatter": "pass",
    "lock": "pass",
    "no-policy-input": "pass",
    "outputs-empty": "pass",
    "outputs-evaluate": "pass",
    "outputs-removal": "pass",
    "root-nixpkgs": "pass",
    "shell": "pass",
    "sibling-follows-nixpkgs": "pass",
    "sibling-one-revision": "pass",
    "sibling-tag": "pass"
  },
  "siblings": [],
  "status": "pass"
}
```

Run the tests of the `Tests` and `VM tests` jobs the same way:

```bash
nix run github:petohorvath/nixos-project-policy/v0.6 -- test . --nixpkgs locked
nix run github:petohorvath/nixos-project-policy/v0.6 -- test . --nixpkgs stable --vm
```

## Commands

| Command                                             | Action                                                                                                                       |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `check REPO`                                        | Applies the input and public-output rules, starts the default development shell, and evaluates the formatter                 |
| `test REPO --nixpkgs locked\|stable\|unstable`      | Evaluates every output and builds the entries of the `checks` output that are not VM tests                                   |
| `test REPO --nixpkgs locked\|stable\|unstable --vm` | Builds only the VM tests, the entries of the `checks` output that require `nixos-test`, without evaluating the other outputs |
| `ci REPO [--systems JSON]`                          | Prints the CI job matrices and the required status names                                                                     |
| `survey WORKSPACE`                                  | Runs `check` on every Git repo directly under `WORKSPACE` and prints a table of rule results to standard error               |
| `validate`                                          | Validates the bundled pins and repo list                                                                                     |

`check` and `test` cover the host system. Each command prints a JSON report and exits 0 on success, 1 when a rule or test fails, and 2 when the request cannot be processed. `survey` also exits 1 when the check of any repo cannot be processed. `--help` lists each command's arguments.

`test` builds each entry whose `requiredSystemFeatures` the host's `system-features` provide and lists the others under `skipped`. A mode whose nixpkgs revision equals that of an earlier mode, in the order locked, stable, unstable, passes without building and names that mode in `sameAs`.

[docs/development.md](docs/development.md#checking-another-repo) shows how to run a local checkout of the checker against another repo.

## Policy updates

`v0.6` moves to every patch release, so pin bumps and fixes reach the repo without a change to its caller. Breaking rule changes start a new minor series, such as `v0.7`, and [CHANGELOG.md](CHANGELOG.md) describes the migration to it.

Before a human merges a pin bump, its tests run against every repo listed in [data/repos.json](data/repos.json). [docs/releases.md](docs/releases.md) describes pin bumps and releases.

Nix caches the `v0.6` reference for up to an hour. Add `--refresh` after `nix run` to use a patch release published within that time.

## Limitations

- `check` fails on a shallow clone, which can lack the last release tag.
- `check` and `test` execute the checked repo's code, such as its development shell and builds.
- On a host without KVM, plain `nix flake check` fails when the repo has VM tests. Build the other entries with `nix build .#checks.<system>.<name>`, or give `nix flake check` a remote builder with the `kvm` feature; `test --vm` reads only the local `system-features` and still skips the VM tests.

## Contributing

Changes go through pull requests that a human approves. [CONTRIBUTING.md](CONTRIBUTING.md) describes the process, and [docs/development.md](docs/development.md) the development shell and checks. Report problems and propose rule changes in [GitHub Issues](https://github.com/petohorvath/nixos-project-policy/issues).

## License

Original code is licensed under `MIT`; see [LICENSE](LICENSE).
