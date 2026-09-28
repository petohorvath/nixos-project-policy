"""External project policy checks; see docs/checker.md for the command contract."""

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__:
    from . import declarations, locks, records
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import declarations
    import locks
    import records

ci_plan = declarations.ci_plan
LockGraph = locks.LockGraph
repository_identity = locks.repository_identity


REVISION = records.REVISION
SOURCE_ROOT = Path(__file__).resolve().parents[1]
PARENT_POLICY_ROOT = re.compile(r"--policy-root(?:[\s=]|\\\n)+[\"']?\.\.(?![^/\s\"'])")
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
    parser.add_argument(
        "--policy-root", type=Path, help="Trusted checkout of current central records"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate the central records")
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
        "check", help="Check one project; missing approval fails"
    )
    check.add_argument("project_dir", type=Path)
    check.add_argument("--project", required=True)
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
        if (
            args.command in {"check", "vm", "compatibility", "ci"}
            and args.policy_root is None
        ):
            raise ValueError(
                f"{args.command} requires --policy-root with current central records; "
                "a policy release's bundled pins do not establish current approval"
            )
        records_root = args.policy_root or SOURCE_ROOT
        config, pins = records.load(records_root)
        project = None
        if args.command in {"check", "ci", "compatibility", "vm"}:
            project = member_project(
                args.project_dir,
                args.project,
                config,
                hosted_inputs=json.loads(args.inputs_json)
                if args.command == "ci" and args.inputs_json is not None
                else None,
            )
        if args.command == "validate":
            result = {
                "status": "valid",
                "approvedPins": pins["approved"] is not None,
            }
        elif args.command == "ci":
            result = {
                "status": "planned",
                "project": args.project,
                "policyVersion": project["policyVersion"],
                "memberSettings": member_settings(project),
                "enrollment": enrollment(config, args.project),
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
                pins,
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
                "enrollment": enrollment(config, args.project),
            }
        else:
            result = inspect_project(
                args.project_dir,
                args.project,
                config,
                pins,
                project=project,
            )
            if args.shell:
                result["issues"].extend(check_shell(args.project_dir))
                if result["issues"]:
                    result["status"] = "fail"
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


def enrollment(config, name):
    return "enrolled" if name in config["_members"] else "not-enrolled"


def inspect_project(root, name, config, pins, *, project):
    root = root.resolve()
    issues = check_structure(root, config, project["policyVersion"])
    revision = git_revision(root)
    pairs = [pins["approved"]] if pins["approved"] else []
    if not pairs:
        issues.append("pins: no approved family baseline; compliance cannot pass yet")
    observations = []
    shared_observations = []
    dependencies = set()
    locks = source_files(root, "flake.lock")
    if root / "flake.lock" not in locks:
        issues.append("pins: missing root flake.lock")
    known_repos = {
        repository.lower(): name for name, repository in config["_members"].items()
    }
    for path in locks:
        lock = LockGraph(records.read_json(path))
        independent_node = None
        if path == root / "flake.lock":
            try:
                independent_node = selected_nixpkgs(lock)
            except ValueError as error:
                issues.append(f"flake.lock: {error}")
        lock_pins = []
        channels = {}
        for node_id, node in lock.reachable().items():
            identity = repository_identity(node)
            if identity == config["policyRepository"].lower():
                issues.append(
                    f"{path.relative_to(root)}: policy repository is a flake dependency"
                )
            if identity in known_repos and known_repos[identity] != name:
                dependencies.add(known_repos[identity])
            if identity != "nixos/nixpkgs":
                continue
            selected = node.get("locked", {}).get("rev")
            channel = nixpkgs_channel(node)
            if channel is None:
                matches = {
                    channel
                    for pair in pairs
                    for channel, rev in pair.items()
                    if rev == selected
                }
                if len(matches) == 1:
                    channel = matches.pop()
            channels[node_id] = channel
            observation = {
                "lockfile": str(path.relative_to(root)),
                "node": node_id,
                "channel": channel,
                "rev": selected,
                "selection": "independent" if node_id == independent_node else "shared",
            }
            observations.append(observation)
            if node_id == independent_node:
                continue
            shared_observations.append(observation)
            if (
                channel is None
                or not isinstance(selected, str)
                or not REVISION.fullmatch(selected)
            ):
                issues.append(
                    f"{path.relative_to(root)}:{node_id}: unclassified or non-immutable nixpkgs input"
                )
            else:
                lock_pins.append(observation)
        for input_name, reference in lock.nodes[lock.root].get("inputs", {}).items():
            if input_name == "nixpkgs" and lock.resolve(reference) == independent_node:
                continue
            channel = channels.get(lock.resolve(reference))
            expected_name = {"stable": "nixpkgs", "unstable": "nixpkgs-unstable"}.get(
                channel
            )
            if expected_name is not None and input_name != expected_name:
                issues.append(
                    f"{path.relative_to(root)}: {channel} nixpkgs input "
                    f"{input_name!r} must be named {expected_name!r}"
                )
        if (
            lock_pins
            and pairs
            and not any(pins_match(lock_pins, pair) for pair in pairs)
        ):
            issues.append(
                f"{path.relative_to(root)}: nixpkgs revisions do not match one allowed pin pair"
            )
    if (
        shared_observations
        and pairs
        and not any(pins_match(shared_observations, pair) for pair in pairs)
    ):
        issues.append("pins: project lockfiles do not share one allowed pair")
    if issues:
        status = "fail"
    else:
        status = "pass"
    return {
        "project": name,
        "policyVersion": project["policyVersion"],
        "revision": revision,
        "status": status,
        "compatibility": "not-run",
        "enrollment": enrollment(config, name),
        "issues": issues,
        "pins": observations,
        "dependencies": sorted(dependencies),
        "requiredChecks": ci_plan(project, config["ci"])["requiredChecks"],
        "memberSettings": member_settings(project),
    }


def selected_nixpkgs(lock):
    inputs = lock.nodes[lock.root].get("inputs", {})
    if "nixpkgs" not in inputs:
        raise ValueError("missing root nixpkgs input")
    node_id = lock.resolve(inputs["nixpkgs"])
    node = lock.nodes[node_id]
    locked = node.get("locked", {})
    if (
        repository_identity({"locked": locked}) != "nixos/nixpkgs"
        or locked.get("type") not in {"github", "git"}
        or locked.get("dir")
        or node.get("flake") is False
    ):
        raise ValueError("root nixpkgs must identify the NixOS/nixpkgs flake")
    records.require_revision(locked.get("rev"))
    return node_id


def check_structure(root, config, version):
    issues = []
    for file in [
        "flake.nix",
        ".envrc",
        "README.md",
        "CONTRIBUTING.md",
        "AGENTS.md",
        "LICENSE",
    ]:
        if not (root / file).is_file():
            issues.append(f"structure: missing {file}")
    envrc = root / ".envrc"
    if envrc.exists() and not re.search(
        r"^\s*use flake(?:\s+\.)?\s*(?:#.*)?$", envrc.read_text(), re.M
    ):
        issues.append("development: .envrc must activate the root flake")
    rule_link = re.compile(
        r"https://github\.com/"
        + re.escape(config["policyRepository"])
        + "/blob/"
        + re.escape(version)
        + r"/POLICY\.md(?:[)#\s]|$)"
    )
    for file in ["CONTRIBUTING.md", "AGENTS.md"]:
        path = root / file
        if path.exists() and (
            not rule_link.search(path.read_text())
            or (
                any(
                    linked != version
                    for linked in re.findall(
                        r"https://github\.com/"
                        + re.escape(config["policyRepository"])
                        + r"/blob/([^/\s]+)/POLICY\.md(?:[)#\s]|$)",
                        path.read_text(),
                    )
                )
            )
        ):
            issues.append(
                f"documentation: {file} needs only the selected policy release's POLICY.md links"
            )
    for path in source_files(root, "*.md"):
        if PARENT_POLICY_ROOT.search(path.read_text()):
            issues.append(
                f"documentation: {path.relative_to(root)} passes a ../ path to --policy-root; "
                "clone records into a mktemp -d directory"
            )
    return issues


def check_compatibility(root, name, config, pins, channel, *, project=None):
    root = root.resolve()
    project = project or member_project(root, name, config)
    result = {
        "project": name,
        "policyVersion": project["policyVersion"],
        "memberSettings": member_settings(project),
        "enrollment": enrollment(config, name),
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
        pair = pins["approved"]
        if pair is None:
            raise ValueError("No approved family baseline for compatibility")
        result["pinStatus"] = "approved"
        revision = pair[channel]
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


def nixpkgs_channel(node):
    ref = node.get("original", {}).get("ref", "")
    if ref in {"nixos-unstable", "nixpkgs-unstable", "nixos-unstable-small"}:
        return "unstable"
    if re.fullmatch(r"nixos-\d{2}\.\d{2}(?:-small)?", ref):
        return "stable"
    return None


def pins_match(observations, pair):
    return all(
        item["channel"] in pair and item["rev"] == pair[item["channel"]]
        for item in observations
    )


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
