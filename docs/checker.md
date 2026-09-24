# Checker reference

This reference describes the prepared v0.4.0 contract. Publication and member migrations require separate decisions.

Run the checker from its selected policy release or packaged `nixos-project-policy` executable. Use `--version` to identify it.

`--policy-root PATH` selects a trusted record checkout. It is required for member checks, audits, CI planning, compatibility, agreement, and VM execution. These commands do not default to a release's bundled records. Candidate coordination also requires explicit trusted records. Other commands default to bundled records.

## Commands

Use this prefix with the commands below:

```bash
nix run .# -- --policy-root . COMMAND
```

| Command                                                         | Behavior                                                                                                         |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `validate`                                                      | Check record structure and report whether pins are approved.                                                     |
| `validate --previous-policy-root OLD --workspace WORKSPACE`     | Review legacy removals against prior records, release retirement, and exact migrated checkouts.                  |
| `ci PATH --project NAME`                                        | Report CI matrices and required status names. Do not execute checks.                                             |
| `check PATH --project NAME`                                     | Inspect a member checkout against its selected policy and approved pins.                                         |
| `check PATH --project NAME --shell`                             | Also smoke-test the member shell and evaluate its root formatter.                                                |
| `check PATH --project NAME --batch ID`                          | Check a registered candidate at its exact clean member commit.                                                   |
| `compatibility PATH --project NAME --channel stable`            | Verify the stable override and run full root host checks. Use `unstable` for the other revision.                 |
| `compatibility PATH --project NAME --channel stable --batch ID` | Run compatibility checks for an exact registered candidate.                                                      |
| `lint PATH`                                                     | Run statix, deadnix, and root formatting in a temporary source copy.                                             |
| `host-checks PATH`                                              | Require nonempty `checks.<host-system>` under the committed lock, without building checks.                       |
| `audit WORKSPACE`                                               | Inspect each enrolled checkout with its selected published checker; report failures and dependency cycles.       |
| `audit WORKSPACE --fetch --github`                              | Also clone missing public checkouts and inspect GitHub enforcement. Leave existing checkouts unchanged.          |
| `agreement PATH --project NAME`                                 | Compare an integration project's policy selection with its exact locked member revisions.                        |
| `vm PATH --project NAME`                                        | Execute declared VM targets on a suitable builder. Return `not-applicable` if none exist.                        |
| `candidate --stable COMMIT --unstable COMMIT`                   | Emit an unapproved pair. Do not write locks or register a batch.                                                 |
| `pin-batch plan\|execute\|aggregate`                            | [Plan, execute, and replay](#unmerged-candidate-coordination) a candidate for one member or the enrolled roster. |
| `pin-pr capture\|collect\|finish\|invalidate`                   | [Check central PR evidence](#central-pin-prs) and report eligibility on the proposal head.                       |

`check --readiness` remains accepted for older callers. It does not weaken v0.4.0 checks before enrollment.

For a shell-only probe, run `nix run .# -- shell PATH`. This first evaluates `devShells.<host-system>.default.drvPath`, enters the development shell with the inherited environment cleared, and executes `bash -c ':'`. It also evaluates the root formatter without asserting compliance. A default package or non-default shell cannot satisfy the development-shell requirement. Policy CI uses it as a host smoke test. The probe checks startup and command execution, not a fixed tool list or project-specific development tasks.

After publication, use a selected release with current records:

```bash
nix run github:petohorvath/nixos-project-policy/v0.4.0 -- \
  --policy-root ../nixos-project-policy-records \
  check ../member --project member --shell
```

Update the trusted records checkout from `main` before a current check. Use captured snapshots for replay. Local commands neither fetch records nor prove that a checkout is current.

`check`, `ci`, `compatibility`, and `vm` require one member caller selecting the executing checker release. The caller's `policy_version` must match its immutable workflow reference. Enrollment and stale legacy selections do not change this requirement.

### Results

Record and member commands print JSON. Reports include `checkerVersion`, `policyRecordsDigest`, and `policyRecordsRevision` when Git metadata is available. Member reports also identify `policyVersion`, validated `memberSettings`, and the member commit when available. Reusable CI logs the captured checker and record commits.

| Exit | Meaning                                                                                                     |
| ---- | ----------------------------------------------------------------------------------------------------------- |
| 0    | The requested operation succeeded. Planning and candidate success do not establish approved-pin compliance. |
| 1    | Enforced checks failed.                                                                                     |
| 2    | The request, records, or inspection could not be processed.                                                 |

A normal `check` can return `pass` before enrollment. Its separate `enrollment` field is `enrolled` or `not-enrolled`. Static checks report `compatibility: "not-run"`. Candidate static checks return `candidate-ready`; successful candidate execution returns `candidate-pass`. No result changes enrollment or approves pins. Older released checkers retain their original readiness and adoption contracts.

`ci` returns `planned`, `matrix`, `compatibilityMatrix`, `vmTargets`, and the complete `requiredChecks`. Omit `PATH` only when the current directory is the member. Hosted `--inputs-json JSON` must normalize to the checked-out caller's inputs; it cannot replace member settings.

An audit succeeds only if every enrolled assessment passes or the roster is empty. Candidate and PR reports use the evidence formats below.

### Lock handling

Default checks and shell probes use `--no-update-lock-file` alone to reject required lock updates. Do not add `--no-write-lock-file`. In Nix 2.34.6, disabling writes also bypasses update rejection and permits an in-memory replacement lock. See the [locking implementation](https://github.com/NixOS/nix/blob/2.34.6/src/libflake/flake.cc#L749-L825).

Compatibility checks deliberately use an overridden graph. `--override-input` implies `--no-write-lock-file`. Adding `--no-update-lock-file` does not make an override check test the committed selection.

## Unmerged candidate coordination

`pin-batch plan`, `execute`, and `aggregate` check enrolled members against an unmerged proposal. Select one member with `--project` or the full roster with `--all`.

| Input                                 | Authority                                                                      |
| ------------------------------------- | ------------------------------------------------------------------------------ |
| `--policy-root BASELINE`              | Trusted enrollment, identities, legacy settings, support, and current approval |
| Exact clean member commit             | Member declarations                                                            |
| `--proposal-root PROPOSAL --batch ID` | Candidate pair and registered member commits                                   |
| Selected published immutable release  | Member requirements and checker code                                           |

The coordinator verifies each registered commit in its trusted repository. It resolves the selected release and executes its checker at the exact release commit.

The proposal can include future approval and a routine batch's future `complete` state. Execution copies baseline records to a new evidence directory. It retains current approval and changes the selected batch to `candidate` in that copy. Approved and historical baseline batches cannot be reused.

Proposals cannot change enrollment, support, legacy settings, policy identity, or unrelated rollout records. Proposed checker code and requirements never execute. All four proposed record files must be regular committed Git blobs that match working-tree bytes. The loader rejects symlinks and parses only verified blobs. Reports distinguish baseline, proposal, and execution-record identities.

Run trusted coordinator code against the registered member commit. Use new output directories outside all input checkouts:

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch plan ./member \
  --proposal-root ./proposal --project MEMBER --batch BATCH \
  --attempt review-1 --output ./plan
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch execute ./member \
  --proposal-root ./proposal --plan ./plan/plan.json \
  --system x86_64-linux --output ./results/x86_64-linux
```

Repeat execution on every native system in the plan. Keep the same member, checker, baseline, proposal, and plan. ARM jobs require an ARM host. VM targets require an x86_64 KVM host, even with ARM-only ordinary coverage.

Each worker independently runs compliance/shell, lint, committed-lock checks, and both candidate compatibility revisions. A failed category does not suppress other runnable categories. Additional gates use GitHub check runs or commit statuses at the exact member commit. Gate names neither create jobs nor become commands. Every required gate must complete successfully.

For v0.2.0 and later, compatibility verifies root overrides, nonempty native checks, and lock preservation. The v0.1.x adapter retains whole-project committed-pin requirements. Both candidate revisions must occur in its effective committed root graph, with nonempty native root checks. Missing coverage and pin-bound locks require actual member changes.

Collect complete native evidence directories, then aggregate against the original plan:

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch aggregate ./member \
  --proposal-root ./proposal --plan ./plan/plan.json \
  --results ./results --output ./summary
```

Planning and replay recheck trusted inputs and release support. Changed inputs or an effective retirement invalidate the plan. Aggregation requires all planned jobs. It rejects missing, substituted, conflicting, failed, or unreadable evidence. Reports and logs remain available after failure. Successful Nix builds can use cached results.

Single-member success returns `candidate-pass` with `eligible: false` for the enrolled batch. Digests identify content and freshness; they do not authenticate execution. Local aggregation requires trusted worker evidence.

The manual workflow uses its own run's artifacts, attempt-specific names, captured commits, and read-only repository permissions. Run it from trusted `main`. Its single-member completion status cannot approve a whole-batch PR. Local fixtures do not prove hosted execution or live merge protection.

## Complete enrolled batches

Whole-batch planning requires a registration for every baseline roster identity. A proposal cannot reduce membership or replace selected-release requirements. Member checkouts live at `WORKSPACE/PROJECT`. `--fetch` clones missing checkouts from trusted repositories at registered commits. Existing checkouts must already match and be clean.

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch plan ./members --all --fetch \
  --proposal-root ./proposal --batch BATCH --attempt review-1 \
  --output ./batch-plan
```

A failed member capture remains visible; other members can retain their plans and artifacts. The parent plan records:

- `scope: "whole-batch"`, full `roster`, and registrations.
- Baseline, proposal, candidate pair, attempt, and coordinator identities.
- Each selected-release member plan.
- A matrix with `project`, `repository`, `revision`, `system`, `runner`, stable `worker` ID, and expected native `job` name.

Execute every matrix row on its native architecture. Supply the expected attempt independently of the plan:

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch execute ./members/MEMBER --all \
  --proposal-root ./proposal --plan ./batch-plan/plan.json \
  --project MEMBER --system x86_64-linux --attempt review-1 \
  --output ./batch-results/MEMBER--x86_64-linux
```

Workers recheck their inputs and run the same checks as single-member execution. Reports bind `batchPlanDigest`, child `planDigest`, attempt, worker, system, source, checker, settings, and execution records. Older members retain their committed-pin requirements.

`Integration / Policy agreement` invokes the integration project's selected immutable checker. Replay checks its report, actual locked members, and `dependencySetDigest`. Committed-lock and candidate root checks still run. Separate member-head results and static agreement cannot replace integration tests.

Retain each complete worker output directory as one artifact. Its root `result.json` describes that worker; nested reports, metadata, and logs remain attached.

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch aggregate ./members --all \
  --proposal-root ./proposal --plan ./batch-plan/plan.json \
  --results ./batch-results --attempt review-1 --output ./batch-summary
```

Aggregation rechecks all members and accounts for every worker and required child job. It retains inputs and member outcomes after failure. Missing, duplicate, conflicting, skipped, cancelled, failed, malformed, or foreign evidence prevents eligibility.

Changes to sources, settings, checkers, enrollment, support, dependencies, records, or pins require a new plan and attempt. Aggregation does not combine attempts. Newly executed commands can still use the Nix build cache. Support is checked when consuming evidence and at completion, including retirements that take effect without a record edit.

Only complete success returns `status: "candidate-pass"` and `eligible: true`. Plans, workers, and single-member summaries remain ineligible. Every summary retains `approval: "not-granted"`.

The manual `Complete candidate batch` workflow runs from trusted `main`. It retains plan, worker, and summary artifacts for each attempt. It supplies the expected attempt separately and rejects failed overall execution through `--execution-status`.

External integrations can pass `--worker-outcomes FILE` after verifying worker outcomes independently. The file uses schema version 1, parent `planDigest`, expected `attempt`, and a `workers` list. Every matrix row needs one `{ "id": WORKER_ID, "status": "completed", "conclusion": "success" }` entry. Missing or unsuccessful entries fail. The caller must establish provenance; the file and its digest cannot authenticate a GitHub run.

Manual workflow results do not establish a required check on the central PR head.

## Central pin PRs

`Central pin proposal` uses trusted `pull_request_target` orchestration and the complete-batch commands. It creates `Pin batch / Complete candidate` on the reviewed proposal's `head_sha`. See GitHub's [required-check rules](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

The workflow executes no proposed actions, Python, shell, or Nix code. The trusted coordinator and selected published releases supply commands and runners.

All `pin-pr` operations require `--policy-root BASELINE` and a new `--output DIR` outside inputs. `capture`, `collect`, and `finish` run in GitHub Actions and also require:

```text
--proposal-root PROPOSAL --number PR --head COMMIT --run RUN_ID --attempt RUN_ATTEMPT
```

`finish` also requires `--check CHECK_RUN_ID`.

GitHub's [`GITHUB_WORKFLOW_SHA`](https://docs.github.com/en/actions/reference/workflows-and-actions/variables) must match the trusted base. GitHub metadata must independently confirm the repository, event, workflow path, proposal commit, and attempt. An older workflow rerun cannot attest a newer baseline.

| Operation    | Behavior                                                                                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `capture`    | Classify committed records and create a pending check on the proposal head. Candidate approval requires whole-batch evidence.                                             |
| `collect`    | Check jobs and artifact metadata for the exact attempt. Download verified plan/worker archives and emit `worker-outcomes.json`. Retain available evidence after failures. |
| `finish`     | Recheck jobs, artifacts, complete summary, source freshness, and release support. Update only the check bound to that head and attempt.                                   |
| `invalidate` | Mark stale successful or pending checks failed after baseline changes or retirements. Do not rerun checks or modify PRs.                                                  |

These operations use `GH_TOKEN`, falling back to `GITHUB_TOKEN`. Collection requires policy-repository Actions and PR metadata read access. Capture, reporting, and invalidation write Checks in that repository; run them only when reporting is intended.

Native workers use read-only permissions and disable persisted checkout credentials. Member subprocesses receive no inspection tokens, Actions runtime variables, or workflow command-file paths.

Plans and artifacts identify the run, attempt, worker, and complete subject. [Attempt job metadata](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt) supplies independent names, native labels, and conclusions. [Artifact metadata](https://docs.github.com/en/rest/actions/artifacts#list-workflow-run-artifacts) is scoped to the run. Collection requires attempt-specific names, matching run IDs and proposal commits, available artifacts, and matching download digests. Unsafe paths and symlinks fail.

Artifact metadata does not identify the producing job directly. Verification also depends on expected trusted workflow jobs and their outcomes. Local digests authenticate no execution.

Artifacts use these names:

- `pin-plan-RUN-ATTEMPT`
- `pin-result-RUN-ATTEMPT-WORKER`
- `pin-summary-RUN-ATTEMPT`
- `pin-provenance-RUN-ATTEMPT`

For replay, retain complete native directories, raw reports, logs, and the summary's captured plan. Pass the independently captured attempt and, for hosted replay, `--worker-outcomes FILE`. Replay neither grants approval nor refreshes the live PR check.

PR reports identify `scope: "pin-pr"`, head, baseline/proposal identities, run/attempt, plan digest, members, and issues. Complete current candidate evidence returns `eligible: true`; every report retains `approval: "not-granted"`. Unrelated and state-only PRs use ordinary review and return `eligible: false` on success. Missing, unsuccessful, expired, substituted, or unreadable evidence prevents a successful candidate gate. A newer validation check supersedes older attempts.

See [renewing PR evidence](maintenance.md#renewing-pr-evidence) for activation and merge-time requirements. Local workflow tests do not verify live GitHub protection.

## Compatibility execution and evidence

`compatibility` uses the same runner locally and in CI. It requires:

- A member caller selecting the executing checker release.
- A Git checkout whose root lock matches its committed copy.
- A supported native Linux host.
- An approved pair or an exact registered candidate.

The runner resolves the root input through `LockGraph`, including renamed nodes and `follows`. It verifies the repository and revision from `nix flake metadata --json`. A missing input, ignored override, wrong source, or wrong revision fails before execution.

The runner requires nonempty `checks.<host-system>` under the same override. It then executes:

```bash
nix flake check PATH --print-build-logs \
  --override-input nixpkgs github:NixOS/nixpkgs/REVISION
```

It does not use `--no-build`. Nix can satisfy builds from its cache; success does not prove every test process ran again. See the Nix [check options](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake-check.html) and [metadata output](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake-metadata.html).

| Selection                                       | Test target and result                                                                |
| ----------------------------------------------- | ------------------------------------------------------------------------------------- |
| Ordinary run                                    | Exact `approved` pair, including active rollouts; `pass` with `pinStatus: "approved"` |
| Candidate registered for the exact clean commit | Exact candidate pair; `candidate-pass` with `pinStatus: "candidate"`                  |
| Multiple matching candidates                    | Require explicit selection with `--batch`                                             |
| No approval or matching candidate               | Fail                                                                                  |

The checker automatically selects a unique matching candidate. `--batch` explicitly selects a registered candidate. Completed and withdrawn batches add no test targets. Results do not change enrollment or pin approval.

Reports include:

- Source: `revision`, `sourceDirty`, and `sourceDigest`.
- Checker: `checkerVersion`, `checkerRevision`, and `checkerSourceDigest`.
- Records: `policyRecordsRevision` and `policyRecordsDigest`.
- Execution: `channel`, `system`, `expectedRevision`, `resolvedRevision`, `candidateBatch`, host check names, command arguments, and exit outcomes.

Packaged checkers embed their clean flake revision. Dirty or unpacked sources can lack a checker commit; release replay requires a clean exact release checkout. The checker digest covers executing Python code, requirements, and version. Ordinary runs identify dirty member sources by digest; replay requires those sources. Candidates require a clean registered commit. Record digests identify the records actually used.

`--output PATH` selects a new evidence directory outside the member checkout. Without it, the runner creates a temporary directory and reports `artifacts`. It writes available metadata to `metadata.json` and the completed attempt to `result.json`, including failures.

Source or lock changes during execution fail the run. The runner does not repair files changed by project code. Compatibility failures return exit 1; invalid requests or unreadable records return exit 2. CI uploads evidence after failure without masking the failed command.

For replay, create clean checkouts at the reported project, checker, and record commits. Verify the record digest, then run:

```bash
nix run --no-update-lock-file ./checker -- \
  --policy-root ./records compatibility ./project \
  --project PROJECT --channel stable --output ./replay-stable
nix flake check ./project --no-update-lock-file --print-build-logs
```

Repeat with `unstable` on every required native architecture. Preserve candidate registration and add `--batch ID` when needed. Keep the captured records unchanged. The replay digest must match the original evidence.

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

| File                       | Schema | Contents                                                                                                  |
| -------------------------- | ------ | --------------------------------------------------------------------------------------------------------- |
| `policy/members.json`      | 1      | `members` maps enrolled names to GitHub `owner/repository` identities                                     |
| `policy/projects.json`     | 2      | Legacy identities, stable update branch, adoption, selections, architectures, VM targets, and check lists |
| `policy/pins.json`         | 1      | `approved` is null or an exact stable/unstable pair; `batches` records updates                            |
| `policy/support.json`      | 1      | Explicit release [retirements](#release-support-and-retirement)                                           |
| `policy/requirements.json` | 1      | Default runner systems and release-owned CI requirements                                                  |

The member roster contains no copied selections, settings, or adoption flags. Names and repository identities must be unique; repository comparison ignores case. Identities must agree with existing legacy records. Missing, malformed, or duplicate-key records fail inspection. An empty roster is valid and distinct from missing data.

Reviewed central changes control enrollment and removal. Ordinary upgrades and checks before enrollment leave the roster unchanged.

### Legacy records

Supported older checkers read the whole snapshot, so legacy records still undergo global validation. A migrated member's caller supplies its selection and settings; stale legacy copies do not control it.

Retain legacy entries after migration or roster removal, including identities used by historical batches. Before a new member participates in batches, add a compatibility entry with `repository`, `adopted: false`, `policyVersion: null`, and `vmTargets: []`. This entry does not copy current settings or change on upgrades.

Adopted legacy entries must retain complete `requiredChecks`. Retained v0.3.0 lists must match that legacy selection's generated requirements, including `additionalRequiredChecks`. They do not track a migrated member's caller. Remove data only through [retirement and cleanup review](#release-support-and-retirement).

### Release requirements

The checker reads `requirements.json` beside its own code, independently of `--policy-root`. Current records cannot replace release requirements.

The `ci` fields define the caller name, common `requiredChecks`, per-architecture `architectureChecks`, `compatibilityChecks` templates, `runners`, and conditional VM status. Templates use `{architecture}`. The checker combines these fields with member settings to produce ordinary and compatibility matrices and the complete gate list. Additional gate names do not create workflow jobs.

### Pin batches

The root bootstrap lock does not approve shared pins. Approval comes from current `pins.json`. Pin updates use new records with the same selected checker release.

Each batch contains an ID, state, `pins`, optional `previous` pair, and `projects` mapping member names to tested commits. Pair values contain only exact stable and unstable commits. States are `candidate`, `approved`, `rolling`, `paused`, `complete`, and `withdrawn`.

The checker reports an automatically selected candidate as `candidateBatch`; `--batch` requests one explicitly. Callers cannot supply arbitrary approved pins. Active rollouts must remain tied to central approval.

During `approved`, `rolling`, or `paused` batches, affected lock scopes can use the old or new pair. All shared-pin observations in one project must select one allowed pair. Policy v0.2.0 and later exclude the selected root lock node from this comparison. Compatibility execution still targets the central approved pair. Completed and withdrawn batches grant no extra allowance.

## Implemented coverage

The checker resolves version-7 lock graphs, including root-relative `follows`, across first-party lockfiles. It ignores unreachable nodes and vendor/cache directories. It identifies GitHub sources from GitHub inputs and Git URLs, checks immutable selections, flags ambiguous nixpkgs sources, and builds member dependency graphs. Policy repository flake dependencies fail.

For v0.2.0 and later, root `nixpkgs` must resolve to an immutable `NixOS/nixpkgs` flake. Its revision and update branch can differ from shared pins. The exemption includes references that follow the resolved root node.

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

## Release support and retirement

`policy/support.json` uses schema version 1. Its `retirements` object maps exact release tags to decisions. A release without a decision remains supported, regardless of age or newer releases. Support does not establish valid declarations or remote publication.

Each decision requires an HTTPS review reference, nonempty reason, and UTC timestamps in `YYYY-MM-DDTHH:MM:SSZ` format. Retirement must follow migration start. This example is not an actual decision:

```json
{
  "schemaVersion": 1,
  "retirements": {
    "v9.9.0": {
      "decision": "https://github.com/example/policy/pull/123",
      "reason": "Example reviewed retirement",
      "migrationStartsAt": "2030-01-01T00:00:00Z",
      "retiresAt": "2030-02-01T00:00:00Z"
    }
  }
}
```

Before `retiresAt`, support remains `supported` with the pending decision and migration period. At the deadline it becomes `retired`. `check`, `ci`, `compatibility`, `vm`, and audits share this assessment.

Retired member commands return exit 1, `selectionStatus: "retired"`, the `support` decision, and a failure reason. They provide no usable CI matrix and start no shell, compatibility, or VM execution. Commands reassess support before returning. Hosted ordinary jobs repeat planning after execution to detect deadlines crossed during a run.

Malformed or missing support records cause inspection errors with `selectionStatus: "unknown"`. Invalid declarations return `selectionStatus: "invalid"`.

The `support` object contains `policyVersion`, effective `status`, and `retirement` or null. It has no observation timestamp. Its identity stays stable until the decision or effective status changes. Record digests include decision content, with the historical project/pin digest retained separately. A retirement can take effect without a record edit. Evidence consumers must reassess support even when digests match.

Audits retain retired enrolled selections as failures with the member revision and decision. They can reject a retired selection without executing its checker. Immutable older checkers do not read the support file; their local behavior stays unchanged.

### Legacy cleanup

Plain `validate` checks structure and returns `legacyCleanup: "not-assessed"`. Retained adopted entries still need full `requiredChecks` after retirement, because retirement does not prove migration.

`--previous-policy-root OLD` compares proposed records with a trusted prior snapshot. It detects removed entries, fields, batches, participants, and edits to completed or withdrawn batches. Any supported legacy release blocks cleanup, including unselected immutable patch releases.

After known legacy releases retire, cleanup review checks the complete published release inventory for other supported legacy patches. Incomplete or inaccessible information blocks cleanup.

`--workspace` must contain exact clean checkouts for the union of prior and current enrolled identities. Each must select a supported new-contract release and pass its published immutable checker against the proposal. Roster removal does not waive migration evidence. Invalid callers, legacy selections, changed sources, and failed checks block cleanup.

Success returns `legacyCleanup: "eligible"` with exact migration evidence. It deletes nothing and authorizes no merge. Comparisons without removals return `not-needed`. All retained schemas and historical batch references must remain valid. See the [cleanup procedure](maintenance.md#retirement-and-legacy-cleanup).

## Integration policy agreement

`agreement PATH --project NAME` checks the integration project's exact consumed enrolled member revisions. Each must select the integration project's own supported release. Run its selected checker with explicit trusted records. The integration checkout must be clean, contain a root lock, and declare the agreement gate.

Success proves policy agreement only. The report states `behavioralIntegration: "not-run"`.

Inspection covers each committed first-party `flake.lock`, including independently locked examples. Vendor, generated-result, and cache exclusions apply. Reachable nodes define the consumed set, including transitive members, aliases, and root-relative `follows`. Unreachable nodes add no dependencies. The consumed graph takes precedence over a dependency's standalone lockfile. See [Nix's lock contract](https://nix.dev/manual/nix/2.29/command-ref/new-cli/nix3-flake#lock-files).

Trusted roster identities define membership. Legacy-only entries do not enroll dependencies. The historical `petohorvath/nix-nftzones` identity maps to `petohorvath/nixos-nftzones` only when the latter is enrolled. Inspection still fetches the actual locked URL and revision. An original input cannot give an enrolled identity to a contradictory locked source. A graph without enrolled dependencies cannot establish agreement.

Supported sources are GitHub inputs and Git inputs with GitHub HTTPS or `ssh://` URLs and exact 40-character revisions. A locked `dir` selects the flake subdirectory. Inspection fetches committed workflow blobs into temporary Git storage without checking out files, running hooks, evaluating flakes, or executing member code.

Missing commits, unsupported transports, escaping subdirectories, symlinked declarations, and LFS/submodule expansion prevent a passing result. Private sources require existing Git read access. Inaccessible sources cause inspection errors.

Each consumed member needs one valid caller with matching literal identity and `policy_version`. Its selection must equal the integration selection. Current member branches and copied central versions do not affect the result. Partial dependency upgrades fail. Cycles fail, including those through nonmember nodes or `follows` to the integration root. Policy repository inputs remain forbidden.

Reports identify:

- Integration `revision`, central `records`, and committed `lockfiles`.
- Each member's trusted identity, `revision`, `source`, lock-node `references`, `policyVersion`, support, and outcome.
- `dependencyGraph`, `cycles`, and `dependencySetDigest`.

The digest binds lock contents and discovered sources/selections, without wall-clock seconds. Evidence consumers must reassess support. Agreement rechecks all consumed selections at completion and rejects changed integration sources or records. Invalid declarations are failures; unavailable sources are inspection errors. Both outcomes retain JSON on standard output.

Use the [integration caller template](../templates/integration-caller.yml). The ordinary Policy caller declares `additional_required_checks: '["Integration / Policy agreement"]'`. The separate `Integration` caller uses `agreement.yml` at the same release. It depends on Policy and passes exactly its `project_revision` and `records_revision` outputs. Custom conditions, matrices, alternate sources, and live record references fail validation.

The agreement workflow verifies the release and captured commits, invokes the command, and retains failure reports. It runs once after Policy succeeds, independently of architecture. Its exact status is `Integration / Policy agreement`. Declaring this name adds a required gate; it does not configure GitHub protection. Other additional names remain project-owned and never become commands. Follow the [integration procedure](maintenance.md#integration-project-agreement).

## Enrollment audits

`audit WORKSPACE` inspects every trusted roster identity at `WORKSPACE/NAME`, using a clean exact commit. Missing checkouts and invalid, ambiguous, missing, or disabled callers remain failed enrolled entries. They cannot reduce coverage or create pending status.

`--fetch` clones missing public repositories from trusted roster identities. It leaves existing checkouts at their local revision. Source changes during inspection cause errors.

The audit discovers the selected tag from the unique caller and checks literal `project` and `policy_version`. It verifies publication, immutability, and non-prerelease status in the trusted policy repository. It resolves lightweight or annotated tags to exact commits and invokes that commit's Nix package with the captured records.

A local development checker cannot replace an unpublished selected release. Each checker enforces its own rules. Historical checkers require their legacy records; migrated members select releases independently of those copies.

### Audit reports

Each report identifies roster `repository`, member `revision`, `policyVersion`, verified `checkerRevision`, `checkerRepository`, and central `records`:

| Field                  | Meaning                                         |
| ---------------------- | ----------------------------------------------- |
| `records.revision`     | Git commit, when available                      |
| `records.digest`       | Project, pin, roster, and support records       |
| `records.legacyDigest` | Projects and pins only, matching older checkers |

The dispatcher verifies returned identities, revisions, digests, report types, outcomes, and new-contract settings. Substituted or malformed reports cause inspection errors. Older `policyRecordsDigest` values retain their historical scope. Changed central records fail the audit, even for an initially empty roster.

Static success retains `compatibility: "not-run"`; audits do not rerun member CI. Candidate-only assessments remain visible but cannot establish approved-pin compliance. Member failures and inspection errors remain distinct; aggregate inspection errors take precedence. Errors retain JSON on standard output, including initial record failures. Artifact upload cannot mask command failure.

### GitHub enforcement

`--github` checks squash-only merging, a PR requirement, and required statuses through rulesets or branch protection. It obtains required names from:

- Generated requirements for members selecting this release.
- The selected checker's validated `requiredChecks` report for v0.3.0 and later.
- Retained records after checker inspection for older releases.

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
- Test evidence and human approval for pin-batch state changes.

Required status names alone do not prove which workflow code ran. New generated-source exclusions need a documented checker extension before enrollment.

## CI integration

Use [templates/policy-caller.yml](../templates/policy-caller.yml). Set the member name and both version placeholders to the same published immutable release tag. The policy repository's `main` branch requires agreed human and CI merge controls before activation.

The first job captures the checker, member, and current record commits. All later jobs use these snapshots. The caller's `name: Policy` supplies the status prefix. Run `ci PATH --project NAME` for exact names and matrices.

| Status                                                | Required for               |
| ----------------------------------------------------- | -------------------------- |
| `Policy / Verify policy version and load shared pins` | Every enrolled member      |
| `Policy / Compliance (<architecture>)`                | Each required architecture |
| `Policy / Formatting and lint (<architecture>)`       | Each required architecture |
| `Policy / Project tests (<architecture>)`             | Each required architecture |
| `Policy / Compatibility (stable, <architecture>)`     | Each required architecture |
| `Policy / Compatibility (unstable, <architecture>)`   | Each required architecture |
| `Policy / VM tests (x86_64-linux)`                    | Members with VM targets    |

The first job verifies the release and generates matrices once on x86_64. This metadata job does not add x86_64 to the member's required architectures or publish a release.

Compliance runs structural, pin, caller, and shell checks. Formatting/lint runs `lint` in the member shell and formats a disposable copy. Project tests first run `host-checks` to require nonempty host checks with `--no-update-lock-file`, then run full committed-lock root checks. Candidate execution and replay enforce the same sequence for v0.4.0 and later; older selected releases retain their existing behavior. Compatibility runs both shared revisions.

Each category runs independently on every required architecture with `fail-fast: false`. They and the VM job depend only on the first job. A failure does not suppress other categories. Declared VM targets require the x86_64 gate even with ARM-only ordinary coverage. Without targets, VM reports `not-applicable` and its status need not be required.

Standard PR checkout tests GitHub's candidate merge commit. A registered member candidate must name that clean commit. Registration changes central records, without a caller batch field or workflow-reference change. After the record PR merges, rerun member checks to capture new records. After approval, ordinary checks use the approved pair. For routine updates through an unmerged central proposal, see [central pin PRs](#central-pin-prs).

Workflows use read-only permissions and do not persist checkout credentials. Automation that creates member PRs requires a separately reviewed write identity. Normal policy checks require no such credential.

The workflow invokes `check --shell` with the same requirements before and after enrollment. It reports checks, candidates, and enrollment separately. Successful CI does not enroll members or approve pins.
