"""Nix answers that describe the entries of a `checks` output as derivations.

`checks` maps each entry name to its required system features. The answers
follow `nix eval` of each entry's `drvPath` and `nix derivation show` in
format version 4, which keys derivations by store path name.
"""

import json

SYSTEMS = ["x86_64-linux", "aarch64-linux", "riscv64-linux"]


def repo_checks(vm_tests):
    """The entries of a fixture repo's `checks` output on one system."""
    vm = {"vm-boot": ["kvm", "nixos-test"]} if vm_tests else {}
    return {"behavior": [], **vm}


def drv_path(name):
    return f"/nix/store/{'0' * 32}-{name}.drv"


def drv_paths(checks):
    """Answer the `drvPath` evaluation; a malformed `checks` passes through."""
    if isinstance(checks, dict) and all(
        isinstance(features, list) for features in checks.values()
    ):
        return {name: drv_path(name) for name in checks}
    return checks


def answer(command, systems):
    """Answer `nix eval` of `#checks.<system>` drvPaths or `nix derivation show`.

    systems maps each system to the entries of its `checks` output. An entry
    name has the same features on every system that has it.
    """
    if command[:2] == ["nix", "eval"]:
        system = command[3].rsplit("#checks.", 1)[1]
        return json.dumps(drv_paths(systems[system]))
    entries = {
        name: features
        for checks in systems.values()
        for name, features in checks.items()
    }
    return show(command, entries)


def show(command, checks, structured=(), version=4):
    """Answer `nix derivation show PATH...` for entries of checks.

    Entries named in structured keep their features in `structuredAttrs`, as
    derivations with `__structuredAttrs` do; the others in `env`.
    """
    names = {drv_path(name): name for name in checks}
    derivations = {}
    for path in command[3:]:
        name = names[path]
        derivation = {"name": name, "env": {}}
        if name in structured:
            derivation["structuredAttrs"] = {"requiredSystemFeatures": checks[name]}
        elif checks[name]:
            derivation["env"]["requiredSystemFeatures"] = " ".join(checks[name])
        derivations[path.removeprefix("/nix/store/")] = derivation
    return json.dumps({"derivations": derivations, "version": version})
