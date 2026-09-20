# Checker reference

This reference describes the prepared v0.4.0 contract. Publication and each member migration remain separate decisions.

The checker runs from a selected policy release or its packaged `nixos-project-policy` executable. `--version` reports its version. The global `--policy-root PATH` option selects a trusted checkout of current records and is required for `check`, `audit`, `vm`, `compatibility`, `agreement`, and `ci`. These commands never silently use a release's historical pin snapshot. Other commands default to bundled records when no path is given. Passing project tests alone does not establish family compliance.

## Commands

| Command after `nix run .# -- --policy-root .`                   | Behavior                                                                                                                                          |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `validate`                                                      | Validate record structure; report whether approved pins exist. This does not assess legacy cleanup.                                               |
| `validate --previous-policy-root OLD --workspace WORKSPACE`     | Review proposed legacy removals against a trusted prior snapshot, release retirement, and exact migrated member checkouts.                        |
| `ci PATH --project NAME`                                        | Report the member's CI matrices and required status names, including additional project checks. This does not run checks or establish compliance. |
| `check PATH --project NAME`                                     | Inspect a member checkout and fail on missing requirements or an unapproved baseline.                                                             |
| `check PATH --project NAME --readiness`                         | Accepted for legacy command compatibility; new callers run the same checks before and after enrollment.                                           |
| `check PATH --project NAME --shell`                             | Also execute the member shell with a cleared inherited environment, probe required tools, and evaluate its root formatter.                        |
| `check PATH --project NAME --batch ID`                          | Test a registered candidate only at the exact project commit in that batch.                                                                       |
| `compatibility PATH --project NAME --channel stable`            | Verify the shared stable override and execute full root host checks; `unstable` selects the other channel.                                        |
| `compatibility PATH --project NAME --channel stable --batch ID` | Execute a registered candidate at its exact clean project commit, without approving it.                                                           |
| `lint PATH`                                                     | Execute statix, deadnix, and root formatting in a temporary source copy; report required formatting changes without modifying the checkout.       |
| `audit WORKSPACE`                                               | Inspect each enrolled identity at its exact checkout revision with its discovered published release; report failures and dependency cycles.       |
| `audit WORKSPACE --fetch --github`                              | Clone missing public checkouts and inspect enrolled members' merge settings and required checks. Existing local checkouts are not updated.        |
| `agreement PATH --project NAME`                                 | Compare the integration project's supported selection with declarations at its exact committed member dependency revisions.                       |
| `vm PATH --project NAME`                                        | Execute the member-declared VM targets; report `not-applicable` when the member has none. Requires a suitable builder.                            |
| `candidate --stable COMMIT --unstable COMMIT`                   | Emit an unapproved pair of exact commits. It neither writes locks nor registers or approves a batch.                                              |
| `title TITLE`                                                   | Validate Conventional Commit PR-title syntax.                                                                                                     |

Commands print JSON with `checkerVersion`, `policyRecordsRevision` when Git metadata is available, and `policyRecordsDigest` for the records actually used. Member reports also identify their discovered `policyVersion` and validated `memberSettings`. Exit 0 means the requested operation succeeded; planning and candidate validation can succeed without establishing compliance. An audit succeeds only when every enrolled assessment passes (or its roster is empty). Exit 1 means enforced checks failed. Exit 2 means the request, record, or inspection could not be processed. Reports include checked project commits when Git metadata is available. Reusable CI separately logs the exact checker and record checkout revisions.

After publication, a selected release can check a member against separate current records:

```bash
nix run github:petohorvath/nixos-project-policy/v0.4.0 -- \
  --policy-root ../nixos-project-policy-records \
  check ../member --project member --shell
```

Update the trusted records checkout from `main` before a current check. Use captured snapshots when reproducing results. Local commands do not fetch records or prove that a checkout is current. `check`, `ci`, `compatibility`, and `vm` inspect exactly one member caller and require its release to match the executing checker, regardless of stale legacy records or absence from enrollment. The caller's `policy_version` must match its immutable workflow reference.

`ci PATH --project NAME` returns `planned`, the ordinary `matrix`, both-channel `compatibilityMatrix`, applicable `vmTargets`, and the complete `requiredChecks`. Omit `PATH` only when the current directory is the member. The workflow passes `--inputs-json JSON` with its input object: the same parser validates defaults and literals and requires the normalized inputs to match the checked-out caller. This option cannot supply settings that differ from the member source.

A normal `check` can report `pass` before enrollment. Its `enrollment` field is separate (`enrolled` or `not-enrolled`); no check changes enrollment or approved pins. `--readiness` remains accepted but does not weaken requirements. Registered candidates report `candidate-ready` from static checks and `candidate-pass` from successful compatibility execution. Static reports include `compatibility: "not-run"`; they do not establish execution evidence. Older released checkers retain their readiness/adoption contract and central selection records.

`nix run .# -- shell PATH` probes only the common tools and root formatter, without asserting member compliance. The policy repository runs this host-level smoke test in its own CI. Tool probes use supported help/version commands; statix does not expose a `--version` flag at the bootstrap pin.

CI and runtime probes use `--no-update-lock-file` by itself to reject a required lock update. Do not combine it with `--no-write-lock-file`: the Nix 2.34.6 source and a controlled local fixture show that disabling writes also bypasses the update rejection, allowing an in-memory replacement lock. See the [locking implementation](https://github.com/NixOS/nix/blob/2.34.6/src/libflake/flake.cc#L749-L825). This rejection rule applies to the default check and shell/tool probes. Compatibility runs deliberately use a different effective graph: `--override-input` implies `--no-write-lock-file`, and adding `--no-update-lock-file` does not make an override test the committed selection.

## Unmerged candidate coordination

`pin-batch plan`, `pin-batch execute`, and `pin-batch aggregate` validate enrolled members against an explicitly selected unmerged proposal. Planning selects one member with `--project` or the complete trusted roster with `--all`. These commands require separate `--policy-root BASELINE` and `--proposal-root PROPOSAL` checkouts. The baseline supplies enrollment, repository identities, legacy settings, and support decisions; each exact clean member commit supplies modern declarations. The proposal's selected `--batch` supplies an exact stable/unstable pair and registers member commits. The coordinator verifies each commit in its trusted enrolled repository, resolves the selected published immutable release, and uses that release's requirements and checker by exact commit.

The proposal may contain the future approval change and a new routine batch's future `complete` state. Candidate execution copies baseline records into a new evidence directory, keeps the baseline's approved pair, and normalizes the selected batch to `candidate`. Approved or historical baseline batches cannot be reused. Proposed enrollment, support, legacy settings, policy identity, and unrelated rollout rewrites are rejected. Proposed checker code and requirements never execute. The four proposed record files must be regular committed Git blobs with matching working-tree bytes; the loader rejects symlinks and stages only verified blobs for parsing. Record revisions and digests distinguish the trusted baseline, exact proposed snapshot, and temporary execution records.

Run planning from trusted coordinator code, with the member checkout at the exact commit in the proposal. Each output directory must be new and outside all input checkouts:

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

Repeat execution on each native system listed in the plan's matrix, preserving the same exact member, checker, baseline, proposal, and plan. ARM jobs require an ARM host. Applicable VM targets require an x86_64 KVM-capable host even when ordinary coverage is ARM-only. Each worker runs independent compliance/shell, lint, committed-lock root checks, and both candidate compatibility channels; a category failure does not suppress other runnable categories. Additional gate names are inspected through GitHub check runs or commit statuses at the exact member revision. They do not become shell commands or create missing member jobs. An unavailable, incomplete, skipped, neutral, or failed required gate cannot complete the member result.

For v0.2.0 and later, the selected compatibility runner verifies effective root overrides, nonempty native checks, and lock preservation. For v0.1.x, the adapter retains whole-project committed-pin requirements and never grants a root override exception. Both candidate revisions must appear in the effective committed root graph, and nonempty native root checks must execute against it. Missing historical channel coverage or pin-bound additional/transitive/example locks require explicit member source or lock changes. Successful checks do not manufacture those changes.

Collect the native evidence directories and aggregate against the original plan:

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch aggregate ./member \
  --proposal-root ./proposal --plan ./plan/plan.json \
  --results ./results --output ./summary
```

Planning and replay re-inspect trusted inputs and the selected release. Changed pins, sources, settings, records, checker code, or an effective retirement invalidate the prior plan. Aggregation requires every planned job and rejects substituted, missing, conflicting, failed, or unprocessable evidence. Reports and logs remain in the evidence directories on failure. `candidate-pass` means the requested candidate coverage succeeded; every single-member result remains `eligible: false` for the enrolled batch and grants no approval. Nix may satisfy executed builds from its cache; reports do not claim each derivation rebuilt.

Plan/result digests bind content and freshness; they do not authenticate execution. Local aggregation requires trusted worker evidence. The central manual workflow uses artifacts from its own run with attempt-specific names, pinned coordinator/record/source snapshots, and read-only repository permissions. This workflow must run from trusted `main`; its single-member completion job is not a whole-batch PR approval gate. New workflow definitions need publication before a real hosted run, and local workflow fixtures do not establish live GitHub merge protection.

## Complete enrolled batches

Whole-batch planning enumerates the baseline roster and requires a registration for every enrolled identity. A proposal cannot choose a smaller participant set or replace selected-release requirements with its own matrix. Checkouts live at `WORKSPACE/PROJECT`; optional `--fetch` clones missing checkouts from the trusted repositories at the exact registered commits. Existing checkouts must already match and remain clean. A failed member capture remains visible while independently runnable members retain their plans and artifacts.

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch plan ./members --all --fetch \
  --proposal-root ./proposal --batch BATCH --attempt review-1 \
  --output ./batch-plan
```

The parent plan records `scope: "whole-batch"`, the complete `roster`, registrations, baseline and proposal identities, candidate pair, attempt, coordinator identity, and each selected-release member plan. Its matrix supplies `project`, `repository`, `revision`, `system`, `runner`, a stable `worker` ID, and the expected native `job` name. Execute every row on its native architecture with the captured plan and an independently supplied expected attempt:

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch execute ./members/MEMBER --all \
  --proposal-root ./proposal --plan ./batch-plan/plan.json \
  --project MEMBER --system x86_64-linux --attempt review-1 \
  --output ./batch-results/MEMBER--x86_64-linux
```

Each worker re-inspects its selected subject and executes the same contract as single-member coordination. Reports bind the parent `batchPlanDigest`, child `planDigest`, attempt, worker ID, native system, exact source, checker, settings, and execution records. Historical members keep their committed-pin requirements. The `Integration / Policy agreement` gate invokes the integration project's selected immutable agreement checker directly, captures its actual locked members and `dependencySetDigest`, and checks that exact report during replay. The integration project's own committed-lock and candidate root checks still execute; member-head success and static policy agreement cannot replace behavioral integration coverage.

Keep each worker's entire output directory as one artifact under the results directory. Its root `result.json` is the worker envelope; nested checker reports, metadata, and logs remain attached to that worker. Aggregate all artifacts against the original plan:

```bash
nix run --no-update-lock-file ./coordinator -- \
  --policy-root ./baseline pin-batch aggregate ./members --all \
  --proposal-root ./proposal --plan ./batch-plan/plan.json \
  --results ./batch-results --attempt review-1 --output ./batch-summary
```

Aggregation re-inspects all planned members, accounts for every native worker and required child job, and retains full input artifacts and member outcomes on failure. Missing, duplicate, conflicting, skipped, cancelled, failed, malformed, or foreign evidence cannot produce eligibility. Source, setting, checker, enrollment, support, dependency, record, or candidate changes require a renewed plan and fresh attempt. This coordinator conservatively invalidates the captured batch when shared record identities change and does not combine saved reports across attempts. Ordinary Nix build-cache reuse remains valid during newly executed commands. Support is reassessed when evidence is consumed and again for every captured selection at completion, including retirements that become effective without a record edit.

Only a complete successful aggregate reports both `status: "candidate-pass"` and `eligible: true`. Plans, workers, and single-member summaries remain ineligible; every summary retains `approval: "not-granted"`. No command changes approved pins, enrolls members, or authorizes a merge.

The prepared `Complete candidate batch` manual workflow runs from trusted `main`, captures all sources through this CLI, executes independent native workers, and retains attempt-specific plan, worker, and summary artifacts. It passes the expected attempt separately and rejects an unsuccessful overall worker job through `--execution-status`. Integrations that verify individual external worker outcomes can pass `--worker-outcomes FILE`: schema version 1, the parent `planDigest`, expected `attempt`, and a `workers` list containing exactly one `{ "id": WORKER_ID, "status": "completed", "conclusion": "success" }` entry for every matrix row. Missing or unsuccessful entries fail aggregation. The caller must establish those outcomes' provenance; a supplied JSON file or digest does not authenticate a GitHub run. This manual workflow does not establish a required check on a central PR's proposal revision.

## Central pin PRs

The `Central pin proposal` workflow uses trusted `pull_request_target` orchestration and the complete-batch CLI. Its public gate is `Pin batch / Complete candidate`, created explicitly on the reviewed proposal's `head_sha`. GitHub's [required-check rules](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks) distinguish current-head results from old commits and manual workflow-job checks. The workflow does not execute proposed actions, Python, shell, or Nix code. Matrix commands and runners come from the trusted coordinator and selected published releases.

The `pin-pr` commands implement the hosted boundary. Each requires `--policy-root BASELINE` and a new `--output DIR` outside inputs. `capture`, `collect`, and `finish` also require `--proposal-root PROPOSAL --number PR --head COMMIT --run RUN_ID --attempt RUN_ATTEMPT`. `finish` additionally requires `--check CHECK_RUN_ID`. The run's repository, event, workflow path, exact workflow/base revision, and attempt must match independently queried GitHub metadata. An older workflow rerun cannot attest a newer baseline.

| Operation           | Behavior                                                                                                                                                                                                   |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pin-pr capture`    | Classify exact committed records and create a pending proposal-head check. Candidate approval requires whole-batch evidence; unrelated and state-only PRs retain ordinary review.                          |
| `pin-pr collect`    | Read exact-attempt jobs and run-scoped artifact metadata, download verified plan/worker archives, and emit `worker-outcomes.json` for `pin-batch aggregate`. It retains available evidence after failures. |
| `pin-pr finish`     | Independently recheck jobs, artifacts, complete summary, proposal/base freshness, and root/dependency support, then update only the check bound to that head and attempt.                                  |
| `pin-pr invalidate` | Inspect open PR checks and mark stale successes/pending checks failed after a baseline change or effective retirement. It does not rerun checks or modify PRs.                                             |

These operations use `GH_TOKEN`, falling back to `GITHUB_TOKEN`. Collection needs read access to policy-repository Actions and PR metadata. Capture, reporting, and invalidation write Checks only in that repository; run them only when that reporting action is intended. Native execution jobs have read-only permissions, disable persisted checkout credentials, and remove inspection tokens, Actions runtime variables, and workflow command-file paths from member subprocess environments.

Plans, results, and artifact names bind run ID, attempt, worker, and the complete subject. GitHub [attempt job metadata](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt) supplies independent names, native labels, and conclusions. Its [artifact API](https://docs.github.com/en/rest/actions/artifacts#list-workflow-run-artifacts) is run-scoped, so collection requires unique attempt-specific names, matching run/base metadata, available artifacts, and matching download digests. Unsafe archive paths and symlinks fail. Metadata does not directly attest each artifact's producing job; expected trusted workflow jobs and their outcomes remain part of the provenance boundary. Local digests alone authenticate no execution.

The artifacts use `pin-plan-RUN-ATTEMPT`, `pin-result-RUN-ATTEMPT-WORKER`, `pin-summary-RUN-ATTEMPT`, and `pin-provenance-RUN-ATTEMPT`. Retain complete native directories and the summary's captured plan when replaying through `pin-batch aggregate`; pass the independently captured attempt and, for hosted replay, `--worker-outcomes FILE`. Preserve raw checker reports and logs. A successful replay remains candidate evidence and does not grant approval or refresh the live PR check.

PR reports identify `scope: "pin-pr"`, head, baseline/proposal identities, run/attempt, plan digest, members, and issues. Only complete current candidate evidence yields `eligible: true`; every report retains `approval: "not-granted"`. A successful non-applicable or rollout-state-only assessment has `eligible: false`. Missing, skipped, cancelled, neutral, failed, expired, substituted, or unprocessable required evidence prevents a successful candidate gate. A newer validation check supersedes an older attempt.

See [renewal and activation](maintenance.md#renewing-pr-evidence) for base-change invalidation, fresh target events, trusted merge-gate configuration, and the remaining merge-time boundary. Local fixtures test the actual workflow shell and external API seams; they do not establish live GitHub protection.

## Compatibility execution and evidence

`compatibility` uses the same runner locally and in reusable CI. It requires a member caller selecting the executing checker release, a Git checkout whose root lock matches its committed copy, a supported native Linux host, and an approved pair or an exact registered candidate. It resolves the root input through `LockGraph`, including renamed nodes and `follows`, then verifies the locked repository and commit returned by `nix flake metadata --json`. A missing input, ignored override, wrong source, or wrong revision fails before the check command.

The runner evaluates the names of `checks.<host-system>` with the same override and requires a nonempty interface. It then runs `nix flake check PATH --print-build-logs --override-input nixpkgs github:NixOS/nixpkgs/REVISION` without `--no-build`. This retains ordinary root evaluation and check builds. Nix may satisfy builds from its cache; a successful result does not claim that every test process ran again. Review remains responsible for meaningful coverage and VM separation. See the Nix [check options](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake-check.html) and [metadata output](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake-metadata.html).

Ordinary runs select exactly `approved`, including during approved, rolling, and paused batches. A unique candidate registered for the exact clean project commit selects exactly that candidate's pair, automatically or through `--batch`. Multiple matches require explicit disambiguation. Completed and withdrawn batches supply no additional test target. Candidate success returns `candidate-pass` with `pinStatus: "candidate"`; approved-pin execution returns `pass` with `pinStatus: "approved"`. Neither result records adoption or changes pin approval. Missing approval without a matching candidate fails.

Reports contain the project `revision`, `sourceDirty`, `sourceDigest`, `checkerVersion`, `checkerRevision`, `checkerSourceDigest`, `policyRecordsRevision`, `policyRecordsDigest`, `channel`, detected `system`, `expectedRevision`, `resolvedRevision`, `candidateBatch`, host check names, and each command's arguments and exit outcome. Git commits are read from checkouts; packaged checkers embed their clean flake revision. An unpacked or dirty package may lack a checker commit, so release reproduction requires a clean exact release checkout. The checker source digest covers the executing Python code, requirements, and version. Dirty ordinary project sources are identified by their digest and must be preserved to replay them; candidates require a clean registered commit. Record digests identify the in-memory records actually used, even if the record checkout later changes.

`--output PATH` selects a new evidence directory outside the member checkout; omission creates a temporary directory and reports its path as `artifacts`. The runner writes `metadata.json` when metadata is available and `result.json` for the completed attempt, including execution failures. It detects source or lockfile changes during execution and fails. It does not repair files changed by project code. Exit 1 means a compatibility attempt failed; invalid CLI requests or unprocessable records use exit 2. CI uploads evidence even when the runner fails; upload success cannot mask the failed step.

Reproduce a run using the reported commits, not current policy `main`. Create clean checkouts at the project `revision`, `checkerRevision`, and `policyRecordsRevision`, verify the policy-record digest against the saved result, then run:

```bash
nix run --no-update-lock-file ./checker -- \
  --policy-root ./records compatibility ./project \
  --project PROJECT --channel stable --output ./replay-stable
nix flake check ./project --no-update-lock-file --print-build-logs
```

Run `unstable` separately and repeat on each native architecture in the member's `required_architectures` input. Retain the same candidate registration and add `--batch ID` when reproducing an explicitly selected candidate. Do not refresh the record checkout while replaying. The replay's reported record digest must match the original evidence.

## Member declarations and central records

The existing policy caller owns `project`, `policy_version`, and these literal string inputs:

| Input                        | Contract                                                                          |
| ---------------------------- | --------------------------------------------------------------------------------- |
| `required_architectures`     | Required nonempty JSON list, containing `x86_64-linux`, `aarch64-linux`, or both. |
| `vm_targets`                 | Optional JSON list of simple lowercase build target names, default `[]`.          |
| `additional_required_checks` | Optional JSON list of additional GitHub status names, default `[]`.               |

Reject malformed JSON, wrong types, empty or duplicate architectures, unsupported systems, duplicate or invalid targets/check names, dynamic expressions, forbidden inputs, and missing or multiple policy callers. Additional gates cannot remove mandatory statuses or create project jobs. Reports use `requiredArchitectures`, `vmTargets`, and `additionalRequiredChecks` inside `memberSettings` for the validated values. Review coverage reductions explicitly and verify actual merge settings separately.

`policy/members.json` uses schema version 1 and a `members` object mapping project names to GitHub `owner/repository` strings. It contains enrolled identities only, with no copied selections, settings, or adoption flags. Names and repository identities must be unique, including case-insensitive repository equality. An identity already present in legacy records must agree. Missing, malformed, or duplicate-key records fail inspection; a valid empty roster is distinct from missing data. New-contract enrollment reports and audit coverage use this roster independently of legacy adoption flags.

Enrollment and removal are explicit reviewed central changes. Ordinary upgrades and pre-enrollment checks do not edit the roster. Preserve all legacy entries after roster removal, including names referenced by completed or withdrawn batches. For a newly enrolled identity that later participates in pin batches, retain an inert legacy entry with its `repository`, `adopted: false`, `policyVersion: null`, and `vmTargets: []`. These compatibility placeholders do not copy current member settings and do not change on upgrades; older checkers require all batch names to remain globally resolvable.

`policy/projects.json` retains schema version 2 and the complete legacy consumer interface: repository identities, stable update branch, VM targets, adoption state, selected releases, architecture lists, and required check lists. New member commands obtain selection and settings from the caller; a stale legacy copy does not control their requirements. Records still undergo global structural validation because supported older checkers read the whole snapshot.

Retain complete `requiredChecks` lists on adopted legacy entries, including entries selecting v0.3.0, while supported older consumers need them. Preserve mandatory fields and identities referenced by historical pin batches. Retained v0.3.0 lists must match that legacy selection's generated requirements, including `additionalRequiredChecks`; they do not track a migrated member's current declaration. Remove compatibility data only after explicit retirement and completed migration of affected old releases. Publication alone does not retire older releases.

`policy/requirements.json` uses schema version 1 and belongs to the checker release. It defines required tools, supported Linux systems, README headings, and `ci`: the caller job name, common mandatory statuses in `requiredChecks`, per-architecture job names in `architectureChecks`, channel-to-status templates in `compatibilityChecks` using `{architecture}`, system-to-runner mappings in `runners`, and the conditional VM status. The checker expands these requirements with each member's `required_architectures` declaration and adds any `additionalRequiredChecks`. The `ci` command returns `matrix` for ordinary jobs, `compatibilityMatrix` for both shared-pin channels, and the complete `requiredChecks` list. Additional project checks do not create policy workflow jobs. The checker reads requirements beside its own code, even when `--policy-root` selects different current records. Placing these release requirements at the top level of current project records is rejected, so a record change cannot silently change a release's rules.

`policy/pins.json` uses schema version 1 and contains `approved`, either null or an exact stable/unstable pair, and `batches`. Initially no pair is approved. The policy repository's own bootstrap lock does not change that state. Pin changes use the same checker release with new current records. Migrated members can retain their selected root locks while rerunning compatibility; other checked lock scopes and older-policy members may need lock updates.

A batch records its ID, state, proposed `pins`, optional `previous` pair, and a `projects` map from affected member names to exact tested commits. States are `candidate`, `approved`, `rolling`, `paused`, `complete`, and `withdrawn`. Pair values contain only exact stable and unstable commit strings. The checker automatically selects the unique candidate registered for the matching clean source commit and names it in `candidateBatch`; `--batch` explicitly requests one for local testing. A caller cannot supply arbitrary approved pins. An active rollout must remain tied to the central approved baseline.

During an approved, rolling, or paused batch, affected projects may use its old or new pair. All shared-pin observations within one project must select one allowed pair. The independent root selection is excluded from that comparison only for policy v0.2.0 and later. Compatibility execution always targets the central approved pair during these active rollout states; the old/new allowance is not a choice of test target. Completed or withdrawn batches grant no extra allowance. Review remains responsible for recording real test evidence and the human approval behind each state change.

## Implemented coverage

The checker resolves version-7 lock graphs, including root-relative `follows`, and inspects reachable nixpkgs nodes across discovered first-party lockfiles. It ignores unreachable old nodes and vendor/cache directories. It recognizes GitHub repository identities from GitHub inputs and Git URLs, checks immutable selections and applicable approved revisions, flags ambiguous nixpkgs sources, rejects a policy repository flake dependency, and builds the member dependency graph. Arbitrary fetch expressions, indirect source imports, and unsupported source transports require review; a lock graph is not a complete source-level architecture audit.

For policy v0.2.0 and later, the root `nixpkgs` input must resolve to an immutable `NixOS/nixpkgs` flake. Its revision and update branch are independent of the shared pins. The exemption covers that resolved lock node, including references that follow it. Additional root inputs, distinct transitive nixpkgs nodes, and independently locked examples still need one allowed shared-pin pair across the project. In those scopes, first-party inputs name stable nixpkgs `nixpkgs` and unstable `nixpkgs-unstable`; an absent channel need not be added. Lock node identifiers and transitive input names remain unrestricted. Branch declarations identify shared channels; exact revisions can identify one when they match a single channel in the allowed pairs. Source review also covers first-party flakes without independent locks. Old-policy member checks continue to run their selected release.

Structural checks cover the root development entrypoint, required documentation files, README sections, and links to the selected release's `POLICY.md`. Caller checks require the selected release tag in the reusable-workflow reference, with a matching `policy_version` input and the literal job name `Policy`. Caller matrices are unsupported because they can change or duplicate required status names; the reusable workflow owns the check and architecture matrix. The PR trigger must declare exactly `types: [opened, synchronize, reopened, edited]`, in any order, so title edits refresh the Conventional Commit result. Branch, path, and other trigger filters remain unsupported. The caller job must have no matrix, error suppression, `if` condition, or `needs` dependencies: a skipped prerequisite would prevent it from running. The caller accepts only the identity, release, and settings inputs listed above; callers cannot supply compatibility revisions or skip a required channel. The caller intentionally runs for every PR; optimized documentation-only job selection can be added with tests later.

The reusable workflow verifies through GitHub that the selected release is published, immutable, and not a prerelease. It checks out the exact tag, checks that `VERSION` matches, and captures its commit alongside the current `main` record commit and member source commit. All policy, compatibility, and VM jobs use those three captured commits. A missing release, mutable release, unavailable API response, or version mismatch stops the workflow. Local structural checks do not attest remote publication or immutability.

## Release support and retirement

`policy/support.json` uses schema version 1 with a `retirements` object keyed by exact release tags. The real record starts empty. A release without an explicit retirement decision remains supported; publishing later releases, the number of newer releases, and release age do not retire it. Support does not replace caller validation or attest remote publication.

Each decision contains an HTTPS review reference in `decision`, a nonempty `reason`, and UTC `migrationStartsAt` and `retiresAt` timestamps using `YYYY-MM-DDTHH:MM:SSZ`. The end must follow the start. The decision below illustrates the schema; it is not an actual retirement:

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

Before `retiresAt`, the assessment stays `supported` and includes the pending decision and full migration period. At or after that time it becomes `retired`. New-contract `check`, `ci`, `compatibility`, `vm`, and central audits use the same assessment. Retired member commands return exit 1 with `selectionStatus: "retired"`, the `support` decision, and a failure reason; they produce no usable CI matrix and begin no shell, compatibility, or VM execution. Commands reassess support before returning, and hosted ordinary check jobs repeat planning after execution so a deadline crossed during a run cannot leave a successful required gate. Missing or malformed support records return an inspection error with `selectionStatus: "unknown"`; invalid member declarations return `selectionStatus: "invalid"`.

The `support` object contains `policyVersion`, effective `status`, and the decision in `retirement` (or null). Its identity remains stable while the decision and effective status are unchanged; it contains no observation timestamp. Record digests include decision content, and retain the exact historical project/pin digest separately. A retirement becoming effective changes the support assessment without changing record content. Evidence consumers must reassess support when using a plan or result; a matching record digest alone does not establish continuing support.

Retired selections remain visible in the enrolled audit with their member revision, failed assessment, and retirement decision. The central audit can establish this failure without executing the retired checker. Historical immutable checkers do not interpret the new support file; their local behavior is unchanged. Keep their compatibility records until both retirement and member migration permit reviewed cleanup.

`validate` alone checks structure and returns `legacyCleanup: "not-assessed"`. Retained adopted legacy entries require full `requiredChecks`, including after retirement, because retirement alone does not prove migration. To review actual removals, select a trusted prior snapshot with `--previous-policy-root OLD`. The checker detects removed legacy entries or fields, removed batches or participants, and edits to completed/withdrawn historical batches. Any supported legacy release blocks cleanup, including immutable patch releases that are no longer selected by current members.

Once all known legacy releases have effectively retired, cleanup review inspects the complete published release inventory for other still-supported legacy patches. Inaccessible or incomplete information cannot authorize cleanup. It then requires exact clean checkouts for the union of prior and current enrolled identities through `--workspace`: removing an identity from the proposed roster does not waive its migration evidence. Each must select a supported new-contract release and pass its published immutable checker against the proposed snapshot. Missing callers, retained legacy selections, dirty or changed sources, and failing checks prevent cleanup. A successful review returns `legacyCleanup: "eligible"` with exact migration evidence; it deletes nothing and grants no merge authority. Ordinary pin/record updates without removals report `not-needed` through this comparison. Existing schemas and every retained historical batch reference must still validate.

## Integration policy agreement

`agreement PATH --project NAME` reads the integration project's committed dependency graphs and checks that every consumed enrolled member revision selects the integration project's own supported release. Run the integration project's selected checker against an explicit trusted record snapshot. The project must be a clean Git checkout, retain a root `flake.lock`, and declare the required agreement gate. A successful report establishes policy agreement only; `behavioralIntegration: "not-run"` records that this command does not execute behavioral tests.

The command inspects every first-party committed `flake.lock` scope, including independently locked examples. Existing vendor, generated-result, and development-cache exclusions apply. Each graph's reachable nodes determine the consumed set, including transitive members, aliases, and root-relative follows. Unreachable stale nodes do not add dependencies. The consumed graph takes precedence over each dependency's standalone lockfile: [Nix's lock contract](https://nix.dev/manual/nix/2.29/command-ref/new-cli/nix3-flake#lock-files) transitively locks the inputs used by the integration project.

Trusted `policy/members.json` identities define the member scope. Retained legacy-only entries do not silently enroll dependencies. The historical `petohorvath/nix-nftzones` identity maps to `petohorvath/nixos-nftzones` only when the canonical repository is enrolled. Inspection still fetches the actual locked source and revision, including the historical URL. An original input naming an enrolled member cannot lend its identity to a contradictory locked source. A graph with no enrolled member dependencies cannot establish integration agreement.

Supported member sources are `github` inputs on GitHub and `git` inputs using GitHub HTTPS or `ssh://` URLs, each with an exact 40-character locked Git revision. A locked `dir` selects the flake subdirectory. The checker fetches that exact commit into temporary Git storage and reads committed workflow blobs without checking out dependency files, running hooks, evaluating flakes, or executing member code. Missing commits, unsupported transports, escaping subdirectories, symlinked declarations, and LFS/submodule source expansion produce non-passing inspection results. Git read access must already be available for private sources; inaccessible sources remain errors.

Every consumed member must have one valid caller with a matching literal identity and `policy_version`. Its selected release must equal the integration selection. Newer current member branches and copied central version fields do not affect this result. A partial locked-set upgrade fails; an integration PR may update its selection, policy links, caller references, and complete dependency set together. Member dependency cycles fail, including cycles through transitive nonmember nodes or follows to the integration root. Policy repository inputs remain forbidden.

The report identifies the integration `revision`, central `records`, committed `lockfiles`, and each consumed member's trusted identity, exact `revision`, actual `source`, lock-node `references`, discovered `policyVersion`, support assessment, and outcome. `dependencyGraph` and `cycles` describe enrolled member edges. `dependencySetDigest` binds the committed lock contents and discovered member source/selection set; it does not encode wall-clock seconds. Inspect support again when consuming evidence. The command reassesses every consumed selection at completion and rejects changes to the integration checkout or trusted record snapshot. Invalid declarations are failures; unavailable sources are inspection errors. JSON remains on standard output for both outcomes.

Use [the integration caller template](../templates/integration-caller.yml) alongside the integration project's own behavioral tests. Its ordinary `Policy` caller declares `additional_required_checks: '["Integration / Policy agreement"]'`. Its separate caller is named `Integration` and uses `agreement.yml` at the same release. That caller depends on the primary Policy job and passes exactly its `project_revision` and `records_revision` outputs. This is a constrained snapshot dependency; custom conditions, matrices, alternate sources, and live record references are rejected. The agreement workflow verifies the immutable release and captured commits, invokes the public command, and retains its report on failure.

The required status is exactly `Integration / Policy agreement`. Declaring the name makes it part of `ci` and audit gate requirements; it does not configure GitHub protection. Verify the observed status and separately review actual merge settings. Other additional gate names remain project-owned names and never become executable commands. The separate committed-lock `Project tests` jobs still run the integration project's behavioral root checks on every declared architecture, along with both shared-pin compatibility checks and applicable VM tests. Agreement runs once as architecture-independent source inspection after the primary Policy workflow succeeds.

## Enrollment audits

`audit WORKSPACE` iterates every identity in the trusted roster and inspects `WORKSPACE/NAME` at a clean exact Git commit. Missing checkouts remain failed enrolled entries; missing, ambiguous, invalid, or disabled callers cannot remove coverage or create pending status. `--fetch` clones missing public repositories from roster identities; it leaves existing checkouts at their selected local revision. Source changes during inspection are errors.

Discover the selected tag from the unique caller and require matching literal `project` and `policy_version` inputs. The audit verifies that the release in the trusted policy repository is published, immutable, and not a prerelease, resolves its tag to an exact commit (including annotated tags), and runs that commit's Nix package with the same trusted records checkout. A local development checker cannot stand in for an unpublished selected release. Each selected checker enforces its own caller, settings, and policy rules. Historical checkers still require their retained central contract; new-contract members can upgrade independently of those frozen copies.

Every member report identifies its roster `repository`, inspected `revision`, discovered `policyVersion`, verified `checkerRevision` and `checkerRepository`, and central `records` identity. `records.revision` identifies the Git commit when available; `records.digest` covers the complete project, pin, roster, and support records. `records.legacyDigest` covers only projects and pins, matching historical checkers. The dispatcher validates returned project, release, member revision, record revision/digest, report types, outcome, and new-contract member settings. Substituted or malformed reports produce inspection errors. New-contract `policyRecordsDigest` includes the roster and support decisions; an older checker's digest retains its historical scope. A changed central snapshot fails the audit, including an initially empty roster.

A static passing assessment retains `compatibility: "not-run"`; audits do not claim to rerun member CI. Candidate-only results remain visible as noncompliant with their original `assessment`; they do not establish approved-pin compliance. Member failures remain separate from inspection errors, and an aggregate inspection error takes precedence. Audit errors print JSON on standard output so the scheduled/manual workflow retains a report even when initial records cannot be processed. Artifact upload cannot turn the failed audit command into success.

The GitHub audit checks squash-only merging, a PR requirement, and configured required status names through rulesets or branch protection. For members selecting this release, it compares GitHub settings against the generated ordinary, compatibility, applicable VM, and additional project gates. For another selected release from v0.3.0 onward, it uses that checker's `requiredChecks` report; missing or malformed names produce an inspection error. Older selected releases use their recorded names after inspection with their checker. A failed release inspection cannot supply a trusted gate set. GitHub requests always use the roster repository identity; a member declaration or report cannot redirect inspection. Public release lookup can run without a member credential; merge-setting inspection requires the credential below. When REST omits a merge-method flag or returns a non-boolean value, the checker reads all three settings through [GraphQL](https://docs.github.com/en/graphql/reference/repos#repository). Incomplete responses or GraphQL errors leave the settings unknown and produce an inspection error. Required status names do not attest the workflow implementation. Bypass lists, second-person review settings, full permission inventories, and release publication configuration still need setup review.

GitHub audits of enrolled members require the [read-only member credential](maintenance.md#audit-access). A missing credential or an inaccessible API response produces `error` for the member and the audit, with exit 2; the report remains available for inspection. HTTP 401, 403, and 404 are not evidence that protection is absent. Rulesets that establish every required gate need no classic-protection lookup. Otherwise the checker reads branch metadata: `protected: false` establishes absent protection, while a protected branch requires readable classic settings to resolve the remaining gates. An ambiguous 404 for a protected branch leaves those settings unknown, including when incomplete rulesets exist without classic protection.

The reusable workflow runs policy checks, external formatting/Nix lint, and ordinary committed-lock checks on every required member architecture. Both shared-pin compatibility channels use the same required member architectures, and member-declared VM targets run on x86_64 Linux. The lint command checks discovered first-party Nix files using the project's shell and runs root formatting in a disposable copy. Additional generated-source exclusions require a documented checker extension before enrollment. Actual functional coverage, use of the overridden input by tests, formatter language coverage, Nix/plugin ABI compatibility, and VM separation must be verified when a project enrolls. A visible root `systems` binding, explicit flake outputs, applicable language tools, architecture, NixOS option semantics, prose quality, and justified suppressions remain review criteria.

## CI integration

Use [templates/policy-caller.yml](../templates/policy-caller.yml) with the same exact published policy release tag in both version placeholders and set the member name. The reusable workflow checks out caller code and checker code separately. A preliminary job verifies the release and captures the release commit, current `main` record commit, and member commit; all jobs use those snapshots. This lets approvals and rollout state change independently of the selected policy release. The policy repository's `main` branch needs the agreed human and CI merge controls before activation.

The caller's `name: Policy` supplies the common status prefix. The release defines the job names once and the checker expands `<architecture>` using the member's `required_architectures` declaration. Run `ci PATH --project NAME` with current records to obtain the exact required names, including additional project checks, and runner matrices. The mandatory names are:

| Status                                                | Required for                      |
| ----------------------------------------------------- | --------------------------------- |
| `Policy / Verify policy version and load shared pins` | Every enrolled member             |
| `Policy / Compliance (<architecture>)`                | Each required member architecture |
| `Policy / Formatting and lint (<architecture>)`       | Each required member architecture |
| `Policy / Project tests (<architecture>)`             | Each required member architecture |
| `Policy / Compatibility (stable, <architecture>)`     | Each required member architecture |
| `Policy / Compatibility (unstable, <architecture>)`   | Each required member architecture |
| `Policy / VM tests (x86_64-linux)`                    | Members with declared VM targets  |

The first job is architecture-independent: it verifies the immutable policy release, captures the shared-pin records and member declaration, and runs the selected checker to generate the member's matrices from those snapshots. It runs once on an x86_64 runner, even when ordinary member checks require only ARM. The runner hosts the metadata checks; it does not add x86_64 to the member's required architectures. This job does not publish releases.

Compliance runs the structural, pin, caller, and shell checks, plus PR-title validation on pull requests. Formatting and lint runs `lint`; project tests run root `nix flake check`. The three categories and both compatibility channels produce independent jobs for every required architecture through generated matrices with `fail-fast: false`. Each category, the compatibility jobs, and the VM job depend only on the initial snapshot job, so a failure in one category does not prevent the others from running. A member without VM targets receives a successful `not-applicable` VM result and need not require that status. When targets exist, the x86_64 VM gate remains required regardless of the architectures selected for ordinary checks.

Standard PR checkout tests GitHub's candidate merge commit, which is the clean commit a candidate batch must register. Registering that commit changes only the central records; it does not change the member's pinned workflow reference or add a temporary batch field to its caller. After the record PR merges, rerun the member PR checks to capture the new record snapshot. Once the pair is approved, ordinary merge-commit checks use the approved baseline. Caller changes remain subject to human review and drift audits.

The workflow uses read-only repository permissions and does not persist checkout credentials. Cross-repository update PR creation needs a separately reviewed automation identity; normal policy checks do not require those write credentials. The action inputs were checked against the [checkout definition](https://github.com/actions/checkout/blob/11d5960a326750d5838078e36cf38b85af677262/action.yml) and [Nix installation action](https://github.com/cachix/install-nix-action/blob/b85815f71a6de0ddee80b8a80a98d43f4bcc66c7/action.yml).

The reusable workflow invokes `check --shell`. Full checks use the member declaration before and after enrollment. A passing check, candidate result, and enrollment are reported separately; successful CI does not enroll a member or approve a pin pair.
