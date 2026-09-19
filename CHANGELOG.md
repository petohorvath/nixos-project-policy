# Changelog

## 0.2.0

- Allow an independent immutable root `nixpkgs` selection, including an unstable update source, while retaining shared-pin checks for other nixpkgs nodes and independently locked examples.
- Run policy-owned full root compatibility checks against the shared stable and unstable pins on both Linux architectures, alongside ordinary committed-lock, shell/tool, lint, and applicable VM checks.
- Add `compatibility` with an explicit trusted record snapshot, resolved-input verification, nonempty host checks, lock preservation checks, and metadata/result evidence outside member sources.
- Distinguish static validation, approved-pin execution, and candidate execution. Candidates remain bound to exact clean project commits; active rollouts target the central approved pair.
- Require verified compatibility status registrations for migrated members and retain audits through older members' selected policy releases.

### Migration

This minor release changes rules, checker reports, and required workflow jobs. Publish v0.2.0 before activating callers. Select member migrations separately; update policy links, caller references and `policy_version`, and central `policyVersion` together. Keep a committed root lock and meaningful root host checks. The root selected dependency and its tools need not use shared stable pins, and no second input or compatibility flake is required solely for compatibility coverage.

Run both channels on both native architectures and complete a member trial. Inspect actual statuses on a reviewable PR before registering their exact names or activating merge gates. Static `check` reports now explicitly say compatibility was not run; use the new runner's evidence for compatibility. Shared-pin updates may need new runs without default-lock changes. Project and pin record schemas are unchanged, and members on v0.1.x retain their existing contracts. See the [migration procedure](docs/maintenance.md#migration-to-v020) and [runner reference](docs/checker.md#compatibility-execution-and-evidence).

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
