"""Trusted enrollment and record identities, with the legacy digest preserved."""

import hashlib
import json
import re


REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PROJECT = re.compile(r"[a-z0-9-]+\Z")
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
