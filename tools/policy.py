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
    from . import declarations, inputs, locks, outputs, records, tests_runner, vm
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import declarations
    import inputs
    import locks
    import outputs
    import records
    import tests_runner
    import vm

ci_plan = declarations.ci_plan
LockGraph = locks.LockGraph
repository_identity = locks.repository_identity


REVISION = records.REVISION
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
    ci = commands.add_parser(
        "ci", help="Report a member's CI matrix and required gates"
    )
    ci.add_argument("project_dir", type=Path, nargs="?", default=Path("."))
    ci.add_argument("--project", required=True)
    ci.add_argument(
        "--inputs-json",
        help="Hosted workflow inputs to compare with local declarations",
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
    shell = commands.add_parser(
        "shell", help="Smoke-test the default development shell and root formatter"
    )
    shell.add_argument("project_dir", type=Path)
    vm_command = commands.add_parser(
        "vm", help="Build every legacyPackages.<system>.vmTests entry on this host"
    )
    vm_command.add_argument("project_dir", type=Path)
    candidate = commands.add_parser(
        "candidate", help="Print an unapproved pin proposal"
    )
    candidate.add_argument("--stable", required=True)
    candidate.add_argument("--unstable", required=True)
    args = parser.parse_args(argv)
    try:
        declarations.require_policy_version(f"v{version}")
        data = records.load()
        config = records.load_requirements()
        project = None
        if args.command == "ci":
            project = member_project(
                args.project_dir,
                args.project,
                config,
                hosted_inputs=json.loads(args.inputs_json)
                if args.inputs_json is not None
                else None,
            )
        if args.command == "validate":
            result = {"status": "valid", "pins": data.pins, "repos": data.repos}
        elif args.command == "ci":
            result = {
                "status": "planned",
                "project": args.project,
                "policyVersion": project["policyVersion"],
                "memberSettings": member_settings(project),
                "enrollment": enrollment(data.repos, args.project),
                "revision": git_revision(args.project_dir),
                **ci_plan(project, config["ci"]),
            }
        elif args.command == "candidate":
            pair = {"stable": args.stable, "unstable": args.unstable}
            records.validate_pair(pair)
            result = {"schemaVersion": 1, "status": "proposal", "pins": pair}
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
            result = check_repo(args.project_dir, data, config["policyRepository"])
        if project is not None:
            result["selectionStatus"] = "supported"
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
            json.dumps(
                {
                    "status": "error",
                    "selectionStatus": "invalid"
                    if isinstance(error, InvalidDeclaration)
                    else "unknown",
                    "error": str(error),
                }
            ),
            file=sys.stderr,
        )
        return 2


class InvalidDeclaration(ValueError):
    """The inspected caller cannot select a valid policy contract."""


def member_project(root, name, config, *, hosted_inputs=None):
    try:
        return declarations.inspect(
            root.resolve(),
            config["policyRepository"],
            name,
            config,
            checker_version=f"v{(SOURCE_ROOT / 'VERSION').read_text().strip()}",
            hosted_inputs=hosted_inputs,
        )
    except ValueError as error:
        raise InvalidDeclaration(str(error)) from error


def member_settings(project):
    return {field: project[field] for field in declarations.INPUT_FIELDS.values()}


def repo_name(repository):
    return repository.rsplit("/", 1)[1].lower()


def enrollment(repos, name):
    listed = {repo_name(repository) for repository in repos}
    return "enrolled" if name.lower() in listed else "not-enrolled"


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


def check_shell(root):
    """Probe the default development shell and the formatter on this host."""
    root = root.resolve()
    system = host_system()
    return [
        problem
        for problem in [
            shell_problem(root, system),
            formatter_problem(root, system),
        ]
        if problem is not None
    ]


def shell_problem(root, system):
    """Start devShells.<system>.default with a cleared environment."""
    # Unnamed nix develop can fall back to the default package, so evaluate
    # the shell by name first.
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


def probe(problem, commands):
    """Run COMMANDS in order; return PROBLEM with the failure, or None."""
    for command in commands:
        try:
            # Nix output goes to standard error to keep the JSON report intact.
            subprocess.run(command, check=True, timeout=900, stdout=sys.stderr)
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
