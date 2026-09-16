# Preparation status

## First member migration

On 2026-09-16, the maintainer selected `nixos-cross-config` as the first migration. Its [migration record](cross-config-adoption.md) tracks the implementation, validation, and remaining activation work. The policy revision is recorded while adoption remains pending; the other six member repositories remain unchanged.

The policy repository is now published at `6208eb8c11a338a96e07002dc45696a5e32abad8`. Its current default branch is `docs/agent-skills`, and `main` is absent. The reusable workflow needs a reviewed `main` for central records before member CI can run. The sections below retain the original preparation and review history.

## Scope

Prepare `nixos-project-policy` as a new sibling repository. Q35 adopted the coding/documentation baseline, Q36 adopted formatting for applicable first-party languages, and Q37 deferred member migrations. The [interview record](normalization-design.md) preserves all accepted decisions.

## Prepared

- Portable normative rules, contributor and agent instructions, glossary, ADRs, and the full design audit.
- An independent root flake and exact bootstrap lock, root direnv activation, stable development tools, formatters, and ordinary checks.
- A policy checker with behavioral tests for lock resolution, pin transitions, CI callers, documentation structure, and adoption reporting.
- Central records that explicitly leave all members pending and the shared pin baseline unapproved.
- Pinned GitHub workflow definitions for this repository's CI, reusable member checks, daily drift reports, and weekly candidate artifacts.
- A member caller template for later adoption.

The existing workspace document paths forward to the moved notes. Members keep their existing source, lockfiles, and repository settings.

## Deferred

No GitHub repository has been published or configured by this preparation. Required hosted checks, two-architecture execution on GitHub, release publication automation, read-only member audit credentials, cross-repository update PR credentials, member enrollment, and the first approved family baseline remain future work. The initial maintenance job prepares review artifacts and performs read-only audits; it does not update member locks or merge changes.

Select member migrations after reviewing this repository. The earlier nix-libnet pilot order remains a recommendation in the historical notes, not an accepted instruction.

## Validation

Local verification on 2026-09-16:

| Verification                                            | Result                                                                                                                                                                                      |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nix flake check --no-update-lock-file` on x86_64 Linux | Passed: 34 behavioral tests, record validation, formatting, statix, deadnix, Ruff, and actionlint.                                                                                          |
| Both Linux flake outputs                                | Evaluated successfully; aarch64 builds and execution still require the declared CI runner.                                                                                                  |
| Root direnv activation                                  | `direnv allow` and `direnv exec . nixos-project-policy validate` passed with the host's built-in flake integration.                                                                         |
| Common tools and formatter                              | The `shell` probe passed with an isolated inherited environment.                                                                                                                            |
| External lint and formatting                            | The `lint` command passed against a temporary copy of this repository.                                                                                                                      |
| Lock update guard                                       | A controlled fixture confirmed that `--no-update-lock-file` alone rejects a required update; adding `--no-write-lock-file` bypasses that rejection in the tested Nix 2.34.6 implementation. |
| Family audit                                            | Reported all seven projects as pending adoption and found no member dependency cycle in the inspected lock graphs.                                                                          |
| Existing members                                        | All seven HEADs and worktree statuses matched the initial snapshot, including pre-existing untracked files.                                                                                 |
| Documentation links                                     | Local targets, Markdown anchors, and workspace forwarding notes were checked.                                                                                                               |

This is a local Git repository with prepared files; no commit, push, or GitHub publication was performed. Hosted execution, required-check settings, and release publication remain deferred. The policy repository's own locked tools support preparation without marking the family baseline approved.

## Review remediation

The 2026-09-16 review fixes separate explicit enrollment readiness from compliance, validate every non-null policy revision as immutable, refresh title checks on PR edits, and reject caller dependencies. The maintenance workflow accepts a dedicated member audit credential and reports inaccessible settings separately from verified missing gates. Record validation and pin selection share one definition of active batch states.

The updated suite passes 51 behavioral tests, including command-level adoption and record-loading regressions, caller-trigger checks, and simulated GitHub authorization failures. `nix fmt --no-update-lock-file` and `nix flake check --no-update-lock-file --print-build-logs` passed on x86_64 Linux; the latter ran all three check derivations for tests, formatting, and lint. The original preparation and setup additions were preserved. Hosted GitHub verification, credential provisioning, and aarch64 execution remain deferred.

## Nix skill review

The supplied `writing-nix-code` skill was reviewed against all three Nix files and the Nix invocations in Python, shell integration, GitHub workflows, and documentation on 2026-09-16.

| Skill area               | Review result                                                                                                                                                                                                        |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Modern CLI               | Existing invocations use modern `nix` subcommands. The policy now explicitly names those commands and prohibits introducing legacy invocations.                                                                      |
| Outputs and dependencies | Root outputs use the standard namespaces; both nixpkgs inputs have exact locks. Package and formatter functions declare their dependencies and use `pkgs.callPackage`. No non-flake fetchers are present.            |
| Names and functions      | The check constructor now uses named arguments, its callers precede its implementation, and the source directory is named `sourceDir`. Module scope uses qualified references and alphabetized explicit inheritance. |
| Module boundaries        | The root flake composes the internal package and formatter functions; neither depends on the root output API. There are no NixOS/Home Manager option declarations or argument-taking module imports here.            |
| Preferences              | Plain Nix composition remains an explicit departure from the flake-parts preference. No transformation pipeline needs `lib.pipe`; the internal package files and root aggregator do not need public-module headers.  |
| Tests and formatting     | The existing behavioral suite spans the Python checker; there are no Nix unit-test modules to relocate. Nix formatting uses the locked nixfmt through root `nix fmt`.                                                |

See [Nix conventions](development.md#nix-conventions) for the local choices and the limited host-platform probe. This review covers the policy repository; member repositories remain outside the current migration scope. Passing lint does not establish compliance with every design or naming convention in the skill.
