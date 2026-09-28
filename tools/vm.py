"""Discover and build a repo's VM tests from `legacyPackages.<system>.vmTests`."""

import json
import os
import subprocess


FLAKE_VARIABLE = "NIXOS_PROJECT_POLICY_VM_FLAKE"
SYSTEM_VARIABLE = "NIXOS_PROJECT_POLICY_VM_SYSTEM"
# `or { }` keeps a missing `legacyPackages`, system, or `vmTests` from failing
# evaluation; `nix eval` on an installable cannot express that fallback.
DISCOVERY = f"""
let
  flake = builtins.getFlake (builtins.getEnv "{FLAKE_VARIABLE}");
  requested = builtins.getEnv "{SYSTEM_VARIABLE}";
  system = if requested == "" then builtins.currentSystem else requested;
in
{{
  inherit system;
  names = builtins.attrNames (flake.legacyPackages.${{system}}.vmTests or {{ }});
}}
"""


def discover(root, system=None):
    """Return the evaluation system and the sorted `vmTests` attribute names.

    `system` defaults to the host system. A flake without
    `legacyPackages.<system>.vmTests` yields no names. Evaluation failures
    raise `subprocess.CalledProcessError`.
    """
    result = subprocess.run(
        ["nix", "eval", "--json", "--impure", "--expr", DISCOVERY],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            FLAKE_VARIABLE: f"path:{root.resolve()}",
            SYSTEM_VARIABLE: system or "",
        },
    )
    found = json.loads(result.stdout)
    names = found["names"]
    if not isinstance(found["system"], str) or not all(
        isinstance(name, str) for name in names
    ):
        raise ValueError("VM test discovery returned malformed names")
    return found["system"], sorted(names)


def run(root):
    """Build every VM test on the host system and report the failing names."""
    root = root.resolve()
    system, names = discover(root)
    failed = [name for name in names if not build(root, system, name)]
    if not names:
        status = "not-applicable"
    else:
        status = "fail" if failed else "pass"
    return {"status": status, "system": system, "vmTests": names, "failed": failed}


def build(root, system, name):
    # Quote the name so dots and other characters stay inside one attribute.
    attribute = f'"{name}"'
    result = subprocess.run(
        [
            "nix",
            "build",
            "--no-link",
            "--no-update-lock-file",
            "--print-build-logs",
            f"path:{root}#legacyPackages.{system}.vmTests.{attribute}",
        ],
        check=False,
    )
    return result.returncode == 0
