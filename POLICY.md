# Shared project policy

This document defines the shared requirements. The [design](docs/normalization-design.md) explains their architecture and rationale. Member projects link to this document at their selected policy release. The v0.4.0 contract is prepared and must be published before members select it. Members own release selection and settings in their policy caller; central records own the [enrolled-member roster](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/members.json) and [pin approval](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/pins.json). [Legacy project records](https://github.com/petohorvath/nixos-project-policy/blob/main/policy/projects.json) remain available to supported older checkers. Copies bundled with a release are historical snapshots.

## Independence and scope

Each member remains independently usable and releases separately. Each owns its root flake, shell, formatter, tests, and lockfiles. Keep the policy repository out of member flake inputs, Nix imports, development shells, and builds. A small member GitHub Actions workflow may call a pinned policy workflow. Keep project-specific contribution and agent instructions local and link to the approved shared rules.

The rules for tools, pins, entrypoints, lint, and CI apply to enrolled projects as a whole. Coding and documentation style applies to new and changed first-party material. Preserve vendored code, generated output, upstream package metadata, and third-party notices. Declare exclusions and technical overrides with their reason and scope; review them as part of adoption.

## Development

Provide a default `devShells` output in root `flake.nix`, with root `.envrc` activation. Document host Nix, enabled flake commands, direnv with flake support, and shell integration. The flake integration may come from direnv itself or nix-direnv. After those prerequisites, `direnv allow` must provide the normal development environment. Keep specialized host requirements explicit; installing packages cannot supply KVM or namespace permissions.

Support development on `x86_64-linux` and `aarch64-linux`. Preserve existing Darwin outputs as best effort and document their lack of required CI coverage. Runtime support remains part of each project's public contract.

Keep an easy-to-find `systems` binding in root `flake.nix`. Declare the applicable top-level outputs explicitly there, including `devShells`, `formatter`, `checks`, `packages`, and library or module outputs that the project provides. Local helpers may implement them, but must not hide the flake interface behind an imported aggregate. Do not add empty outputs or artificial packages solely to populate namespaces.

The default shell supplies Nix CLI, nil, nixfmt, statix, deadnix, the applicable formatters, and project-specific tools. Common tools, the shell, and the formatter come from the member's selected root nixpkgs revision. Document technical replacements, including Shields' matched Nix/plugin wrapper. The effective executable and its compatibility matter when a wrapper replaces a stock tool.

Include language-specific tools only for first-party code that needs them. For example, Ruff belongs in projects with Python sources. Remove obsolete tools and formatter configuration when their sources are removed.

Root `nix fmt` formats applicable first-party Nix, Go, shell, Markdown, YAML, and JSON files. Include `.envrc` and applicable extensionless scripts. Projects own their formatter configuration; retain useful project-specific conventions and explicit formatter ordering. Exclude vendor/generated files, lockfiles, and fixtures whose exact serialized text is part of a test. Preserve existing Markdown wrapping during focused edits and use one source line per paragraph in new prose files.

## Dependencies and compatibility

Keep a committed root `flake.lock` with an immutable `NixOS/nixpkgs` selection. The root input named `nixpkgs` is the member's selected dependency and may use a revision outside the shared pins, including an unstable update source. Development, formatting, and ordinary builds use that selection. External consumers may override nixpkgs; historical releases retain their original locks.

Required compatibility coverage uses the shared stable and unstable pins on every architecture in the member's `required_architectures` workflow input. The policy-owned runner selects one exact pair from trusted central records, verifies the effective root input through Nix metadata, and runs the member's full root `nix flake check` with `--override-input nixpkgs`. It must find nonempty host checks and preserve the member lockfile. Metadata resolution alone is not a compatibility result. The ordinary committed-lock check runs separately with `--no-update-lock-file` and no overrides. VM suites remain separate. Review must establish that the checks exercise the selected input and cover the member's promised behavior; a nonempty interface alone cannot prove meaningful coverage.

The independent-selection rule applies only to the root `nixpkgs` input and the lock node it resolves to, including through `follows`. Additional root nixpkgs inputs, distinct transitive nixpkgs nodes, and independently locked first-party examples still require one allowed shared-pin pair across the project. In those scopes, stable inputs are named `nixpkgs` and unstable inputs `nixpkgs-unstable`; absent channels need not be added. Third-party input names and lock node identifiers remain unrestricted. Do not add a second input or compatibility flake just to hold the two test revisions. Members still selecting a 0.1.x policy release retain that release's whole-project pin and naming rules.

Keep dependencies between members acyclic, including test and tooling dependencies. Put integration tests in the higher-level consumer or a separate integration project. Review source-level imports as well as lock graphs. Normally consume member releases at tags locked to exact commits. Track temporary commits needed for changes spanning projects and replace them with released dependencies through PRs.

Prepare weekly pin candidates, with urgent candidates when needed. A routine central PR proposes the pair and future approval together, validates every enrolled member with trusted checking authority, and changes approval only through human-reviewed merge. Keep normal member CI on current approval while the PR is open. A root-only routine batch can become complete in that same PR without member lock, version, or caller changes. Track actual member fixes and separate merges as a batch, including temporary differences and exact tested revisions. Candidate compatibility uses the registered pair through the policy runner; the separate default check uses committed locks. Older-policy members still test proposed committed lock updates. See the [maintenance procedure](docs/maintenance.md).

When a regression appears during rollout, pause further update merges. Prefer an understood, tested fix on the new pins. Retain rollback for unacceptable impact or an uncertain repair. Both paths require checks and human merge approval. A change to either candidate revision requires renewed validation across affected projects.

## Nix code and tests

Use standard flake output namespaces: `lib`, `nixosModules`, `homeManagerModules`, `packages`, and `devShells` for the corresponding interfaces. Build from declared dependencies, locked flake inputs, and hashed non-flake fetches. Package derivations declare their dependencies through arguments and follow `pkgs.callPackage` conventions. Preserve intentionally pure library interfaces. Existing flake frameworks may remain; a shared framework is not required.

Use the modern `nix` CLI in scripts, CI, and documentation: `nix build`, `nix eval`, `nix develop`, `nix shell`, `nix run`, `nix fmt`, and `nix flake ...`. Do not introduce legacy command invocations such as `nix-build`, `nix-instantiate`, or `nix-shell`. Examples that identify or explain replacing a legacy command may name it. Review command usage as well as Nix expressions; the current checker does not parse every embedded shell command.

Give NixOS options types and descriptions. Provide appropriate defaults, or document why an option must be supplied. Use `mkEnableOption`, `mkIf`, `mkMerge`, and `mkDefault` for their intended roles. Keep internal dependencies directed toward lower-level implementations and access external dependencies through their public APIs.

Use consistent names, explicit module-scope references, and argument shapes suited to the function. Comments explain reasons that the code does not make clear. Architecture and interface semantics require review; text matching alone cannot establish compliance.

Register meaningful tests for behavior changes and keep their organization aligned with the code. Preserve existing test and coverage commitments unless explicitly changed through compatibility review. The family does not mandate TDD, one test framework, or a universal coverage percentage.

Benchmarks are optional and require a concrete project need; policy adoption does not require a benchmark suite or a `dev/` directory. Add or retain performance tooling only when it serves the project's agreed scope.

Fix real statix and deadnix findings before requiring their gates. Narrow, documented suppressions may cover intentional code or false positives. Broad disabling is not compliance.

Root `nix flake check` runs applicable non-VM checks for the host platform. Provide nonempty host checks under the committed lock as well as under the shared compatibility pins. Keep VM execution and its build dependencies outside default checks. Provide explicit VM commands where applicable. Each member declares `required_architectures` in its policy caller as a nonempty subset of the policy release's supported Linux systems. Required CI covers formatting/lint and relevant evaluation/unit/integration checks on every recorded architecture, with pinned stable and unstable compatibility where relevant. Applicable VM suites gate merges on x86_64 Linux independently of that selection. Documentation-only PRs may use relevant documentation, formatting, and policy checks.

## Documentation and agent guidance

Start the README with the project's purpose. Include support/status, quickstart, and links to development, contribution, and detailed documentation. The initial checker uses `Support`, `Quickstart`, `Development`, `Contributing`, and `Documentation` headings. Detailed guides and reference material follow the project's needs. Create glossaries and ADRs when meaningful terminology or architectural decisions need recording.

Write direct explanations with clear subjects, useful examples, and concise paragraphs. Tag fenced code blocks with their language. Keep project instructions accurate and agent guidance concise, pointing to authoritative rules when needed. A contributor must not need this maintainer's global skills or workstation paths to understand the policy.

Keep documentation focused on current usage, design, structure, and architectural decisions. Keep a changelog for release-facing changes and migration notes. Use Git commits, issues, PRs, and CI results for implementation history and validation evidence; do not duplicate them in dated progress logs, completed implementation plans, or separate review and validation reports. Central machine-readable enrollment, legacy compatibility, and pin records remain operational state.

## Changes, releases, and licenses

Use PRs for all changes. Main receives one Conventional Commit per PR through squash merge; use the PR title as the squash subject and mark breaking changes explicitly. Intermediate branch commits may be edited before merge. Additional title casing and length limits are not mandatory gates.

A human must approve every merge initially. The maintainer may self-merge after required checks; a second human reviewer is not required. Agents do not acquire merge authority from passing CI. Change that authority only through a later explicit policy decision.

A human may record an exception for an urgent change during a CI infrastructure outage. Record the reason, checks completed, and checks still owed on the PR, then run missing checks after recovery. Known code/test failures do not qualify. Agents have no bypass authority.

Use independent Semantic Versioning tags and changelogs. Declare public contracts, including Nix APIs, NixOS options/defaults, and daemon interfaces. At 0.x, breaking changes require a minor bump; patch releases remain compatible. Record migration notes for public contract changes and decide readiness for 1.0 explicitly. Released versions are immutable.

A release PR specifies its version, changelog, and migration notes. Its human merge authorizes publication only after checks pass on the release commit. Ordinary merges do not publish releases. Preserve existing release tags and reconcile inaccurate release-history wording during later migrations.

Use MIT for the original code in nix-nftypes, nixos-cross-config, nixos-nftzones, nixos-registry, and this policy repository. Preserve existing member copyright and license notices, third-party notices, and inherited upstream package license metadata.

## Enforcement and policy changes

Keep checking code and the approved pin record here. Run common checks locally from this repository and in member CI through an immutable reusable-workflow reference. Require the relevant results in GitHub merge gates and audit pins, policy references, documentation structure, dependency direction, and CI configuration for drift.

Name the member caller job `Policy`. The selected release defines mandatory status names and architecture-specific job names in `policy/requirements.json`: verification of the policy release and shared pins, plus separate compliance, formatting/lint, committed-lock project tests, and stable/unstable compatibility jobs on every declared required architecture. Derive matrices and required names from the same validated member settings. Applicable VM targets require a separate x86_64 status, including for ARM-only ordinary coverage. Keep these jobs independent after the shared source, release, and record snapshots. Additional project gates supplement the minimum and do not create jobs. The [checker reference](docs/checker.md#ci-integration) lists the names.

Select policy releases through exact published immutable `vMAJOR.MINOR.PATCH` tags. Declare exactly one reusable-workflow caller; its `uses` reference, `policy_version` input, documentation links, and executing checker must agree. Branches, abbreviated versions, prereleases, and bare commit references do not select a release. Publish releases with GitHub release immutability enabled; never move or reuse a released version.

The caller retains the literal `project` identity and requires `required_architectures`, a nonempty JSON list encoded as a string. Optional `vm_targets` and `additional_required_checks` use the same encoding and default to empty lists. Architectures must belong to the selected release's supported systems. Settings cannot be dynamic expressions or arbitrary policy overrides. Local commands and hosted jobs use the same declaration validation. Review must explicitly justify reductions in architecture, VM, or additional gate coverage; passing checks cannot establish that review.

The selected release supplies written rules, tools, documentation requirements, supported systems, mandatory check categories and names, checker code, and workflow behavior. Current central records supply approved shared pins, pin update batches, and member enrollment. Members own their selected release and settings. A reviewed member PR authorizes an ordinary policy upgrade without repeating its selection, settings, or activation in central records. A valid checkout can run all applicable checks before enrollment; reports identify enrollment separately, and successful checks neither enroll a member nor approve pins.

The central roster records enrolled repository identities only. Additions and removals require reviewed central changes; enrollment plans belong in issues. Independent audits inspect every enrolled identity at an exact clean revision, discover its selected published immutable release, and use that release's checker and gate contract. Missing or disabled enforcement remains a failed enrolled assessment. Member declarations cannot redirect repository inspection or replace policy minimum gates. Keep selection, enrollment, and compliance distinct in reports.

Capture one member commit, checker commit, and central-record commit for all jobs in a CI run. Static checks validate the independently selected root dependency and compare other lock scopes against applicable shared pins. Compatibility jobs test the selected recorded pair and report execution separately from static validation. Local member checks explicitly select a trusted record checkout with `--policy-root`.

An integration project requires its exact consumed enrolled member revisions to select its own supported policy release. Inspect committed dependency graphs, including reachable transitive members, follows relationships, and independent lock scopes; current member branches and legacy version copies do not describe those sources. Require the separate `Integration / Policy agreement` status through the existing additional-check declaration and matching agreement caller, using the primary Policy workflow's captured source and record revisions. Keep behavioral integration tests in ordinary root checks. A matching policy selection does not establish functional compatibility, and locked member dependencies must remain acyclic. Upgrade the integration selection and complete matching dependency set together when needed; independent member branches need not upgrade simultaneously.

Retain the complete legacy record surface while supported immutable older checkers require it, including adopted entries' full check lists and identities referenced by historical batches. A migrated member's current declaration does not rewrite those compatibility records. Publishing a newer release does not retire an older one. Record an explicit retirement in `policy/support.json` with a reviewed decision, reason, migration start, and effective retirement time. New-contract local checks, CI, and central audits share that support assessment; an enrolled retired selection remains a visible compliance failure. Immutable older checkers retain their original behavior. Remove obsolete legacy records only after affected old releases, including patch releases, have effectively retired and their members have migrated. Validate cleanup against a trusted prior snapshot and exact member revisions; keep historical batch references while any legacy consumer needs them.

Change shared pins through a pin update batch without changing the policy version or member workflow references. Changes to rules, checker behavior, workflows, or their public interfaces require a new policy release before members use them. Keep current record schemas compatible with supported older policy releases; coordinate retirement and migration before an incompatible record change.

Human review covers architecture, prose, public compatibility, caller changes, and limitations of mechanical checks. Required status names alone do not prove which workflow code ran. The [checker reference](docs/checker.md) identifies implemented checks and remaining review responsibilities.

Prepare and publish changed requirements as a new release. Members upgrade independently through reviewed member PRs; enrollment and member migration scope require explicit selection. The policy repository does not authorize changes to member code or live merge settings.
