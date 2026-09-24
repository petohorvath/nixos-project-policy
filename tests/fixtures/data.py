"""Member locks, release identities, and support records used by scenarios."""

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
COMPATIBILITY_CHECKS = [
    "Policy / Compatibility (stable, x86_64-linux)",
    "Policy / Compatibility (stable, aarch64-linux)",
    "Policy / Compatibility (unstable, x86_64-linux)",
    "Policy / Compatibility (unstable, aarch64-linux)",
]
REQUIRED_CHECKS = [
    "Policy / Verify policy version and load shared pins",
    *COMPATIBILITY_CHECKS,
    "Policy / Compliance (x86_64-linux)",
    "Policy / Compliance (aarch64-linux)",
    "Policy / Project tests (x86_64-linux)",
    "Policy / Project tests (aarch64-linux)",
]
VM_CHECK = "Policy / VM tests (x86_64-linux)"


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


START = "2030-01-01T00:00:00Z"
BEFORE = "2030-01-31T23:59:59Z"
EFFECTIVE = "2030-02-01T00:00:00Z"
AFTER = "2030-02-02T00:00:00Z"


def retirement():
    return {
        "decision": "https://github.com/example/policy/pull/123",
        "reason": "Reviewed fixture retirement after the announced migration period",
        "migrationStartsAt": START,
        "retiresAt": EFFECTIVE,
    }
