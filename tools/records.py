"""Load, capture, and write central records while preserving legacy identities."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


if __package__:
    from . import declarations, support
else:
    import declarations
    import support


REVISION = re.compile(r"[0-9a-f]{40}\Z")
ACTIVE_BATCH_STATES = {"approved", "rolling", "paused"}
_REQUIREMENTS_PATH = Path(__file__).resolve().parents[1] / "policy/requirements.json"
_REQUIREMENT_FIELDS = ("systems", "ci")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PROJECT = re.compile(r"[a-z0-9-]+\Z")
FILES = ("projects.json", "pins.json", "members.json", "support.json")
_RUNTIME_FIELDS = {
    *_REQUIREMENT_FIELDS,
    # Legacy release requirements, never part of record digests.
    "requiredTools",
    "readmeSections",
    "_members",
    "_support",
}


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(), object_pairs_hook=unique_mapping)


def load(root):
    """Read current records using the executing release's requirements."""
    config = read_json(root / "policy/projects.json")
    pins = read_json(root / "policy/pins.json")
    if not isinstance(config, dict) or not isinstance(pins, dict):
        raise ValueError("Policy records must be JSON objects")
    if config.get("schemaVersion") != 2 or pins.get("schemaVersion") != 1:
        raise ValueError("Unsupported policy record schema")
    if "_members" in config or "_support" in config:
        raise ValueError(
            "Enrollment and support belong in their own policy record files"
        )
    config["_members"] = load_members(root, config["projects"])
    config["_support"] = support.validate(read_json(root / "policy/support.json"))
    requirements = read_json(_REQUIREMENTS_PATH)
    if requirements.get("schemaVersion") != 1:
        raise ValueError("Unsupported policy requirements schema")
    # Requirements belong to the selected checker release, never the live records.
    for field in _REQUIREMENT_FIELDS:
        if field in config:
            raise ValueError(
                f"{field} belongs in the release's policy/requirements.json"
            )
        config[field] = requirements[field]
    if not REPOSITORY.fullmatch(config["policyRepository"]):
        raise ValueError("Invalid policy repository")
    if (
        not isinstance(config["systems"], list)
        or not config["systems"]
        or any(
            not isinstance(system, str) or not system for system in config["systems"]
        )
        or len(set(config["systems"])) != len(config["systems"])
        or set(config["ci"]["runners"]) != set(config["systems"])
    ):
        raise ValueError("Supported systems need unique names and matching CI runners")
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
            declarations.require_policy_version(project["policyVersion"])
        if "requiredArchitectures" in project:
            architectures = project["requiredArchitectures"]
            if (
                not isinstance(architectures, list)
                or not architectures
                or any(
                    not isinstance(system, str) or not system.strip()
                    for system in architectures
                )
                or len(set(architectures)) != len(architectures)
            ):
                raise ValueError(f"Invalid requiredArchitectures for {name}")
        if project["policyVersion"] == "v0.3.0":
            if "requiredArchitectures" not in project:
                raise ValueError(f"Project {name} needs requiredArchitectures")
            unsupported = set(project["requiredArchitectures"]) - set(config["systems"])
            if unsupported:
                raise ValueError(
                    f"Unsupported requiredArchitectures for {name}: "
                    + ", ".join(sorted(unsupported))
                )
        for field in ("requiredChecks", "additionalRequiredChecks"):
            if not valid_check_names(project.get(field, [])):
                raise ValueError(f"Invalid {field} names for {name}")
        if (
            project["policyVersion"] is not None
            and not uses_derived_checks(project["policyVersion"])
            and "additionalRequiredChecks" in project
        ):
            raise ValueError(
                f"Project {name} needs policy v0.3.0 or later for additionalRequiredChecks"
            )
        if project["policyVersion"] == "v0.3.0":
            if "requiredChecks" in project:
                required = set(
                    declarations.ci_plan(project, config["ci"])["requiredChecks"]
                )
                if set(project["requiredChecks"]) != required:
                    raise ValueError(
                        f"Project {name}: legacy requiredChecks must match the generated "
                        "CI checks; record project-specific gates in additionalRequiredChecks"
                    )
        elif project["adopted"] and not uses_derived_checks(project["policyVersion"]):
            if not project.get("requiredChecks"):
                raise ValueError(
                    f"Adopted project {name} needs verified required check names"
                )
    # Retirement alone is not migration proof; retained adopted legacy entries stay readable.
    for name, project in config["projects"].items():
        if project["adopted"] and not project.get("requiredChecks"):
            raise ValueError(
                f"Adopted project {name} needs legacy requiredChecks until reviewed legacy cleanup"
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


def proposed_snapshot(root):
    """Load regular committed blobs; never follow a proposal's record indirection."""
    root = root.resolve()

    def git(*arguments):
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments], stderr=subprocess.PIPE
        )

    revision = git("rev-parse", "HEAD").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Proposal requires an exact Git revision")
    descriptor = os.open(root / "policy", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with tempfile.TemporaryDirectory(prefix="policy-proposal-") as temporary:
            captured = Path(temporary)
            (captured / "policy").mkdir()
            for name in FILES:
                entry = git("ls-tree", "-z", revision, "--", f"policy/{name}").decode()
                match = re.fullmatch(
                    r"100(?:644|755) blob ([0-9a-f]{40})\tpolicy/"
                    + re.escape(name)
                    + "\x00",
                    entry,
                )
                if match is None:
                    raise ValueError(
                        f"Proposed policy/{name} must be a regular committed file"
                    )
                data = git("cat-file", "blob", match[1])
                file = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
                )
                with os.fdopen(file, "rb") as stream:
                    if (
                        not stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
                        or stream.read() != data
                    ):
                        raise ValueError(
                            f"Proposed policy/{name} differs from its exact Git revision"
                        )
                (captured / "policy" / name).write_bytes(data)
            config, pins = load(captured)
    finally:
        os.close(descriptor)
    if git("rev-parse", "HEAD").decode().strip() != revision:
        raise ValueError("Proposal changed during capture")
    return config, pins, identity(config, pins, revision)


def load_members(root, projects):
    roster = read_json(root / "policy/members.json")
    if (
        not isinstance(roster, dict)
        or set(roster) != {"schemaVersion", "members"}
        or type(roster["schemaVersion"]) is not int
        or roster["schemaVersion"] != 1
        or not isinstance(roster["members"], dict)
    ):
        raise ValueError("Unsupported enrolled-member roster schema")
    identities = set()
    for name, repository in roster["members"].items():
        if (
            not PROJECT.fullmatch(name)
            or not isinstance(repository, str)
            or not REPOSITORY.fullmatch(repository)
            or repository.lower() in identities
        ):
            raise ValueError(
                f"Invalid or duplicate enrolled repository identity: {name}"
            )
        if (
            name in projects
            and projects[name]["repository"].lower() != repository.lower()
        ):
            raise ValueError(
                f"Enrolled repository disagrees with legacy identity: {name}"
            )
        identities.add(repository.lower())
    return roster["members"]


def write(root, config, pins):
    """Write the four records into a new policy directory, excluding runtime fields."""
    directory = root / "policy"
    directory.mkdir(parents=True)
    for name, value in _documents(config, pins).items():
        (directory / name).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n"
        )


def digest(config, pins, *, legacy=False):
    data = {
        name.removesuffix(".json"): value
        for name, value in _documents(config, pins, legacy=legacy).items()
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def identity(config, pins, revision):
    return {
        "revision": revision,
        "digest": digest(config, pins),
        "legacyDigest": digest(config, pins, legacy=True),
    }


def _documents(config, pins, *, legacy=False):
    documents = {
        "projects.json": {
            key: value for key, value in config.items() if key not in _RUNTIME_FIELDS
        },
        "pins.json": pins,
    }
    if not legacy:
        documents["members.json"] = {"schemaVersion": 1, "members": config["_members"]}
        documents["support.json"] = config["_support"]
    return documents


def valid_check_names(checks):
    return (
        isinstance(checks, list)
        and all(isinstance(check, str) and check.strip() for check in checks)
        and len(set(checks)) == len(checks)
    )


def uses_derived_checks(version):
    return version is not None and tuple(
        map(int, version.removeprefix("v").split("."))
    ) >= (0, 3, 0)


def validate_pair(pair):
    if not isinstance(pair, dict) or set(pair) != {"stable", "unstable"}:
        raise ValueError("A pin pair must contain stable and unstable revisions")
    for revision in pair.values():
        require_revision(revision)


def require_revision(value):
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise ValueError("Expected an exact 40-character lowercase Git commit")
