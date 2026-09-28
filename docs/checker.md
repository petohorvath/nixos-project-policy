# Checker reference

The checker supports v0.4.0 and later. Member callers and consumed member revisions must select a release in that range.

Run the checker from its selected policy release or packaged `nixos-project-policy` executable. Use `--version` to identify it.

Every command reads the pins and the repo list bundled with the executing checker, in `data/pins.json` and `data/repos.json`. No command takes a separate data checkout.

## Commands

Use this prefix with the commands below:

```bash
nix run --no-update-lock-file .# -- COMMAND
```

| Command                                              | Behavior                                                                                         |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `validate`                                           | Check the bundled pins and repo list and print them.                                             |
| `ci PATH --project NAME`                             | Report CI matrices and required status names. Do not execute checks.                             |
| `check PATH`                                         | Apply the input rules to the root `flake.lock`.                                                  |
| `check PATH --shell`                                 | Also smoke-test the member shell and evaluate its root formatter.                                |
| `compatibility PATH --project NAME --channel stable` | Verify the stable override and run full root host checks. Use `unstable` for the other revision. |
| `host-checks PATH`                                   | Require nonempty `checks.<host-system>` under the committed lock, without building checks.       |
| `vm PATH`                                            | Build every `legacyPackages.<host-system>.vmTests` entry. Return `not-applicable` if none exist. |
| `candidate --stable COMMIT --unstable COMMIT`        | Emit an unapproved pair. Do not write locks or approve pins.                                     |

For a shell-only probe, run `nix run --no-update-lock-file .# -- shell PATH`. This first evaluates `devShells.<host-system>.default.drvPath`, enters the development shell with the inherited environment cleared, and executes `bash -c ':'`. It also evaluates the root formatter without asserting compliance. A default package or non-default shell cannot satisfy the development-shell requirement. Policy CI uses it as a host smoke test. The probe checks startup and command execution, not a fixed tool list or project-specific development tasks.

Run a selected release directly:

```bash
nix run github:petohorvath/nixos-project-policy/v0.4.0 -- \
  check ../member --shell
```

`ci` and `compatibility` require one member caller selecting the executing checker release. The caller's `policy_version` must match its immutable workflow reference. Enrollment does not change this requirement.

### Results

Record and member commands print JSON. Reports include `checkerVersion`. Member reports also identify `policyVersion`, validated `memberSettings`, and the member commit when available. Reusable CI logs the checker and project commits.

| Exit | Meaning                                                                             |
| ---- | ----------------------------------------------------------------------------------- |
| 0    | The requested operation succeeded. Success applies to the supplied record snapshot. |
| 1    | Enforced checks failed.                                                             |
| 2    | The request, records, or inspection could not be processed.                         |

`check` reads no caller workflow. Its report maps each rule id to `pass`, `fail`, `notice`, or `not-run` in `rules`. `issues` lists failures and `notices` lists findings that pass but need review; each names its `rule`, `input`, and `message`. `siblings` lists every sibling input with its repository, revision, and reference kind (`tag`, `commit`, or `branch`). No result changes enrollment or approves pins.

`ci` returns `planned`, `matrix`, `compatibilityMatrix`, and the complete `requiredChecks`. Omit `PATH` only when the current directory is the member. Hosted `--inputs-json JSON` must normalize to the checked-out caller's inputs; it cannot replace member settings.

### Lock handling

Default checks and shell probes use `--no-update-lock-file` alone to reject required lock updates. Do not add `--no-write-lock-file`. In Nix 2.34.6, disabling writes also bypasses update rejection and permits an in-memory replacement lock. See the [locking implementation](https://github.com/NixOS/nix/blob/2.34.6/src/libflake/flake.cc#L749-L825).

Compatibility checks deliberately use an overridden graph. `--override-input` implies `--no-write-lock-file`. Adding `--no-update-lock-file` does not make an override check test the committed selection.

## Compatibility execution

`compatibility` uses the same runner locally and in CI. It requires:

- A member caller selecting the executing checker release.
- A Git checkout whose root lock matches its committed copy.
- A native Nix host whose system name passes declaration validation and whose package set can build the checker and member checks. The runner has no Linux-only restriction.
- A non-null approved pair in the supplied trusted records.

The runner resolves the root input through `LockGraph`, including renamed nodes and `follows`. It verifies the repository and revision from `nix flake metadata --json`. A missing input, ignored override, wrong source, or wrong revision fails before execution.

The runner requires nonempty `checks.<host-system>` under the same override. It then executes:

```bash
nix flake check PATH --print-build-logs \
  --override-input nixpkgs github:NixOS/nixpkgs/REVISION
```

It does not use `--no-build`. Nix can satisfy builds from its cache; success does not prove every test process ran again. See the Nix [check options](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake-check.html) and [metadata output](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake-metadata.html).

The runner tests the exact pin from the checker's bundled `data/pins.json`. Success returns `pass` with `pinStatus: "approved"`.

Reports include:

- Source: the member `revision`.
- Checker: `checkerVersion`.
- Execution: `channel`, `system`, `expectedRevision`, `resolvedRevision`, host check names, command arguments, and exit outcomes.

Source or lock changes during execution fail the run. The runner does not repair files changed by project code. Compatibility failures return exit 1; invalid requests or unreadable records return exit 2.

## Member declarations and central records

The policy caller owns literal `project`, `policy_version`, and these string inputs:

| Input                        | Contract                                                           |
| ---------------------------- | ------------------------------------------------------------------ |
| `required_architectures`     | Required nonempty JSON list of unique Nix system names             |
| `additional_required_checks` | Optional JSON list of additional GitHub status names; default `[]` |

Validation rejects malformed JSON, wrong types, duplicates, unsupported values, dynamic expressions, forbidden inputs, and missing or multiple callers. Architectures cannot be empty. Additional gates cannot remove mandatory statuses or create jobs. Reports use `requiredArchitectures` and `additionalRequiredChecks` inside `memberSettings`. The caller no longer declares VM tests; `vm_targets` and `vm_architecture` are rejected.

Architecture selection has no platform allowlist. System names use an architecture and platform separated by a hyphen, such as `riscv64-linux` or `aarch64-darwin`, with letters, digits, underscores, and hyphens. The existing `x86_64-linux` and `aarch64-linux` mappings use `ubuntu-24.04` and `ubuntu-24.04-arm`. Other systems use runner labels `["self-hosted", SYSTEM]`. Provide a matching runner with Nix and the workflow prerequisites before running hosted checks; accepting a declaration does not establish runner availability or successful builds. Compliance, project tests, and compatibility jobs use these mappings.

VM tests are discovered, not declared. `vm` evaluates `legacyPackages.<host-system>.vmTests` and builds every entry with `nix build --no-link --no-update-lock-file --print-build-logs`. It continues after a failed build and reports the discovered names in `vmTests` and the failing names in `failed`. A missing or empty `vmTests` returns `not-applicable`. `nix flake check` does not build `vmTests`. The reusable workflow runs `vm` on `ubuntu-24.04` (x86_64 Linux) with KVM; locally, run it on a host with KVM or a configured builder.

The policy flake exposes its executable for every system in its pinned nixpkgs package sets. Systems outside those package sets require checker packaging support before hosted execution can succeed. This repository's own development and check outputs remain on its two Linux CI platforms.

### Record ownership

| File                       | Contents                                                          |
| -------------------------- | ----------------------------------------------------------------- |
| `data/pins.json`           | `stable` and `unstable` pins and the `stableBranch` update branch |
| `data/repos.json`          | `repos`: a list of GitHub `owner/repository` identities           |
| `policy/requirements.json` | Policy repository identity and release-owned CI requirements      |

Repository identities must be unique; comparison ignores case. A member counts as enrolled when a listed identity's repository name equals its project name. Missing, malformed, or duplicate-key files fail every command with exit 2. An empty repo list is valid.

`policy/pins.json` and `policy/members.json` remain on `main` unchanged for v0.4.0 callers. The checker no longer reads them.

### Release requirements

The checker reads `requirements.json` beside its own code. Named runner systems come from `ci.runners`; there is no separate systems list.

The `ci` fields define the caller name, common `requiredChecks`, per-architecture `architectureChecks`, `compatibilityChecks` templates, and `runners`. Templates use `{architecture}`. The checker combines these fields with member settings to produce ordinary and compatibility matrices and the complete gate list. Additional gate names do not create workflow jobs.

### Shared pins

`data/pins.json` contains only `stable`, `unstable`, and `stableBranch`. The pins are exact 40-character lowercase commits. The stable branch names the NixOS update source.

Compatibility execution tests both revisions through root input overrides.

## Implemented coverage

`check` reads only the root `flake.lock`; other lock files, such as independently locked examples, are ignored. It resolves the version-7 lock graph, including root-relative `follows`, and ignores unreachable nodes. It identifies GitHub sources from GitHub inputs and Git URLs. A sibling is any GitHub repository under an owner of a repo in `data/repos.json`, except the policy repository. Third-party inputs are not checked.

| Rule                      | Fails when                                                                                                      |
| ------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `lock`                    | The root `flake.lock` is missing or is not a valid version-7 lock graph. The other rules then report `not-run`. |
| `root-nixpkgs`            | Root `nixpkgs` is missing or does not resolve to a `NixOS/nixpkgs` flake locked to an exact commit.             |
| `extra-nixpkgs`           | Never. Another root input that resolves to a distinct `NixOS/nixpkgs` node is reported as a notice.             |
| `sibling-tag`             | A root sibling input references a branch or the default branch. A commit reference is a temporary notice.       |
| `sibling-one-revision`    | A sibling repository appears at more than one revision in the reachable lock graph.                             |
| `sibling-follows-nixpkgs` | A reachable sibling's `nixpkgs` input does not resolve to the root `nixpkgs` node.                              |
| `no-policy-input`         | The policy repository appears anywhere in the reachable lock graph.                                             |

`check --shell` adds the `shell` rule.

Caller validation for `ci`, `compatibility`, and `vm` requires:

- One exact release reference, matching `policy_version`, and literal job name `Policy`.
- PR event types must include `opened`, `synchronize`, and `reopened`, in any order. The `edited` event is optional.
- No branch, path, or other trigger filters.
- No caller matrix, error suppression, `if`, or `needs` on the Policy job.
- Only the declared identity, release, and settings inputs. No compatibility revision overrides or skipped required revisions.

The reusable workflow owns job matrices. The caller runs for every PR, including documentation changes.

The hosted workflow checks out the selected tag, verifies `VERSION`, and captures checker, member, and current record commits. All jobs use these commits. A missing tag or version mismatch stops execution.

## Review responsibilities

Automatic checks do not prove all policy requirements. Review these properties:

- Meaningful functional coverage, use of the overridden input, and separation of VM tests.
- Formatter language coverage and documented generated-source exclusions.
- Project-specific development tasks, Nix/plugin compatibility, visible root `systems`, and explicit flake outputs.
- Source-level dependencies, arbitrary fetch expressions, unsupported transports, and first-party flakes without independent locks.
- NixOS option semantics, names, prose quality, and justified lint suppressions.
- Justified reductions in required architectures, VM tests, or additional gates.
- Actual workflow provenance, bypass permissions, review settings, and release publication controls.
- Test evidence and human approval for shared-pin updates.

Required status names alone do not prove which workflow code ran. New generated-source exclusions need a documented checker extension before enrollment.

## CI integration

Use [templates/policy-caller.yml](../templates/policy-caller.yml). Set the member name and both version placeholders to the same published release tag. The policy repository's `main` branch requires agreed human and CI merge controls before activation.

The first job captures the checker, member, and current record commits. All later jobs use these snapshots. The caller's `name: Policy` supplies the status prefix. Run `ci PATH --project NAME` for exact names and matrices.

| Status                                                | Required for               |
| ----------------------------------------------------- | -------------------------- |
| `Policy / Verify policy version and load shared pins` | Every enrolled member      |
| `Policy / Compliance (<architecture>)`                | Each required architecture |
| `Policy / Project tests (<architecture>)`             | Each required architecture |
| `Policy / Compatibility (stable, <architecture>)`     | Each required architecture |
| `Policy / Compatibility (unstable, <architecture>)`   | Each required architecture |

The first job verifies the release version and generates matrices once on x86_64. This metadata job does not add x86_64 to the member's required architectures or publish a release.

Compliance runs the input rules and shell checks. Members own formatting and lint enforcement. Project tests first run `host-checks` to require nonempty host checks with `--no-update-lock-file`, then run full committed-lock root checks. Compatibility runs both shared revisions.

Each category runs independently on every required architecture with `fail-fast: false`. They and the VM job depend only on the first job. A failure does not suppress other categories. The `VM tests` job always runs on x86_64 Linux and reports `not-applicable` without `vmTests`; `ci` does not list it among the required statuses.

Standard PR checkout tests GitHub's candidate merge commit. Each run captures its source and current records without registering that commit centrally. A merged pin update takes effect when a new member CI run captures the updated records.

Workflows use read-only permissions and do not persist checkout credentials. Automation that creates member PRs requires a separately reviewed write identity. Normal policy checks require no such credential.

The workflow invokes `check --shell` with the same requirements before and after enrollment. Successful CI does not enroll members or approve pins.
