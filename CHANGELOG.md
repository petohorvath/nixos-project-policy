# Changelog

## 0.5.1

- Update the stable pin (`nixos-26.05`) to `0d9e9b832d03ac387417e16ce1febf73b2e631e1` and the unstable pin to `a7868a727837f3c09cee2ce0ca671c76b1589fed`.

## 0.5.0

This release reduces the policy to three guarantees for each repo: its inputs, its public outputs, and its tests ([ADR 0009](docs/adr/0009-inputs-public-outputs-and-tests.md)). It breaks compatibility with v0.4.0.

### Breaking changes

- Replace the rules in `POLICY.md` with the input, public-output, and test guarantees and a short list of conventions. Remove the style, file, `.envrc`, Markdown-link, shared-pin, and nixpkgs input-name rules.
- Reduce the reusable workflow's inputs to the optional `systems`, a JSON list in a string that defaults to `'["x86_64-linux", "aarch64-linux"]'`. Remove `project`, `policy_version`, `required_architectures`, `vm_targets`, `vm_architecture`, and `additional_required_checks`. The workflow runs the checker from its own commit and no longer checks out `main`, verifies `VERSION` against the tag, validates the caller, or inspects published releases.
- Rename the required statuses. `Policy / Compliance (<system>)` becomes `Policy / Check (<system>)`; `Policy / Project tests (<system>)` becomes `Policy / Tests (locked, <system>)`; `Policy / Compatibility (stable|unstable, <system>)` becomes `Policy / Tests (stable|unstable, <system>)`; `Policy / VM tests (<system>)` becomes `Policy / VM tests`, required only when the repo provides `vmTests`. `Policy / Verify policy version and load shared pins` is gone.
- Bundle the pins and the listed repos with the checker in `data/pins.json` and `data/repos.json`, and remove `--policy-root`. `validate` checks both files. Remove `policy/requirements.json`; job names, status names, and runners are fixed in the checker.
- Replace the `check` rules with the input rules: root `nixpkgs` is a locked `NixOS/nixpkgs` commit on any branch; extra nixpkgs inputs are reported; inputs from other repos (any repository under the GitHub owner of the listed repos, except the policy repository) reference a release tag (a commit is reported as temporary), appear at one revision, and follow root `nixpkgs`; the policy repository is never an input. Only the root `flake.lock` is read. Reports name each rule by a stable id in `rules`, `issues`, and `notices`.
- Add the public-output rules to `check`: every public output (each top-level output except `checks`, `devShells`, and `formatter`) must evaluate on the host system, empty namespaces are reported, and removing a public output after a release tag requires a minor or major bump in the topmost `CHANGELOG.md` release heading. Without a release tag, the comparison is skipped and the report recommends a first tag; on a shallow clone, the comparison fails, so the workflow's `Check` job fetches the full history. The report adds `release`.
- Always start the default development shell and evaluate the formatter in `check`, as the `shell` and `formatter` rules. The shell starts in an empty temporary directory, so its `shellHook` cannot write to the checked or calling repo. Remove the `--shell` option and the `shell` command.
- Replace `compatibility` and `host-checks` with `test PATH --nixpkgs locked|stable|unstable`. Every mode runs `nix flake check`, requires nonempty `checks.<system>`, and fails when the repo's lock or sources change. `stable` and `unstable` first verify the pin override through Nix metadata.
- Discover VM tests from `legacyPackages.<system>.vmTests` instead of declaring them. `vm PATH` builds every entry on the host system, continues after a failure, reports failing names, and returns `not-applicable` without VM tests.
- Replace `ci --project NAME` with `ci PATH [--systems JSON]`, which reports `checkMatrix`, `testMatrix`, `vmTests`, `vmJob`, and `requiredChecks`. No command takes `--project` or reads the caller workflow.
- Remove the `audit`, `agreement`, and `candidate` commands, the audit and agreement workflows, the integration caller template, and the `pin-proposal` artifact. The checker no longer calls the GitHub API. Remove source and data digests, replay data, and the `--output` evidence directories from reports.

### Added

- `survey WORKSPACE` runs `check` on every Git repo directly under a workspace directory, marks listed repos, and prints a JSON report with a pass or fail table per repo and rule. The table also goes to standard error.
- Pin bumps are automated patch releases. The weekly or manually dispatched Propose pin bump workflow opens an `automation/pin-bump` PR that updates `data/pins.json`, the transitional `policy/pins.json`, `VERSION`, and the changelog. The Pin-bump tests workflow runs `test --nixpkgs stable`, `test --nixpkgs unstable`, and `vm` from the PR head against the `main` branch of every listed repo. Merging the PR tags the next patch release and moves the minor-series tag.

### Migration from v0.4.0

1. Replace the policy caller with [templates/policy-caller.yml](templates/policy-caller.yml): `uses: petohorvath/nixos-project-policy/.github/workflows/check.yml@v0.5`. Delete the removed inputs. Set `systems` only when the repo needs systems other than both Linux systems; move additional required checks into the repo's own workflows.
2. Reference `@v0.5`, not an exact tag. The `v0.5` tag moves to every patch release, so pin bumps and fixes arrive without a caller change.
3. Move VM tests to `legacyPackages.<system>.vmTests`, outside `checks`.
4. Update the required statuses to the new names listed in `POLICY.md`: `Policy / Check (<system>)`, `Policy / Tests (locked|stable|unstable, <system>)`, and `Policy / VM tests` when the repo has VM tests. The `Plan` job writes the list to its step summary.
5. Replace local checks that passed `--policy-root` with `nix run github:petohorvath/nixos-project-policy/v0.5 -- check .`, and remove policy procedures copied into the repo's documentation.
6. Fix what the new rules report: move inputs from other repos to release tags and make them follow root `nixpkgs`, state the reason for a second nixpkgs input in `flake.nix`, and tag a first release to turn on the public-output removal comparison.

`policy/pins.json` and `policy/members.json` stay on `main` unchanged until nixos-registry and nixos-cross-config call `@v0.5`, so v0.4.0 callers keep working.

## 0.4.0

- Allow members to select a Linux VM execution system with `vm_architecture`. Derive its worker and required status together; preserve x86_64 Linux as the default. Other VM systems require matching self-hosted runners with KVM. Members opting in must update their required VM status after selecting a release that includes this input.

- Separate daily member audits and weekly pin preparation into independently dispatched workflows. Consolidate CI host tests and cancel superseded PR CI runs.

- Require v0.4.0 or later for all member selections. Remove pre-v0.4.0 checker adapters, compatibility records, readiness and cleanup options, and old-release tests and guides. Reduce `policy/` to enrollment, pins, and release requirements. Store the stable update branch with pins and policy identity with release requirements; derive default systems from runner mappings.

- Remove the policy-owned formatting/lint job and `lint` command. Members own formatting and lint enforcement.
- Accept any nonempty selection of Nix systems for required architectures. Keep existing Linux runner mappings and use matching self-hosted runners for other systems in member CI.
- Remove PR-title validation and make the caller's `edited` PR event optional.
- Smoke-test development-shell startup and command execution without requiring a fixed tool list.
- Require only the presence of `README.md`, without prescribing its content or headings.
- Require nonempty host checks under the committed lock before project tests in member CI.
- Require an explicit host `devShells.<system>.default` in shell probes; a default package or non-default shell cannot satisfy the development-shell requirement.
- Read policy selection and member settings from one literal reusable-workflow caller, with enrollment recorded separately.
- Require `required_architectures` as a JSON list encoded as a string; default `vm_targets` and `additional_required_checks` to empty lists. Reject ambiguous callers, dynamic inputs, malformed settings, and mismatched release references.
- Use the same validated declarations for local checking, hosted planning, compatibility execution, and VM targets. Preserve separate compliance, committed-lock, both compatibility channels, and applicable x86_64 VM gates.
- Capture one member commit with checker and record commits for all hosted jobs; validate hosted input parity with that checkout.
- Allow full pre-enrollment checks and report enrollment separately from check results.

- Keep active enrollment in an identity-only roster. Audit exact member revisions with their discovered published immutable checkers and validated report identities; retain missing enforcement and inaccessible metadata as visible failures.

- Remove scheduled release retirement handling and its tests. Remove member batch tracking, rollout allowances, candidate coordinators, and the pin-PR gate. Pin records hold only the approved pair and stable update branch; member test evidence stays in PRs and CI artifacts.
- Check integration policy agreement at exact committed member dependency revisions, including reachable transitive members, follows, aliases, and independent lock scopes. Preserve behavioral integration tests and reject mixed selections, cycles, missing declarations, and unavailable sources.
- Provide the separate `Integration / Policy agreement` gate and caller template with primary Policy source/record snapshot outputs. Keep member upgrades independent of the integration project's locked set.
- Match central PR runs and artifacts to the proposal commit. Verify the trusted workflow revision separately through `GITHUB_WORKFLOW_SHA`.

### Migration from v0.3.0 and earlier

This release breaks compatibility with earlier policy contracts. Releases below v0.4.0 are unsupported; current records and tools no longer provide adapters for them.

- After publication, replace the member caller with [the policy caller template](https://github.com/petohorvath/nixos-project-policy/blob/v0.4.0/templates/policy-caller.yml). Set both the reusable workflow reference and `policy_version` to `v0.4.0`, retain the literal project identity, and update policy documentation links to the same release.
- Move required architectures, VM targets, and additional required checks from central records into the caller's `required_architectures`, `vm_targets`, and `additional_required_checks` inputs. Encode each list as a literal JSON string. Preserve existing coverage; optional lists default to empty. `vm_architecture` defaults to `x86_64-linux`; other Linux VM systems require matching self-hosted runners with KVM.
- Run the selected checker's `ci` command with an explicit trusted `--policy-root` checkout and use its generated status names when updating merge gates. Retain member-owned formatting and lint checks after removing the policy-owned formatting/lint gate and `lint` command.
- Provide an explicit default development shell and nonempty host checks under the committed lock. Validate committed-lock checks, both shared-pin compatibility channels on every required architecture, and applicable VM and additional gates before completing the upgrade.
- Use current identity-only enrollment and pin records. Replace retired batch, readiness, cleanup, and old-release commands with the procedures in [maintenance](https://github.com/petohorvath/nixos-project-policy/blob/v0.4.0/docs/maintenance.md); retain pin approval evidence in reviewed PRs and CI artifacts.
- Integration projects must upgrade their policy selection and complete consumed member dependency set together. Use [the integration caller template](https://github.com/petohorvath/nixos-project-policy/blob/v0.4.0/templates/integration-caller.yml), preserve its source and record snapshot bindings, and require `Integration / Policy agreement` alongside behavioral integration tests.

Member upgrades, enrollment, and live merge-setting changes require separate reviewed decisions. Publishing this release does not perform those migrations.

## Historical releases (unsupported)

The entries below describe immutable release history. Current tools and records support only v0.4.0 and later.

## 0.3.0

- Derive mandatory GitHub merge gates from the selected policy release, `requiredArchitectures`, and `vmTargets`, with optional `additionalRequiredChecks` for project-specific gates.
- Include the complete required set in `ci` and `check` reports and use the selected checker's report when auditing members on another release with derived checks.
- Retain legacy check lists for members on releases before v0.3.0. Require complete compatibility lists for all adopted members while any older checker still consumes current records, and reject drift from the generated set for this release.

## 0.2.0

- Allow an independent immutable root `nixpkgs` selection, including an unstable update source, while retaining shared-pin checks for other nixpkgs nodes and independently locked examples.
- Run policy-owned full root compatibility checks against the shared stable and unstable pins on each member's required architectures, alongside ordinary committed-lock, shell/tool, lint, and applicable VM checks.
- Add `compatibility` with an explicit trusted record snapshot, resolved-input verification, nonempty host checks, lock preservation checks, and metadata/result evidence outside member sources.
- Distinguish static validation, approved-pin execution, and candidate execution. Candidates remain bound to exact clean project commits; active rollouts target the central approved pair.
- Require verified compatibility status registrations for migrated members and retain audits through older members' selected policy releases.
- Run compliance, formatting/lint, and project tests as independent jobs on each member's required architectures, with separate results and reruns.
- Use the caller name `Policy` and name the initial job `Verify policy version and load shared pins`.
- Define mandatory status names in the policy release. Reject adoption records that omit a required gate, including the VM gate when VM targets are declared, while allowing additional project checks.
- Reject caller name changes and caller matrices that would change or duplicate the required status names.
- Let each member select a nonempty subset of supported CI architectures through `requiredArchitectures`. Generate both workflow matrices and mandatory status names together with `ci --project NAME`, using job names and runner mappings from the selected release.
- Run policy-version and shared-pin preparation once, independently of the member's selected architectures. Applicable VM suites continue to use their separate x86_64 gate.

## 0.1.1

- Read merge settings through GraphQL when GitHub omits them from a read-only REST response, so compliant repositories pass the audit.
- Treat incomplete or inaccessible merge settings as inspection errors and continue rejecting verified merge-policy violations.
- Enroll `nixos-cross-config` and complete its initial approved pin rollout.

## 0.1.0

- Record the shared project policy and accepted design decisions.
- Provide root development tools, formatting, checker tests, and CI definitions.
- Inspect lock graphs, pin batches, documentation structure, CI callers, and adoption drift.
- Prepare weekly candidate artifacts without approving pins or migrating members.
- Prepare `nixos-cross-config` as the first selected member migration, with a pinned policy reference and an unapproved candidate batch.
- Require conventional `nixpkgs` and `nixpkgs-unstable` input names, a visible root `systems` binding, and explicit flake outputs.
- Check first-party nixpkgs input names, including resolved `follows` and independently locked examples.
- Limit language tooling to applicable sources and clarify that benchmarks are optional.
- Keep changelogs and current design documentation while removing duplicate implementation, review, and validation histories.
- Select shared rules and CI through exact immutable policy release tags while reading shared pins and enrollment from current central records.
- Keep required tools, supported development systems, and README requirements with the selected policy release.
- Report checker versions and central record commits, and require an explicit current record checkout for member checks, audits, and VM commands.
- Require local member checks to use their registered release and retain that selection in family audits.
