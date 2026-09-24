# Checker reference

The checker supports v0.4.0 and later. Member callers and consumed member revisions must select a release in that range.

Run the checker from its selected policy release or packaged `nixos-project-policy` executable. Use `--version` to identify it.

`--policy-root PATH` selects a trusted record checkout. It is required for member checks, audits, CI planning, compatibility, agreement, and VM execution. These commands do not default to a release's bundled records. Other commands default to bundled records.

## Commands

Use this prefix with the commands below:

```bash
nix run .# -- --policy-root . COMMAND
```

| Command                                              | Behavior                                                                                                   |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `validate`                                           | Check record structure and report whether pins are approved.                                               |
| `ci PATH --project NAME`                             | Report CI matrices and required status names. Do not execute checks.                                       |
| `check PATH --project NAME`                          | Inspect a member checkout against its selected policy and approved pins.                                   |
| `check PATH --project NAME --shell`                  | Also smoke-test the member shell and evaluate its root formatter.                                          |
| `compatibility PATH --project NAME --channel stable` | Verify the stable override and run full root host checks. Use `unstable` for the other revision.           |
| `host-checks PATH`                                   | Require nonempty `checks.<host-system>` under the committed lock, without building checks.                 |
| `audit WORKSPACE`                                    | Inspect each enrolled checkout with its selected published checker; report failures and dependency cycles. |
| `audit WORKSPACE --fetch --github`                   | Also clone missing public checkouts and inspect GitHub enforcement. Leave existing checkouts unchanged.    |
| `agreement PATH --project NAME`                      | Compare an integration project's policy selection with its exact locked member revisions.                  |
| `vm PATH --project NAME`                             | Execute declared VM targets on a suitable builder. Return `not-applicable` if none exist.                  |
| `candidate --stable COMMIT --unstable COMMIT`        | Emit an unapproved pair. Do not write locks or approve pins.                                               |

For a shell-only probe, run `nix run .# -- shell PATH`. This first evaluates `devShells.<host-system>.default.drvPath`, enters the development shell with the inherited environment cleared, and executes `bash -c ':'`. It also evaluates the root formatter without asserting compliance. A default package or non-default shell cannot satisfy the development-shell requirement. Policy CI uses it as a host smoke test. The probe checks startup and command execution, not a fixed tool list or project-specific development tasks.

Use a selected release with current records:

```bash
nix run github:petohorvath/nixos-project-policy/v0.4.0 -- \
  --policy-root ../nixos-project-policy-records \
  check ../member --project member --shell
```

Update the trusted records checkout from `main` before a current check. Use captured snapshots for replay. Local commands neither fetch records nor prove that a checkout is current.

`check`, `ci`, `compatibility`, and `vm` require one member caller selecting the executing checker release. The caller's `policy_version` must match its immutable workflow reference. Enrollment does not change this requirement.

### Results

Record and member commands print JSON. Reports include `checkerVersion`, `policyRecordsDigest`, and `policyRecordsRevision` when Git metadata is available. Member reports also identify `policyVersion`, validated `memberSettings`, and the member commit when available. Reusable CI logs the captured checker and record commits.

| Exit | Meaning                                                                             |
| ---- | ----------------------------------------------------------------------------------- |
| 0    | The requested operation succeeded. Success applies to the supplied record snapshot. |
| 1    | Enforced checks failed.                                                             |
| 2    | The request, records, or inspection could not be processed.                         |

A normal `check` can return `pass` before enrollment. Its separate `enrollment` field is `enrolled` or `not-enrolled`. Static checks report `compatibility: "not-run"`. No result changes enrollment or approves pins.

`ci` returns `planned`, `matrix`, `compatibilityMatrix`, `vmTargets`, and the complete `requiredChecks`. Omit `PATH` only when the current directory is the member. Hosted `--inputs-json JSON` must normalize to the checked-out caller's inputs; it cannot replace member settings.

An audit succeeds only if every enrolled assessment passes or the roster is empty.

### Lock handling

Default checks and shell probes use `--no-update-lock-file` alone to reject required lock updates. Do not add `--no-write-lock-file`. In Nix 2.34.6, disabling writes also bypasses update rejection and permits an in-memory replacement lock. See the [locking implementation](https://github.com/NixOS/nix/blob/2.34.6/src/libflake/flake.cc#L749-L825).

Compatibility checks deliberately use an overridden graph. `--override-input` implies `--no-write-lock-file`. Adding `--no-update-lock-file` does not make an override check test the committed selection.

## Compatibility execution and evidence

`compatibility` uses the same runner locally and in CI. It requires:

- A member caller selecting the executing checker release.
- A Git checkout whose root lock matches its committed copy.
- A supported native Linux host.
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

- Source: `revision`, `sourceDirty`, and `sourceDigest`.
- Checker: `checkerVersion`, `checkerRevision`, and `checkerSourceDigest`.
- Records: `policyRecordsRevision` and `policyRecordsDigest`.
- Execution: `channel`, `system`, `expectedRevision`, `resolvedRevision`, host check names, command arguments, and exit outcomes.

Packaged checkers embed their clean flake revision. Dirty or unpacked sources can lack a checker commit; release replay requires a clean exact release checkout. The checker digest covers executing Python code, requirements, and version. Ordinary runs identify dirty member sources by digest; replay requires those sources. Record digests identify the records actually used.

`--output PATH` selects a new evidence directory outside the member checkout. Without it, the runner creates a temporary directory and reports `artifacts`. It writes available metadata to `metadata.json` and the completed attempt to `result.json`, including failures.

Source or lock changes during execution fail the run. The runner does not repair files changed by project code. Compatibility failures return exit 1; invalid requests or unreadable records return exit 2. CI uploads evidence after failure without masking the failed command.

For replay, create clean checkouts at the reported project, checker, and record commits. Verify the record digest, then run:

```bash
nix run --no-update-lock-file ./checker -- \
  --policy-root ./records compatibility ./project \
  --project PROJECT --channel stable --output ./replay-stable
nix flake check ./project --no-update-lock-file --print-build-logs
```

Repeat with `unstable` on every required native architecture. Keep the captured records unchanged. The replay digest must match the original evidence.

## Member declarations and central records

The policy caller owns literal `project`, `policy_version`, and these string inputs:

| Input                        | Contract                                                                |
| ---------------------------- | ----------------------------------------------------------------------- |
| `required_architectures`     | Required nonempty JSON list of unique Nix system names                  |
| `vm_targets`                 | Optional JSON list of simple lowercase build target names; default `[]` |
| `additional_required_checks` | Optional JSON list of additional GitHub status names; default `[]`      |

Validation rejects malformed JSON, wrong types, duplicates, unsupported values, dynamic expressions, forbidden inputs, and missing or multiple callers. Architectures cannot be empty. Additional gates cannot remove mandatory statuses or create jobs. Reports use `requiredArchitectures`, `vmTargets`, and `additionalRequiredChecks` inside `memberSettings`.

Architecture selection has no platform allowlist. System names use an architecture and platform separated by a hyphen, such as `riscv64-linux` or `aarch64-darwin`, with letters, digits, underscores, and hyphens. The existing `x86_64-linux` and `aarch64-linux` mappings use `ubuntu-24.04` and `ubuntu-24.04-arm`. Other systems use runner labels `["self-hosted", SYSTEM]`. Provide a matching runner with Nix and the workflow prerequisites before running hosted checks; accepting a declaration does not establish runner availability or successful builds. The same runner selection applies to candidate workers. Declared VM targets still require a separate x86_64 Linux worker.

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

The `ci` fields define the caller name, common `requiredChecks`, per-architecture `architectureChecks`, `compatibilityChecks` templates, `runners`, and conditional VM status. Templates use `{architecture}`. The checker combines these fields with member settings to produce ordinary and compatibility matrices and the complete gate list. Additional gate names do not create workflow jobs.

### Shared pins

`pins.json` contains only `schemaVersion`, `stableBranch`, and `approved`. The stable branch names the NixOS update source. The approved pair contains exact `stable` and `unstable` commits, or is null before initial approval. The root bootstrap lock does not approve shared pins. Changes to member commits require no pin-record update.

Static checks compare shared-pin lock scopes against this single pair, excluding the independently selected root lock node. Compatibility execution tests both revisions through root input overrides. Proposed pairs can be tested with a reviewed record checkout supplied through `--policy-root`; retain that snapshot and test evidence in the PR. Normal member CI captures records from `main`.

## Implemented coverage

The checker resolves version-7 lock graphs, including root-relative `follows`, across first-party lockfiles. It ignores unreachable nodes and vendor/cache directories. It identifies GitHub sources from GitHub inputs and Git URLs, checks immutable selections, flags ambiguous nixpkgs sources, and builds member dependency graphs. Policy repository flake dependencies fail.

Root `nixpkgs` must resolve to an immutable `NixOS/nixpkgs` flake. Its revision and update branch can differ from shared pins. The exemption includes references that follow the resolved root node.

Additional root inputs, distinct transitive nodes, and independently locked examples require one allowed pair across the project. In these scopes, first-party stable inputs use `nixpkgs`; unstable inputs use `nixpkgs-unstable`. An absent input need not be added. Transitive input names and lock node identifiers are unrestricted. Branch declarations identify stable/unstable selections; exact revisions can identify them when they match one allowed value uniquely.

Structural checks inspect the root development entrypoint, required files, and links to the selected release's `POLICY.md`. The checker requires a root `README.md` without inspecting its content or headings.

Caller validation requires:

- One exact release reference, matching `policy_version`, and literal job name `Policy`.
- PR event types must include `opened`, `synchronize`, and `reopened`, in any order. The `edited` event is optional.
- No branch, path, or other trigger filters.
- No caller matrix, error suppression, `if`, or `needs` on the Policy job.
- Only the declared identity, release, and settings inputs. No compatibility revision overrides or skipped required revisions.

The reusable workflow owns job matrices. The caller runs for every PR, including documentation changes.

Hosted release verification requires a published immutable release that is not a prerelease. The workflow checks out the tag, verifies `VERSION`, and captures checker, member, and current record commits. All jobs use these commits. Missing or mutable releases, unavailable GitHub responses, and version mismatches stop execution. Local structural checks do not verify remote publication.

## Integration policy agreement

`agreement PATH --project NAME` checks the integration project's exact consumed enrolled member revisions. Each must select the integration project's own supported release. Run its selected checker with explicit trusted records. The integration checkout must be clean, contain a root lock, and declare the agreement gate.

Success proves policy agreement only. The report states `behavioralIntegration: "not-run"`.

Inspection covers each committed first-party `flake.lock`, including independently locked examples. Vendor, generated-result, and cache exclusions apply. Reachable nodes define the consumed set, including transitive members, aliases, and root-relative `follows`. Unreachable nodes add no dependencies. The consumed graph takes precedence over a dependency's standalone lockfile. See [Nix's lock contract](https://nix.dev/manual/nix/2.29/command-ref/new-cli/nix3-flake#lock-files).

Trusted roster identities define membership. Inspection fetches the actual locked URL and revision. An original input cannot give an enrolled identity to a contradictory locked source. A graph without enrolled dependencies cannot establish agreement.

Supported sources are GitHub inputs and Git inputs with GitHub HTTPS or `ssh://` URLs and exact 40-character revisions. A locked `dir` selects the flake subdirectory. Inspection fetches committed workflow blobs into temporary Git storage without checking out files, running hooks, evaluating flakes, or executing member code.

Missing commits, unsupported transports, escaping subdirectories, symlinked declarations, and LFS/submodule expansion prevent a passing result. Private sources require existing Git read access. Inaccessible sources cause inspection errors.

Each consumed member needs one valid caller with matching literal identity and `policy_version`. Its selection must equal the integration selection. Current member branches and copied central versions do not affect the result. Partial dependency upgrades fail. Cycles fail, including those through nonmember nodes or `follows` to the integration root. Policy repository inputs remain forbidden.

Reports identify:

- Integration `revision`, central `records`, and committed `lockfiles`.
- Each member's trusted identity, `revision`, `source`, lock-node `references`, `policyVersion`, and outcome.
- `dependencyGraph`, `cycles`, and `dependencySetDigest`.

The digest binds lock contents and discovered sources and selections. Agreement rejects changed integration sources or records. Invalid declarations are failures; unavailable sources are inspection errors. Both outcomes retain JSON on standard output.

Use the [integration caller template](../templates/integration-caller.yml). The ordinary Policy caller declares `additional_required_checks: '["Integration / Policy agreement"]'`. The separate `Integration` caller uses `agreement.yml` at the same release. It depends on Policy and passes exactly its `project_revision` and `records_revision` outputs. Custom conditions, matrices, alternate sources, and live record references fail validation.

The agreement workflow verifies the release and captured commits, invokes the command, and retains failure reports. It runs once after Policy succeeds, independently of architecture. Its exact status is `Integration / Policy agreement`. Declaring this name adds a required gate; it does not configure GitHub protection. Other additional names remain project-owned and never become commands. Follow the [integration procedure](maintenance.md#integration-project-agreement).

## Enrollment audits

`audit WORKSPACE` inspects every trusted roster identity at `WORKSPACE/NAME`, using a clean exact commit. Missing checkouts and invalid, ambiguous, missing, or disabled callers remain failed enrolled entries. They cannot reduce coverage or create pending status.

`--fetch` clones missing public repositories from trusted roster identities. It leaves existing checkouts at their local revision. Source changes during inspection cause errors.

The audit discovers the selected tag from the unique caller and checks literal `project` and `policy_version`. It verifies publication, immutability, and non-prerelease status in the trusted policy repository. It resolves lightweight or annotated tags to exact commits and invokes that commit's Nix package with the captured records.

A local development checker cannot replace an unpublished selected release. Each checker enforces its own rules. Members select supported releases independently.

### Audit reports

Each report identifies roster `repository`, member `revision`, `policyVersion`, verified `checkerRevision`, `checkerRepository`, and central `records`:

| Field              | Meaning                    |
| ------------------ | -------------------------- |
| `records.revision` | Git commit, when available |
| `records.digest`   | Pin and roster records     |

The dispatcher verifies returned identities, revisions, digests, report types, outcomes, and member settings. Substituted or malformed reports cause inspection errors. Changed central records fail the audit, even for an initially empty roster.

Static success retains `compatibility: "not-run"`; audits do not rerun member CI. Member failures and inspection errors remain distinct; aggregate inspection errors take precedence. Errors retain JSON on standard output, including initial record failures. Artifact upload cannot mask command failure.

### GitHub enforcement

`--github` checks squash-only merging, a PR requirement, and required statuses through rulesets or branch protection. It obtains required names from the selected checker's validated `requiredChecks` report.

Failed release inspection cannot supply a trusted gate set. GitHub requests always use the roster identity. Member declarations and reports cannot redirect them.

If REST omits a merge-method flag or returns a non-boolean value, inspection queries all three settings through [GraphQL](https://docs.github.com/en/graphql/reference/repos#repository). Incomplete responses or errors leave settings unknown and produce inspection errors.

The audit also requires enabled repository Actions and an `active` caller workflow. It reads [Actions permissions](https://docs.github.com/en/rest/actions/permissions#get-github-actions-permissions-for-a-repository) and [workflow metadata](https://docs.github.com/en/rest/actions/workflows#get-a-workflow) for the committed caller filename. The returned path must match. Disabled Actions or an inactive caller fails assessment. Missing, malformed, mismatched, or inaccessible metadata leaves enforcement unknown.

Member inspection requires the [read-only audit credential](maintenance.md#audit-access). Public release lookup can work without it. Missing credentials or inaccessible responses return `error` and exit 2. HTTP 401, 403, and 404 do not prove absent protection.

Complete ruleset coverage needs no classic-protection lookup. Otherwise, `protected: false` in branch metadata proves absent protection. A protected branch requires readable classic settings for remaining gates. An ambiguous 404 leaves those settings unknown, even when incomplete rulesets exist.

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

Use [templates/policy-caller.yml](../templates/policy-caller.yml). Set the member name and both version placeholders to the same published immutable release tag. The policy repository's `main` branch requires agreed human and CI merge controls before activation.

The first job captures the checker, member, and current record commits. All later jobs use these snapshots. The caller's `name: Policy` supplies the status prefix. Run `ci PATH --project NAME` for exact names and matrices.

| Status                                                | Required for               |
| ----------------------------------------------------- | -------------------------- |
| `Policy / Verify policy version and load shared pins` | Every enrolled member      |
| `Policy / Compliance (<architecture>)`                | Each required architecture |
| `Policy / Project tests (<architecture>)`             | Each required architecture |
| `Policy / Compatibility (stable, <architecture>)`     | Each required architecture |
| `Policy / Compatibility (unstable, <architecture>)`   | Each required architecture |
| `Policy / VM tests (x86_64-linux)`                    | Members with VM targets    |

The first job verifies the release and generates matrices once on x86_64. This metadata job does not add x86_64 to the member's required architectures or publish a release.

Compliance runs structural, pin, caller, and shell checks. Members own formatting and lint enforcement. Project tests first run `host-checks` to require nonempty host checks with `--no-update-lock-file`, then run full committed-lock root checks. Compatibility runs both shared revisions.

Each category runs independently on every required architecture with `fail-fast: false`. They and the VM job depend only on the first job. A failure does not suppress other categories. Declared VM targets require the x86_64 gate even with ARM-only ordinary coverage. Without targets, VM reports `not-applicable` and its status need not be required.

Standard PR checkout tests GitHub's candidate merge commit. Each run captures its source and current records without registering that commit centrally. A merged pin update takes effect when a new member CI run captures the updated records.

Workflows use read-only permissions and do not persist checkout credentials. Automation that creates member PRs requires a separately reviewed write identity. Normal policy checks require no such credential.

The workflow invokes `check --shell` with the same requirements before and after enrollment. It reports checks and enrollment separately. Successful CI does not enroll members or approve pins.
