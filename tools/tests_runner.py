"""Test a repo's `checks` output with the locked, stable, or unstable nixpkgs.

A mode whose nixpkgs revision equals that of an earlier mode in MODES passes
without Nix; the earlier mode's run covers it. Otherwise a run builds either
the VM tests, the entries that require the `nixos-test` system feature, or
the other entries. Only the run for the other entries first evaluates every
output with `nix flake check --no-build`. A run builds each entry whose
`requiredSystemFeatures` the host's `system-features` provide and reports the
others as skipped.
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
VM_FEATURE = "nixos-test"


def run(root, mode, pins, *, vm=False, fingerprints):
    """Return the `test` report for the repo at root.

    fingerprints(root) maps source paths to digests; the run fails when the
    mapping or flake.lock bytes differ afterwards.
    """
    root = root.resolve()
    result = {
        "status": "fail",
        "nixpkgs": mode,
        "vm": vm,
        "system": None,
        "expectedRevision": None,
        "resolvedRevision": None,
        "sameAs": None,
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
        revisions = {"locked": locked, **{other: pins[other] for other in MODES[1:]}}
        result["expectedRevision"] = revisions[mode]
        # An earlier mode with the same revision builds the same entries.
        result["sameAs"] = next(
            (
                earlier
                for earlier in MODES[: MODES.index(mode)]
                if revisions[earlier] == revisions[mode]
            ),
            None,
        )
        if result["sameAs"]:
            result["status"] = "pass"
            return result
        if mode == "locked":
            flags = ["--no-update-lock-file"]
        else:
            flags = [
                "--override-input",
                "nixpkgs",
                f"github:NixOS/nixpkgs/{revisions[mode]}",
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
        checks = entry_features(
            lambda command: nix(result, command), str(root), attribute, flags
        )
        entries = {
            name: features
            for name, features in checks.items()
            if (VM_FEATURE in features) == vm
        }
        result["checks"] = sorted(entries)
        # The run without --vm evaluates every output, so VM runs only build.
        if not vm:
            nix(
                result,
                ["nix", "flake", "check", str(root), "--no-build", *flags],
                capture=False,
            )
        host = set(nix(result, ["nix", "config", "show", "system-features"]).split())
        for name, features in entries.items():
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


def entry_features(nix_output, flake, attribute, flags):
    """Map each entry of the `checks` set at flake#attribute to its features.

    nix_output(command) returns the standard output of a Nix command. The
    features come from the instantiated derivations: lazy derivations, such as
    NixOS tests, have no `requiredSystemFeatures` attribute.
    """
    try:
        paths = json.loads(
            nix_output(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"{flake}#{attribute}",
                    "--apply",
                    "builtins.mapAttrs (_: entry: entry.drvPath)",
                    *flags,
                ]
            )
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise ValueError(f"{attribute} does not evaluate: {error}") from error
    if not isinstance(paths, dict) or not paths:
        raise ValueError(f"{attribute} must contain at least one entry")
    if not all(
        isinstance(path, str) and path.endswith(".drv") for path in paths.values()
    ):
        raise ValueError(f"The entries of {attribute} must be derivations")
    shown = json.loads(nix_output(["nix", "derivation", "show", *paths.values()]))
    # Format version 4, which Nix 2.34 reports, keys derivations by store path
    # name.
    if not isinstance(shown, dict) or shown.get("version") != 4:
        raise ValueError("nix derivation show must report format version 4")
    features = {}
    for name, path in paths.items():
        derivation = shown["derivations"][path.rsplit("/", 1)[1]]
        if "structuredAttrs" in derivation:
            found = derivation["structuredAttrs"].get("requiredSystemFeatures", [])
        else:
            found = derivation["env"].get("requiredSystemFeatures", "").split()
        if not isinstance(found, list) or not all(
            isinstance(feature, str) for feature in found
        ):
            raise ValueError(f"requiredSystemFeatures of {name} must be strings")
        features[name] = found
    return features


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
