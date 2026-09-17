"""External project policy checks; see docs/checker.md for the command contract."""

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml


REVISION = re.compile(r"[0-9a-f]{40}\Z")
POLICY_VERSION = re.compile(
    r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)
SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PR_ACTIVITIES = {"opened", "synchronize", "reopened", "edited"}
ACTIVE_BATCH_STATES = {"approved", "rolling", "paused"}
TITLE = re.compile(
    r"(?:build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(?:\([^()\r\n]+\))?!?: [^\r\n]+\Z"
)
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
    title = commands.add_parser("title", help="Check a Conventional Commit PR title")
    title.add_argument("title")
    check = commands.add_parser(
        "check", help="Check one project; missing approval fails"
    )
    check.add_argument("project_dir", type=Path)
    check.add_argument("--project", required=True)
    check.add_argument(
        "--readiness",
        action="store_true",
        help="Allow enrollment readiness before adoption",
    )
    check.add_argument(
        "--batch", help="Registered candidate batch, bound to the tested commit"
    )
    check.add_argument(
        "--shell", action="store_true", help="Execute the project's Nix shell"
    )
    lint = commands.add_parser(
        "lint", help="Run Nix lint and formatting in a temporary copy"
    )
    lint.add_argument("project_dir", type=Path)
    shell = commands.add_parser(
        "shell", help="Probe common tools and the root formatter"
    )
    shell.add_argument("project_dir", type=Path)
    vm = commands.add_parser(
        "vm", help="Run the centrally declared VM targets on a suitable host"
    )
    vm.add_argument("project_dir", type=Path)
    vm.add_argument("--project", required=True)
    audit = commands.add_parser(
        "audit", help="Report family drift and pending adoption"
    )
    audit.add_argument("workspace", type=Path)
    audit.add_argument(
        "--fetch", action="store_true", help="Clone missing public checkouts"
    )
    audit.add_argument(
        "--github", action="store_true", help="Inspect adopted projects' merge gates"
    )
    candidate = commands.add_parser(
        "candidate", help="Print an unapproved pin proposal"
    )
    candidate.add_argument("--stable", required=True)
    candidate.add_argument("--unstable", required=True)
    args = parser.parse_args(argv)
    try:
        require_policy_version(f"v{version}")
        if args.command in {"check", "audit", "vm"} and args.policy_root is None:
            raise ValueError(
                f"{args.command} requires --policy-root with current central records; "
                "a policy release's bundled pins do not establish current approval"
            )
        records_root = args.policy_root or SOURCE_ROOT
        config, pins = load_policy(records_root)
        if args.command == "validate":
            result = {"status": "valid", "approvedPins": pins["approved"] is not None}
        elif args.command == "title":
            result = {"status": "pass" if TITLE.fullmatch(args.title) else "fail"}
        elif args.command == "candidate":
            pair = {"stable": args.stable, "unstable": args.unstable}
            validate_pair(pair)
            result = {"schemaVersion": 1, "status": "proposal", "pins": pair}
        elif args.command == "shell":
            issues = check_shell(args.project_dir, config["requiredTools"])
            result = {"status": "fail" if issues else "pass", "issues": issues}
        elif args.command == "lint":
            issues = check_lint(args.project_dir)
            result = {"status": "fail" if issues else "pass", "issues": issues}
        elif args.command == "vm":
            targets = config["projects"][args.project]["vmTargets"]
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
            }
        elif args.command == "check":
            selected = config["projects"][args.project]["policyVersion"]
            if selected is not None and selected != f"v{version}":
                raise ValueError(
                    f"Project {args.project} selects policy {selected}; "
                    f"run that release instead of checker v{version}"
                )
            result = inspect_project(
                args.project_dir,
                args.project,
                config,
                pins,
                args.batch,
                readiness=args.readiness,
            )
            if args.shell:
                result["issues"].extend(
                    check_shell(args.project_dir, config["requiredTools"])
                )
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
        result["checkerVersion"] = f"v{version}"
        result["policyRecordsRevision"] = git_revision(records_root)
        result["policyRecordsDigest"] = hashlib.sha256(
            json.dumps(
                {
                    "projects": {
                        key: value
                        for key, value in config.items()
                        if key not in {"systems", "requiredTools", "readmeSections"}
                    },
                    "pins": pins,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        print(json.dumps(result, indent=2, sort_keys=True))
        return {"fail": 1, "error": 2}.get(result.get("status"), 0)
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2


def load_policy(root):
    config = read_json(root / "policy/projects.json")
    pins = read_json(root / "policy/pins.json")
    if not isinstance(config, dict) or not isinstance(pins, dict):
        raise ValueError("Policy records must be JSON objects")
    if config.get("schemaVersion") != 2 or pins.get("schemaVersion") != 1:
        raise ValueError("Unsupported policy record schema")
    requirements = read_json(SOURCE_ROOT / "policy/requirements.json")
    if requirements.get("schemaVersion") != 1:
        raise ValueError("Unsupported policy requirements schema")
    # Requirements belong to the selected checker release, never the live records.
    for field in ("systems", "requiredTools", "readmeSections"):
        if field in config:
            raise ValueError(
                f"{field} belongs in the release's policy/requirements.json"
            )
        config[field] = requirements[field]
    if not REPOSITORY.fullmatch(config["policyRepository"]):
        raise ValueError("Invalid policy repository")
    if config["systems"] != ["x86_64-linux", "aarch64-linux"]:
        raise ValueError("Both agreed Linux systems must be declared")
    for tool in config["requiredTools"]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", tool):
            raise ValueError(f"Invalid tool name: {tool}")
    for name, project in config["projects"].items():
        if not re.fullmatch(r"[a-z0-9-]+", name) or not REPOSITORY.fullmatch(
            project["repository"]
        ):
            raise ValueError(f"Invalid project identity: {name}")
        if not isinstance(project["adopted"], bool):
            raise ValueError(f"Invalid adoption state: {name}")
        for target in project["vmTargets"]:
            if not re.fullmatch(r"[a-z0-9-]+", target):
                raise ValueError(f"Invalid VM target for {name}")
        if project["policyVersion"] is not None or project["adopted"]:
            require_policy_version(project["policyVersion"])
        if project["adopted"]:
            if not project.get("requiredChecks"):
                raise ValueError(
                    f"Adopted project {name} needs verified required check names"
                )
    if pins["approved"] is not None:
        validate_pair(pins["approved"])
    ids = set()
    for batch in pins["batches"]:
        if batch["id"] in ids:
            raise ValueError("Duplicate pin batch id")
        ids.add(batch["id"])
        if batch["status"] not in {
            "candidate",
            "approved",
            "rolling",
            "paused",
            "complete",
            "withdrawn",
        }:
            raise ValueError("Invalid pin batch state")
        validate_pair(batch["pins"])
        if batch.get("previous") is not None:
            validate_pair(batch["previous"])
        if batch["status"] in ACTIVE_BATCH_STATES:
            if pins["approved"] is None or pins["approved"] not in (
                batch["pins"],
                batch.get("previous"),
            ):
                raise ValueError(
                    "An active rollout must be tied to the approved baseline"
                )
        for name, revision in batch["projects"].items():
            if name not in config["projects"]:
                raise ValueError(f"Unknown project in batch: {name}")
            require_revision(revision)
    return config, pins


def inspect_project(root, name, config, pins, batch_id=None, *, readiness=False):
    root = root.resolve()
    if name not in config["projects"]:
        raise ValueError(f"Unknown project: {name}")
    project = config["projects"][name]
    issues = check_structure(root, config, project["policyVersion"])
    revision = git_revision(root)
    candidate = candidate_for(pins, name, revision, batch_id)
    if candidate:
        dirty = subprocess.check_output(
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
        if dirty:
            raise ValueError(
                "A candidate check requires the clean registered project commit"
            )
    pairs = allowed_pairs(pins, name, revision, batch_id)
    if not pairs:
        issues.append("pins: no approved family baseline; adoption cannot pass yet")
    observations = []
    dependencies = set()
    locks = source_files(root, "flake.lock")
    if root / "flake.lock" not in locks:
        issues.append("pins: missing root flake.lock")
    known_repos = {
        item["repository"].lower(): key for key, item in config["projects"].items()
    }
    known_repos["petohorvath/nix-nftzones"] = "nixos-nftzones"
    for path in locks:
        lock = LockGraph(read_json(path))
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
            }
            observations.append(observation)
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
        observations
        and pairs
        and not any(pins_match(observations, pair) for pair in pairs)
    ):
        issues.append("pins: project lockfiles do not share one allowed pair")
    issues.extend(
        check_caller(root, config["policyRepository"], project["policyVersion"], name)
    )
    if not project["adopted"] and not readiness:
        issues.append(
            "adoption: project is pending; use --readiness to validate enrollment"
        )
    if issues:
        status = "fail"
    elif candidate:
        status = "candidate-ready"
    elif not project["adopted"]:
        status = "ready"
    else:
        status = "pass"
    return {
        "project": name,
        "policyVersion": project["policyVersion"],
        "revision": revision,
        "candidateBatch": candidate["id"] if candidate else None,
        "status": status,
        "issues": issues,
        "pins": observations,
        "dependencies": sorted(dependencies),
    }


def check_structure(root, config, version=None):
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
    if (root / "dev/flake.nix").exists():
        issues.append(
            "development: separate dev/flake.nix must be migrated to the root"
        )
    readme = root / "README.md"
    if readme.exists():
        headings = {
            heading.lower()
            for heading in re.findall(r"^##\s+(.+?)\s*$", readme.read_text(), re.M)
        }
        for section in config["readmeSections"]:
            if section.lower() not in headings:
                issues.append(f"documentation: README is missing '{section}'")
    rule_link = re.compile(
        r"https://github\.com/"
        + re.escape(config["policyRepository"])
        + "/blob/"
        + (
            re.escape(version)
            if version
            else POLICY_VERSION.pattern.removesuffix(r"\Z")
        )
        + r"/POLICY\.md(?:[)#\s]|$)"
    )
    for file in ["CONTRIBUTING.md", "AGENTS.md"]:
        path = root / file
        if path.exists() and not rule_link.search(path.read_text()):
            issues.append(
                f"documentation: {file} needs the registered policy release's POLICY.md link"
            )
    return issues


def check_caller(root, repository, version, project_name=None):
    issues = []
    if version is None:
        issues.append("ci: no policy release version is recorded")
    expected = f"{repository}/.github/workflows/check.yml@{version}"
    for path in sorted((root / ".github/workflows").glob("*")):
        if path.suffix not in {".yml", ".yaml"}:
            continue
        try:
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        except yaml.YAMLError as error:
            issues.append(f"ci: invalid YAML in {path.name}: {error}")
            continue
        if not isinstance(workflow, dict):
            continue
        events = workflow.get("on", {})
        has_pr = events == "pull_request" or (
            isinstance(events, (dict, list)) and "pull_request" in events
        )
        for job in workflow.get("jobs", {}).values():
            if (
                has_pr
                and isinstance(job, dict)
                and version
                and job.get("uses") == expected
            ):
                if job.get("if"):
                    issues.append(
                        "ci: the required policy caller must run for every PR"
                    )
                if "needs" in job:
                    issues.append(
                        "ci: policy caller dependencies are not supported; run it independently for every PR"
                    )
                trigger = (
                    events.get("pull_request") if isinstance(events, dict) else None
                )
                supported_activities = (
                    isinstance(trigger, dict)
                    and set(trigger) == {"types"}
                    and isinstance(trigger["types"], list)
                    and all(isinstance(activity, str) for activity in trigger["types"])
                    and set(trigger["types"]) == PR_ACTIVITIES
                )
                if not supported_activities:
                    issues.append(
                        "ci: policy PR trigger must declare exactly opened, synchronize, reopened, edited activities without other filters"
                    )
                if job.get("with", {}).get("policy_version") != version:
                    issues.append(
                        "ci: policy_version must equal the registered release tag"
                    )
                if project_name and job.get("with", {}).get("project") != project_name:
                    issues.append("ci: caller must select its own registered project")
                return issues
    issues.append(
        "ci: missing unconditional PR caller at the registered policy release"
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


def check_shell(root, tools):
    # --ignore-environment prevents host tools from satisfying shell requirements.
    script = 'set -eu; for tool in "$@"; do command -v "$tool"; case "$tool" in nix) "$tool" --version ;; *) "$tool" --help >/dev/null ;; esac; done'
    try:
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
                script,
                "policy-shell",
                *tools,
            ],
            check=True,
            timeout=900,
            stdout=sys.stderr,
        )
        system = subprocess.check_output(
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
            text=True,
            timeout=30,
        ).strip()
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


def check_lint(root):
    with tempfile.TemporaryDirectory(prefix="nixos-policy-lint-") as temporary:
        source = Path(temporary) / "source"
        shutil.copytree(
            root,
            source,
            ignore=shutil.ignore_patterns(
                ".git", ".direnv", "result", "result-*", "__pycache__", ".ruff_cache"
            ),
        )
        paths = [
            f"./{path.relative_to(source)}" for path in source_files(source, "*.nix")
        ]
        script = 'set -eu; for source in "$@"; do statix check "$source"; deadnix --fail "$source"; done'
        before = fingerprints(source)
        try:
            subprocess.run(
                [
                    "nix",
                    "develop",
                    "--no-update-lock-file",
                    "--ignore-environment",
                    f"path:{source}",
                    "--command",
                    "bash",
                    "-c",
                    script,
                    "policy-lint",
                    *paths,
                ],
                cwd=source,
                check=True,
                timeout=900,
                stdout=sys.stderr,
            )
            subprocess.run(
                ["nix", "fmt", "--no-update-lock-file"],
                cwd=source,
                check=True,
                timeout=900,
                stdout=sys.stderr,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return [f"lint: execution failed: {error}"]
        after = fingerprints(source)
        changed = sorted(
            path
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        )
        return [f"formatting: changes required in {path}" for path in changed]


def fingerprints(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_files(root, "*", include_vendor=True)
        if path.name != ".treefmt-cache"
    }


def audit_family(
    workspace, config, pins, fetch=False, github=False, *, records_root=None
):
    reports = []
    graph = {}
    checker_version = f"v{(SOURCE_ROOT / 'VERSION').read_text().strip()}"
    for name, project in config["projects"].items():
        root = workspace / name
        if fetch and not root.exists():
            workspace.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    f"https://github.com/{project['repository']}.git",
                    str(root),
                ],
                check=True,
                timeout=180,
            )
        if not root.exists():
            report = {"project": name, "status": "fail", "issues": ["checkout missing"]}
        elif (
            records_root is not None
            and project["adopted"]
            and project["policyVersion"] != checker_version
        ):
            report = inspect_released_project(
                root,
                name,
                config["policyRepository"],
                project["policyVersion"],
                records_root,
            )
            graph[name] = report.get("dependencies", [])
        else:
            report = inspect_project(root, name, config, pins, readiness=True)
            graph[name] = report["dependencies"]
        if not project["adopted"]:
            report["assessment"] = report["status"]
            report["status"] = "pending-adoption"
        elif github:
            try:
                report["issues"].extend(check_github(project))
            except (ValueError, OSError) as error:
                report["issues"].append(f"github: {error}")
                report["status"] = "error"
            else:
                if report["issues"] and report["status"] != "error":
                    report["status"] = "fail"
        reports.append(report)
    cycles = dependency_cycles(graph)
    return {
        "status": "error"
        if any(item["status"] == "error" for item in reports)
        else "fail"
        if cycles or any(item["status"] == "fail" for item in reports)
        else "reported",
        "approvedPins": pins["approved"] is not None,
        "projects": reports,
        "cycles": cycles,
    }


def inspect_released_project(root, name, repository, version, records_root):
    require_policy_version(version)
    command = [
        "nix",
        "run",
        "--no-update-lock-file",
        f"github:{repository}/{version}",
        "--",
        "--policy-root",
        str(records_root.resolve()),
        "check",
        str(root.resolve()),
        "--project",
        name,
    ]
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, timeout=900, check=False
        )
        if process.returncode not in {0, 1}:
            raise ValueError(process.stderr.strip() or "Released checker could not run")
        report = json.loads(process.stdout)
        if (
            not isinstance(report, dict)
            or report.get("project") != name
            or report.get("checkerVersion") != version
            or report.get("status") not in {"pass", "fail", "candidate-ready"}
            or (report["status"] == "fail") != (process.returncode == 1)
            or not isinstance(report.get("issues"), list)
            or not isinstance(report.get("dependencies"), list)
        ):
            raise ValueError("Released checker returned an incompatible report")
        return report
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        return {
            "project": name,
            "policyVersion": version,
            "status": "error",
            "issues": [f"policy release: {error}"],
        }


def check_github(project):
    repository = project["repository"]
    info = github_get(f"repos/{repository}")
    merge_settings = github_merge_settings(repository, info)
    branch = quote(info["default_branch"], safe="")
    rules = github_get(f"repos/{repository}/rules/branches/{branch}")
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
            contexts.update(
                check["context"]
                for check in rule["parameters"]["required_status_checks"]
            )
    if not pr_rule or not set(project["requiredChecks"]).issubset(contexts):
        details = github_get(f"repos/{repository}/branches/{branch}")
        if details["protected"]:
            protection = github_get(f"repos/{repository}/branches/{branch}/protection")
            pr_rule = pr_rule or bool(protection.get("required_pull_request_reviews"))
            status_checks = protection.get("required_status_checks") or {}
            contexts.update(status_checks.get("contexts", []))
            contexts.update(
                check["context"] for check in status_checks.get("checks", [])
            )
    if not pr_rule:
        issues.append("github: pull requests are not required")
    for check in project["requiredChecks"]:
        if check not in contexts:
            issues.append(f"github: missing required check '{check}'")
    return issues


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
            "GitHub inspection requires a token in GH_TOKEN or GITHUB_TOKEN with Administration, Contents, and Metadata read access to enrolled members; configure the MEMBER_AUDIT_TOKEN secret for maintenance"
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


class LockGraph:
    def __init__(self, lock):
        if not isinstance(lock, dict) or lock.get("version") != 7:
            raise ValueError("Unsupported flake lock format")
        self.nodes = lock["nodes"]
        self.root = lock["root"]
        if not isinstance(self.nodes, dict) or not isinstance(self.root, str):
            raise ValueError("Invalid lock nodes or root")
        if self.root not in self.nodes:
            raise ValueError("Missing lock root")
        for node in self.nodes.values():
            if not isinstance(node, dict) or any(
                not isinstance(node.get(field, {}), dict)
                for field in ("inputs", "locked", "original")
            ):
                raise ValueError("Invalid lock node structure")

    def resolve(self, reference, aliases=()):
        if isinstance(reference, str):
            if reference not in self.nodes:
                raise ValueError(f"Missing lock node: {reference}")
            return reference
        if not isinstance(reference, list) or not all(
            isinstance(item, str) for item in reference
        ):
            raise ValueError("Invalid lock input reference")
        path = tuple(reference)
        if path in aliases:
            raise ValueError(f"Cyclic follows path: {reference}")
        current = self.root
        for part in path:
            inputs = self.nodes[current].get("inputs", {})
            if part not in inputs:
                raise ValueError(f"Missing follows path: {reference}")
            current = self.resolve(inputs[part], (*aliases, path))
        return current

    def reachable(self):
        result = {}
        pending = [self.root]
        while pending:
            current = pending.pop()
            if current in result:
                continue
            result[current] = self.nodes[current]
            pending.extend(
                self.resolve(reference)
                for reference in self.nodes[current].get("inputs", {}).values()
            )
        return result


def dependency_cycles(graph):
    visited = set()
    cycles = []

    def visit(node, stack):
        if node in stack:
            cycles.append([*stack[stack.index(node) :], node])
        elif node not in visited:
            for dependency in graph.get(node, []):
                visit(dependency, [*stack, node])
            visited.add(node)

    for node in graph:
        visit(node, [])
    return cycles


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


def repository_identity(node):
    for source in [node.get("locked", {}), node.get("original", {})]:
        if "owner" in source and "repo" in source:
            return f"{source['owner']}/{source['repo']}".lower()
        match = re.search(r"github\.com[:/]([^/]+/[^/?#]+)", source.get("url", ""))
        if match:
            return match.group(1).removesuffix(".git").lower()
    return None


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


def validate_pair(pair):
    if not isinstance(pair, dict) or set(pair) != {"stable", "unstable"}:
        raise ValueError("A pin pair must contain stable and unstable revisions")
    for revision in pair.values():
        require_revision(revision)


def require_policy_version(value):
    if not isinstance(value, str) or not POLICY_VERSION.fullmatch(value):
        raise ValueError("Policy versions must be exact release tags such as v0.1.0")


def require_revision(value):
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise ValueError("Expected an exact 40-character lowercase Git commit")


def read_json(path):
    with path.open() as stream:
        return json.load(stream)


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
