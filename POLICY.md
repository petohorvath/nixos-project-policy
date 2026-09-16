# Shared project policy

This document defines the shared requirements. The [design](docs/normalization-design.md) explains their architecture and rationale. Member projects link to an approved immutable revision of this document. Central [project records](policy/projects.json) and [pin records](policy/pins.json) determine adoption and pin approval.

## Independence and scope

Each member remains independently usable and releases separately. Each owns its root flake, shell, formatter, tests, and lockfiles. Keep the policy repository out of member flake inputs, Nix imports, development shells, and builds. A small member GitHub Actions workflow may call a pinned policy workflow. Keep project-specific contribution and agent instructions local and link to the approved shared rules.

The rules for tools, pins, entrypoints, lint, and CI apply to enrolled projects as a whole. Coding and documentation style applies to new and changed first-party material. Preserve vendored code, generated output, upstream package metadata, and third-party notices. Declare exclusions and technical overrides with their reason and scope; review them as part of adoption.

## Development

Provide a default `devShells` output in root `flake.nix`, with root `.envrc` activation. Document host Nix, enabled flake commands, direnv with flake support, and shell integration. The flake integration may come from direnv itself or nix-direnv. After those prerequisites, `direnv allow` must provide the normal development environment. Keep specialized host requirements explicit; installing packages cannot supply KVM or namespace permissions.

Support development on `x86_64-linux` and `aarch64-linux`. Preserve existing Darwin outputs as best effort and document their lack of required CI coverage. Runtime support remains part of each project's public contract.

Keep an easy-to-find `systems` binding in root `flake.nix`. Declare the applicable top-level outputs explicitly there, including `devShells`, `formatter`, `checks`, `packages`, and library or module outputs that the project provides. Local helpers may implement them, but must not hide the flake interface behind an imported aggregate. Do not add empty outputs or artificial packages solely to populate namespaces.

The default shell supplies Nix CLI, nil, nixfmt, statix, deadnix, the applicable formatters, and project-specific tools. Common tools come from the shared stable pin. Document needed unstable overrides and technical replacements, including Shields' matched Nix/plugin wrapper. The effective executable and its compatibility matter when a wrapper replaces a stock tool.

Include language-specific tools only for first-party code that needs them. For example, Ruff belongs in projects with Python sources. Remove obsolete tools and formatter configuration when their sources are removed.

Root `nix fmt` formats applicable first-party Nix, Go, shell, Markdown, YAML, and JSON files. Include `.envrc` and applicable extensionless scripts. Projects own their formatter configuration; retain useful project-specific conventions and explicit formatter ordering. Exclude vendor/generated files, lockfiles, and fixtures whose exact serialized text is part of a test. Preserve existing Markdown wrapping during focused edits and use one source line per paragraph in new prose files.

## Dependencies and compatibility

Use the exact approved stable and unstable nixpkgs revisions wherever a project selects those inputs, including development, checks, builds, and independently locked examples. Both stable and unstable are pinned: a branch name alone is not immutable. Projects without an input need not add it solely for conformity. External consumers may override nixpkgs; historical releases retain their original locks.

Name stable nixpkgs inputs `nixpkgs` and unstable inputs `nixpkgs-unstable`. Apply this convention to each first-party flake, including independently locked examples and root inputs that use `follows`. Lock node identifiers and input names owned by third-party dependencies need not match these names. Internal channel labels such as `stable` and `unstable` may still identify checks and pin records.

Keep dependencies between members acyclic, including test and tooling dependencies. Put integration tests in the higher-level consumer or a separate integration project. Review source-level imports as well as lock graphs. Normally consume member releases at tags locked to exact commits. Track temporary commits needed for changes spanning projects and replace them with released dependencies through PRs.

Prepare weekly pin candidates, with urgent candidates when needed. Approve a new pair only after all affected projects pass. Track separate member merges as one batch, including temporary differences and exact tested revisions. Tests must use the proposed committed locks. See the [maintenance procedure](docs/maintenance.md).

When a regression appears during rollout, pause further update merges. Prefer an understood, tested fix on the new pins. Retain rollback for unacceptable impact or an uncertain repair. Both paths require checks and human merge approval. A change to either candidate revision requires renewed validation across affected projects.

## Nix code and tests

Use standard flake output namespaces: `lib`, `nixosModules`, `homeManagerModules`, `packages`, and `devShells` for the corresponding interfaces. Build from declared dependencies, locked flake inputs, and hashed non-flake fetches. Package derivations declare their dependencies through arguments and follow `pkgs.callPackage` conventions. Preserve intentionally pure library interfaces. Existing flake frameworks may remain; a shared framework is not required.

Use the modern `nix` CLI in scripts, CI, and documentation: `nix build`, `nix eval`, `nix develop`, `nix shell`, `nix run`, `nix fmt`, and `nix flake ...`. Do not introduce legacy command invocations such as `nix-build`, `nix-instantiate`, or `nix-shell`. Examples that identify or explain replacing a legacy command may name it. Review command usage as well as Nix expressions; the current checker does not parse every embedded shell command.

Give NixOS options types and descriptions. Provide appropriate defaults, or document why an option must be supplied. Use `mkEnableOption`, `mkIf`, `mkMerge`, and `mkDefault` for their intended roles. Keep internal dependencies directed toward lower-level implementations and access external dependencies through their public APIs.

Use consistent names, explicit module-scope references, and argument shapes suited to the function. Comments explain reasons that the code does not make clear. Architecture and interface semantics require review; text matching alone cannot establish compliance.

Register meaningful tests for behavior changes and keep their organization aligned with the code. Preserve existing test and coverage commitments unless explicitly changed through compatibility review. The family does not mandate TDD, one test framework, or a universal coverage percentage.

Benchmarks are optional and require a concrete project need; policy adoption does not require a benchmark suite or a `dev/` directory. Add or retain performance tooling only when it serves the project's agreed scope.

Fix real statix and deadnix findings before requiring their gates. Narrow, documented suppressions may cover intentional code or false positives. Broad disabling is not compliance.

Root `nix flake check` runs applicable non-VM checks for the host platform. Keep VM execution and its build dependencies outside default checks. Provide explicit VM commands where applicable. Required CI covers formatting/lint and relevant evaluation/unit/integration checks on both Linux architectures, with pinned stable and unstable compatibility where relevant. Applicable VM suites gate merges on x86_64 Linux. Documentation-only PRs may use relevant documentation, formatting, and policy checks.

## Documentation and agent guidance

Start the README with the project's purpose. Include support/status, quickstart, and links to development, contribution, and detailed documentation. The initial checker uses `Support`, `Quickstart`, `Development`, `Contributing`, and `Documentation` headings. Detailed guides and reference material follow the project's needs. Create glossaries and ADRs when meaningful terminology or architectural decisions need recording.

Write direct explanations with clear subjects, useful examples, and concise paragraphs. Tag fenced code blocks with their language. Keep project instructions accurate and agent guidance concise, pointing to authoritative rules when needed. A contributor must not need this maintainer's global skills or workstation paths to understand the policy.

Keep documentation focused on current usage, design, structure, and architectural decisions. Keep a changelog for release-facing changes and migration notes. Use Git commits, issues, PRs, and CI results for implementation history and validation evidence; do not duplicate them in dated progress logs, completed implementation plans, or separate review and validation reports. Central machine-readable adoption and pin records remain operational state.

## Changes, releases, and licenses

Use PRs for all changes. Main receives one Conventional Commit per PR through squash merge; use the PR title as the squash subject and mark breaking changes explicitly. Intermediate branch commits may be edited before merge. Additional title casing and length limits are not mandatory gates.

A human must approve every merge initially. The maintainer may self-merge after required checks; a second human reviewer is not required. Agents do not acquire merge authority from passing CI. Change that authority only through a later explicit policy decision.

A human may record an exception for an urgent change during a CI infrastructure outage. Record the reason, checks completed, and checks still owed on the PR, then run missing checks after recovery. Known code/test failures do not qualify. Agents have no bypass authority.

Use independent Semantic Versioning tags and changelogs. Declare public contracts, including Nix APIs, NixOS options/defaults, and daemon interfaces. At 0.x, breaking changes require a minor bump; patch releases remain compatible. Record migration notes for public contract changes and decide readiness for 1.0 explicitly. Released versions are immutable.

A release PR specifies its version, changelog, and migration notes. Its human merge authorizes publication only after checks pass on the release commit. Ordinary merges do not publish releases. Preserve existing release tags and reconcile inaccurate release-history wording during later migrations.

Use MIT for the original code in nix-nftypes, nixos-cross-config, nixos-nftzones, nixos-registry, and this policy repository. Preserve existing member copyright and license notices, third-party notices, and inherited upstream package license metadata.

## Enforcement and policy changes

Keep checking code and the approved pin record here. Run common checks locally from this repository and in member CI through an immutable reusable-workflow reference. Require the relevant results in GitHub merge gates and audit pins, policy references, documentation structure, dependency direction, and CI configuration for drift.

Human review covers architecture, prose, public compatibility, caller changes, and limitations of mechanical checks. Required status names alone do not prove which workflow code ran. The [checker reference](docs/checker.md) identifies implemented checks and remaining review responsibilities.

After initial adoption, prepare and test new requirements while the existing policy stays active. Activate a requirement only when all affected projects are ready. Member enrollment and migration scope require explicit selection.
