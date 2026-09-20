"""Compare declarations from the exact enrolled member sources in a lock graph."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

if __package__:
    from . import declarations, locks, records, releases, support
else:
    import declarations
    import locks
    import records
    import releases
    import support


GATE = "Integration / Policy agreement"
EXCLUDED_DIRS = {
    ".git",
    ".direnv",
    "vendor",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
}


def require_caller(root, project, repository):
    policy_path, policy_workflow, policy_caller, _ = declarations.discover(
        root, repository
    )
    caller_path, workflow, caller, version = declarations.discover(
        root, repository, workflow_name="agreement.yml"
    )
    policy_job = next(
        key for key, job in policy_workflow["jobs"].items() if job is policy_caller
    )
    declarations.require_execution(workflow, caller, "Integration", needs=policy_job)
    expected = {
        "project": project["project"],
        "policy_version": version,
        "project_revision": f"${{{{ needs.{policy_job}.outputs.project_revision }}}}",
        "records_revision": f"${{{{ needs.{policy_job}.outputs.records_revision }}}}",
    }
    if (
        caller_path != policy_path
        or version != project["policyVersion"]
        or caller.get("with") != expected
    ):
        raise ValueError(
            "Agreement caller must use its Policy caller's identity, release, and exact source/record snapshot outputs"
        )


def git(root, *arguments):
    process = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *arguments],
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if process.returncode:
        raise ValueError(
            process.stderr.strip() or "Cannot inspect the exact Git source"
        )
    return process.stdout


class GitTree:
    """Read Git blobs without checking out or executing member code."""

    def __init__(self, repository, revision, directory=""):
        self.repository, self.revision = repository, revision
        prefix = directory + "/" if directory else ""
        self.files = {}
        for entry in git(
            repository, "ls-tree", "-r", "-z", "--full-tree", revision
        ).split("\0"):
            if not entry:
                continue
            metadata, name = entry.split("\t", 1)
            mode, kind, digest = metadata.split()
            if name.startswith(prefix):
                self.files[name[len(prefix) :]] = (mode, kind, digest)

    def read(self, name):
        mode, kind, digest = self.files[name]
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Required source is not a regular committed file: {name}")
        return git(self.repository, "cat-file", "blob", digest)

    def declaration(self, destination):
        for name in self.files:
            path = PurePosixPath(name)
            if path.parent == PurePosixPath(".github/workflows") and path.suffix in {
                ".yml",
                ".yaml",
            }:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self.read(name))
        return destination

    def lockfiles(self):
        return [
            name
            for name in sorted(self.files)
            if PurePosixPath(name).name == "flake.lock"
            and not any(
                part in EXCLUDED_DIRS or part == "result" or part.startswith("result-")
                for part in PurePosixPath(name).parts[:-1]
            )
        ]


def member_repositories(config):
    members = {
        repository.lower(): name for name, repository in config["_members"].items()
    }
    canonical = "petohorvath/nixos-nftzones"
    if canonical in members:
        members["petohorvath/nix-nftzones"] = members[canonical]
    return members


def source_identity(source):
    identity = locks.repository_identity({"locked": source})
    if identity is not None:
        return identity
    # Archive URLs must not omit a member just because their transport is unsupported.
    parsed = urlsplit(source.get("url", ""))
    if parsed.hostname in {"github.com", "codeload.github.com"}:
        candidate = "/".join(unquote(parsed.path).strip("/").split("/")[:2])
        if records.REPOSITORY.fullmatch(candidate):
            return candidate.lower()
    return None


def locked_source(node):
    source = node.get("locked", {})
    revision = source.get("rev")
    if not isinstance(revision, str) or not releases.REVISION.fullmatch(revision):
        raise ValueError("Consumed member source needs an exact locked Git revision")
    identity = locks.repository_identity({"locked": source})
    directory = source.get("dir", "")
    if (
        not isinstance(directory, str)
        or directory.startswith("/")
        or any(part in {".", ".."} for part in directory.split("/") if part)
        or "\\" in directory
    ):
        raise ValueError(
            "Consumed member source needs a safe relative flake subdirectory"
        )
    if source.get("type") == "github" and identity is not None:
        url = f"https://github.com/{source['owner']}/{source['repo']}.git"
    elif source.get("type") == "git" and identity is not None:
        url = source.get("url", "")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"https", "ssh"}
            or parsed.hostname != "github.com"
            or parsed.query
            or parsed.fragment
            or parsed.port is not None
            or parsed.password
            or (parsed.scheme == "https" and parsed.username)
        ):
            raise ValueError(
                "Consumed member Git URL must use GitHub HTTPS or SSH without query parameters"
            )
    else:
        raise ValueError(
            "Consumed member source requires a supported GitHub or Git transport"
        )
    if source.get("lfs") or source.get("submodules"):
        raise ValueError(
            "Member declaration inspection does not support LFS or submodule source expansion"
        )
    return {
        "type": source["type"],
        "url": url,
        "revision": revision,
        "dir": directory.rstrip("/"),
    }


def fetch(source, destination):
    destination.mkdir()
    git(destination, "init", "--bare", "--quiet")
    git(
        destination,
        "fetch",
        "--quiet",
        "--depth=1",
        "--no-tags",
        "--",
        source["url"],
        source["revision"],
    )
    if (
        git(destination, "rev-parse", "FETCH_HEAD^{commit}").strip()
        != source["revision"]
    ):
        raise ValueError("Fetched member source substituted the locked revision")
    return GitTree(destination, source["revision"], source["dir"])


def member_edges(graph, owners):
    edges = {}
    for node_id, owner in owners.items():
        pending = [
            graph.resolve(reference)
            for reference in graph.nodes[node_id].get("inputs", {}).values()
        ]
        visited = set()
        while pending:
            dependency = pending.pop()
            if dependency in visited:
                continue
            visited.add(dependency)
            if dependency in owners:
                edges.setdefault(owner, set()).add(owners[dependency])
            else:
                pending.extend(
                    graph.resolve(reference)
                    for reference in graph.nodes[dependency].get("inputs", {}).values()
                )
    return edges


def inspect(root, project, config, pins, records_root, *, git_revision, git_dirty):
    root = root.resolve()
    if GATE not in project["additionalRequiredChecks"]:
        raise ValueError(f"Agreement requires the additional required gate {GATE}")
    name, version = project["project"], project["policyVersion"]
    revision = git_revision(root)
    if (
        not isinstance(revision, str)
        or not releases.REVISION.fullmatch(revision)
        or git_dirty(root)
    ):
        raise ValueError("Agreement requires a clean committed integration project")
    prefix = git(root, "rev-parse", "--show-prefix").strip().rstrip("/")
    tree = GitTree(root, revision, prefix)
    paths = tree.lockfiles()
    if "flake.lock" not in paths:
        raise ValueError("Agreement requires a committed root flake.lock")
    snapshot = records.identity(config, pins, git_revision(records_root))
    result = {
        "status": "pass",
        "project": name,
        "revision": revision,
        "policyVersion": version,
        "records": snapshot,
        "members": [],
        "lockfiles": [],
        "issues": [],
        "behavioralIntegration": "not-run",
    }
    known = member_repositories(config)
    consumed = {}
    dependencies = {}
    for path in paths:
        content = tree.read(path)
        result["lockfiles"].append(
            {"path": path, "digest": hashlib.sha256(content.encode()).hexdigest()}
        )
        graph = locks.LockGraph(
            json.loads(content, object_pairs_hook=records.unique_mapping)
        )
        owners = {graph.root: name}
        for node_id, node in graph.reachable().items():
            if node_id == graph.root:
                continue
            identity = source_identity(node.get("locked", {}))
            original = source_identity(node.get("original", {}))
            if original in known and known.get(identity) != known[original]:
                raise ValueError(
                    f"{path}:{node_id}: locked source contradicts the enrolled original repository"
                )
            if identity == config["policyRepository"].lower():
                result["issues"].append(
                    f"{path}:{node_id}: policy repository is a flake dependency"
                )
            if identity not in known:
                continue
            owners[node_id] = known[identity]
            try:
                source = locked_source(node)
            except ValueError as error:
                raise ValueError(f"{path}:{node_id}: {error}") from error
            key = json.dumps(source, sort_keys=True)
            if key not in consumed:
                consumed[key] = {
                    "project": known[identity],
                    "repository": config["_members"][known[identity]],
                    "revision": source["revision"],
                    "source": source,
                    "references": [],
                    "policyVersion": None,
                    "selectionStatus": "unknown",
                    "status": "error",
                    "issues": [],
                }
            consumed[key]["references"].append(
                {"lockfile": path, "node": node_id, "locked": node["locked"]}
            )
        for owner, children in member_edges(graph, owners).items():
            dependencies.setdefault(owner, set()).update(children)
    result["dependencyGraph"] = {
        owner: sorted(children) for owner, children in sorted(dependencies.items())
    }
    result["cycles"] = locks.dependency_cycles(result["dependencyGraph"])
    if result["cycles"]:
        result["issues"].append("Locked member dependencies must be acyclic")
    if not consumed:
        result["issues"].append(
            "No enrolled member dependencies were found in committed lock scopes"
        )
    with tempfile.TemporaryDirectory(prefix="policy-agreement-") as temporary:
        for index, member in enumerate(consumed.values()):
            try:
                source_tree = fetch(
                    member["source"], Path(temporary) / f"source-{index}"
                )
                caller_root = source_tree.declaration(
                    Path(temporary) / f"caller-{index}"
                )
                try:
                    _, _, _, selected = declarations.read_identity(
                        caller_root, config["policyRepository"], member["project"]
                    )
                    member["policyVersion"] = selected
                    member["support"] = support.assess(selected, config["_support"])
                    member["selectionStatus"] = member["support"]["status"]
                    member["status"] = "pass"
                    if selected != version:
                        member["issues"].append(
                            f"Locked member selects {selected}; integration selects {version}"
                        )
                    else:
                        declarations.inspect(
                            caller_root,
                            config["policyRepository"],
                            member["project"],
                            config,
                            checker_version=version,
                        )
                    if member["support"]["status"] == "retired":
                        member["issues"].append(
                            support.retirement_issue(member["support"])
                        )
                except ValueError as error:
                    member.update(status="fail", selectionStatus="invalid")
                    member["issues"].append(str(error))
                if member["issues"]:
                    member["status"] = "fail"
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                member["issues"].append(str(error))
            result["members"].append(member)
    assessment_time = support.now()
    for member in result["members"]:
        if "support" in member:
            current = support.assess(
                member["policyVersion"], config["_support"], at=assessment_time
            )
            if current != member["support"]:
                member["support"] = current
                if member["selectionStatus"] in {"supported", "retired"}:
                    member["selectionStatus"] = current["status"]
                if current["status"] == "retired":
                    member["status"] = (
                        "fail" if member["status"] != "error" else "error"
                    )
                    member["issues"].append(support.retirement_issue(current))
    result["dependencySetDigest"] = hashlib.sha256(
        json.dumps(
            {
                "lockfiles": result["lockfiles"],
                "sources": [
                    {
                        key: member[key]
                        for key in (
                            "project",
                            "repository",
                            "revision",
                            "source",
                            "references",
                            "policyVersion",
                        )
                    }
                    for member in result["members"]
                ],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    try:
        current_config, current_pins = records.load(records_root)
        changed = (
            records.identity(current_config, current_pins, git_revision(records_root))
            != snapshot
        )
    except (ValueError, OSError, KeyError, TypeError):
        changed = True
    if changed:
        result["issues"].append("Central records changed during agreement inspection")
        result["status"] = "error"
    if git_revision(root) != revision or git_dirty(root):
        result["issues"].append(
            "Integration source changed during agreement inspection"
        )
        result["status"] = "error"
    elif changed or any(member["status"] == "error" for member in result["members"]):
        result["status"] = "error"
    elif result["issues"] or any(
        member["status"] == "fail" for member in result["members"]
    ):
        result["status"] = "fail"
    return result
