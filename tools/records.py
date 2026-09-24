"""Load central records and compute their identities."""

import hashlib
import json
from pathlib import Path
import re

if __package__:
    from . import declarations
else:
    import declarations


REVISION = re.compile(r"[0-9a-f]{40}\Z")
_REQUIREMENTS_PATH = Path(__file__).resolve().parents[1] / "policy/requirements.json"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PROJECT = re.compile(r"[a-z0-9-]+\Z")


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
    if not isinstance(pins, dict) or set(pins) != {
        "schemaVersion",
        "stableBranch",
        "approved",
    }:
        raise ValueError(
            "Pin records require only schemaVersion, stableBranch, and approved"
        )
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
    return config, pins


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


def digest(config, pins):
    data = {
        "pins": pins,
        "members": {"schemaVersion": 1, "members": config["_members"]},
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def identity(config, pins, revision):
    return {
        "revision": revision,
        "digest": digest(config, pins),
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
