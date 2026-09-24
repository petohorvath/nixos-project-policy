# Changelog

## 0.4.0

- Require v0.4.0 or later for all member selections. Remove pre-v0.4.0 checker adapters, compatibility records, readiness and cleanup options, and old-release tests and guides. Central configuration now lives in `policy/config.json`; member identities come only from `policy/members.json`.

- Remove the policy-owned formatting/lint job and `lint` command. Members own formatting and lint enforcement.
- Accept any nonempty selection of Nix systems for required architectures. Keep existing Linux runner mappings and use matching self-hosted runners for other systems in member CI and candidate batches.
- Remove PR-title validation and make the caller's `edited` PR event optional.
- Smoke-test development-shell startup and command execution without requiring a fixed tool list.
- Require only the presence of `README.md`, without prescribing its content or headings.
- Require nonempty host checks under the committed lock before project tests in member CI and candidate execution, including replay validation.
- Require an explicit host `devShells.<system>.default` in shell probes; a default package or non-default shell cannot satisfy the development-shell requirement.
- Read policy selection and member settings from one literal reusable-workflow caller, with enrollment recorded separately.
- Require `required_architectures` as a JSON list encoded as a string; default `vm_targets` and `additional_required_checks` to empty lists. Reject ambiguous callers, dynamic inputs, malformed settings, and mismatched release references.
- Use the same validated declarations for local checking, hosted planning, compatibility execution, and VM targets. Preserve separate compliance, committed-lock, both compatibility channels, and applicable x86_64 VM gates.
- Capture one member commit with checker and record commits for all hosted jobs; validate hosted input parity with that checkout.
- Allow full pre-enrollment checks and report enrollment separately from check results.

- Keep active enrollment in an identity-only roster. Audit exact member revisions with their discovered published immutable checkers and validated report identities; retain missing enforcement and inaccessible metadata as visible failures.

- Add explicit retirement decisions with migration periods, shared enforcement in local commands, CI, and audits, and stable support assessments for evidence.
- Add single-member candidate planning, native execution, and replayable aggregation against unmerged proposals. Keep baseline authority separate from proposed approval fields, use each selected immutable checker, and retain complete gate evidence without granting batch approval.
- Check integration policy agreement at exact committed member dependency revisions, including reachable transitive members, follows, aliases, and independent lock scopes. Preserve behavioral integration tests and reject mixed selections, cycles, missing declarations, and unavailable sources.
- Provide the separate `Integration / Policy agreement` gate and caller template with primary Policy source/record snapshot outputs. Keep member upgrades independent of the integration project's locked set.
- Extend candidate coordination to the complete trusted roster, with exact locked integration evidence, required native worker accounting, strict attempt and execution bindings, and retained replay artifacts. Add a manual whole-batch workflow and distinguish complete candidate eligibility from human approval.
- Validate routine root-only shared-pin updates in one central PR. Attach `Pin batch / Complete candidate` to the exact proposal head after trusted native worker and artifact verification; renew evidence when its authority or subjects change. Keep candidate execution separate from human-reviewed approval and separately activate the live merge gate. See the [pin routine](docs/maintenance.md#pin-candidates-and-approval).
- Match central PR runs and artifacts to the proposal commit. Verify the trusted workflow revision separately through `GITHUB_WORKFLOW_SHA`.

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
