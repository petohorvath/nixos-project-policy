"""Load, capture, and write central records and their identities."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

if __package__:
    from . import declarations
else:
    import declarations


REVISION = re.compile(r"[0-9a-f]{40}\Z")
ACTIVE_BATCH_STATES = {"approved", "rolling", "paused"}
_REQUIREMENTS_PATH = Path(__file__).resolve().parents[1] / "policy/requirements.json"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PROJECT = re.compile(r"[a-z0-9-]+\Z")
FILES = ("pins.json", "members.json")


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
    pins = read_json(root / "policy/pins.json")
    if not isinstance(pins, dict):
        raise ValueError("Policy records must be JSON objects")
    if type(pins.get("schemaVersion")) is not int or pins["schemaVersion"] != 1:
        raise ValueError("Unsupported policy record schema")
    if not isinstance(pins.get("stableBranch"), str) or not re.fullmatch(
        r"nixos-[0-9]{2}\.[0-9]{2}", pins["stableBranch"]
    ):
        raise ValueError("Expected a stable NixOS update branch")
    # Requirements belong to the selected checker release, never the live records.
    config = load_requirements()
    config["_members"] = load_members(root)
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
            if not PROJECT.fullmatch(name):
                raise ValueError(f"Invalid project in batch: {name}")
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


def load_members(root):
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
        identities.add(repository.lower())
    return roster["members"]


def write(root, config, pins):
    """Write the current records into a new policy directory."""
    directory = root / "policy"
    directory.mkdir(parents=True)
    for name, value in _documents(config, pins).items():
        (directory / name).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n"
        )


def digest(config, pins):
    data = {
        name.removesuffix(".json"): value
        for name, value in _documents(config, pins).items()
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def identity(config, pins, revision):
    return {
        "revision": revision,
        "digest": digest(config, pins),
    }


def _documents(config, pins):
    return {
        "pins.json": pins,
        "members.json": {"schemaVersion": 1, "members": config["_members"]},
    }


def load_requirements():
    requirements = read_json(_REQUIREMENTS_PATH)
    validate_requirements(requirements)
    return requirements


def validate_requirements(requirements):
    if (
        not isinstance(requirements, dict)
        or type(requirements.get("schemaVersion")) is not int
        or requirements["schemaVersion"] != 1
    ):
        raise ValueError("Unsupported policy requirements schema")
    repository = requirements.get("policyRepository")
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid policy repository")
    ci = requirements.get("ci")
    if not isinstance(ci, dict):
        raise ValueError("Policy requirements need CI configuration")
    runners = ci.get("runners")
    if (
        not isinstance(runners, dict)
        or not runners
        or any(
            not declarations.valid_system(system)
            or not isinstance(runner, str)
            or not runner
            for system, runner in runners.items()
        )
    ):
        raise ValueError("Supported systems require named CI runners")


def validate_pair(pair):
    if not isinstance(pair, dict) or set(pair) != {"stable", "unstable"}:
        raise ValueError("A pin pair must contain stable and unstable revisions")
    for revision in pair.values():
        require_revision(revision)


def require_revision(value):
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise ValueError("Expected an exact 40-character lowercase Git commit")
