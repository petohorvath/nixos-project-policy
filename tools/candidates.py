"""Plan, execute, and replay member candidate checks without granting approval."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

if __package__:
    from . import agreement, declarations, proposals, records, releases, support
else:
    import agreement
    import declarations
    import proposals
    import records
    import releases
    import support


SYSTEMS = {"x86_64-linux": "ubuntu-24.04", "aarch64-linux": "ubuntu-24.04-arm"}
RECORD_FILES = ("projects.json", "pins.json", "members.json", "support.json")
ERRORS = (
    ValueError,
    OSError,
    KeyError,
    TypeError,
    IndexError,
    AttributeError,
    subprocess.SubprocessError,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def evidence_directory(output, *inputs):
    output = output.resolve()
    if any(output.is_relative_to(root.resolve()) for root in inputs):
        raise ValueError("Candidate evidence must be outside every input checkout")
    output.mkdir(parents=True, exist_ok=False)
    return output


def identity(source_root, revision):
    return {
        "orchestratorVersion": "v" + (source_root / "VERSION").read_text().strip(),
        "orchestratorRevision": revision,
        "orchestratorDigest": digest(
            {
                str(path.relative_to(source_root)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in [
                    *sorted((source_root / "tools").glob("*.py")),
                    source_root / "VERSION",
                    source_root / "policy/requirements.json",
                ]
            }
        ),
    }


def add_commands(commands):
    command = commands.add_parser(
        "pin-batch",
        help="Validate an unapproved candidate through bound member evidence",
    )
    operations = command.add_subparsers(dest="operation", required=True)
    for operation in ("plan", "execute", "aggregate"):
        parser = operations.add_parser(operation)
        parser.add_argument("project_dir", type=Path)
        parser.add_argument("--proposal-root", required=True, type=Path)
        parser.add_argument("--output", required=True, type=Path)
        parser.add_argument("--attempt", default=None)
        if operation == "plan":
            subjects = parser.add_mutually_exclusive_group(required=True)
            subjects.add_argument("--project")
            subjects.add_argument("--all", action="store_true")
            parser.add_argument("--batch", required=True)
        else:
            parser.add_argument("--plan", required=True, type=Path)
            parser.add_argument("--all", action="store_true")
        if operation == "execute":
            parser.add_argument("--project")
            parser.add_argument("--system", required=True, choices=SYSTEMS)
        else:
            parser.add_argument("--fetch", action="store_true")
        if operation == "aggregate":
            parser.add_argument("--results", required=True, type=Path)
            parser.add_argument("--worker-outcomes", type=Path)
            parser.add_argument("--execution-status")


class Coordinator:
    def __init__(
        self,
        args,
        *,
        source_root,
        load_policy,
        git_revision,
        git_dirty,
        fingerprints,
        lock_graph,
        repository_identity,
        orchestrator_revision,
    ):
        self.args = args
        self.source_root = source_root
        self.load_policy = load_policy
        self.git_revision = git_revision
        self.git_dirty = git_dirty
        self.fingerprints = fingerprints
        self.lock_graph = lock_graph
        self.repository_identity = repository_identity
        self.orchestrator_revision = orchestrator_revision
        self.root = args.project_dir.resolve()
        self.baseline = args.policy_root.resolve()
        self.proposal = args.proposal_root.resolve()
        self.output = evidence_directory(
            args.output, self.root, self.baseline, self.proposal, source_root
        )

    def source(self):
        revision = self.git_revision(self.root)
        if (
            not isinstance(revision, str)
            or not releases.REVISION.fullmatch(revision)
            or self.git_dirty(self.root)
        ):
            raise ValueError(
                "Candidate validation requires an exact clean member commit"
            )
        return {"revision": revision, "digest": digest(self.fingerprints(self.root))}

    def snapshot(self, root):
        if root.resolve() == self.proposal:
            return records.proposed_snapshot(root, self.load_policy)
        config, pins = self.load_policy(root)
        return config, pins, records.identity(config, pins, self.git_revision(root))

    def authority(self, name, batch_id):
        proposal = proposals.read(
            self.baseline,
            self.proposal,
            load_policy=self.load_policy,
            git_revision=self.git_revision,
        )
        if name not in proposal.config["_members"]:
            raise ValueError("Candidate subject must be in the trusted enrolled roster")
        pins, batch = proposal.candidate(batch_id)
        if batch["projects"].get(name) != self.source()["revision"]:
            raise ValueError("Candidate is not registered for this exact member commit")
        return proposal.config, pins, proposal.baseline, proposal.proposal, batch

    def execution_records(self, config, pins):
        root = self.output / "records"
        directory = root / "policy"
        directory.mkdir(parents=True)
        values = {
            "projects.json": {
                key: value
                for key, value in config.items()
                if key not in records.RUNTIME_FIELDS
            },
            "pins.json": pins,
            "members.json": {"schemaVersion": 1, "members": config["_members"]},
            "support.json": config["_support"],
        }
        for file, value in values.items():
            write_json(directory / file, value)
        return root, records.identity(config, pins, self.git_revision(root))

    def capture(self, name, batch_id, attempt):
        before = self.source()
        config, pins, baseline, proposal, batch = self.authority(name, batch_id)
        subject = releases.public_get(
            f"repos/{config['_members'][name]}/git/commits/{before['revision']}"
        )
        if not isinstance(subject, dict) or subject.get("sha") != before["revision"]:
            raise ValueError(
                "Candidate revision is unavailable from the trusted enrolled repository"
            )
        _, _, _, version = declarations.read_identity(
            self.root, config["policyRepository"], name
        )
        assessment = support.assess(version, config["_support"])
        if assessment["status"] != "supported":
            raise ValueError(support.retirement_issue(assessment))
        release = releases.inspect_release(config["policyRepository"], version)
        requirements = release_json(release, "policy/requirements.json")
        if (
            requirements.get("schemaVersion") != 1
            or not isinstance(requirements.get("systems"), list)
            or not set(requirements["systems"]) <= SYSTEMS.keys()
        ):
            raise ValueError("Unsupported selected release requirements")
        modern = releases.version_at_least(version, (0, 4, 0))
        overrides = releases.version_at_least(version, (0, 2, 0))
        if modern:
            member = declarations.inspect(
                self.root,
                config["policyRepository"],
                name,
                requirements,
                checker_version=version,
            )
            settings = {
                field: member[field] for field in declarations.INPUT_FIELDS.values()
            }
        else:
            member = config["projects"].get(name)
            if member is None or member["policyVersion"] != version:
                raise ValueError(
                    "Historical member selection needs its matching trusted legacy record"
                )
            settings = {
                "requiredArchitectures": member.get(
                    "requiredArchitectures", requirements["systems"]
                )
                if overrides
                else requirements["systems"],
                "vmTargets": member["vmTargets"],
                "additionalRequiredChecks": member.get("additionalRequiredChecks", []),
            }
        record_root, execution = self.execution_records(config, pins)
        if overrides:
            expected_ci = declarations.ci_plan(settings, requirements["ci"])
            arguments = ["ci", *([str(self.root)] if modern else []), "--project", name]
            process = run_process(
                releases.checker_command(release, record_root, *arguments),
                self.output / "planning",
            )
            report = child_report(process, release, execution)
            if (
                process["returncode"] != 0
                or report.get("status") != "planned"
                or report.get("project") != name
                or report.get("policyVersion") != version
            ):
                raise ValueError(
                    "Selected checker could not produce a valid candidate plan"
                )
            for field in ("matrix", "compatibilityMatrix", "requiredChecks"):
                if report.get(field) != expected_ci[field]:
                    raise ValueError(
                        "Selected checker returned an incompatible CI plan"
                    )
            if modern and (
                report.get("memberSettings") != settings
                or report.get("revision") != before["revision"]
                or report.get("support") != assessment
            ):
                raise ValueError(
                    "Selected checker plan substituted member settings or support"
                )
            required = list(expected_ci["requiredChecks"])
            mandatory = declarations.ci_plan(
                {**settings, "additionalRequiredChecks": []}, requirements["ci"]
            )["requiredChecks"]
            extras = [gate for gate in required if gate not in mandatory]
            if not modern and not releases.version_at_least(version, (0, 3, 0)):
                extras = [
                    gate for gate in member["requiredChecks"] if gate not in required
                ]
                required.extend(extras)
            jobs = [
                {
                    "id": f"{job['system']}:{job['check']}",
                    "kind": {
                        "Compliance": "compliance",
                        "Formatting and lint": "lint",
                        "Project tests": "tests",
                    }[job["check"]],
                    "system": job["system"],
                    "gate": f"Policy / {job['check']} ({job['system']})",
                }
                for job in expected_ci["matrix"]["include"]
            ]
        else:
            required = list(member["requiredChecks"])
            ordinary = {
                f"Shared policy / Policy ({system})"
                for system in settings["requiredArchitectures"]
            }
            extras = [
                gate
                for gate in required
                if gate not in ordinary
                and gate
                not in {
                    "Shared policy / Policy records",
                    "Shared policy / VM tests (x86_64-linux)",
                    "Policy / Policy records",
                    "Policy / VM tests (x86_64-linux)",
                    *(f"Policy / Policy ({system})" for system in SYSTEMS),
                }
            ]
            jobs = [
                {
                    "id": f"{system}:{kind}",
                    "kind": kind,
                    "system": system,
                    "gate": f"Policy ({system})",
                }
                for system in settings["requiredArchitectures"]
                for kind in ("compliance", "lint", "tests")
            ]
        for system in settings["requiredArchitectures"]:
            for channel in ("stable", "unstable"):
                jobs.append(
                    {
                        "id": f"{system}:compatibility:{channel}",
                        "kind": "compatibility",
                        "system": system,
                        "channel": channel,
                        "gate": f"Policy / Compatibility ({channel}, {system})",
                    }
                )
        if settings["vmTargets"]:
            jobs.append(
                {
                    "id": "x86_64-linux:vm",
                    "kind": "vm",
                    "system": "x86_64-linux",
                    "gate": "Policy / VM tests (x86_64-linux)",
                }
            )
        minimum = set(required) - set(extras)
        for gate in extras:
            if gate not in minimum:
                jobs.append(
                    {
                        "id": f"additional:{gate}",
                        "kind": "agreement" if gate == agreement.GATE else "additional",
                        "system": settings["requiredArchitectures"][0],
                        "gate": gate,
                    }
                )
        plan = {
            "schemaVersion": 1,
            "scope": "single-member",
            "status": "planned",
            "eligible": False,
            "attempt": attempt,
            "project": name,
            "repository": config["_members"][name],
            "source": before,
            "release": release,
            "support": assessment,
            "memberSettings": settings,
            "baseline": baseline,
            "proposal": proposal,
            "executionRecords": execution,
            "batch": batch_id,
            "pins": batch["pins"],
            "compatibilityMode": "root-overrides" if overrides else "committed-pair",
            "requiredChecks": required,
            "jobs": jobs,
            "matrix": {
                "include": [
                    {"system": system, "runner": SYSTEMS[system]}
                    for system in SYSTEMS
                    if any(job["system"] == system for job in jobs)
                ]
            },
            **identity(self.source_root, self.orchestrator_revision),
        }
        if any(job["kind"] == "agreement" for job in jobs):
            process = run_process(
                releases.checker_command(
                    release, record_root, "agreement", self.root, "--project", name
                ),
                self.output / "planning-agreement",
            )
            report = child_report(process, release, execution)
            validate_agreement_subject(report, plan)
            plan["agreement"] = report
        self.verify_inputs(plan)
        self.verify_execution_records(record_root, plan)
        plan["planDigest"] = digest(plan)
        return plan, record_root

    def verify_inputs(self, plan):
        if self.source() != plan["source"]:
            raise ValueError("Member source changed during candidate validation")
        config, _, current = self.snapshot(self.baseline)
        if (
            current != plan["baseline"]
            or self.snapshot(self.proposal)[2] != plan["proposal"]
        ):
            raise ValueError(
                "Baseline or proposed records changed; renew candidate evidence"
            )
        verify_support(plan, config)

    def verify_execution_records(self, root, plan):
        if self.snapshot(root)[2] != plan["executionRecords"]:
            raise ValueError("Candidate execution records changed during validation")

    def expected(self):
        saved = records.read_json(self.args.plan)
        if not isinstance(saved, dict) or saved.get("scope") != "single-member":
            raise ValueError("Expected a captured single-member candidate plan")
        require_attempt(self.args, saved)
        plan, root = self.capture(saved["project"], saved["batch"], saved["attempt"])
        if saved != plan:
            raise ValueError(
                "Candidate plan changed or is stale; generate renewed evidence"
            )
        return plan, root

    def execute(self, plan, record_root):
        result = {
            "status": "candidate-pass",
            "scope": "native-member-results",
            "eligible": False,
            "planDigest": plan["planDigest"],
            "attempt": plan["attempt"],
            "project": plan["project"],
            "system": self.args.system,
            "executionSubject": {
                "root": str(self.root),
                "records": str(record_root),
                "source": plan["source"],
            },
            "results": [],
            "issues": [],
        }
        native = run_process(
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
            self.output / "native",
        )
        result["native"] = native
        if native["returncode"] or native["stdout"].strip() != self.args.system:
            raise ValueError(
                "Candidate worker requires the planned native architecture"
            )
        jobs = [job for job in plan["jobs"] if job["system"] == self.args.system]
        if not jobs:
            raise ValueError("This native architecture is not in the captured plan")
        for index, job in enumerate(jobs):
            item = {"job": job, "status": "fail", "commands": [], "issues": []}
            result["results"].append(item)
            try:
                self.verify_inputs(plan)
                self.verify_execution_records(record_root, plan)
                self.execute_job(
                    plan, record_root, job, self.output / f"job-{index}", item
                )
                self.verify_inputs(plan)
                self.verify_execution_records(record_root, plan)
                item["status"] = "pass"
            except ERRORS as error:
                item["issues"].append(str(error))
                result["status"] = "fail"
        try:
            self.verify_inputs(plan)
        except ERRORS as error:
            result["status"] = "fail"
            result["issues"].append(str(error))
        return result

    def execute_job(self, plan, record_root, job, output, result):
        release = plan["release"]
        kind = job["kind"]
        if kind == "additional":
            result["evidence"] = additional_evidence(
                plan["repository"], plan["source"]["revision"], job["gate"]
            )
            return
        commands = job_command_plan(
            plan,
            job,
            self.root,
            record_root,
            compatibility_output=output / "compatibility",
        )
        legacy_compatibility = (
            kind == "compatibility" and plan["compatibilityMode"] == "committed-pair"
        )
        if legacy_compatibility:
            inspection = run_process(commands[0], output / "pins")
            result["commands"].append(inspection)
            report = child_report(inspection, release, plan["executionRecords"])
            validate_member_report(report, plan, "compliance")
            result["pinInspection"] = report
            result["mode"] = "committed-pair"
            metadata = run_process(commands[1], output / "metadata")
            result["commands"].append(metadata)
            graph = self.lock_graph(
                json.loads(
                    metadata["stdout"], object_pairs_hook=records.unique_mapping
                )["locks"]
            )
            revision = plan["pins"][job["channel"]]
            if metadata["returncode"] or not any(
                self.repository_identity(node) == "nixos/nixpkgs"
                and node.get("locked", {}).get("rev") == revision
                for node in graph.reachable().values()
            ):
                raise ValueError(
                    f"Historical {job['channel']} coverage requires its candidate input in the committed root graph; prepare the required member source/lock change"
                )
            result["resolvedRevision"] = revision
            result["metadata"] = json.loads(metadata["stdout"])
            checks = run_process(commands[2], output / "checks")
            result["commands"].append(checks)
            result["checks"] = json.loads(
                checks["stdout"], object_pairs_hook=records.unique_mapping
            )
            if (
                checks["returncode"]
                or not result["checks"]
                or not declarations.valid_check_names(result["checks"])
            ):
                raise ValueError(
                    "Historical compatibility requires nonempty native root checks"
                )
        process = run_process(commands[-1], output / "execution")
        result["commands"].append(process)
        if kind == "tests" or legacy_compatibility:
            if process["returncode"] != 0:
                raise ValueError(
                    "Committed-lock root checks failed; inspect execution logs"
                )
        else:
            report = child_report(process, release, plan["executionRecords"])
            validate_member_report(report, plan, kind, job=job)
            result["report"] = report

    def aggregate(self, plan, *, evidence=None):
        result = {
            "status": "candidate-pass",
            "scope": "single-member",
            "eligible": False,
            "planDigest": plan["planDigest"],
            "attempt": plan["attempt"],
            "project": plan["project"],
            "results": [],
            "issues": [],
            "approval": "not-granted",
        }
        expected = {job["id"]: job for job in plan["jobs"]}
        seen = set()
        paths = (
            [(path, None) for path in sorted(self.args.results.glob("**/result.json"))]
            if evidence is None
            else evidence
        )
        for path, supplied in paths:
            try:
                worker = records.read_json(path) if supplied is None else supplied
                if worker.get("scope") != "native-member-results":
                    continue
                if (
                    worker.get("planDigest") != plan["planDigest"]
                    or worker.get("attempt") != plan["attempt"]
                    or worker.get("project") != plan["project"]
                ):
                    raise ValueError(
                        "Result belongs to a different candidate, subject, or attempt"
                    )
                if (
                    worker.get("status") != "candidate-pass"
                    or not isinstance(worker.get("results"), list)
                    or worker.get("issues") != []
                    or type(worker.get("native", {}).get("returncode")) is not int
                    or worker.get("native", {}).get("returncode") != 0
                    or worker.get("native", {}).get("stdout", "").strip()
                    != worker.get("system")
                    or worker.get("native", {}).get("command")
                    != [
                        "nix",
                        "eval",
                        "--raw",
                        "--impure",
                        "--expr",
                        "builtins.currentSystem",
                    ]
                    or worker.get("executionSubject", {}).get("source")
                    != plan["source"]
                ):
                    raise ValueError(
                        "Required native worker failed or returned malformed evidence"
                    )
                for item in worker["results"]:
                    job = item["job"]
                    key = job["id"]
                    if (
                        key in seen
                        or expected.get(key) != job
                        or worker.get("system") != job["system"]
                    ):
                        raise ValueError(
                            "Duplicate, unexpected, or substituted candidate job"
                        )
                    seen.add(key)
                    if item.get("status") != "pass" or item.get("issues"):
                        raise ValueError(f"Required job did not pass: {key}")
                    validate_job_commands(item, worker, plan)
                    if job["kind"] == "additional":
                        # Refresh externally owned gates; saved strings cannot assert success.
                        item["evidence"] = additional_evidence(
                            plan["repository"], plan["source"]["revision"], job["gate"]
                        )
                    elif not item.get("commands") or any(
                        command.get("returncode") != 0 for command in item["commands"]
                    ):
                        raise ValueError(f"Missing successful execution: {key}")
                    elif "report" in item:
                        replayed = child_report(
                            item["commands"][-1],
                            plan["release"],
                            plan["executionRecords"],
                        )
                        if replayed != item["report"]:
                            raise ValueError(
                                "Selected checker report disagrees with execution output"
                            )
                        validate_member_report(
                            item["report"], plan, job["kind"], job=job
                        )
                    elif (
                        job["kind"] == "compatibility"
                        and plan["compatibilityMode"] == "committed-pair"
                    ):
                        inspection = child_report(
                            item["commands"][0],
                            plan["release"],
                            plan["executionRecords"],
                        )
                        if inspection != item.get("pinInspection"):
                            raise ValueError(
                                "Historical pin inspection disagrees with execution output"
                            )
                        validate_member_report(inspection, plan, "compliance")
                        metadata = json.loads(
                            item["commands"][1]["stdout"],
                            object_pairs_hook=records.unique_mapping,
                        )
                        graph = self.lock_graph(metadata["locks"])
                        revision = plan["pins"][job["channel"]]
                        if item.get("resolvedRevision") != revision or not any(
                            self.repository_identity(node) == "nixos/nixpkgs"
                            and node.get("locked", {}).get("rev") == revision
                            for node in graph.reachable().values()
                        ):
                            raise ValueError(
                                "Historical channel evidence does not resolve the candidate revision"
                            )
                        if not item.get("checks") or not declarations.valid_check_names(
                            item["checks"]
                        ):
                            raise ValueError(
                                "Historical compatibility has no nonempty root checks"
                            )
                        validate_committed_execution(item["commands"][-1])
                    elif job["kind"] == "tests":
                        validate_committed_execution(item["commands"][-1])
                    else:
                        raise ValueError(f"Missing selected checker report: {key}")
                    result["results"].append(item)
            except ERRORS as error:
                result["issues"].append(f"{path.name}: {error}")
        for missing in sorted(expected.keys() - seen):
            result["issues"].append(f"Missing required execution: {missing}")
        self.verify_inputs(plan)
        if result["issues"]:
            result["status"] = "fail"
        return result


def release_json(release, path):
    response = releases.public_get(
        f"repos/{release['repository']}/contents/{path}?ref={release['revision']}"
    )
    if (
        not isinstance(response, dict)
        or response.get("encoding") != "base64"
        or not isinstance(response.get("content"), str)
    ):
        raise ValueError("Selected release requirements could not be inspected")
    raw = base64.b64decode(response["content"].replace("\n", ""), validate=True)
    return json.loads(raw, object_pairs_hook=records.unique_mapping)


def run_process(command, output):
    output.mkdir(parents=True, exist_ok=True)
    command = list(map(str, command))
    # The checker is immutable; member shells must not receive inspection credentials.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ACTIONS_")
        and key
        not in {
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "MEMBER_AUDIT_TOKEN",
            "NIX_CONFIG",
            "GITHUB_ENV",
            "GITHUB_OUTPUT",
            "GITHUB_PATH",
            "GITHUB_STEP_SUMMARY",
            "GITHUB_STATE",
        }
    }
    outcome = {
        "command": command,
        "returncode": None,
        "stdout": "",
        "stderr": "",
    }
    try:
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=7200,
            check=False,
            env=environment,
        )
        outcome.update(
            returncode=process.returncode,
            stdout=process.stdout or "",
            stderr=process.stderr or "",
        )
    except (OSError, subprocess.SubprocessError) as error:
        outcome["stderr"] = str(error)
        if isinstance(error, subprocess.TimeoutExpired):
            outcome["stdout"] = (
                error.stdout.decode(errors="replace")
                if isinstance(error.stdout, bytes)
                else error.stdout or ""
            )
    for stream in ("stdout", "stderr"):
        (output / f"{stream}.log").write_text(outcome[stream])
    return outcome


def child_report(process, release, snapshot):
    if process.get("command", [])[:6] != [
        "nix",
        "run",
        "--no-update-lock-file",
        f"github:{release['repository']}/{release['revision']}",
        "--",
        "--policy-root",
    ]:
        raise ValueError("Selected checker execution did not use its immutable source")
    if type(process["returncode"]) is not int or process["returncode"] not in {0, 1}:
        raise ValueError(
            process["stderr"].strip() or "Selected checker could not execute"
        )
    report = json.loads(process["stdout"], object_pairs_hook=records.unique_mapping)
    modern = releases.version_at_least(release["version"], (0, 4, 0))
    expected = {
        "checkerVersion": release["version"],
        "policyRecordsRevision": snapshot["revision"],
        "policyRecordsDigest": snapshot["digest" if modern else "legacyDigest"],
    }
    if (
        not isinstance(report, dict)
        or any(report.get(field) != value for field, value in expected.items())
        or report.get("checkerRevision", release["revision"]) != release["revision"]
        or (report.get("status") == "fail") != (process["returncode"] == 1)
    ):
        raise ValueError(
            "Selected checker returned an incompatible release or record report"
        )
    return report


def validate_committed_execution(process):
    command = process.get("command", [])
    if (
        command[:3] != ["nix", "flake", "check"]
        or "--no-update-lock-file" not in command
        or any(
            flag in command
            for flag in ("--override-input", "--no-build", "--no-write-lock-file")
        )
    ):
        raise ValueError("Missing full committed-lock root execution")


def job_command_plan(plan, job, root, record_root, *, compatibility_output=None):
    """Build the exact ordered argv contract shared by execution and replay."""
    root = str(root)
    committed = [
        "nix",
        "flake",
        "check",
        root,
        "--no-update-lock-file",
        "--print-build-logs",
    ]
    arguments = {
        "compliance": [
            "check",
            root,
            "--project",
            plan["project"],
            "--batch",
            plan["batch"],
            "--readiness",
            "--shell",
        ],
        "lint": ["lint", root],
        "vm": ["vm", root, "--project", plan["project"]],
        "agreement": ["agreement", root, "--project", plan["project"]],
    }
    if job["kind"] == "tests":
        return [committed]
    if job["kind"] == "compatibility" and plan["compatibilityMode"] == "committed-pair":
        return [
            releases.checker_command(
                plan["release"], Path(record_root), *arguments["compliance"][:-1]
            ),
            ["nix", "flake", "metadata", root, "--json", "--no-update-lock-file"],
            [
                "nix",
                "eval",
                "--json",
                f"{root}#checks.{job['system']}",
                "--apply",
                "builtins.attrNames",
                "--no-update-lock-file",
            ],
            committed,
        ]
    if job["kind"] == "compatibility":
        arguments["compatibility"] = [
            "compatibility",
            root,
            "--project",
            plan["project"],
            "--batch",
            plan["batch"],
            "--channel",
            job["channel"],
            "--output",
            compatibility_output,
        ]
    return [
        releases.checker_command(
            plan["release"], Path(record_root), *arguments[job["kind"]]
        )
    ]


def validate_job_commands(item, worker, plan):
    job = item["job"]
    if job["kind"] == "additional":
        return
    subject = worker["executionSubject"]
    root, record_root = subject["root"], subject["records"]
    if (
        not isinstance(root, str)
        or not isinstance(record_root, str)
        or not Path(root).is_absolute()
        or not Path(record_root).is_absolute()
    ):
        raise ValueError("Missing captured execution paths")
    commands = [process["command"] for process in item["commands"]]
    if any(
        type(process.get("returncode")) is not int or process["returncode"] != 0
        for process in item["commands"]
    ):
        raise ValueError(
            "Required execution did not return a successful process outcome"
        )
    compatibility_output = None
    if job["kind"] == "compatibility" and plan["compatibilityMode"] == "committed-pair":
        if item.get("checks") != json.loads(
            item["commands"][2]["stdout"], object_pairs_hook=records.unique_mapping
        ):
            raise ValueError(
                "Historical check coverage disagrees with execution output"
            )
    elif job["kind"] == "compatibility":
        compatibility_output = commands[0][-1]
        if (
            not isinstance(compatibility_output, str)
            or not Path(compatibility_output).is_absolute()
        ):
            raise ValueError("Missing captured compatibility output path")
        for process in item.get("report", {}).get("commands", []):
            command = process.get("command", [])
            if command[:3] == ["nix", "flake", "check"] and (
                command[3] != root
                or command[-3:]
                != [
                    "--override-input",
                    "nixpkgs",
                    f"github:NixOS/nixpkgs/{plan['pins'][job['channel']]}",
                ]
            ):
                raise ValueError(
                    "Compatibility execution substituted its root or candidate"
                )
    expected = job_command_plan(
        plan,
        job,
        root,
        record_root,
        compatibility_output=compatibility_output,
    )
    if commands != expected:
        raise ValueError(
            "Required execution substituted the selected checker, source, or gate"
        )


def validate_agreement_subject(report, plan):
    for field, expected in {
        "project": plan["project"],
        "revision": plan["source"]["revision"],
        "policyVersion": plan["release"]["version"],
        "memberSettings": plan["memberSettings"],
        "support": plan["support"],
        "records": plan["executionRecords"],
        "behavioralIntegration": "not-run",
    }.items():
        if report.get(field) != expected:
            raise ValueError("Agreement checker substituted the integration subject")
    if (
        not isinstance(report.get("members"), list)
        or not report["members"]
        or not isinstance(report.get("lockfiles"), list)
        or not report["lockfiles"]
        or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("dependencySetDigest")))
    ):
        raise ValueError("Agreement requires captured locked dependency evidence")


def validate_member_report(report, plan, kind, *, job=None):
    expected_status = (
        "candidate-ready"
        if kind == "compliance"
        else "candidate-pass"
        if kind == "compatibility"
        else "pass"
    )
    if report.get("status") != expected_status or report.get("issues"):
        raise ValueError(
            f"Required {kind} check failed: {report.get('issues', report.get('status'))}"
        )
    if kind in {"compliance", "compatibility"}:
        for field, expected in {
            "project": plan["project"],
            "revision": plan["source"]["revision"],
            "policyVersion": plan["release"]["version"],
            "candidateBatch": plan["batch"],
        }.items():
            if report.get(field) != expected:
                raise ValueError("Selected checker substituted the candidate subject")
    if kind == "compatibility":
        expected = {
            "system": job["system"],
            "channel": job["channel"],
            "pinStatus": "candidate",
            "expectedRevision": plan["pins"][job["channel"]],
            "resolvedRevision": plan["pins"][job["channel"]],
            "checkerRevision": plan["release"]["revision"],
            "sourceDirty": False,
            "sourceDigest": plan["source"]["digest"],
        }
        checks, commands = report.get("checks"), report.get("commands")
        if (
            any(report.get(field) != value for field, value in expected.items())
            or not checks
            or not declarations.valid_check_names(checks)
            or not isinstance(commands, list)
            or not any(
                command.get("command", [])[:3] == ["nix", "flake", "check"]
                and command.get("returncode") == 0
                and "--no-build" not in command["command"]
                for command in commands
            )
        ):
            raise ValueError(
                "Compatibility needs verified effective pins and full nonempty root execution"
            )
    if kind == "vm" and report.get("targets") != plan["memberSettings"]["vmTargets"]:
        raise ValueError("Selected checker did not execute the required VM targets")
    if kind == "agreement":
        validate_agreement_subject(report, plan)
        if report != plan.get("agreement") or any(
            member.get("status") != "pass"
            or member.get("policyVersion") != plan["release"]["version"]
            or member.get("support", {}).get("status") != "supported"
            for member in report["members"]
        ):
            raise ValueError("Integration agreement changed or did not pass")
    if releases.version_at_least(plan["release"]["version"], (0, 4, 0)) and kind in {
        "compliance",
        "compatibility",
        "vm",
    }:
        if (
            report.get("memberSettings") != plan["memberSettings"]
            or report.get("support") != plan["support"]
        ):
            raise ValueError("Selected checker substituted member settings or support")


def additional_evidence(repository, revision, gate):
    matches = []
    for page in range(1, 101):
        data = releases.public_get(
            f"repos/{repository}/commits/{revision}/check-runs?filter=latest&per_page=100&page={page}"
        )
        checks = data.get("check_runs") if isinstance(data, dict) else None
        if not isinstance(checks, list):
            raise ValueError("Additional required check evidence is unavailable")
        matches.extend(check for check in checks if check.get("name") == gate)
        if len(checks) < 100:
            break
    else:
        raise ValueError("Additional check inspection could not be completed")
    if not matches:
        for page in range(1, 101):
            statuses = releases.public_get(
                f"repos/{repository}/commits/{revision}/statuses?per_page=100&page={page}"
            )
            if not isinstance(statuses, list):
                raise ValueError("Additional required status evidence is unavailable")
            latest = next(
                (status for status in statuses if status.get("context") == gate), None
            )
            if latest is not None:
                if latest.get("state") != "success":
                    raise ValueError(f"Additional required status did not pass: {gate}")
                return {
                    "repository": repository,
                    "revision": revision,
                    "gate": gate,
                    "status": latest,
                }
            if len(statuses) < 100:
                break
        raise ValueError(
            f"Additional required check has no exact successful execution: {gate}"
        )
    if any(
        check.get("head_sha") != revision
        or check.get("status") != "completed"
        or check.get("conclusion") != "success"
        for check in matches
    ):
        raise ValueError(
            f"Additional required check has no exact successful execution: {gate}"
        )
    return {
        "repository": repository,
        "revision": revision,
        "gate": gate,
        "checks": matches,
    }


def require_attempt(args, plan):
    if args.attempt is not None and args.attempt != plan.get("attempt"):
        raise ValueError("Evidence belongs to a different expected attempt")


def verify_support(plan, config, *, at=None):
    if (
        support.assess(plan["release"]["version"], config["_support"], at=at)
        != plan["support"]
    ):
        raise ValueError("Selected release support changed; renew candidate evidence")
    for member in plan.get("agreement", {}).get("members", []):
        if (
            "support" in member
            and support.assess(member["policyVersion"], config["_support"], at=at)
            != member["support"]
        ):
            raise ValueError(
                "Locked member support changed; renew integration evidence"
            )


def run(args, **services):
    coordinator = None
    result = {
        "status": "error",
        "scope": "single-member",
        "eligible": False,
        "approval": "not-granted",
        "issues": [],
    }
    try:
        coordinator = Coordinator(args, **services)
        if args.operation == "plan":
            attempt = args.attempt or uuid.uuid4().hex
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", attempt):
                raise ValueError("Candidate attempt needs a simple unique identifier")
            result, _ = coordinator.capture(args.project, args.batch, attempt)
            write_json(coordinator.output / "plan.json", result)
        else:
            plan, root = coordinator.expected()
            write_json(coordinator.output / "plan.json", plan)
            if args.operation == "execute":
                result = coordinator.execute(plan, root)
            else:
                result = coordinator.aggregate(plan)
    except ERRORS as error:
        result["status"] = "error"
        result.setdefault("issues", []).append(str(error))
    if coordinator is not None:
        write_json(coordinator.output / "result.json", result)
    return result
