# Changelog

## 0.4.0 (prepared)

- Read policy selection and member settings from one literal reusable-workflow caller, independently of central legacy selections or adoption state.
- Require `required_architectures` as a JSON list encoded as a string; default `vm_targets` and `additional_required_checks` to empty lists. Reject ambiguous callers, dynamic inputs, malformed settings, and mismatched release references.
- Use the same validated declarations for local checking, hosted planning, compatibility execution, and VM targets. Preserve separate compliance, formatting/lint, committed-lock, both compatibility channels, and applicable x86_64 VM gates.
- Capture one member commit with checker and record commits for all hosted jobs; validate hosted input parity with that checkout.
- Allow full pre-enrollment checks and report enrollment separately from check results. Preserve older immutable checker contracts and complete legacy records, including global check lists and historical batch identities.

- Keep active enrollment in an identity-only roster, independently of complete legacy records. Audit exact member revisions with their discovered published immutable checkers and validated report identities; retain missing enforcement and inaccessible metadata as visible failures.

- Add explicit retirement decisions with migration periods, shared enforcement in local commands, CI, and audits, and stable support assessments for evidence. Preserve support when newer releases publish.
- Guard legacy cleanup against trusted prior records, supported immutable patch releases, and exact member migration evidence; keep all real retirement decisions empty.
- Add single-member candidate planning, native execution, and replayable aggregation against unmerged proposals. Keep baseline authority separate from proposed approval fields, use each selected immutable checker, preserve legacy lock requirements, and retain complete gate evidence without granting batch approval.

### Migration

After separately authorized publication, update member policy links, the caller reference, and `policy_version` in one reviewed member PR. Put the intended architectures, VM targets, and additional gates into caller inputs. Use `ci PATH --project NAME` to review the generated names; verify actual statuses and merge settings, and justify any coverage reduction explicitly. Keep central legacy copies unchanged. This prepared release does not publish a tag, migrate members, change live settings, approve pins, or retire older releases. See [migration guidance](docs/maintenance.md#migration-to-v040-prepared).

## 0.3.0

- Derive mandatory GitHub merge gates from the selected policy release, `requiredArchitectures`, and `vmTargets`, with optional `additionalRequiredChecks` for project-specific gates.
- Include the complete required set in `ci` and `check` reports and use the selected checker's report when auditing members on another release with derived checks.
- Retain legacy check lists for members on releases before v0.3.0. Require complete compatibility lists for all adopted members while any older checker still consumes current records, and reject drift from the generated set for this release.

### Migration

Publish v0.3.0 before activating member callers and select each migration separately. Update policy references, the reusable-workflow reference and `policy_version`, and the central `policyVersion` together. Move project-specific statuses into `additionalRequiredChecks`; the selected release supplies mandatory names. Existing v0.2.0 workflow status names, architectures, pins, and VM targets need no change solely for this upgrade.

Project records retain schema version 2. Members on older releases keep their full `requiredChecks` lists. While any member still selects a release before v0.3.0, every adopted member must retain a full list for those older checkers. Generate each migrated member's compatibility list with `ci --project NAME`; it must match the complete required set. Once no older releases remain selected, omit the duplicated list from migrated records. Verify observed statuses and GitHub merge gates during adoption and retain evidence on the PR. See the [migration procedure](docs/maintenance.md#migration-to-v030).

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

### Migration

This minor release changes rules, checker reports, and required workflow jobs. Publish v0.2.0 before activating callers. Select member migrations separately; update policy links, caller references and `policy_version`, and central `policyVersion` together. Keep a committed root lock and meaningful root host checks. The root selected dependency and its tools need not use shared stable pins, and no second input or compatibility flake is required solely for compatibility coverage.

Run both channels on every required native architecture and complete a member trial. Inspect actual statuses on a reviewable PR before registering their exact names or activating merge gates. Static `check` reports now explicitly say compatibility was not run; use the new runner's evidence for compatibility. Shared-pin updates may need new runs without default-lock changes. Project and pin record schemas are unchanged, and members on v0.1.x retain their existing contracts. See the [migration procedure](docs/maintenance.md#migration-to-v020) and [runner reference](docs/checker.md#compatibility-execution-and-evidence).

This release changes the required CI status names. After publication and explicit selection of a member migration, use the updated caller template with `name: Policy`, update policy references and `policyVersion` to `v0.2.0`, and record the required architectures. Run `ci --project NAME` with trusted current records to obtain the mandatory status names, then configure `requiredChecks` and GitHub merge gates with that set as described in the [checker reference](docs/checker.md#ci-integration). Verify the new statuses before replacing the old gates. Shared pins and member lockfiles need no change solely for this upgrade.

Current project records retain schema version 2 and the full `requiredChecks` list, adding `requiredArchitectures` for the new release. Existing records declare both Linux architectures; members still selecting older releases keep their existing requirements and may omit the new field. Policy requirements retain schema version 1 and add release-owned `ci` settings. This preparation does not migrate any member or publish the release.

## 0.1.1

- Read merge settings through GraphQL when GitHub omits them from a read-only REST response, so compliant repositories pass the audit.
- Treat incomplete or inaccessible merge settings as inspection errors and continue rejecting verified merge-policy violations.
- Enroll `nixos-cross-config` and complete its initial approved pin rollout.

### Migration

Members retain their selected policy releases and current pins. The central audit continues to use each member's selected checker. Audit credentials still need only Administration, Contents, and Metadata read access.

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

### Migration

Project records use schema version 2 and replace `policyRevision` with `policyVersion`, an exact release tag such as `v0.1.0`. Move `systems`, `requiredTools`, and `readmeSections` from `policy/projects.json` to the release's `policy/requirements.json`. Pin records retain schema version 1.

After the policy release is published, update member documentation links and workflow references to its tag, and replace the workflow input `policy_revision` with `policy_version`. Local `check`, `audit`, and `vm` commands require `--policy-root PATH` before the command to select current central records. Pin update batches do not change the policy version.
