"""External project policy checks; see docs/checker.md for the command contract."""

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__:
    from . import ci, inputs, locks, records, tests_runner, vm
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ci
    import inputs
    import locks
    import records
    import tests_runner
    import vm

LockGraph = locks.LockGraph
repository_identity = locks.repository_identity


REVISION = records.REVISION
POLICY_REPOSITORY = "petohorvath/nixos-project-policy"
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
    check = commands.add_parser("check", help="Check a repo's inputs")
    check.add_argument("project_dir", type=Path)
    check.add_argument(
        "--shell", action="store_true", help="Execute the project's Nix shell"
    )
    test = commands.add_parser(
        "test", help="Run nix flake check with the locked, stable, or unstable nixpkgs"
    )
    test.add_argument("project_dir", type=Path)
    test.add_argument("--nixpkgs", required=True, choices=tests_runner.MODES)
    shell = commands.add_parser(
        "shell", help="Smoke-test the default development shell and root formatter"
    )
    shell.add_argument("project_dir", type=Path)
    vm_command = commands.add_parser(
        "vm", help="Build every legacyPackages.<system>.vmTests entry on this host"
    )
    vm_command.add_argument("project_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        data = records.load()
        if args.command == "validate":
            result = {"status": "valid", "pins": data.pins, "repos": data.repos}
        elif args.command == "ci":
            result = ci.plan(args.project_dir, ci.parse_systems(args.systems))
        elif args.command == "shell":
            issues = check_shell(args.project_dir)
            result = {"status": "fail" if issues else "pass", "issues": issues}
        elif args.command == "test":
            result = tests_runner.run(
                args.project_dir, args.nixpkgs, data.pins, fingerprints=fingerprints
            )
        elif args.command == "vm":
            result = vm.run(args.project_dir)
        else:
            result = check_repo(
                args.project_dir, data, POLICY_REPOSITORY, shell=args.shell
            )
        result["checkerVersion"] = f"v{version}"
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


def check_repo(root, data, policy_repository, *, shell):
    """Apply the check rules to one repo and report each rule by its id."""
    root = root.resolve()
    findings, siblings = inputs.check(root, data.repos, policy_repository)
    rules = inputs.summarize(findings)
    # Public-output rules add their findings and rule ids here.
    if shell:
        problems = check_shell(root)
        findings.extend(
            inputs.finding("shell", None, "fail", problem) for problem in problems
        )
        rules["shell"] = "fail" if problems else "pass"
    return {
        "status": "fail" if "fail" in rules.values() else "pass",
        "revision": git_revision(root),
        "rules": rules,
        "issues": report_findings(findings, "fail"),
        "notices": report_findings(findings, "notice"),
        "siblings": siblings,
    }


def report_findings(findings, level):
    return [
        {key: value for key, value in item.items() if key != "level"}
        for item in findings
        if item["level"] == level
    ]


def check_shell(root):
    # Smoke-test shell startup without inheriting the caller's environment.
    try:
        system = subprocess.check_output(
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
            text=True,
            timeout=30,
        ).strip()
        # Unnamed nix develop can fall back to the default package.
        subprocess.run(
            [
                "nix",
                "eval",
                "--no-update-lock-file",
                "--raw",
                f"path:{root.resolve()}#devShells.{system}.default.drvPath",
            ],
            check=True,
            timeout=120,
            stdout=sys.stderr,
        )
        subprocess.run(
            [
                "nix",
                "develop",
                "--no-update-lock-file",
                "--ignore-environment",
                f"path:{root.resolve()}",
                "--command",
                "bash",
                "-c",
                ":",
            ],
            check=True,
            timeout=900,
            stdout=sys.stderr,
        )
        subprocess.run(
            [
                "nix",
                "eval",
                "--no-update-lock-file",
                "--raw",
                f"path:{root.resolve()}#formatter.{system}.drvPath",
            ],
            check=True,
            timeout=120,
            stdout=sys.stderr,
        )
        return []
    except (subprocess.SubprocessError, OSError) as error:
        return [f"development: shell or formatter probe failed: {error}"]


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
