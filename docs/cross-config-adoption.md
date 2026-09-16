# First member migration: nixos-cross-config

The maintainer selected `nixos-cross-config` on 2026-09-16 as the first migration to the shared policy. The other six members remain pending and unchanged. This record prepares adoption; it does not approve pins, authorize merges, or record hosted checks that have not run.

## Migration

The member now owns a root development shell, direnv activation, formatter, and non-VM checks on both declared Linux systems. The root lockfile is the former development lock, moved without changing its bytes. Public fixtures still run against stable and unstable, including diagnostic and native-cycle checks. Existing statix findings were resolved without suppressions.

The initial aggregate check was stopped after evaluator memory exceeded 5 GiB. Default evaluation checks now use separate offline evaluator processes for fixtures and individual expected failures, preserving the JSON result structure. This keeps root check evaluation small while retaining the existing assertions and focused `lib.tests` interface.

The factory signature, receiver-supplied `lib`, plain-import usage, and production module are preserved. Root development inputs can now enter consumer lock graphs, and `dev/flake.nix` commands move to the root. The member's changelog records those migration steps.

The shell supplies all common tools plus Git, shfmt, Prettier, Ruff, actionlint, Python, and GNU time from stable. Root formatting covers Nix, shell and `.envrc`, Markdown, YAML, JSON, and Python. Generated historical benchmark reports and measurements are excluded; the runner and workloads remain in scope. The benchmark runner uses root inputs and finds GNU time on `PATH`.

The README, contributor instructions, agent guidance, MIT license, and changelog provide the required local contract. The caller and shared-rule links select published policy commit `6208eb8c11a338a96e07002dc45696a5e32abad8`. There is no policy input, Nix import, build dependency, or member dependency introduced by this migration.

## Candidate pins

Batch `2026-09-16-cross-config-adoption` records clean member commit `fd2076c2c4cc9e79a9152c867886f2c1f2f14ac9` on `chore/adopt-project-policy`.

| Channel             | Revision                                   |
| ------------------- | ------------------------------------------ |
| Stable, NixOS 26.05 | `c3eea5b2156db11c7eeeada3dc737711255b253e` |
| Unstable            | `ef34387ddd751e1ab8857adf4676492d32eb24ec` |

These are the member's existing pins and the policy repository's bootstrap pins. `policy/pins.json` keeps `approved: null`; `policy/projects.json` records the proposed policy revision with `adopted: false`. Candidate validation must use the exact clean member commit recorded in the batch. Hosted checks must register GitHub's actual candidate merge commit and rerun against the updated central record snapshot.

## Local validation

Validation on 2026-09-16 used the member's committed locks and source at `fd2076c2c4cc9e79a9152c867886f2c1f2f14ac9`:

| Check                                                                           | Result                                                                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Root `nix flake check --no-update-lock-file --print-build-logs` on x86_64 Linux | Passed all eight check derivations. Each pin produced 100 passing fixture results across 17 groups, including 39 expected failures. Diagnostics, native-cycle checks, formatting, and lint passed. The committed-source recheck reused the same derivations. |
| `nix flake check --no-update-lock-file --all-systems --no-build`                | Both Linux systems' check, shell, and formatter outputs evaluated successfully. Aarch64 builds and execution were not run locally.                                                                                                                           |
| Root shell and direnv                                                           | The isolated common-tool and formatter probe passed. `direnv allow` and `direnv exec` supplied the development tools and benchmark entrypoint.                                                                                                               |
| External `lint`                                                                 | Statix, deadnix, and root formatting passed against a disposable source copy.                                                                                                                                                                                |
| External `check --readiness --shell`                                            | Reported `candidate-ready`, selected `2026-09-16-cross-config-adoption`, matched the registered clean commit, and reported no issues.                                                                                                                        |
| Normal `check`                                                                  | Failed solely because adoption remains pending; candidate validation did not become compliance.                                                                                                                                                              |
| Benchmark smoke check                                                           | A frozen copy containing only the root factory and `lib/` passed writable-tag values and assertions on both root pins. GNU time resolved to the shell's Nix-store executable. No performance measurements were regenerated.                                  |
| Preservation                                                                    | The moved lockfile is byte-identical to the old development lock. Production `lib/` and generated benchmark results are unchanged. Archived documentation edits only normalize formatting.                                                                   |

Both fixture result objects have identical structure and values. Passing local checks establishes candidate readiness; hosted execution, approval, and audit evidence remain separate requirements.

## Activation requirements

The published policy repository currently has default branch `docs/agent-skills` and no `main`. Its reusable workflow reads central records from `main`, so the caller cannot run until that branch and its agreed merge controls exist. The immutable checker revision is already published and reviewable.

Complete the following before recording adoption:

1. Establish the policy repository's reviewed `main`, configure its PR and check requirements, and publish the candidate records through human-reviewed changes.
2. Open the member migration PR against the intended base. The local migration starts from `79337a8f48a038fdc742f951b430b54f646936b3`, which includes the existing issues 11–13 maintenance commits; preserve their separate review history.
3. Register the exact PR merge commit in the candidate batch and rerun the pinned caller. Verify both Linux runners. This member has no VM suite.
4. Approve the initial pin pair only after the required candidate checks pass. Passing candidate readiness alone does not approve it.
5. Record the actual required GitHub status names, verify squash-only merging and PR/check gates, and configure the read-only `MEMBER_AUDIT_TOKEN` with access to this member as described in [maintenance](maintenance.md#audit-access).
6. Verify hosted audit access, record adoption through a policy PR, and require a normal `check` result of `pass` against the approved baseline.

No member audit credential is needed while every member remains pending. Aarch64 execution, merge gates, audit access, pin approval, and release publication setup remain hosted work.
