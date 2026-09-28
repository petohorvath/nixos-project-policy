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
    from . import declarations, inputs, locks, records
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import declarations
    import inputs
    import locks
    import records

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
    check = commands.add_parser("check", help="Check a repo's inputs")
    check.add_argument("project_dir", type=Path)
    check.add_argument(
        "--shell", action="store_true", help="Execute the project's Nix shell"
    )
    compatibility = commands.add_parser(
        "compatibility", help="Run root checks with a recorded shared pin"
    )
    compatibility.add_argument("project_dir", type=Path)
    compatibility.add_argument("--project", required=True)
    compatibility.add_argument(
        "--channel", required=True, choices=["stable", "unstable"]
    )
    shell = commands.add_parser(
        "shell", help="Smoke-test the default development shell and root formatter"
    )
    shell.add_argument("project_dir", type=Path)
    host_checks = commands.add_parser(
        "host-checks", help="Require nonempty host checks with the committed lock"
    )
    host_checks.add_argument("project_dir", type=Path)
    vm = commands.add_parser(
        "vm", help="Run member-declared VM targets on a suitable host"
    )
    vm.add_argument("project_dir", type=Path)
    vm.add_argument("--project", required=True)
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
        if args.command in {"ci", "compatibility", "vm"}:
            project = member_project(
                args.project_dir,
                args.project,
                config,
                hosted_inputs=json.loads(args.inputs_json)
                if args.command == "ci" and args.inputs_json is not None
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
        elif args.command == "host-checks":
            result = check_host_checks(args.project_dir)
        elif args.command == "compatibility":
            result = check_compatibility(
                args.project_dir,
                args.project,
                config,
                data,
                args.channel,
                project=project,
            )
        elif args.command == "vm":
            targets = project["vmTargets"]
            for target in targets:
                subprocess.run(
                    [
                        "nix",
                        "build",
                        "--no-link",
                        "--no-update-lock-file",
                        "--print-build-logs",
                        f"path:{args.project_dir.resolve()}#{target}",
                    ],
                    check=True,
                )
            result = {
                "status": "pass" if targets else "not-applicable",
                "targets": targets,
                "policyVersion": project["policyVersion"],
                "memberSettings": member_settings(project),
                "enrollment": enrollment(data.repos, args.project),
            }
        else:
            result = check_repo(
                args.project_dir, data, config["policyRepository"], shell=args.shell
            )
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
    return {
        **{field: project[field] for field in declarations.INPUT_FIELDS.values()},
        "vmArchitecture": project["vmArchitecture"],
    }


def repo_name(repository):
    return repository.rsplit("/", 1)[1].lower()


def enrollment(repos, name):
    listed = {repo_name(repository) for repository in repos}
    return "enrolled" if name.lower() in listed else "not-enrolled"


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


def selected_nixpkgs(lock):
    node_id, problem = inputs.root_nixpkgs(lock)
    if problem is not None:
        raise ValueError(problem)
    return node_id


def check_compatibility(root, name, config, data, channel, *, project=None):
    root = root.resolve()
    project = project or member_project(root, name, config)
    result = {
        "project": name,
        "policyVersion": project["policyVersion"],
        "memberSettings": member_settings(project),
        "enrollment": enrollment(data.repos, name),
        "revision": git_revision(root),
        "channel": channel,
        "system": None,
        "expectedRevision": None,
        "resolvedRevision": None,
        "pinStatus": None,
        "status": "error",
        "commands": [],
        "issues": [],
    }
    before = None
    source_before = None
    try:
        records.require_revision(result["revision"])
        source_before = fingerprints(root)
        before = (root / "flake.lock").read_bytes()
        committed_lock = subprocess.check_output(
            ["git", "-C", str(root), "show", "HEAD:flake.lock"],
            stderr=subprocess.DEVNULL,
        )
        if before != committed_lock:
            raise ValueError("Compatibility requires the committed root lock")
        selected_nixpkgs(LockGraph(json.loads(before)))
        result["pinStatus"] = "approved"
        revision = data.pins[channel]
        result["expectedRevision"] = revision
        result["system"] = compatibility_command(
            result,
            [
                "nix",
                "eval",
                "--raw",
                "--impure",
                "--expr",
                "builtins.currentSystem",
            ],
            capture=True,
        ).strip()
        if not declarations.valid_system(result["system"]):
            raise ValueError(f"Invalid compatibility host: {result['system']}")
        override = ["--override-input", "nixpkgs", f"github:NixOS/nixpkgs/{revision}"]
        metadata = compatibility_command(
            result,
            [
                "nix",
                "flake",
                "metadata",
                str(root),
                "--json",
                *override,
            ],
            capture=True,
        )
        lock = LockGraph(json.loads(metadata)["locks"])
        node = selected_nixpkgs(lock)
        result["resolvedRevision"] = lock.nodes[node]["locked"]["rev"]
        if result["resolvedRevision"] != revision:
            raise ValueError(
                "Resolved root nixpkgs does not match the selected shared pin"
            )
        checks = json.loads(
            compatibility_command(
                result,
                [
                    "nix",
                    "eval",
                    "--json",
                    f"{root}#checks.{result['system']}",
                    "--apply",
                    "builtins.attrNames",
                    *override,
                ],
                capture=True,
            )
        )
        if (
            not isinstance(checks, list)
            or not checks
            or not all(isinstance(check, str) for check in checks)
        ):
            raise ValueError("Compatibility requires nonempty host checks")
        result["checks"] = checks
        compatibility_command(
            result,
            [
                "nix",
                "flake",
                "check",
                str(root),
                "--print-build-logs",
                *override,
            ],
        )
        result["status"] = "pass"
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        result["status"] = "fail"
        result["issues"].append(str(error))
    finally:
        try:
            if before is not None and (
                not (root / "flake.lock").is_file()
                or (root / "flake.lock").read_bytes() != before
            ):
                result["status"] = "fail"
                result["issues"].append("Compatibility changed the project lockfile")
            if source_before is not None and fingerprints(root) != source_before:
                result["status"] = "fail"
                result["issues"].append(
                    "Project sources changed during compatibility checks"
                )
        except (OSError, ValueError) as error:
            result["status"] = "fail"
            result["issues"].append(
                f"Source inspection after compatibility failed: {error}"
            )
    return result


def compatibility_command(result, command, *, capture=False):
    outcome = {"command": command, "returncode": None}
    result["commands"].append(outcome)
    try:
        process = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else sys.stderr,
        )
        outcome["returncode"] = process.returncode
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
        return process.stdout
    except (OSError, subprocess.SubprocessError) as error:
        outcome["error"] = str(error)
        raise


def check_host_checks(root):
    result = {"status": "fail", "system": None, "checks": [], "issues": []}
    try:
        result["system"] = subprocess.check_output(
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
            text=True,
            timeout=30,
        ).strip()
        process = subprocess.run(
            [
                "nix",
                "eval",
                "--json",
                "--no-update-lock-file",
                f"path:{root.resolve()}#checks.{result['system']}",
                "--apply",
                "builtins.attrNames",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=120,
        )
        checks = json.loads(process.stdout)
        if (
            not isinstance(checks, list)
            or not checks
            or not all(isinstance(check, str) for check in checks)
        ):
            raise ValueError(
                "Committed-lock project tests require nonempty host checks"
            )
        result.update(status="pass", checks=checks)
    except (ValueError, subprocess.SubprocessError, OSError) as error:
        result["issues"].append(f"project tests: host check probe failed: {error}")
    return result


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
