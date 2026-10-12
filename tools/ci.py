"""Plan the reusable workflow's jobs and the status names a repo requires."""

import json
import re
import subprocess

if __package__:
    from . import tests_runner
else:
    import tests_runner


# The caller job's `name`; GitHub prefixes every called job's status with it.
CALLER_JOB_NAME = "Policy"
CHECK_JOB_NAME = "Check ({system})"
TESTS_JOB_NAME = "Tests ({nixpkgs}, {system})"
VM_TESTS_JOB_NAME = "VM tests ({nixpkgs}, {system})"
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
    check_jobs = [{"system": system, "runner": runner(system)} for system in systems]
    test_jobs = [
        {"nixpkgs": mode, "system": system, "runner": runner(system)}
        for mode in tests_runner.MODES
        for system in systems
    ]
    with_vm_tests = vm_test_systems(root.resolve(), systems)
    vm_test_jobs = [job for job in test_jobs if job["system"] in with_vm_tests]
    required = [status(CHECK_JOB_NAME.format(**job)) for job in check_jobs]
    required += [status(TESTS_JOB_NAME.format(**job)) for job in test_jobs]
    required += [status(VM_TESTS_JOB_NAME.format(**job)) for job in vm_test_jobs]
    return {
        "status": "planned",
        "systems": systems,
        "checkMatrix": {"include": check_jobs},
        "testMatrix": {"include": test_jobs},
        "vmTestMatrix": {"include": vm_test_jobs},
        "requiredChecks": required,
    }


def vm_test_systems(root, systems):
    """Return the systems whose `checks` output has a VM test, with the locked nixpkgs."""

    def run(command):
        return subprocess.run(
            command, check=True, stdout=subprocess.PIPE, text=True
        ).stdout

    return {
        system
        for system in systems
        if any(
            tests_runner.VM_FEATURE in features
            for features in tests_runner.entry_features(
                run, f"path:{root}", f"checks.{system}", ["--no-update-lock-file"]
            ).values()
        )
    }
