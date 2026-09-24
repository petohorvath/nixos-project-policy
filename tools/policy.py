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
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

if __package__:
    from . import (
        agreement,
        batches,
        candidates,
        declarations,
        locks,
        pin_pr,
        records,
        releases,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agreement
    import batches
    import candidates
    import declarations
    import locks
    import pin_pr
    import records
    import releases

ci_plan = declarations.ci_plan
LockGraph = locks.LockGraph
repository_identity = locks.repository_identity
dependency_cycles = locks.dependency_cycles


REVISION = records.REVISION
SOURCE_ROOT = Path(__file__).resolve().parents[1]
ACTIVE_BATCH_STATES = records.ACTIVE_BATCH_STATES
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
    agreement_command = commands.add_parser(
        "agreement",
        help="Compare policy selections at committed member dependency revisions",
    )
    agreement_command.add_argument("project_dir", type=Path)
    agreement_command.add_argument("--project", required=True)
    check = commands.add_parser(
        "check", help="Check one project; missing approval fails"
    )
    check.add_argument("project_dir", type=Path)
    check.add_argument("--project", required=True)
    check.add_argument(
        "--batch", help="Registered candidate batch, bound to the tested commit"
    )
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
    compatibility.add_argument(
        "--batch", help="Registered candidate at this exact commit"
    )
    compatibility.add_argument(
        "--output", type=Path, help="New evidence directory outside the project"
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
    audit = commands.add_parser(
        "audit", help="Inspect every enrolled member's selected policy"
    )
    audit.add_argument("workspace", type=Path)
    audit.add_argument(
        "--fetch", action="store_true", help="Clone missing public checkouts"
    )
    audit.add_argument(
        "--github", action="store_true", help="Inspect enrolled members' merge gates"
    )
    candidate = commands.add_parser(
        "candidate", help="Print an unapproved pin proposal"
    )
    candidate.add_argument("--stable", required=True)
    candidate.add_argument("--unstable", required=True)
    candidates.add_commands(commands)
    pin_pr.add_commands(commands)
    args = parser.parse_args(argv)
    try:
        declarations.require_policy_version(f"v{version}")
        if (
            args.command
            in {
                "check",
                "audit",
                "vm",
                "compatibility",
                "ci",
                "agreement",
                "pin-batch",
                "pin-pr",
            }
            and args.policy_root is None
        ):
            raise ValueError(
                f"{args.command} requires --policy-root with current central records; "
                "a policy release's bundled pins do not establish current approval"
            )
        records_root = args.policy_root or SOURCE_ROOT
        if args.command == "pin-pr":
            result = pin_pr.run(args)
            print(json.dumps(result, indent=2, sort_keys=True))
            return {"fail": 1, "error": 2}.get(result.get("status"), 0)
        if args.command == "pin-batch":
            whole = args.all
            if args.operation != "plan" and not whole:
                try:
                    saved = records.read_json(args.plan)
                    whole = (
                        isinstance(saved, dict) and saved.get("scope") == "whole-batch"
                    )
                except candidates.ERRORS:
                    pass
            run_batch = batches.run if whole else candidates.run
            result = run_batch(
                args,
                source_root=SOURCE_ROOT,
                git_revision=git_revision,
                git_dirty=git_dirty,
                fingerprints=fingerprints,
                orchestrator_revision=globals().get("PACKAGED_REVISION")
                or git_revision(SOURCE_ROOT),
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return {"fail": 1, "error": 2}.get(result.get("status"), 0)
        config, pins = records.load(records_root)
        project = None
        if args.command in {"check", "ci", "compatibility", "vm", "agreement"}:
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
        elif args.command == "agreement":
            result = agreement.inspect(
                args.project_dir,
                project,
                config,
                pins,
                records_root,
                git_revision=git_revision,
                git_dirty=git_dirty,
            )
            result.update(
                memberSettings=member_settings(project),
                enrollment=enrollment(config, args.project),
            )
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
            records_revision = git_revision(records_root)
            result = check_compatibility(
                args.project_dir,
                args.project,
                config,
                pins,
                args.channel,
                args.batch,
                args.output,
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
        elif args.command == "check":
            result = inspect_project(
                args.project_dir,
                args.project,
                config,
                pins,
                args.batch,
                project=project,
            )
            if args.shell:
                result["issues"].extend(check_shell(args.project_dir))
                if result["issues"]:
                    result["status"] = "fail"
        else:
            result = audit_family(
                args.workspace,
                config,
                pins,
                args.fetch,
                args.github,
                records_root=records_root,
            )
        if project is not None:
            result["selectionStatus"] = "supported"
        result["checkerVersion"] = f"v{version}"
        result["policyRecordsRevision"] = (
            records_revision
            if args.command == "compatibility"
            else git_revision(records_root)
        )
        result["policyRecordsDigest"] = records.digest(config, pins)
        if args.command == "compatibility" and "artifacts" in result:
            (Path(result["artifacts"]) / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n"
            )
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
                    if isinstance(error, (InvalidDeclaration, releases.InvalidRelease))
                    else "unknown",
                    "error": str(error),
                }
            ),
            file=sys.stdout if args.command in {"audit", "agreement"} else sys.stderr,
        )
        return 2


class InvalidDeclaration(ValueError):
    """The inspected caller cannot select a valid policy contract."""


def member_project(root, name, config, *, hosted_inputs=None):
    try:
        project = declarations.inspect(
            root.resolve(),
            config["policyRepository"],
            name,
            config,
            checker_version=f"v{(SOURCE_ROOT / 'VERSION').read_text().strip()}",
            hosted_inputs=hosted_inputs,
        )
        if agreement.GATE in project["additionalRequiredChecks"]:
            agreement.require_caller(root, project, config["policyRepository"])
        return project
    except ValueError as error:
        raise InvalidDeclaration(str(error)) from error


def member_settings(project):
    return {field: project[field] for field in declarations.INPUT_FIELDS.values()}


def enrollment(config, name):
    return "enrolled" if name in config["_members"] else "not-enrolled"


def inspect_project(root, name, config, pins, batch_id=None, *, project):
    root = root.resolve()
    issues = check_structure(root, config, project["policyVersion"])
    revision = git_revision(root)
    candidate = candidate_for(pins, name, revision, batch_id)
    if candidate:
        if git_dirty(root):
            raise ValueError(
                "A candidate check requires the clean registered project commit"
            )
    pairs = allowed_pairs(pins, name, revision, batch_id)
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
    elif candidate:
        status = "candidate-ready"
    else:
        status = "pass"
    return {
        "project": name,
        "policyVersion": project["policyVersion"],
        "revision": revision,
        "candidateBatch": candidate["id"] if candidate else None,
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
    return issues


def allowed_pairs(pins, name, revision, batch_id=None):
    candidate = candidate_for(pins, name, revision, batch_id)
    if candidate:
        return [candidate["pins"]]
    pairs = [pins["approved"]] if pins["approved"] else []
    for batch in pins["batches"]:
        if name in batch["projects"] and batch["status"] in ACTIVE_BATCH_STATES:
            pairs.append(batch["pins"])
            if batch.get("previous"):
                pairs.append(batch["previous"])
    return pairs


def candidate_for(pins, name, revision, batch_id=None):
    if batch_id:
        batch = next(
            (entry for entry in pins["batches"] if entry["id"] == batch_id), None
        )
        if batch is None or batch["status"] != "candidate":
            raise ValueError("Requested batch is not a registered candidate")
        if revision is None or batch["projects"].get(name) != revision:
            raise ValueError(
                "Candidate is not registered for this exact project commit"
            )
        return batch
    matches = [
        batch
        for batch in pins["batches"]
        if batch["status"] == "candidate"
        and revision is not None
        and batch["projects"].get(name) == revision
    ]
    if len(matches) > 1:
        raise ValueError(
            "More than one candidate is registered for this project commit"
        )
    return matches[0] if matches else None


def check_compatibility(
    root, name, config, pins, channel, batch_id=None, output=None, *, project=None
):
    root = root.resolve()
    project = project or member_project(root, name, config)
    artifacts = (
        output.resolve()
        if output
        else Path(tempfile.mkdtemp(prefix="nixos-policy-compatibility-"))
    )
    if artifacts.is_relative_to(root):
        raise ValueError("Compatibility evidence must be outside the project checkout")
    if output:
        artifacts.mkdir(parents=True, exist_ok=False)
    result = {
        "project": name,
        "policyVersion": project["policyVersion"],
        "memberSettings": member_settings(project),
        "enrollment": enrollment(config, name),
        "revision": git_revision(root),
        "checkerRevision": globals().get("PACKAGED_REVISION")
        or git_revision(SOURCE_ROOT),
        "checkerSourceDigest": hashlib.sha256(
            b"".join(
                path.read_bytes()
                for path in (
                    *sorted((SOURCE_ROOT / "tools").glob("*.py")),
                    SOURCE_ROOT / "policy/requirements.json",
                    SOURCE_ROOT / "VERSION",
                )
            )
        ).hexdigest(),
        "channel": channel,
        "system": None,
        "expectedRevision": None,
        "resolvedRevision": None,
        "candidateBatch": None,
        "pinStatus": None,
        "status": "error",
        "commands": [],
        "issues": [],
        "artifacts": str(artifacts),
    }
    before = None
    source_before = None
    try:
        records.require_revision(result["revision"])
        result["sourceDirty"] = git_dirty(root)
        source_before = fingerprints(root)
        result["sourceDigest"] = hashlib.sha256(
            json.dumps(source_before, sort_keys=True).encode()
        ).hexdigest()
        before = (root / "flake.lock").read_bytes()
        committed_lock = subprocess.check_output(
            ["git", "-C", str(root), "show", "HEAD:flake.lock"],
            stderr=subprocess.DEVNULL,
        )
        if before != committed_lock:
            raise ValueError("Compatibility requires the committed root lock")
        selected_nixpkgs(LockGraph(json.loads(before)))
        candidate = candidate_for(pins, name, result["revision"], batch_id)
        if candidate:
            result["candidateBatch"] = candidate["id"]
            result["pinStatus"] = "candidate"
            if result["sourceDirty"]:
                raise ValueError(
                    "A candidate check requires the clean registered project commit"
                )
        # Active rollouts always test the central approved pair, even when old
        # locks remain allowed. Candidates select exactly their registered pair.
        pair = candidate["pins"] if candidate else pins["approved"]
        if pair is None:
            raise ValueError("No approved family baseline for compatibility")
        result["pinStatus"] = "candidate" if candidate else "approved"
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
        (artifacts / "metadata.json").write_text(metadata)
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
        result["status"] = "candidate-pass" if candidate else "pass"
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


def audit_family(
    workspace, config, pins, fetch=False, github=False, *, records_root=None
):
    reports = []
    graph = {}
    snapshot = records.identity(config, pins, git_revision(records_root))
    for name, repository in config["_members"].items():
        root = workspace / name
        report = {
            "project": name,
            "repository": repository,
            "enrollment": "enrolled",
            "revision": None,
            "policyVersion": None,
            "selectionStatus": "unknown",
            "checkerVersion": None,
            "checkerRepository": config["policyRepository"],
            "checkerRevision": None,
            "records": snapshot,
            "status": "fail",
            "issues": [],
        }
        try:
            if fetch and not root.exists():
                workspace.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        f"https://github.com/{repository}.git",
                        str(root),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=180,
                )
            if not root.exists():
                report["issues"].append("checkout missing")
            else:
                revision = git_revision(root)
                records.require_revision(revision)
                report["revision"] = revision
                if git_dirty(root):
                    raise ValueError("Audit requires a clean exact member commit")
                try:
                    caller_path, _, caller, version = declarations.read_identity(
                        root, config["policyRepository"], name
                    )
                    report["policyVersion"] = version
                    inputs = caller["with"]
                except ValueError as error:
                    report["selectionStatus"] = "invalid"
                    report["issues"].append(f"ci: {error}")
                else:
                    report["selectionStatus"] = "supported"
                    release = releases.inspect_release(
                        config["policyRepository"], version
                    )
                    report["checkerRevision"] = release["revision"]
                    settings = {
                        field: json.loads(inputs.get(key, "[]"))
                        for key, field in declarations.INPUT_FIELDS.items()
                    }
                    assessed = releases.check_member(
                        release,
                        root,
                        name,
                        repository,
                        revision,
                        records_root,
                        snapshot,
                        settings=settings,
                    )
                    report.update(assessed)
                    # Checker reports cannot redefine trusted enrollment or identity.
                    report.update(
                        repository=repository,
                        enrollment="enrolled",
                        records=snapshot,
                    )
                    report["assessment"] = assessed["status"]
                    if report["status"] == "candidate-ready":
                        report["status"] = "fail"
                        report["issues"].append(
                            "Candidate validation does not establish approved-pin compliance"
                        )
                    graph[name] = report["dependencies"]
                    if github:
                        report["issues"].extend(
                            check_github(
                                {"repository": repository},
                                report["requiredChecks"],
                                workflow=str(caller_path.relative_to(root)),
                            )
                        )
                        if report["issues"]:
                            report["status"] = "fail"
                if git_revision(root) != revision or git_dirty(root):
                    raise ValueError("Member checkout changed during audit")
        except (
            ValueError,
            OSError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ) as error:
            report["status"] = "error"
            report["selectionStatus"] = (
                "invalid" if isinstance(error, releases.InvalidRelease) else "unknown"
            )
            report["issues"].append(f"inspection: {error}")
        reports.append(report)
    # A released checker reads the same directory; reject a concurrently changed snapshot.
    try:
        current_config, current_pins = records.load(records_root)
        changed = (
            records.identity(current_config, current_pins, git_revision(records_root))
            != snapshot
        )
    except (ValueError, OSError, KeyError, TypeError):
        changed = True
    if changed:
        for report in reports:
            report["status"] = "error"
            report["issues"].append("Central records changed during audit")
    cycles = dependency_cycles(graph)
    return {
        "status": "error"
        if changed or any(item["status"] == "error" for item in reports)
        else "fail"
        if cycles or any(item["status"] == "fail" for item in reports)
        else "reported",
        "approvedPins": pins["approved"] is not None,
        "projects": reports,
        "cycles": cycles,
        "records": snapshot,
        "issues": ["Central records changed during audit"] if changed else [],
    }


def check_github(project, checks, *, workflow):
    repository = project["repository"]
    info = github_get(f"repos/{repository}")
    if (
        not isinstance(info, dict)
        or not isinstance(info.get("default_branch"), str)
        or not info["default_branch"]
    ):
        raise ValueError("GitHub branch inspection is incomplete; settings are unknown")
    merge_settings = github_merge_settings(repository, info)
    branch = quote(info["default_branch"], safe="")
    rules = github_get(f"repos/{repository}/rules/branches/{branch}")
    if not isinstance(rules, list) or any(
        not isinstance(rule, dict) or not isinstance(rule.get("type"), str)
        for rule in rules
    ):
        raise ValueError("GitHub rule inspection is incomplete; settings are unknown")
    issues = []
    if (
        not merge_settings["allow_squash_merge"]
        or merge_settings["allow_merge_commit"]
        or merge_settings["allow_rebase_merge"]
    ):
        issues.append("github: configure squash as the only merge method")
    pr_rule = any(rule["type"] == "pull_request" for rule in rules)
    contexts = set()
    for rule in rules:
        if rule["type"] == "required_status_checks":
            parameters = rule.get("parameters")
            if not isinstance(parameters, dict):
                raise ValueError(
                    "GitHub required checks are incomplete; settings are unknown"
                )
            contexts.update(github_contexts(parameters.get("required_status_checks")))
    if not pr_rule or not set(checks).issubset(contexts):
        details = github_get(f"repos/{repository}/branches/{branch}")
        if not isinstance(details, dict) or not isinstance(
            details.get("protected"), bool
        ):
            raise ValueError(
                "GitHub protection inspection is incomplete; settings are unknown"
            )
        if details["protected"]:
            protection = github_get(f"repos/{repository}/branches/{branch}/protection")
            if (
                not isinstance(protection, dict)
                or not {"required_pull_request_reviews", "required_status_checks"}
                <= protection.keys()
            ):
                raise ValueError(
                    "GitHub protection inspection is incomplete; settings are unknown"
                )
            reviews = protection["required_pull_request_reviews"]
            if reviews is not None and (
                not isinstance(reviews, dict)
                or type(reviews.get("required_approving_review_count")) is not int
                or reviews["required_approving_review_count"] < 0
            ):
                raise ValueError(
                    "GitHub review inspection is incomplete; settings are unknown"
                )
            pr_rule = pr_rule or bool(protection.get("required_pull_request_reviews"))
            status_checks = protection["required_status_checks"]
            if status_checks is not None and (
                not isinstance(status_checks, dict)
                or not {"contexts", "checks"} & status_checks.keys()
            ):
                raise ValueError(
                    "GitHub required checks are incomplete; settings are unknown"
                )
            status_checks = {} if status_checks is None else status_checks
            if not isinstance(
                status_checks, dict
            ) or not declarations.valid_check_names(status_checks.get("contexts", [])):
                raise ValueError(
                    "GitHub required checks are incomplete; settings are unknown"
                )
            contexts.update(status_checks.get("contexts", []))
            contexts.update(github_contexts(status_checks.get("checks", [])))
    if not pr_rule:
        issues.append("github: pull requests are not required")
    for check in checks:
        if check not in contexts:
            issues.append(f"github: missing required check '{check}'")
    issues.extend(check_github_enforcement(repository, workflow))
    return issues


def check_github_enforcement(repository, workflow):
    permissions = github_get(f"repos/{repository}/actions/permissions")
    if not isinstance(permissions, dict) or not isinstance(
        permissions.get("enabled"), bool
    ):
        raise ValueError(
            "GitHub Actions inspection is incomplete; enforcement is unknown"
        )
    details = github_get(
        f"repos/{repository}/actions/workflows/{quote(Path(workflow).name, safe='')}"
    )
    if (
        not isinstance(details, dict)
        or details.get("path") != workflow
        or not isinstance(details.get("state"), str)
        or not details["state"]
    ):
        raise ValueError(
            "GitHub workflow inspection is incomplete or disagrees with the discovered caller; enforcement is unknown"
        )
    issues = []
    if not permissions["enabled"]:
        issues.append("github: repository Actions are disabled")
    if details["state"] != "active":
        issues.append(
            f"github: policy caller workflow {workflow} is not active ({details['state']})"
        )
    return issues


def github_contexts(checks):
    if not isinstance(checks, list) or any(
        not isinstance(check, dict)
        or not isinstance(check.get("context"), str)
        or not check["context"].strip()
        for check in checks
    ):
        raise ValueError("GitHub required checks are incomplete; settings are unknown")
    return [check["context"] for check in checks]


def github_merge_settings(repository, info):
    fields = ("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge")
    if all(isinstance(info.get(field), bool) for field in fields):
        return info

    # Read-only tokens can inspect these settings through GraphQL even when
    # GitHub omits them from the REST repository response.
    owner, name = repository.split("/", 1)
    result = github_request(
        "graphql",
        {
            "query": """query($owner: String!, $name: String!) {
                repository(owner: $owner, name: $name) {
                    allow_squash_merge: squashMergeAllowed
                    allow_merge_commit: mergeCommitAllowed
                    allow_rebase_merge: rebaseMergeAllowed
                }
            }""",
            "variables": {"owner": owner, "name": name},
        },
    )
    data = (
        result.get("data")
        if isinstance(result, dict) and not result.get("errors")
        else None
    )
    settings = data.get("repository") if isinstance(data, dict) else None
    if not isinstance(settings, dict) or not all(
        isinstance(settings.get(field), bool) for field in fields
    ):
        raise ValueError(
            f"GitHub merge inspection unavailable for {repository}; settings are unknown"
        )
    return settings


def github_get(path):
    return github_request(path)


def github_request(path, payload=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "GitHub inspection requires a token in GH_TOKEN or GITHUB_TOKEN with Actions, Administration, Contents, and Metadata read access to enrolled members; configure the MEMBER_AUDIT_TOKEN secret for maintenance"
        )
    headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    try:
        with urlopen(
            Request(f"https://api.github.com/{path}", data=data, headers=headers),
            timeout=30,
        ) as response:
            return json.load(response)
    except HTTPError as error:
        raise ValueError(
            f"GitHub inspection unavailable for {path}: HTTP {error.code}; verify repository access and read permissions; settings are unknown"
        ) from error


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


def git_dirty(root):
    return bool(
        subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain",
                "--untracked-files=normal",
            ],
            text=True,
        ).strip()
    )


if __name__ == "__main__":
    sys.exit(main())
