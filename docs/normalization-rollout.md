# Project normalization rollout proposal

This document applies the decisions in [rounds 1–7](normalization-design.md). Q37 authorizes preparation of `nixos-project-policy` only. Member migration selection and order will be decided afterward. The common interface and eventual completion requirements below remain the agreed target for future adoption.

## Intended result

Each project owns its root `flake.nix`, development shell, formatter configuration, project tests, and lockfiles. Root direnv activation supplies the required development tools. The project can be developed, evaluated, and built without obtaining `nixos-project-policy`.

The new sibling `nixos-project-policy` repository owns the shared written rules, approved pin records, checker implementation, reusable GitHub workflow, and maintenance automation. Member workflows call an approved immutable revision of that workflow. Member flakes do not import policy helpers or obtain nixpkgs through a policy flake input. Written project guidance links to the approved shared rules and retains local development instructions.

The expected GitHub home is `petohorvath/nixos-project-policy`, publicly accessible like the seven existing members so contributors and their CI can read the shared rules and workflow. This remote configuration belongs in the concrete repository setup review before publication; the GitHub repository has not been created.

## Project interface

| Operation                   | Interface                                    | Required behavior                                                                                                                                               |
| --------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Enter development           | Root `.envrc`; `nix develop`                 | Default shell supplies Nix CLI, nil, formatters, statix, deadnix, and declared project tools. Common tools use the stable pin unless an override is documented. |
| Format source               | Root `nix fmt`                               | Formats the agreed first-party languages using project-owned configuration and pinned tools.                                                                    |
| Run ordinary project checks | Root `nix flake check`                       | Runs applicable non-VM checks for the host platform. Shared policy code is not imported into the flake.                                                         |
| Run stable VM suites        | Proposed `nix build .#vm-tests`              | Runs all applicable scenarios against the committed stable pin on a suitable builder.                                                                           |
| Run unstable VM suites      | Proposed `nix build .#vm-tests-unstable`     | Runs all applicable scenarios against the committed unstable pin on a suitable builder.                                                                         |
| Check shared policy         | Member GitHub Actions invokes central checks | Verifies the agreed requirements and supplies a required PR result.                                                                                             |

Only projects with VM suites need VM targets. VM derivations and their aggregates must stay outside default `checks` and their build dependencies. CI explicitly runs the VM targets on suitable x86_64 Linux runners. Ordinary checks run on both supported Linux architectures, with the specialized host prerequisites configured where needed. Existing Darwin outputs remain available as best effort.

A local treefmt configuration and formatter derivation are sufficient for the accepted broader formatting interface. Existing treefmt-nix or flake-parts use may remain; other projects need not adopt either framework. Candidate tools are nixfmt for Nix, the existing ordered goimports/gofumpt setup for wanwatch, shfmt for standalone shell scripts and `.envrc`, and Prettier for Markdown, YAML, and JSON. Preserve existing Markdown wrapping where required by the prose conventions. Exclude vendor, generated artifacts, lockfiles, and byte-sensitive fixtures such as wanwatch's `state.golden.json` from generic formatting. See the [feasibility audit](normalization-audit.md#root-formatter-coverage).

## Pin update mechanics

Keep each project's actual stable and unstable revisions in its own lockfiles. The policy repository records the approved pair and any active candidate batch. A batch identifies the candidate commits, affected projects and flake directories, proposed project revisions, check results, and rollout state. The record distinguishes approval from the later completion of separate member merges.

Prepare the selected weekly candidates through coordinated PRs, with urgent candidates using the same process. Freeze the two proposed nixpkgs commits before preparing the member changes. Resolve each flake's existing input names and `follows` paths; changing those names is not required merely to share pins. Update independently owned example locks as well as root locks. A stable release-branch change also updates its input declaration.

Use a targeted lock update and compare the resolved graphs before and after. Reject unexpected changes to unrelated inputs, their original declarations, or their sharing relationships. Tests consume the proposed committed locks without overrides that could hide what the PR actually changes. The [Nix command review](normalization-audit.md#targeted-lock-update-mechanics) records the verified source behavior and the implementation checks required before automation writes member locks.

Candidate PRs are checked against their registered candidate pair; ordinary PRs are checked against the applicable approved baseline. During an approved rollout, explicitly tracked old/new combinations are expected. Approve a new pair only after all affected projects pass, require human approval for every merge, and close the batch only when adoption is complete. A regression pauses further updates and follows the accepted Q31 recovery policy. A change to the candidate pair requires renewed validation across affected projects.

Preparing cross-repository PRs requires suitable automation write access, which is separate from the read-only access needed by ordinary member policy checks. The exact updater identity and permissions must be included in the automation setup review. No additional always-on service is required for the selected PR-check model.

## Current preparation and deferred migrations

Prepare the policy repository, its standards, root development tools, tested checking implementation, and workflow definitions. Keep family pin approval and member enrollment explicit. Repository preparation does not activate required gates in the existing projects or authorize their migration.

The following member order was recommended during the interview but was not selected in Q37. It remains background for a later migration decision.

| Stage                      | Projects                                                       | Purpose                                                                                                                                     |
| -------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Foundation                 | nixos-project-policy                                           | Establish readable rules, pin records, checker tests, the reusable CI workflow, and its own root development tools.                         |
| Pilot                      | nix-libnet                                                     | Prove the common root shell, direnv, required tools, formatter interface, lint cleanup, and member CI caller.                               |
| Independent migrations     | nix-nftypes, nixos-cross-config, nixos-registry, nixos-shields | Add missing defaults and activation, move development flakes into roots, and validate parser prerequisites and the Shields Nix replacement. |
| First VM consumer          | nixos-nftzones                                                 | Validate the common interface with libnet/nftypes dependencies and explicit stable/unstable VM targets.                                     |
| Combined Go and VM project | nixos-wanwatch                                                 | Preserve Go formatting, tests, race/coverage requirements, audits, and firewall integration while adopting the common policy.               |

Any later pilot must be validated on a reviewable PR and cannot declare a family pin baseline approved before all affected projects pass. Initial adoption is tracked separately from routine updates to an established baseline. Existing noncompliance remains visible in the migration record until resolved. The all-project readiness rule for later policy changes does not prohibit preparing the initial migration in stages.

Preserve public APIs and project-specific test commitments during normalization. Keep larger architectural refactors separate unless needed to satisfy an agreed requirement. Fix real lint findings before their gates become mandatory, with narrow documented suppressions for intentional code or false positives. Include the approved license declarations, README structure, local development instructions, and shared-rule links in the relevant migration PRs.

Account for existing state: preserve the shields `v0.1.0` tag, reconcile wanwatch's release-history wording with its lack of remote tags/releases, preserve third-party license notices, correct obsolete local links, and handle stale local hooks without overwriting unrelated user work. Use tracked temporary member commits when integration work precedes a dependency release, then adopt released versions through the agreed workflow.

## Checks and completion evidence

Policy checks validate the actual shell tools and effective dependency revisions, together with the agreed file structure, reference versions, and CI configuration. Project tests continue to check functional behavior. Architecture, compatibility promises, suppressions, and prose quality remain review responsibilities. The [standards matrix](normalization-standards.md) identifies which rules are already accepted and which remain proposed.

CI reports the tested project revision, pinned checker revision, and the central-record revision captured for the run. Approval records can advance through reviewed policy PRs without changing the checker reference in each member. Reusable workflows explicitly obtain both the member source and the intended checker source. Member code execution does not receive credentials used to change repository settings or open maintenance PRs. Required status names alone do not prove which workflow implementation ran, so human review of caller changes and the scheduled drift audit remain part of the selected enforcement model.

The rollout is complete when every member has working development entrypoints on the two supported Linux architectures, required tools, an approved common pin pair in all applicable locks, working local non-VM checks, required applicable VM CI, current policy links, and the agreed contribution/release configuration. Verify required GitHub gates through a pilot PR before applying the pattern more widely. All migration PRs must pass the applicable checks and receive human merge approval; no unresolved batch drift may be presented as completed normalization.

The scheduled drift audit checks approved pins, active batches, checker references, documentation structure, and required GitHub configuration. Weekly candidate CI also provides upstream compatibility feedback; an additional duplicate upstream-tip job is not required merely to repeat those checks. Existing specialized audits remain where they provide distinct coverage.
