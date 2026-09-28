"""Member locks, release identities, and pin records used by scenarios."""

from tools import policy


STABLE = "a" * 40
UNSTABLE = "b" * 40
NEW_STABLE = "c" * 40
NEW_UNSTABLE = "d" * 40
CHECKER = "e" * 40
RELEASE = f"v{(policy.SOURCE_ROOT / 'VERSION').read_text().strip()}"
SOURCE = "f" * 40
PAIR = {"stable": STABLE, "unstable": UNSTABLE}
NEW_PAIR = {"stable": NEW_STABLE, "unstable": NEW_UNSTABLE}
POLICY_REPO = "petohorvath/nixos-project-policy"


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
