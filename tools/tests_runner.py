"""Test a repo's `checks` output with the locked, stable, or unstable nixpkgs.

`nix flake check --no-build` evaluates every output; the run then builds the
checks whose `requiredSystemFeatures` the host's `system-features` provide
and reports the others as skipped.
"""

import json
import re
import subprocess
import sys

if __package__:
    from . import inputs, locks
else:
    import inputs
    import locks

MODES = ("locked", "stable", "unstable")
SYSTEM = re.compile(r"[a-z0-9_]+-[a-z0-9_]+")


def run(root, mode, pins, *, fingerprints):
    """Return the `test` report for the repo at root.

    fingerprints(root) maps source paths to digests; the run fails when the
    mapping or flake.lock bytes differ afterwards.
    """
    root = root.resolve()
    result = {
        "status": "fail",
        "nixpkgs": mode,
        "system": None,
        "expectedRevision": None,
        "resolvedRevision": None,
        "checks": [],
        "skipped": {},
        "commands": [],
        "issues": [],
    }
    lock_path = root / "flake.lock"
    lock_before = None
    sources_before = None
    try:
        sources_before = fingerprints(root)
        if not lock_path.is_file():
            raise ValueError("The repo has no flake.lock")
        lock_before = lock_path.read_bytes()
        locked = root_nixpkgs(locks.LockGraph(json.loads(lock_before)))
        if mode == "locked":
            result["expectedRevision"] = locked
            flags = ["--no-update-lock-file"]
        else:
            result["expectedRevision"] = pins[mode]
            flags = [
                "--override-input",
                "nixpkgs",
                f"github:NixOS/nixpkgs/{pins[mode]}",
            ]
        system = nix(
            result,
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
        ).strip()
        result["system"] = system
        if not SYSTEM.fullmatch(system):
            raise ValueError(f"Invalid host system: {system!r}")
        metadata = nix(
            result, ["nix", "flake", "metadata", str(root), "--json", *flags]
        )
        result["resolvedRevision"] = root_nixpkgs(
            locks.LockGraph(json.loads(metadata)["locks"])
        )
        if result["resolvedRevision"] != result["expectedRevision"]:
            raise ValueError(
                "Resolved root nixpkgs does not match the expected revision"
            )
        attribute = f"checks.{system}"
        try:
            checks = json.loads(
                nix(
                    result,
                    [
                        "nix",
                        "eval",
                        "--json",
                        f"{root}#{attribute}",
                        "--apply",
                        "builtins.mapAttrs (_: check: check.requiredSystemFeatures or [ ])",
                        *flags,
                    ],
                )
            )
        except (subprocess.SubprocessError, OSError) as error:
            raise ValueError(f"{attribute} does not evaluate: {error}") from error
        if (
            not isinstance(checks, dict)
            or not checks
            or not all(
                isinstance(features, list)
                and all(isinstance(feature, str) for feature in features)
                for features in checks.values()
            )
        ):
            raise ValueError(f"{attribute} must contain at least one check")
        result["checks"] = sorted(checks)
        nix(
            result,
            ["nix", "flake", "check", str(root), "--no-build", *flags],
            capture=False,
        )
        host = set(nix(result, ["nix", "config", "show", "system-features"]).split())
        for name, features in checks.items():
            if missing := sorted(set(features) - host):
                result["skipped"][name] = missing
        # Quote each name so dots and other characters stay in one attribute.
        buildable = [
            f'{root}#{attribute}."{name}"'
            for name in result["checks"]
            if name not in result["skipped"]
        ]
        if buildable:
            nix(
                result,
                ["nix", "build", "--no-link", "--print-build-logs", *buildable, *flags],
                capture=False,
            )
        result["status"] = "pass"
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        result["issues"].append(str(error))
    finally:
        try:
            if lock_before is not None and (
                not lock_path.is_file() or lock_path.read_bytes() != lock_before
            ):
                result["issues"].append("flake.lock changed during the run")
            if sources_before is not None and fingerprints(root) != sources_before:
                result["issues"].append("Repo sources changed during the run")
        except (OSError, ValueError) as error:
            result["issues"].append(f"Source inspection after the run failed: {error}")
        if result["issues"]:
            result["status"] = "fail"
    return result


def root_nixpkgs(lock):
    """Return the revision of the root `nixpkgs` input if it is locked NixOS/nixpkgs."""
    node_id, problem = inputs.root_nixpkgs(lock)
    if problem is not None:
        raise ValueError(problem)
    return lock.nodes[node_id]["locked"]["rev"]


def nix(result, command, *, capture=True):
    """Run command, log it in result, and return its captured standard output.

    Without capture, output goes to standard error so the JSON report stays
    the only standard output.
    """
    outcome = {"command": command, "returncode": None}
    result["commands"].append(outcome)
    try:
        process = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else sys.stderr,
        )
    except (OSError, subprocess.SubprocessError) as error:
        outcome["error"] = str(error)
        raise
    outcome["returncode"] = process.returncode
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)
    return process.stdout
