# Issue tracker: GitHub

Issues and specs live in [GitHub Issues](https://github.com/petohorvath/nixos-project-policy/issues). Use the `gh` CLI with `--repo petohorvath/nixos-project-policy` for repository commands so they work in checkouts without a Git remote. API endpoints use `repos/petohorvath/nixos-project-policy/...`.

## Conventions

- **Create or publish an issue or spec**: `gh issue create --repo petohorvath/nixos-project-policy --title "..." --body-file <path>`.
- **Read or fetch a ticket**: `gh issue view <number> --repo petohorvath/nixos-project-policy --json number,title,body,labels,comments`.
- **List issues**: `gh issue list --repo petohorvath/nixos-project-policy --state open --json number,title,body,labels,comments`. Apply appropriate label filters and increase `--limit` when needed.
- **Comment**: `gh issue comment <number> --repo petohorvath/nixos-project-policy --body-file <path>`.
- **Edit a body**: `gh issue edit <number> --repo petohorvath/nixos-project-policy --body-file <path>`.
- **Apply labels**: `gh issue edit <number> --repo petohorvath/nixos-project-policy --add-label "<label>"`. Use `--remove-label` to remove a label.
- **Close**: `gh issue close <number> --repo petohorvath/nixos-project-policy`.

Write multiline bodies to a temporary file and pass its path with `--body-file`. Use the [triage label mapping](triage-labels.md) when a skill names a triage role.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares issue and pull request numbers. Resolve an ambiguous reference with `gh pr view <number> --repo petohorvath/nixos-project-policy`; if it is an issue, use the issue commands above.

## Wayfinding operations

The map is one issue with child issues for decision tickets.

- **Map**: create an issue labelled `wayfinder:map`, with Notes, Decisions-so-far, and Fog sections.
- **Child ticket**: link the ticket as a GitHub sub-issue through `gh api`. Where sub-issues are unavailable, add the child to a task list in the map and put `Part of #<map>` at the top of the child body. Apply `wayfinder:<type>`, where the type is `research`, `prototype`, `grilling`, or `task`.
- **Blocking**: use native issue dependencies. Retrieve a blocker's numeric database ID with `gh api repos/petohorvath/nixos-project-policy/issues/<number> --jq .id`. Add the dependency with `gh api --method POST repos/petohorvath/nixos-project-policy/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-id>`. Where dependencies are unavailable, use a `Blocked by: #<number>, #<number>` line in the child body. A ticket is unblocked when every blocker is closed.
- **Frontier**: inspect the map's open children in map order. Select the first unassigned child with no open blockers; native dependencies expose the open blocker count as `issue_dependencies_summary.blocked_by`.
- **Claim**: before starting work, run `gh issue edit <number> --repo petohorvath/nixos-project-policy --add-assignee @me`.
- **Resolve**: comment with the answer, close the ticket, then append a summary and ticket link to the map's Decisions-so-far section.
