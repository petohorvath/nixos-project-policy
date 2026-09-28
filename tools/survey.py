"""Run `check` on every repo directory in a workspace."""

import subprocess

if __package__:
    from . import locks
else:
    import locks

# Failures of one repo's check that the survey reports and continues after.
REPO_ERRORS = (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError)


def run(workspace, repos, rules, check):
    """Survey the Git repos directly under WORKSPACE.

    CHECK(root) returns one repo's `check` report; RULES orders its rule ids.
    Hidden directories and directories without `.git` are skipped. A missing
    or unreadable WORKSPACE raises.
    """
    listed = {repository.lower() for repository in repos}
    listed_names = {repository.split("/")[1] for repository in listed}
    entries = []
    for root in sorted(workspace.iterdir()):
        if root.name.startswith(".") or not root.is_dir():
            continue
        if not (root / ".git").exists():
            continue
        repository = origin_identity(root)
        entry = {
            "name": root.name,
            "path": str(root),
            "repository": repository,
            "listed": repository in listed
            if repository is not None
            else root.name.lower() in listed_names,
        }
        if not (root / "flake.nix").is_file():
            entry["status"] = "not-a-flake"
        else:
            try:
                report = check(root)
            except REPO_ERRORS as error:
                entry.update(status="error", error=str(error))
            else:
                entry.update(status=report["status"], check=report)
        entries.append(entry)
    return {
        "status": "fail"
        if any(entry["status"] in {"fail", "error"} for entry in entries)
        else "pass",
        "workspace": str(workspace),
        "rules": list(rules),
        "repos": entries,
        "table": table(entries, rules),
    }


def origin_identity(root):
    """Return the lowercase GitHub owner/repo of ROOT's origin remote, or None."""
    try:
        url = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return locks.repository_identity({"original": {"type": "git", "url": url}})


def table(entries, rules):
    """Render one row per repo and one column per rule id."""
    header = ["repo", "listed", "status", *rules]
    rows = [
        [
            entry["name"],
            "yes" if entry["listed"] else "no",
            entry["status"],
            *(entry.get("check", {}).get("rules", {}).get(rule, "-") for rule in rules),
        ]
        for entry in entries
    ]
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = [header, ["-" * width for width in widths], *rows]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip()
        for line in lines
    )
