"""Run a repo's `nix flake check` with the locked, stable, or unstable nixpkgs."""

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
                        "builtins.attrNames",
                        *flags,
                    ],
                )
            )
        except (subprocess.SubprocessError, OSError) as error:
            raise ValueError(f"{attribute} does not evaluate: {error}") from error
        if (
            not isinstance(checks, list)
            or not checks
            or not all(isinstance(check, str) for check in checks)
        ):
            raise ValueError(f"{attribute} must contain at least one check")
        result["checks"] = checks
        nix(
            result,
            ["nix", "flake", "check", str(root), "--print-build-logs", *flags],
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
