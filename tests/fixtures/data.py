"""Repo locks and pins used by scenarios."""

STABLE = "a" * 40
UNSTABLE = "b" * 40
NEW_STABLE = "c" * 40
NEW_UNSTABLE = "d" * 40
SOURCE = "f" * 40
PAIR = {"stable": STABLE, "unstable": UNSTABLE}
NEW_PAIR = {"stable": NEW_STABLE, "unstable": NEW_UNSTABLE}


def nixpkgs(revision, branch):
    return {
        "locked": {
            "type": "github",
            "owner": "NixOS",
            "repo": "nixpkgs",
            "rev": revision,
        },
        "original": {
            "type": "github",
            "owner": "NixOS",
            "repo": "nixpkgs",
            "ref": branch,
        },
    }


def lockfile():
    return {
        "version": 7,
        "root": "entry",
        "nodes": {
            "entry": {
                "inputs": {
                    "nixpkgs": "arbitrary-node",
                    "nixpkgs-unstable": "rolling",
                }
            },
            "arbitrary-node": nixpkgs(STABLE, "nixos-26.05"),
            "rolling": nixpkgs(UNSTABLE, "nixos-unstable"),
        },
    }
