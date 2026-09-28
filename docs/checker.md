# Checker reference

The checker supports v0.4.0 and later. Member callers and consumed member revisions must select a release in that range.

Run the checker from its selected policy release or packaged `nixos-project-policy` executable. Use `--version` to identify it.

`--policy-root PATH` selects a trusted record checkout. It is required for member checks, CI planning, compatibility, and VM execution. These commands do not default to a release's bundled records. Other commands default to bundled records.

## Commands

Use this prefix with the commands below:

```bash
nix run --no-update-lock-file .# -- --policy-root . COMMAND
```

| Command                                              | Behavior                                                                                         |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `validate`                                           | Check record structure and report whether pins are approved.                                     |
| `ci PATH --project NAME`                             | Report CI matrices and required status names. Do not execute checks.                             |
| `check PATH --project NAME`                          | Inspect a member checkout against its selected policy and approved pins.                         |
| `check PATH --project NAME --shell`                  | Also smoke-test the member shell and evaluate its root formatter.                                |
| `compatibility PATH --project NAME --channel stable` | Verify the stable override and run full root host checks. Use `unstable` for the other revision. |
| `host-checks PATH`                                   | Require nonempty `checks.<host-system>` under the committed lock, without building checks.       |
| `vm PATH --project NAME`                             | Execute declared VM targets on a suitable builder. Return `not-applicable` if none exist.        |
| `candidate --stable COMMIT --unstable COMMIT`        | Emit an unapproved pair. Do not write locks or approve pins.                                     |

For a shell-only probe, run `nix run --no-update-lock-file .# -- shell PATH`. This first evaluates `devShells.<host-system>.default.drvPath`, enters the development shell with the inherited environment cleared, and executes `bash -c ':'`. It also evaluates the root formatter without asserting compliance. A default package or non-default shell cannot satisfy the development-shell requirement. Policy CI uses it as a host smoke test. The probe checks startup and command execution, not a fixed tool list or project-specific development tasks.

Use a selected release with current records cloned from `main` into a temporary directory:

```bash
MEMBER_RECORDS_DIR=$(mktemp -d)
git clone --branch main --single-branch \
  https://github.com/petohorvath/nixos-project-policy.git "$MEMBER_RECORDS_DIR"
nix run github:petohorvath/nixos-project-policy/v0.4.0 -- \
  --policy-root "$MEMBER_RECORDS_DIR" \
  check ../member --project member --shell
rm -rf "${MEMBER_RECORDS_DIR:?}"
```

Name the variable after the member, such as `NIXOS_REGISTRY_RECORDS_DIR`. Run every command that uses it in the same shell, before the cleanup command. Local commands neither fetch records nor prove that a checkout is current.

`check`, `ci`, `compatibility`, and `vm` require one member caller selecting the executing checker release. The caller's `policy_version` must match its immutable workflow reference. Enrollment does not change this requirement.

### Results

Record and member commands print JSON. Reports include `checkerVersion`. Member reports also identify `policyVersion`, validated `memberSettings`, and the member commit when available. Reusable CI logs the captured checker and record commits.

| Exit | Meaning                                                                             |
| ---- | ----------------------------------------------------------------------------------- |
| 0    | The requested operation succeeded. Success applies to the supplied record snapshot. |
| 1    | Enforced checks failed.                                                             |
| 2    | The request, records, or inspection could not be processed.                         |

A normal `check` can return `pass` before enrollment. Its separate `enrollment` field is `enrolled` or `not-enrolled`. Static checks report `compatibility: "not-run"`. No result changes enrollment or approves pins.

`ci` returns `planned`, `matrix`, `compatibilityMatrix`, `vmTargets`, `vmJob` (check name, system, and runner), and the complete `requiredChecks`. Omit `PATH` only when the current directory is the member. Hosted `--inputs-json JSON` must normalize to the checked-out caller's inputs; it cannot replace member settings.

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

The runner tests the exact `approved` pair from the supplied record snapshot. Success returns `pass` with `pinStatus: "approved"`; a missing pair fails. This field describes the supplied records, not whether they have merged to `main`. Testing proposed records does not grant central approval.

Reports include:

- Source: the member `revision`.
- Checker: `checkerVersion`.
- Execution: `channel`, `system`, `expectedRevision`, `resolvedRevision`, host check names, command arguments, and exit outcomes.

Source or lock changes during execution fail the run. The runner does not repair files changed by project code. Compatibility failures return exit 1; invalid requests or unreadable records return exit 2.

## Member declarations and central records

The policy caller owns literal `project`, `policy_version`, and these string inputs:

| Input                        | Contract                                                                |
| ---------------------------- | ----------------------------------------------------------------------- |
| `required_architectures`     | Required nonempty JSON list of unique Nix system names                  |
| `vm_architecture`            | Optional literal Linux Nix system; default `x86_64-linux`               |
| `vm_targets`                 | Optional JSON list of simple lowercase build target names; default `[]` |
| `additional_required_checks` | Optional JSON list of additional GitHub status names; default `[]`      |

Validation rejects malformed JSON, wrong types, duplicates, unsupported values, dynamic expressions, forbidden inputs, and missing or multiple callers. Architectures cannot be empty. Additional gates cannot remove mandatory statuses or create jobs. Reports use `requiredArchitectures`, `vmTargets`, `vmArchitecture`, and `additionalRequiredChecks` inside `memberSettings`.

Architecture selection has no platform allowlist. System names use an architecture and platform separated by a hyphen, such as `riscv64-linux` or `aarch64-darwin`, with letters, digits, underscores, and hyphens. The existing `x86_64-linux` and `aarch64-linux` mappings use `ubuntu-24.04` and `ubuntu-24.04-arm`. Other systems use runner labels `["self-hosted", SYSTEM]`. Provide a matching runner with Nix and the workflow prerequisites before running hosted checks; accepting a declaration does not establish runner availability or successful builds. Compliance, project tests, and compatibility jobs use these mappings.

VM execution uses `vm_architecture` independently of ordinary coverage. The `ci.vmRunners` mapping selects `ubuntu-24.04` for x86_64 Linux; other Linux systems use `["self-hosted", SYSTEM]`. VM workers must provide Nix and usable KVM. An ARM-only member can set `vm_architecture: aarch64-linux` and provide an ARM runner with KVM. Locally, `vm` builds each declared target with `nix build --no-link --no-update-lock-file --print-build-logs`; it does not select a worker or configure KVM. Run it on a suitable host or with a configured builder for the declared VM system.

The policy flake exposes its executable for every system in its pinned nixpkgs package sets. Systems outside those package sets require checker packaging support before hosted execution can succeed. This repository's own development and check outputs remain on its two Linux CI platforms.

### Record ownership

| File                       | Schema | Contents                                                              |
| -------------------------- | ------ | --------------------------------------------------------------------- |
| `policy/members.json`      | 1      | `members` maps enrolled names to GitHub `owner/repository` identities |
| `policy/pins.json`         | 1      | Stable update branch, approved stable/unstable pair                   |
| `policy/requirements.json` | 1      | Policy repository identity and release-owned CI requirements          |

The member roster contains no copied selections, settings, or adoption flags. Names and repository identities must be unique; repository comparison ignores case. Missing, malformed, or duplicate-key records fail inspection. An empty roster is valid and distinct from missing data.

Reviewed central changes control enrollment and removal. Ordinary upgrades and checks before enrollment leave the roster unchanged.

### Release requirements

The checker reads `requirements.json` beside its own code, independently of `--policy-root`. Current records cannot replace release requirements or policy repository identity. Named runner systems come from `ci.runners`; there is no separate systems list.

The `ci` fields define the caller name, common `requiredChecks`, per-architecture `architectureChecks`, `compatibilityChecks` templates, `runners`, `vmRunners`, and the conditional `vmCheck` status template. Templates use `{architecture}`. The checker combines these fields with member settings to produce ordinary and compatibility matrices and the complete gate list. Additional gate names do not create workflow jobs.

### Shared pins

`pins.json` contains only `schemaVersion`, `stableBranch`, and `approved`. The stable branch names the NixOS update source. The approved pair contains exact `stable` and `unstable` commits, or is null before initial approval. The root bootstrap lock does not approve shared pins. Changes to member commits require no pin-record update.

Static checks compare shared-pin lock scopes against this single pair, excluding the independently selected root lock node. Compatibility execution tests both revisions through root input overrides. Proposed pairs can be tested with a reviewed record checkout supplied through `--policy-root`. Normal member CI captures records from `main`.

## Implemented coverage

The checker resolves version-7 lock graphs, including root-relative `follows`, across first-party lockfiles. It ignores unreachable nodes and vendor/cache directories. It identifies GitHub sources from GitHub inputs and Git URLs, checks immutable selections, flags ambiguous nixpkgs sources, and builds member dependency graphs. Policy repository flake dependencies fail.

Root `nixpkgs` must resolve to an immutable `NixOS/nixpkgs` flake. Its revision and update branch can differ from shared pins. The exemption includes references that follow the resolved root node.

Additional root inputs, distinct transitive nodes, and independently locked examples require one allowed pair across the project. In these scopes, first-party stable inputs use `nixpkgs`; unstable inputs use `nixpkgs-unstable`. An absent input need not be added. Transitive input names and lock node identifiers are unrestricted. Branch declarations identify stable/unstable selections; exact revisions can identify them when they match one allowed value uniquely.

Structural checks inspect the root development entrypoint, required files, links to the selected release's `POLICY.md`, and `--policy-root` paths in Markdown files. A `--policy-root` value of `..` or one starting with `../` fails, because it places a record checkout beside the member. The checker requires a root `README.md` without inspecting its content or headings.

Caller validation requires:

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
| `Policy / VM tests (<vm_architecture>)`               | Members with VM targets    |

The first job verifies the release version and generates matrices once on x86_64. This metadata job does not add x86_64 to the member's required architectures or publish a release.

Compliance runs structural, pin, caller, and shell checks. Members own formatting and lint enforcement. Project tests first run `host-checks` to require nonempty host checks with `--no-update-lock-file`, then run full committed-lock root checks. Compatibility runs both shared revisions.

Each category runs independently on every required architecture with `fail-fast: false`. They and the VM job depend only on the first job. A failure does not suppress other categories. Declared VM targets require the gate for the selected VM architecture. Changing that selection requires updating the member's required merge status; review must justify any coverage reduction. Without targets, CI skips the VM job without allocating a worker, the local `vm` command reports `not-applicable`, and the VM status need not be required.

Standard PR checkout tests GitHub's candidate merge commit. Each run captures its source and current records without registering that commit centrally. A merged pin update takes effect when a new member CI run captures the updated records.

Workflows use read-only permissions and do not persist checkout credentials. Automation that creates member PRs requires a separately reviewed write identity. Normal policy checks require no such credential.

The workflow invokes `check --shell` with the same requirements before and after enrollment. It reports checks and enrollment separately. Successful CI does not enroll members or approve pins.
