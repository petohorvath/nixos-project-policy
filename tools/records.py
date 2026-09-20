"""Trusted enrollment and record identities, with the legacy digest preserved."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PROJECT = re.compile(r"[a-z0-9-]+\Z")
FILES = ("projects.json", "pins.json", "members.json", "support.json")
RUNTIME_FIELDS = {
    "systems",
    "requiredTools",
    "readmeSections",
    "ci",
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


def proposed_snapshot(root, loader):
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
            config, pins = loader(captured)
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


def digest(config, pins, *, legacy=False):
    data = {
        "projects": {
            key: value for key, value in config.items() if key not in RUNTIME_FIELDS
        },
        "pins": pins,
    }
    if not legacy:
        data["members"] = {"schemaVersion": 1, "members": config["_members"]}
        data["support"] = config["_support"]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def identity(config, pins, revision):
    return {
        "revision": revision,
        "digest": digest(config, pins),
        "legacyDigest": digest(config, pins, legacy=True),
    }
