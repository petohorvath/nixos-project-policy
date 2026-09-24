"""Read release selection and member settings from the policy workflow caller."""

import json
import re

import yaml


POLICY_VERSION = re.compile(
    r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)
PROJECT_NAME = re.compile(r"[a-z0-9-]+\Z")
SYSTEM_NAME = re.compile(r"[A-Za-z0-9_]+-[A-Za-z0-9_-]+\Z")
PR_ACTIVITIES = {"opened", "synchronize", "reopened"}
INPUT_FIELDS = {
    "required_architectures": "requiredArchitectures",
    "vm_targets": "vmTargets",
    "additional_required_checks": "additionalRequiredChecks",
}


class UniqueLoader(yaml.BaseLoader):
    """Keep workflow scalars literal and reject YAML's last-key-wins behavior."""

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Workflow mappings need unique string keys")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def discover(root, repository, *, workflow_name="check.yml"):
    """Return the unique caller even when its trigger or settings are invalid."""
    candidates = []
    prefix = f"{repository}/.github/workflows/{workflow_name}@"
    for path in sorted((root / ".github/workflows").glob("*")):
        if path.suffix not in {".yml", ".yaml"}:
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Policy caller escapes the project directory")
        try:
            workflow = yaml.load(path.read_text(), Loader=UniqueLoader)
        except yaml.YAMLError as error:
            raise ValueError(
                f"Invalid workflow YAML in {path.name}: {error}"
            ) from error
        if not isinstance(workflow, dict):
            continue
        jobs = workflow.get("jobs", {})
        if not isinstance(jobs, dict):
            raise ValueError(f"Invalid workflow jobs in {path.name}")
        for job in jobs.values():
            if isinstance(job, dict) and isinstance(job.get("uses"), str):
                if job["uses"].startswith(prefix):
                    candidates.append((path, workflow, job, job["uses"][len(prefix) :]))
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one policy caller; found {len(candidates)}")
    return candidates[0]


def read_identity(root, repository, name):
    """Read a literal member selection without imposing one release's settings."""
    path, workflow, job, version = discover(root, repository)
    require_policy_version(version)
    inputs = job.get("with")
    if (
        not isinstance(inputs, dict)
        or inputs.get("project") != name
        or inputs.get("policy_version") != version
    ):
        raise ValueError(
            "Policy caller identity and policy_version must agree with its selection"
        )
    return path, workflow, job, version


def inspect(
    root, repository, name, requirements, *, checker_version=None, hosted_inputs=None
):
    path, workflow, job, version = discover(root, repository)
    require_policy_version(version)
    if checker_version is not None and version != checker_version:
        raise ValueError(
            f"Project {name} selects policy {version}; run that release instead of checker {checker_version}"
        )
    require_execution(workflow, job, requirements["ci"]["callerJobName"])
    member = parse_inputs(job.get("with"), name, version)
    if hosted_inputs is not None:
        hosted = parse_inputs(hosted_inputs, name, version)
        if hosted != member:
            raise ValueError(
                "Hosted workflow inputs disagree with the inspected member declaration"
            )
    return {**member, "declaration": str(path.relative_to(root))}


def require_execution(workflow, job, caller_name, *, needs=None):
    if job.get("name") != caller_name:
        raise ValueError(f"Policy caller job must be named '{caller_name}'")
    for forbidden in ("if", "strategy", "continue-on-error"):
        if forbidden in job:
            raise ValueError(
                f"Policy caller must run unconditionally and cannot contain {forbidden}"
            )
    if (needs is None and "needs" in job) or (
        needs is not None and job.get("needs") != needs
    ):
        raise ValueError(
            "Policy caller needs must use only its required snapshot dependency"
        )
    events = workflow.get("on")
    trigger = events.get("pull_request") if isinstance(events, dict) else None
    if not (
        isinstance(trigger, dict)
        and set(trigger) == {"types"}
        and isinstance(trigger["types"], list)
        and all(isinstance(item, str) for item in trigger["types"])
        and len(trigger["types"]) == len(set(trigger["types"]))
        and PR_ACTIVITIES <= set(trigger["types"]) <= PR_ACTIVITIES | {"edited"}
    ):
        raise ValueError(
            "Policy PR trigger must declare opened, synchronize, reopened activities, optionally edited, without other filters"
        )


def parse_inputs(inputs, name, version):
    allowed = {"project", "policy_version", "vm_architecture", *INPUT_FIELDS}
    required = {"project", "policy_version", "required_architectures"}
    if (
        not isinstance(inputs, dict)
        or not required <= inputs.keys()
        or inputs.keys() - allowed
    ):
        raise ValueError(
            "Policy caller requires project, policy_version, required_architectures and permits only vm_targets, vm_architecture and additional_required_checks as optional inputs"
        )
    if any(not isinstance(value, str) or "${{" in value for value in inputs.values()):
        raise ValueError(
            "Policy caller inputs must be literal strings without dynamic expressions"
        )
    if not PROJECT_NAME.fullmatch(name) or inputs["project"] != name:
        raise ValueError("Policy caller must select its own project identity")
    require_policy_version(inputs["policy_version"])
    if inputs["policy_version"] != version:
        raise ValueError("policy_version must equal the caller's release tag")
    settings = {"project": name, "policyVersion": version}
    for input_name, field in INPUT_FIELDS.items():
        try:
            values = json.loads(inputs.get(input_name, "[]"))
        except ValueError as error:
            raise ValueError(
                f"{input_name} must be a JSON list encoded as a string"
            ) from error
        if not valid_check_names(values):
            raise ValueError(
                f"{input_name} must be a list of unique nonempty literal strings"
            )
        settings[field] = values
    architectures = settings["requiredArchitectures"]
    if not architectures or not all(valid_system(system) for system in architectures):
        raise ValueError(
            "required_architectures must be a nonempty list of Nix system names"
        )
    if any(not PROJECT_NAME.fullmatch(target) for target in settings["vmTargets"]):
        raise ValueError("vm_targets must contain simple lowercase target names")
    vm_architecture = inputs.get("vm_architecture", "x86_64-linux")
    if not valid_system(vm_architecture) or not vm_architecture.endswith("-linux"):
        raise ValueError("vm_architecture must be a Linux Nix system name")
    settings["vmArchitecture"] = vm_architecture
    return settings


def valid_check_names(checks):
    return (
        isinstance(checks, list)
        and all(
            isinstance(check, str)
            and check.strip() == check
            and check
            and not any(
                ord(character) < 32 or ord(character) == 127 for character in check
            )
            and "${{" not in check
            for check in checks
        )
        and len(set(checks)) == len(checks)
    )


def require_policy_version(value):
    if not isinstance(value, str) or not POLICY_VERSION.fullmatch(value):
        raise ValueError("Policy versions must be exact release tags such as v0.4.0")

    if tuple(map(int, value[1:].split("."))) < (0, 4, 0):
        raise ValueError("Unsupported policy release: select v0.4.0 or later")


def valid_system(system):
    return isinstance(system, str) and SYSTEM_NAME.fullmatch(system) is not None


def runner_for(system, runners):
    if not valid_system(system):
        raise ValueError(f"Invalid Nix system name: {system!r}")
    return runners.get(system, ["self-hosted", system])


def ci_plan(project, requirements):
    jobs = [
        {
            "check": check,
            "system": architecture,
            "runner": runner_for(architecture, requirements["runners"]),
        }
        for check in requirements["architectureChecks"]
        for architecture in project["requiredArchitectures"]
    ]
    compatibility_jobs = [
        {
            "check": check.format(architecture=architecture),
            "channel": channel,
            "system": architecture,
            "runner": runner_for(architecture, requirements["runners"]),
        }
        for channel, check in requirements["compatibilityChecks"].items()
        for architecture in project["requiredArchitectures"]
    ]
    checks = [
        *requirements["requiredChecks"],
        *(
            f"{requirements['callerJobName']} / {job['check']}"
            for job in compatibility_jobs
        ),
        *(
            f"{requirements['callerJobName']} / {job['check']} ({job['system']})"
            for job in jobs
        ),
    ]
    vm_architecture = project.get("vmArchitecture", "x86_64-linux")
    vm_check = requirements["vmCheck"].format(architecture=vm_architecture)
    if project["vmTargets"]:
        checks.append(vm_check)
    return {
        "matrix": {"include": jobs},
        "compatibilityMatrix": {"include": compatibility_jobs},
        "vmTargets": project["vmTargets"],
        "vmJob": {
            "check": vm_check.removeprefix(f"{requirements['callerJobName']} / "),
            "system": vm_architecture,
            "runner": runner_for(vm_architecture, requirements["vmRunners"]),
        },
        "requiredChecks": list(
            dict.fromkeys([*checks, *project.get("additionalRequiredChecks", [])])
        ),
    }
