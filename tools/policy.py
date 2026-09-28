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
    from . import declarations, locks, records, tests_runner
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import declarations
    import locks
    import records
    import tests_runner

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
        "check", help="Check one project; missing approval fails"
    )
    check.add_argument("project_dir", type=Path)
    check.add_argument("--project", required=True)
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
            result = inspect_project(
                args.project_dir,
                args.project,
                config,
                data,
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


def repo_name(repository):
    return repository.rsplit("/", 1)[1].lower()


def enrollment(repos, name):
    listed = {repo_name(repository) for repository in repos}
    return "enrolled" if name.lower() in listed else "not-enrolled"


def inspect_project(root, name, config, data, *, project):
    root = root.resolve()
    issues = check_structure(root, config, project["policyVersion"])
    revision = git_revision(root)
    pairs = [{channel: data.pins[channel] for channel in ("stable", "unstable")}]
    observations = []
    shared_observations = []
    dependencies = set()
    locks = source_files(root, "flake.lock")
    if root / "flake.lock" not in locks:
        issues.append("pins: missing root flake.lock")
    known_repos = {
        repository.lower(): repo_name(repository) for repository in data.repos
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
        "enrollment": enrollment(data.repos, name),
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
    return issues


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
