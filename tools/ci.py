"""Plan the reusable workflow's jobs and the status names a repo requires."""

import json
import re

if __package__:
    from . import tests_runner, vm
else:
    import tests_runner
    import vm


# The caller job's `name`; GitHub prefixes every called job's status with it.
CALLER_JOB_NAME = "Policy"
CHECK_JOB_NAME = "Check ({system})"
TESTS_JOB_NAME = "Tests ({nixpkgs}, {system})"
VM_JOB_NAME = "VM tests"
VM_SYSTEM = "x86_64-linux"
DEFAULT_SYSTEMS = ("x86_64-linux", "aarch64-linux")
RUNNERS = {
    "x86_64-linux": "ubuntu-24.04",
    "aarch64-linux": "ubuntu-24.04-arm",
}
SYSTEM_NAME = re.compile(r"[A-Za-z0-9_]+-[A-Za-z0-9_-]+\Z")


def parse_systems(text):
    """Read the workflow's `systems` input, a JSON list of Nix system names."""
    if text is None:
        return list(DEFAULT_SYSTEMS)
    try:
        systems = json.loads(text)
    except ValueError as error:
        raise ValueError("systems must be a JSON list of Nix system names") from error
    if (
        not isinstance(systems, list)
        or not systems
        or not all(
            isinstance(system, str) and SYSTEM_NAME.fullmatch(system)
            for system in systems
        )
        or len(set(systems)) != len(systems)
    ):
        raise ValueError(
            "systems must be a nonempty JSON list of unique Nix system names"
        )
    return systems


def runner(system):
    # Other systems need a self-hosted runner labelled with the system name.
    return RUNNERS.get(system, ["self-hosted", system])


def status(job_name):
    return f"{CALLER_JOB_NAME} / {job_name}"


def plan(root, systems):
    """Return the job matrices and the required status names for one repo."""
    _, vm_tests = vm.discover(root.resolve(), VM_SYSTEM)
    check_jobs = [{"system": system, "runner": runner(system)} for system in systems]
    test_jobs = [
        {"nixpkgs": mode, "system": system, "runner": runner(system)}
        for mode in tests_runner.MODES
        for system in systems
    ]
    required = [status(CHECK_JOB_NAME.format(**job)) for job in check_jobs]
    required += [status(TESTS_JOB_NAME.format(**job)) for job in test_jobs]
    if vm_tests:
        required.append(status(VM_JOB_NAME))
    return {
        "status": "planned",
        "systems": systems,
        "checkMatrix": {"include": check_jobs},
        "testMatrix": {"include": test_jobs},
        "vmTests": vm_tests,
        "vmJob": bool(vm_tests),
        "requiredChecks": required,
    }
