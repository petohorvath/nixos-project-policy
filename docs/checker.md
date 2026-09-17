# Checker reference

The checker runs from a selected policy release or its packaged `nixos-project-policy` executable. `--version` reports its version. The global `--policy-root PATH` option selects a trusted checkout of current records and is required for `check`, `audit`, and `vm`. These commands never silently use a release's historical pin snapshot. Other commands default to bundled records when no path is given. Passing project tests alone does not establish family compliance.

## Commands

| Command after `nix run .# -- --policy-root .` | Behavior                                                                                                                                    |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `validate`                                    | Validate the policy records; report whether approved pins exist. This is not an adoption check.                                             |
| `check PATH --project NAME`                   | Inspect a registered member checkout and fail on missing requirements or an unapproved baseline.                                            |
| `check PATH --project NAME --readiness`       | Validate enrollment before adoption; a clean pending member reports `ready`, never compliance.                                              |
| `check PATH --project NAME --shell`           | Also execute the member shell with a cleared inherited environment, probe required tools, and evaluate its root formatter.                  |
| `check PATH --project NAME --batch ID`        | Test a registered candidate only at the exact project commit in that batch.                                                                 |
| `lint PATH`                                   | Execute statix, deadnix, and root formatting in a temporary source copy; report required formatting changes without modifying the checkout. |
| `audit WORKSPACE`                             | Read available member checkouts; distinguish failed compliance from pending adoption and report dependency cycles.                          |
| `audit WORKSPACE --fetch --github`            | Clone missing public checkouts and inspect enrolled members' merge settings and required checks. Existing local checkouts are not updated.  |
| `vm PATH --project NAME`                      | Execute the centrally declared VM targets; report `not-applicable` when the member has none. Requires a suitable builder.                   |
| `candidate --stable COMMIT --unstable COMMIT` | Emit an unapproved pair of exact commits. It neither writes locks nor registers or approves a batch.                                        |
| `title TITLE`                                 | Validate Conventional Commit PR-title syntax.                                                                                               |

Commands print JSON with `checkerVersion`, `policyRecordsRevision` when Git metadata is available, and `policyRecordsDigest` for the records actually used. Member reports also identify their registered `policyVersion`. Exit 0 means the requested operation succeeded; readiness checks, candidate validation, and audits of pending members can succeed without establishing compliance. Exit 1 means enforced checks failed. Exit 2 means the request, record, or inspection could not be processed. Reports include checked project commits when Git metadata is available. Reusable CI separately logs the exact checker and record checkout revisions.

After publication, a selected release can check a member against a separate current records checkout:

```bash
nix run github:petohorvath/nixos-project-policy/v0.1.0 -- \
  --policy-root ../nixos-project-policy-records \
  check ../nixos-cross-config --project nixos-cross-config --readiness
```

Update that trusted records checkout from `main` before a current compliance check. Use a previously captured record commit only when reproducing an earlier result. Local commands do not fetch records or prove that a checkout is current. A direct `check` requires the executing checker version to match the member's registered policy release. Audits invoke a different selected release through `nix run` when an enrolled member needs it, passing the same current records checkout. Pending members are assessed with the executing checker for enrollment preparation. Release lookup or execution failures are inspection errors. Each member's CI uses its selected release.

A normal `check` fails while adoption is pending. `--readiness` allows enrollment validation and returns `ready` when all inspected requirements pass against approved pins. It does not waive pin approval, the recorded policy revision, caller checks, or shell checks. Registered candidates return `candidate-ready`, with or without `--batch`; pending members still need `--readiness`. Only an adopted member checked against an approved baseline or allowed rollout pair can return `pass`. `--shell` preserves these distinctions. An audit reports pending members as `pending-adoption` with their readiness result in `assessment`.

`nix run .# -- shell PATH` probes only the common tools and root formatter, without asserting member compliance. The policy repository runs this host-level smoke test in its own CI. Tool probes use supported help/version commands; statix does not expose a `--version` flag at the bootstrap pin.

CI and runtime probes use `--no-update-lock-file` by itself to reject a required lock update. Do not combine it with `--no-write-lock-file`: the Nix 2.34.6 source and a controlled local fixture show that disabling writes also bypasses the update rejection, allowing an in-memory replacement lock. See the [locking implementation](https://github.com/NixOS/nix/blob/2.34.6/src/libflake/flake.cc#L749-L825). Checks must use committed dependency selections.

## Central records

`policy/projects.json` uses schema version 2 and records project identities, the stable update branch, VM targets, adoption state, and selected policy releases. Every non-null `policyVersion` must be an exact `vMAJOR.MINOR.PATCH` release tag, including versions prepared for pending members. Branch names, abbreviated versions, prereleases, and commit hashes are rejected. A pending member may leave it null until enrollment preparation, but cannot pass readiness checks without one. Enrolled projects also need `requiredChecks`, the exact GitHub status names verified during adoption. A pending project has `adopted: false`; that state never means compliance.

`policy/requirements.json` uses schema version 1 and belongs to the checker release. It defines required tools, Linux systems, and README headings. The checker reads this file beside its own code, even when `--policy-root` selects different current records. Placing these requirements back in current project records is rejected, so a record change cannot silently change a release's rules.

`policy/pins.json` uses schema version 1 and contains `approved`, either null or an exact stable/unstable pair, and `batches`. Initially no pair is approved. The policy repository's own bootstrap lock does not change that state. Pin changes use the same checker release with new current records and member locks.

A batch records its ID, state, proposed `pins`, optional `previous` pair, and a `projects` map from affected member names to exact tested commits. States are `candidate`, `approved`, `rolling`, `paused`, `complete`, and `withdrawn`. Pair values contain only exact stable and unstable commit strings. The checker automatically selects the unique candidate registered for the matching clean source commit and names it in `candidateBatch`; `--batch` explicitly requests one for local testing. A caller cannot supply arbitrary approved pins. An active rollout must remain tied to the central approved baseline.

During an approved, rolling, or paused batch, affected projects may use its old or new pair. All applicable locks within one project must select one allowed pair. Completed or withdrawn batches grant no extra allowance. Review remains responsible for recording real test evidence and the human approval behind each state change.

## Implemented coverage

The checker resolves version-7 lock graphs, including root-relative `follows`, and inspects reachable nixpkgs nodes across discovered first-party lockfiles. It ignores unreachable old nodes and vendor/cache directories. It recognizes GitHub repository identities from GitHub inputs and Git URLs, checks exact approved revisions, flags ambiguous nixpkgs sources, rejects a policy repository flake dependency, and builds the member dependency graph. Arbitrary fetch expressions, indirect source imports, and unsupported source transports require review; a lock graph is not a complete source-level architecture audit.

Each first-party lock's root inputs must name stable nixpkgs `nixpkgs` and unstable `nixpkgs-unstable`, including inputs resolved through `follows`. Lock node identifiers and transitive input names remain unrestricted. An absent channel need not be added. Branch declarations identify channels; an exact revision can identify one when it matches a single channel in the allowed pin pairs. Source review also covers first-party flakes without independent locks.

Structural checks cover the root development entrypoint, required documentation files, README sections, and links to the selected release's `POLICY.md`. Caller checks require the registered release tag in the reusable-workflow reference, with a matching `policy_version` input. The PR trigger must declare exactly `types: [opened, synchronize, reopened, edited]`, in any order, so title edits refresh the Conventional Commit result. Branch, path, and other trigger filters remain unsupported. The caller job must have no `if` condition or `needs` dependencies: a skipped prerequisite would prevent it from running. The initial caller intentionally runs for every PR; optimized documentation-only job selection can be added with tests later.

The reusable workflow verifies through GitHub that the selected release is published, immutable, and not a prerelease. It checks out the exact tag, checks that `VERSION` matches, and captures its commit alongside the current `main` record commit. All policy and VM jobs use those two captured commits. A missing release, mutable release, unavailable API response, or version mismatch stops the workflow. Local structural checks do not attest remote publication or immutability.

The GitHub audit checks squash-only merging, a PR requirement, and configured required status names through rulesets or branch protection. Required status names do not attest the workflow implementation. Bypass lists, second-person review settings, full permission inventories, and release publication configuration still need setup review.

GitHub audits of adopted members require the [read-only member credential](maintenance.md#audit-access). A missing credential or an inaccessible API response produces `error` for the member and the audit, with exit 2; the report remains available for inspection. HTTP 401, 403, and 404 are not evidence that protection is absent. Rulesets that establish every required gate need no classic-protection lookup. Otherwise the checker reads branch metadata: `protected: false` establishes absent protection, while a protected branch requires readable classic settings to resolve the remaining gates. An ambiguous 404 for a protected branch leaves those settings unknown, including when incomplete rulesets exist without classic protection.

The reusable workflow runs policy checks, external formatting/Nix lint, and ordinary project checks on both Linux architectures, and centrally declared VM targets on x86_64 Linux. The lint command checks discovered first-party Nix files using the project's shell and runs root formatting in a disposable copy. Additional generated-source exclusions require a documented checker extension before enrollment. Actual functional coverage, supported stable/unstable test targets, formatter language coverage, Nix/plugin ABI compatibility, and VM separation must be verified when a project enrolls. A visible root `systems` binding, explicit flake outputs, applicable language tools, architecture, NixOS option semantics, prose quality, and justified suppressions remain review criteria.

## CI integration

Use [templates/policy-caller.yml](../templates/policy-caller.yml) with the same exact published policy release tag in both version placeholders and set the member name. The reusable workflow checks out caller code and checker code separately. A preliminary job verifies the release and captures the release commit and current `main` record commit; all jobs use those snapshots. This lets approvals and rollout state change independently of the selected policy release. The policy repository's `main` branch needs the agreed human and CI merge controls before activation.

Standard PR checkout tests GitHub's candidate merge commit, which is the clean commit a candidate batch must register. Registering that commit changes only the central records; it does not change the member's pinned workflow reference or add a temporary batch field to its caller. After the record PR merges, rerun the member PR checks to capture the new record snapshot. Once the pair is approved, ordinary merge-commit checks use the approved baseline. Caller changes remain subject to human review and drift audits.

The workflow uses read-only repository permissions and does not persist checkout credentials. Cross-repository update PR creation needs a separately reviewed automation identity; normal policy checks do not require those write credentials. The action inputs were checked against the [checkout definition](https://github.com/actions/checkout/blob/11d5960a326750d5838078e36cf38b85af677262/action.yml) and [Nix installation action](https://github.com/cachix/install-nix-action/blob/b85815f71a6de0ddee80b8a80a98d43f4bcc66c7/action.yml).

The reusable workflow invokes `check --readiness --shell` so enrollment PRs can validate before adoption. Current central records determine whether the result is `ready`, `candidate-ready`, or `pass`; the flag does not change the checks for adopted members. Passing enrollment CI does not record adoption or approve candidate pins.
