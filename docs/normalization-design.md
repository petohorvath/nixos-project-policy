# Project normalization design

This design session covers the seven Nix and NixOS projects in `devnix-labs`: `nix-libnet`, `nix-nftypes`, `nixos-cross-config`, `nixos-nftzones`, `nixos-registry`, `nixos-shields`, and `nixos-wanwatch`. The objective is a consistent development environment, shared engineering conventions, coordinated dependency pins, maintainable dependency relationships, enforceable policies, consistent documentation, and a contribution and release workflow.

Status: rounds 1–7 are answered. Q35 adopts the coding and documentation baseline; Q36 requires formatting for all applicable first-party languages. Q37 authorizes preparing only `nixos-project-policy` now; selection and order of member migrations are deferred. Member projects own their tooling and do not import policy code into their flakes. Small member GitHub Actions workflows invoke the centrally maintained policy checks. The parent directory remains a workspace. These notes move into the new sibling policy repository during its preparation.

## Accepted decisions

- Keep independently usable projects with independent releases, shared standards, and external maintenance infrastructure. See [ADR 0001](adr/0001-independent-projects.md).
- Support development on x86_64 Linux and aarch64 Linux in the first rollout. Preserve existing Darwin outputs as best effort and document that they are outside the required CI coverage and support commitment. Project-specific runtime compatibility remains documented locally.
- Design the contribution workflow for one maintainer working with coding agents while allowing outside contributors.
- Maintain shared requirements, approved dependency revisions, and policy checking code in a dedicated, versioned policy repository. Member projects do not import policy helpers into their flakes or use the policy repository as a flake input. The policy repository's own flake does not depend on member projects; policy checks inspect their checkouts as test subjects. See [ADR 0002](adr/0002-versioned-policy-repository.md) and [ADR 0005](adr/0005-external-policy-enforcement.md).
- Run policy checks through a small GitHub Actions workflow in each member project, triggered by PR activity and referencing an approved immutable version of the shared checking workflow. This is a CI dependency on the policy repository. Review the caller configuration and audit it for drift.
- Name the policy repository `nixos-project-policy` and place it beside the seven member projects. Keep the parent directory as a workspace and move the design notes into the policy repository when it is established.
- Expose development shells, formatting, and project checks through each project's root `flake.nix`, including the policy repository. Activate the default shell through a root `.envrc`; migrate existing separate development flakes into the root. Each project owns the implementation and may use local functions or modules. See [ADR 0004](adr/0004-root-flake-development.md).
- Require Nix CLI, nil, formatters, statix, and deadnix in development shells. Allow declared project additions and technically required overrides.
- Use the shared stable pin for common development tools by default, with declared overrides from pinned unstable when needed. Preserve technically required project-specific Nix replacements.
- Keep inter-project dependencies acyclic, including tooling and test inputs. Place cross-project integration tests in the consuming project or a separate integration project that depends on both sides.
- Use a common README outline with flexible detailed documentation. Create glossaries and ADRs when meaningful content exists.
- Link to an approved version of the shared written rules in the policy repository, and keep project-specific development and contribution instructions locally. Do not require generated copies of the common rules in each member project.
- Adopt the proposed coding and documentation baseline from the audited global skills for new and changed first-party material. Enforce reliable mechanical properties and review architecture and prose. Existing lint cleanup remains required under Q21; formatter language coverage is decided separately in Q36.
- Root `nix fmt` covers all applicable first-party Nix, Go, shell, Markdown, YAML, and JSON files, with project-owned configuration, preserved project conventions, and exclusions for vendor/generated files, lockfiles, and byte-sensitive fixtures.
- Prepare only the policy repository in the current stage. Choose member migrations separately after reviewing it; no pilot member or migration order has been approved.
- Require PRs for all changes, with maintainer self-merge permitted after required checks pass. Every merge requires human approval during the initial rollout. Revisit agent merge authority through an explicit policy decision after the workflow has been refined.
- Allow a human maintainer to bypass a merge gate for an urgent change during a CI infrastructure outage, recording the reason, completed checks, and checks still owed on the PR. Run missing checks after recovery. Known code or test failures do not qualify, and agents have no bypass authority.
- Target the current stable NixOS release and unstable for compatibility, with both sources pinned to exact revisions. The stable branch receives changes too; selecting its release name alone does not select an immutable version.
- Apply the shared stable and unstable pins wherever member projects select nixpkgs, including development shells, tests, package builds, and examples. Projects without those inputs need not add them; external consumers may choose their own versions, and existing releases retain their recorded pins. See [ADR 0003](adr/0003-shared-nixpkgs-pins.md).
- Enforce automatable family policy through centrally maintained checks invoked by member GitHub CI, required merge gates, and a scheduled drift audit. Member projects keep their own local and CI test commands; their flakes do not enforce the shared policy. Architecture and prose quality remain review criteria.
- Activate a new policy requirement only after all affected projects are ready. Check the proposed rule in advance and prepare project changes while the existing approved policy remains active.
- Use MIT for original code in nftypes, cross-config, nftzones, registry, and the new policy repository. Preserve existing copyright notices, third-party notices, and upstream package license metadata.
- Document and check host Nix, direnv, and shell integration. Supply project tools through the development shell, with specialized test prerequisites documented separately.
- Use independent SemVer tags, changelogs, and migration notes for public contract changes. During 0.x development, use minor-version bumps for breaking changes and reserve patch releases for compatible changes. Decide readiness for 1.0 explicitly.
- Normally consume member-project dependencies through release tags recorded at exact commits in lockfiles, with updates through PRs. Allow tracked temporary commits when testing changes across projects before a dependency release.
- Use a release PR to review the version, changelog, and migration notes. The human maintainer's merge authorizes automated publication after checks pass on the release commit; ordinary PR merges do not publish releases.
- Prepare shared-pin update candidates weekly, with urgent fixes handled when needed. Each update requires tests and human approval.
- Approve new shared pins only after all affected projects pass. Complete the separate PR merges as one tracked rollout batch, accounting for temporary differences until the batch is complete.
- If a regression is found during an approved rollout, pause further pin updates and prefer fixing the problem on the new pins when the repair is understood and testable. Keep rollback available when the impact is unacceptable or the repair is uncertain. Required checks and human merge approval apply to either recovery, and temporary differences remain tracked until the batch is resolved.
- Use squash merges with Conventional Commit PR titles, producing one Conventional Commit per PR. Release versions still receive human review.
- Fix real lint findings before enabling required lint gates. Permit narrow, documented suppressions for intentional code or false positives.
- Include applicable VM tests in merge gates on x86_64 Linux. Require formatting/lint and relevant evaluation/unit/integration checks on both supported Linux architectures, covering the pinned stable and unstable targets where applicable. Documentation-only PRs run relevant documentation, formatting, and policy checks.
- Keep VM tests out of the default local `nix flake check` command so development can proceed on hosts without the required VM capabilities. Expose a separate explicit command for VM tests and require them in CI on suitable x86_64 Linux runners. The default command covers applicable non-VM project checks; family policy remains external.

Shared terms are recorded in [CONTEXT.md](../CONTEXT.md).

## Policy ownership and CI integration

Each member project defines its own development shell, formatter, project tests, and nixpkgs inputs. Its lockfiles contain the actual revisions needed to build and develop that project. Opening a shell, evaluating the flake, or building a package does not require obtaining the policy repository.

The policy repository records the required tools and approved stable and unstable revisions, and holds the checking code. Policy checks inspect project revisions, verify the effective locks and available shell tools, and apply the shared requirements. Changing the approved revision list does not change a member project's lockfiles; coordinated update PRs still implement the selected weekly update process.

Each member project's small GitHub Actions workflow invokes an approved version of the centrally maintained policy checks when a PR opens or changes. The checks produce ordinary PR results for the required merge gate. The caller file and policy reference remain member configuration, covered by review and the scheduled drift audit. Q25 separately selected links to shared written rules and local project-specific instructions.

The initial interpretation that "outside" prohibited even a small member CI workflow was not accepted. Q30 permits a CI reference while preserving the Q23 boundary around project flakes, development shells, and builds. Polling or a hosted receiver is not part of the selected mechanism for normal PR checks.

## Decision tree

The current frontier contains questions whose prerequisites are settled. Later branches remain open until their prerequisites have answers; a recommendation does not settle a decision.

| Decision                                                         | Prerequisites                                       | Status                                                                                                                                           |
| ---------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Q1: Independent projects, coordinated product, or consolidation  | None                                                | Independent projects and releases                                                                                                                |
| Q2: Supported developer platforms                                | None                                                | x86_64 Linux and aarch64 Linux                                                                                                                   |
| Q3: Maintainers and contribution audience                        | None                                                | One maintainer plus agents; contributors welcome                                                                                                 |
| Q4: Shared policy distribution                                   | Q1, Q3                                              | Dedicated versioned policy repository                                                                                                            |
| Q16: Policy repository location                                  | Q4                                                  | New sibling repository named nixos-project-policy                                                                                                |
| Q23: Ownership of tooling and policy enforcement                 | Q4, Q16, Q17                                        | Project-owned tooling; external policy enforcement; no policy imports                                                                            |
| Q30: Invoking policy checks from GitHub CI                       | Q23, GitHub feasibility audit                       | Small member GitHub CI caller; centrally maintained checks                                                                                       |
| Q24: Default development tool source                             | Q6, Q10, Q11                                        | Stable tools with declared overrides                                                                                                             |
| Q25: Delivery of shared written standards                        | Q4, Q8, documentation audit                         | Links to approved shared rules; local project instructions                                                                                       |
| Q5/Q17: Development flake layout and entrypoint                  | Dependency and tooling options                      | Root flake only; migrate separate development flakes                                                                                             |
| Q6: Common tooling and project-specific additions                | Q2, tooling audit                                   | Common tools with declared additions and overrides                                                                                               |
| Q13: Host bootstrap and meaning of ready for development         | Q2, Q6                                              | Document/check host prerequisites; shell supplies tools                                                                                          |
| Q7: Inter-project dependency direction and integration ownership | Q1, dependency audit                                | Acyclic project graph; consumer or separate integration project owns tests                                                                       |
| Consumer versus development dependency boundaries                | Q1, Q7, Q17                                         | Root development inputs may enter consumer lock graphs; shells require explicit reuse                                                            |
| Q10: Meaning and scope of unified pins                           | Q1, Q4, dependency audit                            | Exact shared pins across project-owned uses                                                                                                      |
| Q11: Supported NixOS releases                                    | Q1, Q2, upstream support evidence                   | Current stable and unstable, both pinned                                                                                                         |
| Q18: Pin update cadence                                          | Q10, Q11, Q14                                       | Weekly candidates; urgent fixes as needed                                                                                                        |
| Q19: Failed pin updates and rollout coordination                 | Q10, Q11, Q14                                       | All affected projects pass before approval; tracked rollout batch                                                                                |
| Pin update mechanics                                             | Q18, Q19, Q23, Q30, targeted lock audit             | Proposed implementation: targeted member lockfile PRs, central candidate records, and graph comparison; no additional architecture choice needed |
| Q31: Regression after a pin rollout starts                       | Q18, Q19, Q23                                       | Prefer a tested fix on new pins; rollback for unacceptable impact or uncertain repair                                                            |
| Q22: Required test tiers and CI platforms                        | Q2, Q11, Q6, CI timing sample                       | Applicable VM tests gate merges; relevant checks cover both Linux architectures                                                                  |
| Q12: Enforcement layers and policy drift detection               | Q3, Q4, Q6, Q8, Q9                                  | Required checks and merge gates plus scheduled drift audit; checks defined centrally and invoked by member CI                                    |
| Q32: Activating new policy requirements                          | Q12, Q23, Q25                                       | All affected projects ready before activation                                                                                                    |
| Q21: Existing lint findings and suppressions                     | Q6, Q12, tooling audit                              | Fix findings; allow narrow documented suppressions                                                                                               |
| Q27: Default local checks and VM command                         | Q17, Q21, Q22                                       | Non-VM default checks; separate VM command; CI VM gates retained                                                                                 |
| Q8: README and documentation structure                           | Q1, Q3, documentation audit                         | Common README outline; flexible detailed docs                                                                                                    |
| Q9: PRs and direct pushes                                        | Q3, workflow audit                                  | PRs for all changes; maintainer may self-merge after checks                                                                                      |
| Q14: Agent merge authority                                       | Q9                                                  | Human approval for every merge initially; revisit after workflow refinement                                                                      |
| Q28: Exceptional bypasses                                        | Q12, Q14                                            | Recorded human exception for CI infrastructure outages                                                                                           |
| Q20: Commit conventions and merge strategy                       | Q9, Q15, workflow audit                             | Squash merges with Conventional Commit titles                                                                                                    |
| Q15: Versioning and public compatibility promises                | Q1, Q7, Q9                                          | Independent SemVer with explicit compatibility rules                                                                                             |
| Q26: Member-project dependency versions                          | Q1, Q7, Q15                                         | Releases; tracked temporary commits when needed                                                                                                  |
| Q29: Release approval and publication                            | Q14, Q15, Q20, Q22                                  | Human release PR merge authorizes publication after release checks                                                                               |
| Q33: Missing license declarations                                | Factual audit completed                             | MIT for the four projects and policy repository                                                                                                  |
| Q34: Existing Darwin outputs                                     | Q2, platform audit                                  | Preserve as best effort; Linux remains the required CI coverage                                                                                  |
| Existing release history migration                               | Q15, Q26, Q29, remote release audit                 | Preserve shields v0.1.0; reconcile wanwatch's release wording in migration draft                                                                 |
| Q35: Portable coding and documentation baseline                  | Q8, Q23, Q25, global-skill audit                    | Adopted for new and changed first-party material; mechanical checks where reliable, review for architecture and prose                            |
| Q36: Formatter language coverage                                 | Q6, Q17, formatter feasibility audit                | All applicable first-party languages; project configuration and explicit exclusions                                                              |
| Q37: Pilot project and migration order                           | Agreed policy and dependency model, migration audit | Prepare the policy repository only; member selection and order deferred                                                                          |
| Completion criteria                                              | Accepted requirements, Q37 scope                    | Prepare and verify the policy repository; family adoption remains a later stage                                                                  |

## Round 1

### Q1: Project boundaries

Should these remain independently usable projects that can release separately, or become parts of one product with coordinated releases?

Recommendation: keep independent projects and share their development and policy infrastructure. Consolidation into a monorepo remains an alternative if coordinated changes are the primary requirement.

Answer: independent projects and releases.

### Q2: Developer platforms

Which platforms must the shared development shell support from the first rollout? Editing and running applicable checks is a separate commitment from running NixOS VM tests.

Recommendation: Linux on x86_64 and aarch64 initially, with macOS included if it is an intended development target. Existing platform declarations will inform the migration discussion; this proposal does not change them.

Answer: Linux x86_64 and aarch64.

### Q3: Maintainers

Should the contribution workflow assume one maintainer working with coding agents, several human maintainers, or outside contributors from the start?

Recommendation: support one maintainer and agents while making outside contributions straightforward. Requiring approval from a second human depends on the intended maintainer model.

Answer: one maintainer plus agents; allow contributors.

## Round 2

### Q4: Shared policy home

Should shared tooling and written standards live in a dedicated, versioned repository that each project adopts explicitly, in synchronized copied templates, or in project-local implementations?

Recommendation: a shared repository for tooling, checks, standards, and templates, with no dependency back on member projects. Its permanent location and update mechanism follow this choice.

Answer: a versioned shared tooling and policy repository.

Clarification from Q23: the shared repository owns policy requirements and external validation tools. Member projects own their development configuration and do not import shared Nix helpers from the policy repository.

### Q5: Development entrypoint

Should every project use a separate `dev/` flake, allow either root or `dev/` according to project needs, or place development outputs in every root flake? Cross-config and registry currently keep their consumer flakes free of development inputs.

Recommendation: use `dev/` everywhere. Direnv activates from the repository root; the explicit development command is `nix develop ./dev`. This makes the entrypoint consistent while preserving lightweight consumer flakes.

Answer in round 2: defer the layout decision until the dependency options have been explored; projects may have different layouts for now. This provisional position was replaced by Q17: expose development shells through root flakes and migrate separate development flakes into the root.

### Q6: Common tools and exceptions

Should each development shell supply Nix CLI, nil, formatters, statix, and deadnix, with project tools and documented Nix overrides, or use an identical default toolchain and separate shells for specialized work?

Recommendation: a mandatory common tool set with declared project additions and technically required overrides. Shields needs a Nix wrapper matched to its plugin ABI; wanwatch needs Go tooling.

Answer: common tools plus declared additions and overrides.

### Q7: Dependency direction

If libnet needs an integration test with wanwatch, which already consumes libnet, should that test belong to the higher-level consumer or a separate integration project, or may a separate development flake introduce a reverse project dependency?

Recommendation: the higher-level consumer or a separate integration project owns the test. Keep member-project dependencies acyclic, including tooling and test inputs.

Answer: the consumer or a separate integration project owns the test; keep member-project dependencies acyclic, including tooling and test inputs.

### Q8: Documentation shape

Should documentation share a README outline with flexible detailed docs, use an identical file tree and headings, or share only prose style?

Recommendation: a common README outline covering purpose, support/status, quickstart, and links to detailed documentation, development, and contribution instructions. Shape deeper guides and references to each project, and create glossaries and ADRs when meaningful content exists.

Answer: common README outline; flexible detailed documentation.

### Q9: Landing changes

Should all changes go through PRs, only code changes require PRs while documentation and pin updates can go directly to main, or should direct commits after local checks be the default?

Recommendation: PRs for all changes, with maintainer self-merge after required checks pass. Agent merge authority and exceptional bypasses are separate decisions.

Answer: PRs for all changes; the maintainer may self-merge after required checks pass.

## Round 3

### Q10: Scope of shared pins

Should exact commits from one approved family baseline apply wherever a project owns stable or unstable nixpkgs inputs, including development and examples; apply only to development/test inputs; or should projects merely use the same branch names?

Recommendation: use the exact baseline across project-owned inputs. Projects without those inputs need not add them, external consumers remain free to supply their own nixpkgs, and historical releases retain their original locks. Coordinated updates and failure handling follow this decision.

Answer: agreed after clarification. Apply the shared stable and unstable pins to development shells, tests, package builds, and examples wherever a member project selects nixpkgs.

Clarification: the proposal selects two exact nixpkgs revisions in the policy repository, one stable and one unstable. Member projects use the selected revision for each source in their builds, tests, development shells, and examples. The alternative is to share only development and test versions while allowing product builds to select different versions. This is a decision about the scope of shared versions; the update schedule and handling of a failed update remain separate decisions.

### Q11: Upstream support

Should compatibility cover the current stable release plus pinned unstable, all upstream-supported stable releases plus unstable, or current stable alone with unstable tools where needed?

Recommendation: current stable plus an explicitly pinned unstable compatibility target. Test newer upstream tips in a separate early-warning job. NixOS 26.05 is current stable; 25.11 is past its published support window.

Answer: current stable, also pinned because the stable branch receives updates, plus pinned unstable. The proposed early-warning job remains to be specified.

### Q12: Enforcement

Should policy use required local/CI checks and GitHub merge gates with scheduled drift audits, required checks and gates with manual audits, or advisory checks and review only?

Recommendation: common checks usable locally and in CI, required GitHub merge gates, and a scheduled audit for drift in pins, policy adoption, documentation structure, and CI configuration. Architecture and prose quality remain review criteria.

Answer: required CI and merge gates plus a scheduled drift audit.

Clarification from Q23: enforce the common policy from outside member projects. Their own tests remain available locally and in CI without importing the policy checker into their flakes.

### Q13: Ready for development

May repositories assume functioning host Nix, direnv, and shell integration, or should the shared infrastructure also supply an opt-in bootstrap helper?

Recommendation: document and check host prerequisites, then make one `direnv allow` sufficient for normal development. Keep specialized VM and nftables test prerequisites explicit.

Answer: document and check host prerequisites; the shell supplies project tools.

### Q14: Agent merge authority

Should agents be allowed to merge ordinary task-scoped PRs after required checks pass with specified exceptions, require approval for every merge, or be able to merge any green PR within the authorized task?

Recommendation: allow routine merges, while requiring maintainer approval for breaking public interfaces, shared-policy changes, and publishing releases.

Answer: human approval is required for every merge at the beginning. Agent merges may be allowed after the workflow has been refined, through a later policy decision. The recommendation to permit routine agent merges immediately was not accepted.

### Q15: Versioning

Should member projects use independent Semantic Versioning tags, calendar versions, or commit revisions with optional tags?

Recommendation: independent SemVer, changelogs, and migration notes for public contract changes, including Nix APIs, NixOS options/defaults, and daemon interfaces where applicable. While a project is at 0.x, use minor-version bumps for breaking changes and reserve patch releases for compatible changes. Decide readiness for 1.0 explicitly.

Answer: independent SemVer with explicit compatibility rules.

## Round 4

### Q16: Policy repository location

Should the policy repository be a new sibling of the member projects, or should the current parent directory become that repository and ignore the member checkouts?

Recommendation: create a sibling repository and move the design notes there. Keep the parent directory as a workspace.

Answer: create a new sibling repository named `nixos-project-policy`.

### Q17: Flake layout, revisited

Should shared tooling retain each project's current root or development-flake layout, move into `dev/` everywhere, or become part of every root flake? A pinned external shell is also technically possible but needs additional composition for project-specific tools.

Initial recommendation: retain current layouts with one shared tooling implementation and common commands. The user selected root-only development entrypoints after reviewing the [options audit](normalization-audit.md#development-tooling-options).

Answer: define development shells through root `flake.nix` and avoid separate `dev/` flakes. Use the standard root development commands consistently across the project family.

Rationale: a root flake provides conventional `nix develop`, `nix fmt`, and `nix flake check` entrypoints, with `.envrc` activating the root's default shell. The selected design accepts development-related flake inputs in consumer lock graphs in exchange for that uniform interface. Project-local functions or modules can provide the implementation without introducing a separate development flake; Q23 excludes imports from the policy repository.

Further clarification: using another project as a flake input does not automatically activate, merge, or re-export that project's development shell. Its shell is available through the input's outputs, and using it requires an explicit choice. Transitive flake inputs are recorded in the consuming project's lockfile, including inputs used only for development. Adding a package such as `pkgs.nil` to a shell does not itself add a separate flake input for that tool.

### Q18: Pin update schedule

Should automation prepare candidate shared-pin updates weekly, monthly, or only when requested?

Recommendation: a weekly update batch, with urgent fixes handled when needed. Each update still requires tests and human approval.

Answer: weekly candidates; urgent fixes as needed.

### Q19: One project rejects an update

If a candidate pin update passes six projects but fails the seventh, should the family retain its current pins, or may passing projects advance while the failing project receives a dated exception?

Recommendation: approve new shared pins only after all affected projects pass. Merge the separate PRs as one rollout batch and track temporary differences until the batch is complete.

Answer: all affected projects must pass before approval; complete separate PR merges as a tracked rollout batch.

### Q20: Commit history

Should the projects use squash merges with Conventional Commit titles, preserve individual Conventional Commits, or retain their current project-specific commit and merge conventions?

Recommendation: one Conventional Commit per PR, produced by squash merge. The PR title describes the change and identifies breaking changes. Release versions still receive human review.

Answer: squash merges with Conventional Commit titles.

### Q21: Existing lint findings

Should migration fix existing lint findings before making checks required, baseline existing findings and block only new ones, or start with warnings and enforce later?

Recommendation: fix real findings before enabling the gate, allowing narrow, documented suppressions for intentional code or false positives. Broad linter disabling does not count as compliance.

Answer: fix findings; allow narrow documented suppressions.

### Q22: Tests required before merge

Should applicable VM suites gate code and pin PRs, or run only on a schedule and before releases?

Recommendation: require formatting/lint and evaluation/unit/integration checks for both supported Linux architectures, with applicable VM suites on x86_64 Linux before merge. Test the pinned stable and unstable targets where relevant. Documentation-only PRs run relevant documentation, format, and policy checks.

Answer: include applicable VM tests in merge gates.

## Round 5

### Q23: Ownership of tooling and policy enforcement

The initial question offered shared Nix helpers with either existing project structures or a common flake-parts framework. Both options assumed that member projects would import a shared implementation from the policy repository.

Answer: reject that dependency. Each project stays independent and owns its tooling. The policy defines requirements such as development tools and nixpkgs revisions, and enforcement runs outside member projects.

Consequences: do not add a policy flake input, shared shell factory, or embedded family policy checker to member projects. Keep their root development flakes and lockfiles self-contained with respect to the policy repository. Use external validation and coordinated update PRs to enforce the agreed requirements. An internal framework is not selected by this answer.

Clarification from Q30: a small GitHub Actions workflow may call checks maintained in the policy repository. The CI reference is accepted; member flakes and development shells remain independent of the policy repository. The initial assumption that no such workflow was allowed was not accepted.

### Q24: Default development tool versions

Should the common development tools come from the shared stable pin or the shared unstable pin? Both choices use exact revisions and the agreed update process.

Recommendation: stable by default, with declared overrides from pinned unstable when a tool needs them. Project-specific Nix replacements remain supported, and compatibility tests still cover both agreed targets.

Answer: stable tools with declared overrides.

### Q25: Shared rules after cloning

Should a fresh clone include a generated copy of the common contribution and agent rules, or link to a pinned version in the policy repository?

Initial recommendation: check in a small generated copy of the common rules, keep project-specific instructions separate, and check for drift. The user requested clarification with `wait-what` before selecting links instead.

Clarification: this concerns written guidance, such as the rule to use PRs and Conventional Commit titles. A local copy repeats that text in each project and needs updates when the rules change. A link directs readers to an approved version in the policy repository, while each project keeps its own development instructions. Neither documentation approach creates a Nix dependency or determines the CI integration.

Revised recommendation after Q23: use links to the approved shared rules, with project-specific instructions kept locally. This keeps common policy in one place. A checked-in copy remains an option when reading the full rules without network access is a requirement.

Answer: link to shared rules and keep project-specific instructions locally.

### Q26: Dependencies between projects

Should member projects normally depend on each other's released versions, or on commits from the main branch?

Recommendation: use release tags recorded at exact commits in lockfiles, and update through PRs. Permit an explicitly tracked temporary commit when testing a change that spans projects before its dependency is released. This does not change the rule that member projects release independently.

Answer: releases, with tracked temporary commits when needed.

### Q27: Default local checks and VM command

Should root `nix flake check` run all applicable checks for the host platform, including VM tests on supported x86_64 Linux hosts, or should it run only fast checks with VM tests requiring a separate command?

Initial recommendation: make `nix flake check` the complete check command for that platform, with named checks for smaller selections. The user selected separate VM checks because development hosts, including some VPS environments, may not provide the required capabilities.

Scope after Q23: this command covers the member project's own tests and checks. Shared family policy is checked externally and is not imported into the member's flake.

Answer: keep VM checks separate from the default local command. Preserve the Q22 requirement to run applicable VM tests before merge on suitable CI runners.

### Q28: CI outages and urgent fixes

If CI infrastructure is unavailable and an urgent change is needed, may the human maintainer bypass the merge gate, or must the change wait for CI recovery?

Recommendation: allow a human-only exception for an infrastructure outage, recorded on the PR with the checks completed and checks still owed. Run the missing checks after recovery. A known code or test failure does not qualify for this exception.

Answer: a recorded human exception for CI outages.

### Q29: Publishing releases

Should merging a human-reviewed release PR authorize automation to publish its specified version, or should publication require a separate manual action after the merge?

Recommendation: a release PR states the version, changelog, and any migration notes; the maintainer's merge authorizes publication after checks pass on the release commit. Ordinary PR merges do not publish releases. Version selection remains subject to human review.

Answer: the human maintainer's release PR merge authorizes publication after the required release checks.

## Round 6

### Q30: Invoking policy checks from GitHub CI

The initial question offered central polling or a service receiving PR events. Both assumed that member repositories could not contain even a small CI workflow that invokes policy checks. The user requested clarification with `wait-what`, asking whether GitHub CI could handle these checks.

Initial recommendation: a central job polling every 15 minutes, with a manual run option and a dedicated GitHub App reporting member results. This remains an alternative if all triggers must stay outside member repositories; it is not a requirement for GitHub CI in general.

Clarification: policy checks verify requirements such as available shell tools, approved nixpkgs revisions, and documentation structure. GitHub Actions can execute them. A small workflow in each member project can invoke checks maintained in the policy repository, while the member's flake, development shell, and package builds stay independent of that repository. This introduces a CI reference to the policy implementation.

Revised recommendation: permit the small member CI workflow and use ordinary PR-triggered GitHub Actions. Keep the checking code and requirements centralized. Verify the exact CI reference and gate configuration through review and the scheduled drift audit.

GitHub supports this through [reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows) and ordinary [PR triggers](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request). A small member workflow refers to the approved shared workflow revision; the checking implementation stays in the policy repository. This model needs no custom App, polling job, or hosted receiver for normal member PR checks. The caller file and revision reference remain editable member code, which is a material distinction from a completely central trigger.

Answer: use a small GitHub CI caller in each member project, with centrally maintained rules and checking code. Keep the CI dependency out of member flakes and development shells.

### Q31: Regression during a pin rollout

If a regression appears after an approved pin batch has begun merging, should the normal response be to restore the previous shared pins, or to fix the problem while retaining the new pins?

Initial recommendation: stop the rollout and prepare PRs restoring the previous approved pins in projects that advanced. Restore the matching central baseline and develop the fix as a new candidate batch. The user asked whether retaining the new pins and fixing the problem would itself cause breakage or inconsistency, and selected the revised recommendation below. Required tests and human merge approval apply to either recovery.

Clarification requested with `wait-what`: rollback here means restoring the previous nixpkgs dependency pins in the repositories. Those pins select the exact stable and unstable source revisions used by future evaluations and builds. Deployments are a separate action.

Illustrative scenario: a candidate update passes the CI tests. Wanwatch and two other projects merge it, while four projects still use the previous pins. A later report shows that wanwatch fails in a network setup not covered by CI.

| Recovery    | Three updated projects                                       | Four projects awaiting the update                      | Final shared pins      |
| ----------- | ------------------------------------------------------------ | ------------------------------------------------------ | ---------------------- |
| Roll back   | Restore the previous pins through tested PRs                 | Cancel their pending update PRs                        | Previous approved pair |
| Fix forward | Retain the new pins while the regression is fixed and tested | Pause, then complete their update PRs after validation | New approved pair      |

Q19 already allows tracked temporary differences while separate PRs are merged. Keeping three projects on the new pins and four on the previous pins extends that transition; it does not by itself violate the agreed consistency model. Both recovery paths can converge on one approved pair. The previous explanation overstated rollback as a requirement of shared pins.

Each project still records its selected dependency graph in its own lockfiles. A difference between repository pins does not itself establish a compatibility failure, but the reported regression remains until repaired or reverted, and affected combinations of projects require validation. See the [Nix lockfile reference](https://nix.dev/manual/nix/2.35/command-ref/new-cli/nix3-flake.html#lock-files).

To fix forward, pause further pin-update merges, prepare a corrective PR with a regression test against the new pins, and validate the revised rollout across affected projects. Resume the remaining update merges after the required checks pass and human approval is given. If the repair requires a different nixpkgs revision, treat the changed pair as a revised candidate that must pass all affected projects before approval. Record the paused state and recovery work so temporary differences do not become untracked permanent drift.

The rollback changes restore the dependency-update files needed to use the previous revisions, together with a policy PR restoring the central approved baseline. Required checks run against the current project code, including applicable VM tests in CI, and human approval is required before each merge. Temporary differences remain tracked until all seven projects use the previous pins again. Historical releases retain their original locks.

A failed rollback check requires investigation and a tested recovery plan before merging; a previously approved pin is not assumed to work with all later project changes. A corrected update can be prepared as a new candidate batch after recovery.

Revised recommendation: prefer fixing forward when the repair is understood and can be validated, and retain rollback for unacceptable regression impact or an uncertain repair. Consistency alone does not require rolling back working projects.

Answer: agree with the revised recommendation. Prefer a tested fix on the new pins; use rollback when the regression's impact is unacceptable or the repair is uncertain. Pause further pin updates during recovery and preserve the agreed checks, human approvals, and tracking of temporary differences.

### Q32: Activating a new requirement

After the initial rollout, a proposed policy update adds a required tool that two projects do not yet provide. Should enforcement wait until all affected projects are ready, or start with a deadline for the remaining projects?

Recommendation: check the proposed rule in advance, prepare the necessary project PRs, and activate the requirement only when all affected projects are ready. The existing approved policy continues to apply while those changes are prepared.

Answer: all affected projects must be ready before activation.

### Q33: Missing license declarations

Three projects declare MIT; no first-party license declaration was found for nftypes, cross-config, nftzones, or registry. Should those four projects and the new policy repository also use MIT for their original code, or should their licenses be chosen separately?

Recommendation: use MIT for the original code in those projects if the existing licensing approach is intended for the family. Preserve existing copyright notices, third-party notices, and upstream package license metadata. The [MIT license text](https://opensource.org/license/mit) is the reference; no license has been added or changed.

Answer: MIT for the four projects and the policy repository, preserving existing and third-party notices and upstream package license metadata.

### Q34: Existing Darwin outputs

Some existing flakes expose Darwin outputs although initial development support and required CI cover Linux. Should those outputs remain available as best effort, or should the normalization remove them?

Recommendation: preserve existing Darwin outputs and document that they do not have the family's required CI coverage or support commitment. Adding tested macOS support can be a separate change; removing an existing public output would need compatibility review.

Answer: preserve existing Darwin outputs as best effort.

## Round 7

### Q35: Portable coding and documentation baseline

Should the shared policy adopt the proposed coding and documentation rules from the existing global skills for new and changed first-party material, or initially cover only the already agreed operational rules?

Recommendation: adopt the [proposed baseline](normalization-standards.md#candidate-additions-from-existing-skills). It covers declared and reproducible Nix dependencies, typed and described NixOS options, meaningful tests, clear documentation, and concise agent guidance that links to the authoritative rules. Enforce reliable mechanical properties and review architecture and prose. Existing lint cleanup still follows Q21. Framework preferences, generated documentation, TDD, universal coverage floors, and additional commit-title length or casing rules do not become mandatory through this proposal.

Answer: adopt the proposed baseline for new and changed first-party material, with mechanical checks where reliable and review for architecture and prose. Existing lint cleanup follows Q21, and formatter language coverage remains the separate Q36 decision.

### Q36: Formatter language coverage

Should every root `nix fmt` cover all applicable first-party languages—Nix, Go, shell, Markdown, YAML, and JSON—or should the common policy require only Nix formatting and leave other coverage to each project?

Recommendation: cover all applicable languages using project-owned configuration and pinned tools. Preserve meaningful project-specific formatting, including wanwatch's Go setup. Exclude vendor, generated artifacts, lockfiles, and byte-sensitive fixtures; preserve the agreed treatment of existing Markdown wrapping. A common command does not require adopting the same flake framework. See the [formatter feasibility audit](normalization-audit.md#root-formatter-coverage).

Answer: all applicable languages, using project-owned configuration and preserving project-specific conventions and the stated exclusions.

### Q37: Pilot rollout

Should the rollout start with the policy repository and a nix-libnet pilot before preparing the other member migrations, or should all seven migrations be prepared together?

Recommendation: start with the policy foundation and nix-libnet pilot. Then migrate the independent projects, nftzones, and wanwatch in dependency order. The pilot verifies the shell, checks, documentation, and GitHub gate setup before the more specialized projects adopt it. It does not approve a family pin update before all affected projects pass. The [rollout proposal](normalization-rollout.md) records the stages and completion evidence derived from the accepted requirements.

Answer: prepare only the policy repository now. The user will decide which member projects to migrate afterward. The proposed nix-libnet pilot and member order are deferred, not approved.

## Supporting evidence

The [audit](normalization-audit.md) records tooling, dependency graphs and pins, existing conventions, live GitHub settings, upstream support dates, and technical constraints. The [standards and verification outline](normalization-standards.md) records the accepted requirements, coding/documentation baseline, and formatter coverage. The [rollout scope](normalization-rollout.md) separates policy repository preparation from deferred member migrations.

## Decision records

Create a glossary when shared terminology is resolved. Record architectural decisions as ADRs when they involve meaningful reversal costs, a rationale that future readers would otherwise miss, and a real trade-off. Routine conventions belong in policy documentation.
