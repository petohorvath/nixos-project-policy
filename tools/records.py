"""Load and validate the data bundled with the checker."""

import json
from pathlib import Path
import re
from typing import NamedTuple


REVISION = re.compile(r"[0-9a-f]{40}\Z")
_SOURCE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = _SOURCE_ROOT / "data"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(), object_pairs_hook=unique_mapping)


class Data(NamedTuple):
    """Pins and listed repos bundled with the executing checker."""

    pins: dict
    repos: list


def load():
    """Read and validate the pins and listed repos bundled with this checker.

    Every command reads its data through this function. Tests replace the
    bundled files by patching DATA_ROOT.
    """
    return Data(
        pins=load_pins(DATA_ROOT / "pins.json"),
        repos=load_repos(DATA_ROOT / "repos.json"),
    )


def load_pins(path):
    pins = read_json(path)
    if not isinstance(pins, dict) or set(pins) != {
        "stable",
        "unstable",
        "stableBranch",
    }:
        raise ValueError(
            f"{path.name} requires only stable, unstable, and stableBranch"
        )
    if not isinstance(pins["stableBranch"], str) or not re.fullmatch(
        r"nixos-[0-9]{2}\.[0-9]{2}", pins["stableBranch"]
    ):
        raise ValueError("Expected a stable NixOS update branch")
    validate_pair({"stable": pins["stable"], "unstable": pins["unstable"]})
    return pins


def load_repos(path):
    document = read_json(path)
    if (
        not isinstance(document, dict)
        or set(document) != {"repos"}
        or not isinstance(document["repos"], list)
    ):
        raise ValueError(f"{path.name} requires only a repos list")
    identities = set()
    for repository in document["repos"]:
        if (
            not isinstance(repository, str)
            or not REPOSITORY.fullmatch(repository)
            or repository.lower() in identities
        ):
            raise ValueError(
                f"Invalid or duplicate GitHub owner/repository identity: {repository!r}"
            )
        identities.add(repository.lower())
    return document["repos"]


def validate_pair(pair):
    if not isinstance(pair, dict) or set(pair) != {"stable", "unstable"}:
        raise ValueError("A pin pair must contain stable and unstable revisions")
    for revision in pair.values():
        require_revision(revision)


def require_revision(value):
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise ValueError("Expected an exact 40-character lowercase Git commit")
