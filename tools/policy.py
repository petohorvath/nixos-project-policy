"""External project policy checks; see docs/checker.md for the command contract."""

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys

if __package__:
    from . import ci, inputs, locks, outputs, records, survey, tests_runner, vm
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ci
    import inputs
    import locks
    import outputs
    import records
    import survey
    import tests_runner
    import vm

LockGraph = locks.LockGraph
repository_identity = locks.repository_identity


REVISION = records.REVISION
POLICY_REPOSITORY = "petohorvath/nixos-project-policy"
# Every rule id in a `check` report, in report order; one survey column each.
CHECK_RULES = (*inputs.RULES, *outputs.RULES, "shell", "formatter")
SOURCE_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git",
    ".direnv",
    "vendor",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nixos-project-policy", description=__doc__)
    version = (SOURCE_ROOT / "VERSION").read_text().strip()
    parser.add_argument("--version", action="version", version=f"%(prog)s {version}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate the bundled pins and repo list")
    plan = commands.add_parser(
        "ci", help="Print a repo's CI job matrices and required status names"
    )
    plan.add_argument("project_dir", type=Path)
    plan.add_argument(
        "--systems",
        help="JSON list of Nix systems (default: x86_64-linux and aarch64-linux)",
    )
    check = commands.add_parser(
        "check",
        help="Check a repo's inputs, public outputs, development shell, and formatter",
    )
    check.add_argument("project_dir", type=Path)
    test = commands.add_parser(
        "test", help="Run nix flake check with the locked, stable, or unstable nixpkgs"
    )
    test.add_argument("project_dir", type=Path)
    test.add_argument("--nixpkgs", required=True, choices=tests_runner.MODES)
    vm_command = commands.add_parser(
        "vm", help="Build every legacyPackages.<system>.vmTests entry on this host"
    )
    vm_command.add_argument("project_dir", type=Path)
    survey_command = commands.add_parser(
        "survey",
        help="Run check on every Git repo directly under a workspace directory",
    )
    survey_command.add_argument("workspace", type=Path)
    args = parser.parse_args(argv)
    try:
        data = records.load()
        if args.command == "validate":
            result = {"status": "valid", "pins": data.pins, "repos": data.repos}
        elif args.command == "ci":
            result = ci.plan(args.project_dir, ci.parse_systems(args.systems))
        elif args.command == "test":
            result = tests_runner.run(
                args.project_dir, args.nixpkgs, data.pins, fingerprints=fingerprints
            )
        elif args.command == "vm":
            result = vm.run(args.project_dir)
        elif args.command == "survey":
            result = survey.run(
                args.workspace.resolve(),
                data.repos,
                CHECK_RULES,
                lambda root: check_repo(root, data, POLICY_REPOSITORY),
            )
        else:
            result = check_repo(args.project_dir, data, POLICY_REPOSITORY)
        result["checkerVersion"] = f"v{version}"
        if args.command == "survey":
            # Standard output carries only JSON; the table is for people.
            print(result["table"], file=sys.stderr)
        print(json.dumps(result, indent=2, sort_keys=True))
        return {"fail": 1, "error": 2}.get(result.get("status"), 0)
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        print(
            json.dumps({"status": "error", "error": str(error)}),
            file=sys.stderr,
        )
        return 2


def check_repo(root, data, policy_repository):
    """Apply the check rules to one repo and report each rule by its id."""
    root = root.resolve()
    findings, siblings = inputs.check(root, data.repos, policy_repository)
    rules = inputs.summarize(findings)
    system = host_system()
    output_findings, output_rules, release = outputs.check(root, system)
    findings.extend(output_findings)
    rules.update(output_rules)
    for rule, problem in [
        ("shell", shell_problem(root, system)),
        ("formatter", formatter_problem(root, system)),
    ]:
        if problem is not None:
            findings.append(inputs.finding(rule, None, "fail", problem))
        rules[rule] = "fail" if problem else "pass"
    return {
        "status": "fail" if "fail" in rules.values() else "pass",
        "revision": git_revision(root),
        "rules": rules,
        "issues": report_findings(findings, "fail"),
        "notices": report_findings(findings, "notice"),
        "siblings": siblings,
        "release": release,
    }


def report_findings(findings, level):
    return [
        {key: value for key, value in item.items() if key != "level"}
        for item in findings
        if item["level"] == level
    ]


def host_system():
    return subprocess.run(
        ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        timeout=30,
    ).stdout.strip()


def shell_problem(root, system):
    """Start devShells.<system>.default with a cleared environment."""
    # Unnamed nix develop can fall back to the default package, so evaluate
    # the shell by name first. The shellHook runs in an empty directory
    # because hooks such as git-hooks.nix write to the repository they run in.
    with tempfile.TemporaryDirectory(prefix="nixos-project-policy-shell-") as empty:
        return probe(
            "default development shell does not start",
            [
                [
                    "nix",
                    "eval",
                    "--no-update-lock-file",
                    "--raw",
                    f"path:{root}#devShells.{system}.default.drvPath",
                ],
                [
                    "nix",
                    "develop",
                    "--no-update-lock-file",
                    "--ignore-environment",
                    f"path:{root}",
                    "--command",
                    "bash",
                    "-c",
                    ":",
                ],
            ],
            cwd=empty,
        )


def formatter_problem(root, system):
    return probe(
        "formatter does not evaluate",
        [
            [
                "nix",
                "eval",
                "--no-update-lock-file",
                "--raw",
                f"path:{root}#formatter.{system}.drvPath",
            ]
        ],
    )


def probe(problem, commands, cwd=None):
    """Run COMMANDS in order; return PROBLEM with the failure, or None."""
    for command in commands:
        try:
            # Nix output goes to standard error to keep the JSON report intact.
            subprocess.run(command, check=True, timeout=900, stdout=sys.stderr, cwd=cwd)
        except (subprocess.SubprocessError, OSError) as error:
            return f"{problem}: {error}"
    return None


def fingerprints(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_files(root, "*", include_vendor=True)
        if path.name not in {".treefmt-cache", ".git"}
    }


def source_files(root, filename, include_vendor=False):
    result = []
    excluded = EXCLUDED_DIRS - {"vendor"} if include_vendor else EXCLUDED_DIRS
    for directory, dirs, files in os.walk(root):
        dirs[:] = [
            name
            for name in dirs
            if name not in excluded
            and name != "result"
            and not name.startswith("result-")
        ]
        for name in files:
            if not fnmatch.fnmatch(name, filename):
                continue
            path = Path(directory) / name
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Source file escapes the project directory")
            result.append(path)
    return sorted(result)


def git_revision(root):
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


if __name__ == "__main__":
    sys.exit(main())
