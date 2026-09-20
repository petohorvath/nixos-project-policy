"""Compose selected-release member checks into complete, bound batch evidence."""

import argparse
import re
import shutil
import uuid

if __package__:
    from . import candidates, proposals, records, releases, support
else:
    import candidates
    import proposals
    import records
    import releases
    import support


def worker_id(project, system):
    return f"{project}--{system}"


def matrix(members):
    rows = []
    seen = set()
    for name, member in sorted(members.items()):
        if member["status"] != "planned":
            continue
        plan = member["plan"]
        for row in plan["matrix"]["include"]:
            worker = worker_id(name, row["system"])
            if (
                row["system"] not in candidates.SYSTEMS
                or row["runner"] != candidates.SYSTEMS[row["system"]]
                or worker in seen
            ):
                raise ValueError("Unsafe or conflicting whole-batch native workers")
            seen.add(worker)
            rows.append(
                {
                    **row,
                    "project": name,
                    "repository": plan["repository"],
                    "revision": plan["source"]["revision"],
                    "worker": worker,
                    "job": f"Candidate member ({name}, {row['system']})",
                }
            )
    return {"include": rows}


def validate_plan(plan):
    """Validate captured structure; callers still establish freshness and provenance."""
    if (
        not isinstance(plan, dict)
        or plan.get("scope") != "whole-batch"
        or type(plan.get("schemaVersion")) is not int
        or plan["schemaVersion"] != 1
    ):
        raise ValueError("Expected a captured whole-batch plan")
    if plan.get("planDigest") != candidates.digest(
        {key: value for key, value in plan.items() if key != "planDigest"}
    ):
        raise ValueError("Whole-batch plan identity is invalid")
    if (
        not isinstance(plan.get("roster"), dict)
        or not isinstance(plan.get("registrations"), dict)
        or not isinstance(plan.get("members"), dict)
        or set(plan["members"]) != set(plan["roster"])
    ):
        raise ValueError(
            "Whole-batch plan omitted or substituted an enrolled participant"
        )
    for name, member in plan["members"].items():
        if not isinstance(member, dict) or member.get("status") not in {
            "planned",
            "error",
        }:
            raise ValueError("Malformed required member plan")
        if member["status"] == "planned":
            child = member["plan"]
            expected = {
                "scope": "single-member",
                "project": name,
                "repository": plan["roster"][name],
                **{
                    key: plan[key]
                    for key in (
                        "attempt",
                        "batch",
                        "pins",
                        "baseline",
                        "proposal",
                        "orchestratorVersion",
                        "orchestratorRevision",
                        "orchestratorDigest",
                    )
                },
            }
            if any(
                child.get(key) != value for key, value in expected.items()
            ) or child.get("source", {}).get("revision") != plan["registrations"].get(
                name
            ):
                raise ValueError("Required member plan substituted its bound subject")
    if plan.get("matrix") != matrix(plan["members"]):
        raise ValueError("Whole-batch native workers disagree with member coverage")
    return plan


class Batch:
    def __init__(self, args, services):
        self.args, self.services = args, services
        if args.operation == "aggregate" and args.output.resolve().is_relative_to(
            args.results.resolve()
        ):
            raise ValueError("Batch output must be outside the input artifacts")
        self.root = args.project_dir.resolve()
        self.output = candidates.evidence_directory(
            args.output,
            self.root,
            args.policy_root,
            args.proposal_root,
            services["source_root"],
        )
        self.result = {
            "schemaVersion": 1,
            "scope": "whole-batch",
            "status": "error",
            "eligible": False,
            "approval": "not-granted",
            "members": {},
            "workers": [],
            "issues": [],
        }

    def shared(self, batch):
        proposal = proposals.read(
            self.args.policy_root,
            self.args.proposal_root,
            git_revision=self.services["git_revision"],
        )
        roster = proposal.config["_members"]
        if not roster or any(
            not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) for name in roster
        ):
            raise ValueError("Complete batches require a nonempty trusted roster")
        _, selected = proposal.candidate(batch)
        return {
            "roster": roster,
            "baseline": proposal.baseline,
            "proposal": proposal.proposal,
            "batch": batch,
            "pins": selected["pins"],
            "registrations": selected["projects"],
            **candidates.identity(
                self.services["source_root"], self.services["orchestrator_revision"]
            ),
        }

    def child(self, name, *, root=None):
        arguments = argparse.Namespace(**vars(self.args))
        arguments.project_dir = root or self.root / name
        arguments.output = self.output / "members" / name
        arguments.project = name
        return candidates.Coordinator(arguments, **self.services)

    def fetch(self, name, shared):
        root = self.root / name
        if not getattr(self.args, "fetch", False) or root.exists():
            return
        revision = shared["registrations"].get(name)
        if not isinstance(revision, str) or not releases.REVISION.fullmatch(revision):
            raise ValueError("Missing exact enrolled member registration")
        root.mkdir(parents=True)
        repository = shared["roster"][name]
        commands = [
            ["git", "-c", "core.hooksPath=/dev/null", "init", str(root)],
            [
                "git",
                "-C",
                str(root),
                "-c",
                "core.hooksPath=/dev/null",
                "fetch",
                "--depth",
                "1",
                f"https://github.com/{repository}.git",
                revision,
            ],
            [
                "git",
                "-C",
                str(root),
                "-c",
                "core.hooksPath=/dev/null",
                "checkout",
                "--detach",
                "FETCH_HEAD",
            ],
        ]
        for index, command in enumerate(commands):
            process = candidates.run_process(
                command, self.output / "fetch" / name / str(index)
            )
            if process["returncode"]:
                raise ValueError(
                    f"Could not capture {name} at {revision}: {process['stderr'].strip()}"
                )

    def plan(self):
        attempt = self.args.attempt or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", attempt):
            raise ValueError("Candidate attempt needs a simple unique identifier")
        shared = self.shared(self.args.batch)
        plan = {
            **self.result,
            **shared,
            "attempt": attempt,
            "status": "planned",
            "evidenceTrust": "caller-supplied-native-workers",
        }
        self.result = plan
        captured_children = {}
        extra = set(shared["registrations"]) - set(shared["roster"])
        if extra:
            plan["issues"].append(
                f"Candidate registrations include unenrolled participants: {', '.join(sorted(extra))}"
            )
        for name in sorted(shared["roster"]):
            member = {"status": "error", "issues": []}
            plan["members"][name] = member
            try:
                self.fetch(name, shared)
                child = self.child(name)
                captured, _ = child.capture(name, self.args.batch, attempt)
                captured_children[name] = child
                candidates.write_json(child.output / "plan.json", captured)
                member.update(status="planned", plan=captured)
            except candidates.ERRORS as error:
                member["issues"].append(str(error))
        for name, child in captured_children.items():
            try:
                child.verify_inputs(plan["members"][name]["plan"])
            except candidates.ERRORS as error:
                plan["members"][name]["status"] = "error"
                plan["members"][name]["issues"].append(str(error))
        if self.shared(self.args.batch) != shared:
            plan["issues"].append(
                "Trusted authority or proposal changed during batch capture"
            )
        if plan["issues"] or any(
            member["status"] != "planned" for member in plan["members"].values()
        ):
            plan["status"] = "fail"
        plan["matrix"] = matrix(plan["members"])
        plan["planDigest"] = candidates.digest(plan)
        candidates.write_json(self.output / "plan.json", plan)
        return plan

    def expected(self):
        saved = validate_plan(records.read_json(self.args.plan))
        candidates.require_attempt(self.args, saved)
        shared = self.shared(saved["batch"])
        if any(saved.get(key) != value for key, value in shared.items()):
            raise ValueError(
                "Whole-batch authority, candidate, roster, or source registrations changed"
            )
        return saved

    def recapture(self, plan, name, *, root=None):
        if root is None:
            self.fetch(name, plan)
        child = self.child(name, root=root)
        captured, record_root = child.capture(name, plan["batch"], plan["attempt"])
        if captured != plan["members"][name].get("plan"):
            raise ValueError(
                f"Required member {name} changed or has stale candidate evidence"
            )
        candidates.write_json(child.output / "plan.json", captured)
        return child, captured, record_root

    def execute(self):
        plan = self.expected()
        name = self.args.project
        if name not in plan["members"] or plan["members"][name]["status"] != "planned":
            raise ValueError("Select a captured enrolled member with --project")
        row = next(
            (
                row
                for row in plan["matrix"]["include"]
                if row["project"] == name and row["system"] == self.args.system
            ),
            None,
        )
        if row is None:
            raise ValueError("Required native worker is absent from the batch plan")
        self.result.update(
            scope="native-member-results",
            batchPlanDigest=plan["planDigest"],
            planDigest=plan["members"][name]["plan"]["planDigest"],
            attempt=plan["attempt"],
            project=name,
            system=self.args.system,
            worker=row["worker"],
            workerJob=row["job"],
        )
        child, captured, root = self.recapture(plan, name, root=self.root)
        result = child.execute(captured, root)
        result.update(
            batchPlanDigest=plan["planDigest"],
            worker=row["worker"],
            workerJob=row["job"],
        )
        self.result = result
        candidates.write_json(self.output / "plan.json", plan)
        return result

    def read_evidence(self):
        result = []
        directory = self.output / "evidence"
        directory.mkdir()
        # Each downloaded artifact has one root envelope. Nested checker reports
        # and logs belong to that worker; they are not additional native workers.
        roots = (
            [self.args.results]
            if (self.args.results / "result.json").exists()
            else sorted(self.args.results.iterdir())
            if self.args.results.is_dir()
            else []
        )
        for index, root in enumerate(roots):
            path = root / "result.json"
            retained = directory / f"worker-{index}"
            try:
                if (
                    root.is_symlink()
                    or not root.is_dir()
                    or any(
                        child.is_symlink() or not (child.is_file() or child.is_dir())
                        for child in root.rglob("*")
                    )
                ):
                    raise ValueError(
                        "Worker artifacts require regular files and directories"
                    )
                shutil.copytree(root, retained)
                worker = records.read_json(retained / "result.json")
                if not isinstance(worker, dict):
                    raise ValueError("Worker result is not an object")
                result.append((retained / "result.json", worker))
                self.result["workers"].append(
                    {
                        "artifact": str(retained.relative_to(self.output)),
                        "result": worker,
                    }
                )
            except candidates.ERRORS as error:
                self.result["issues"].append(
                    f"Unprocessable worker evidence {path}: {error}"
                )
        return result

    def outcomes(self, plan):
        if (
            self.args.execution_status is not None
            and self.args.execution_status != "success"
        ):
            self.result["issues"].append(
                f"Required workflow execution did not succeed: {self.args.execution_status}"
            )
        if self.args.worker_outcomes is None:
            self.result["executionEvidence"] = "caller-trusted-local-artifacts"
            return
        value = records.read_json(self.args.worker_outcomes)
        self.result["executionEvidence"] = "caller-trusted-worker-outcomes"
        self.result["workerOutcomes"] = value
        if (
            not isinstance(value, dict)
            or type(value.get("schemaVersion")) is not int
            or value.get("schemaVersion") != 1
            or value.get("planDigest") != plan["planDigest"]
            or value.get("attempt") != plan["attempt"]
            or not isinstance(value.get("workers"), list)
        ):
            raise ValueError("Worker outcomes belong to a different plan or attempt")
        expected = {row["worker"] for row in plan["matrix"]["include"]}
        seen = set()
        for worker in value["workers"]:
            if (
                not isinstance(worker, dict)
                or worker.get("id") not in expected
                or worker["id"] in seen
            ):
                raise ValueError(
                    "Unexpected, duplicate, or malformed native worker outcome"
                )
            seen.add(worker["id"])
            if (
                worker.get("status") != "completed"
                or worker.get("conclusion") != "success"
            ):
                self.result["issues"].append(
                    f"Required native job {worker['id']} did not succeed: {worker.get('status')}/{worker.get('conclusion')}"
                )
        for missing in sorted(expected - seen):
            self.result["issues"].append(
                f"Missing required native job outcome: {missing}"
            )

    def aggregate(self):
        evidence = self.read_evidence()
        plan = self.expected()
        self.result.update(
            status="candidate-pass",
            planDigest=plan["planDigest"],
            attempt=plan["attempt"],
            **{
                key: plan[key]
                for key in ("roster", "batch", "pins", "baseline", "proposal")
            },
        )
        candidates.write_json(self.output / "plan.json", plan)
        if plan["status"] != "planned" or plan["issues"]:
            self.result["issues"].append(
                "The captured complete batch has planning failures"
            )
        if set(plan["registrations"]) != set(plan["roster"]):
            self.result["issues"].append(
                "Candidate registrations do not equal the trusted roster"
            )
        expected = {row["worker"]: row for row in plan["matrix"]["include"]}
        grouped = {name: [] for name in plan["roster"]}
        seen = set()
        for path, worker in evidence:
            try:
                key = worker.get("worker")
                if key not in expected or key in seen:
                    raise ValueError(
                        "Missing, duplicate, or unplanned native worker identity"
                    )
                seen.add(key)
                row = expected[key]
                if (
                    worker.get("scope") != "native-member-results"
                    or worker.get("batchPlanDigest") != plan["planDigest"]
                    or worker.get("attempt") != plan["attempt"]
                    or worker.get("project") != row["project"]
                    or worker.get("system") != row["system"]
                    or worker.get("workerJob") != row["job"]
                ):
                    raise ValueError(
                        "Native result substituted its batch, member, architecture, or attempt"
                    )
                grouped[row["project"]].append((path, worker))
            except candidates.ERRORS as error:
                self.result["issues"].append(f"{path.name}: {error}")
        for missing in sorted(expected.keys() - seen):
            self.result["issues"].append(f"Missing required native worker: {missing}")
        try:
            self.outcomes(plan)
        except candidates.ERRORS as error:
            self.result["issues"].append(str(error))
        validated = {}
        for name, member in sorted(plan["members"].items()):
            summary = {
                "status": "error",
                "issues": [],
                "plan": member.get("plan"),
                "planningIssues": member["issues"],
            }
            self.result["members"][name] = summary
            try:
                if member["status"] != "planned":
                    raise ValueError("Required enrolled member could not be planned")
                child, captured, _ = self.recapture(plan, name)
                validated[name] = (child, captured)
                summary.update(child.aggregate(captured, evidence=grouped[name]))
            except candidates.ERRORS as error:
                summary["issues"].append(str(error))
        for name, (child, captured) in validated.items():
            try:
                child.verify_inputs(captured)
            except candidates.ERRORS as error:
                self.result["members"][name]["status"] = "fail"
                self.result["members"][name]["issues"].append(str(error))
        try:
            if any(
                plan.get(key) != value
                for key, value in self.shared(plan["batch"]).items()
            ):
                raise ValueError("Batch authority changed during aggregation")
        except candidates.ERRORS as error:
            self.result["issues"].append(str(error))
        if self.args.policy_root.resolve() == self.args.proposal_root.resolve():
            config, _, _ = records.proposed_snapshot(self.args.policy_root)
        else:
            config, _ = records.load(self.args.policy_root)
        assessment_time = support.now()
        for name, member in plan["members"].items():
            if member["status"] == "planned":
                try:
                    candidates.verify_support(
                        member["plan"], config, at=assessment_time
                    )
                except candidates.ERRORS as error:
                    self.result["members"][name]["issues"].append(str(error))
                    self.result["members"][name]["status"] = "fail"
        if self.result["issues"] or any(
            member["status"] != "candidate-pass"
            for member in self.result["members"].values()
        ):
            self.result["status"] = "fail"
        self.result["eligible"] = self.result["status"] == "candidate-pass"
        return self.result


def run(args, **services):
    batch = None
    result = {
        "status": "error",
        "scope": "whole-batch",
        "eligible": False,
        "approval": "not-granted",
        "issues": [],
    }
    try:
        batch = Batch(args, services)
        result = getattr(batch, args.operation)()
    except candidates.ERRORS as error:
        if batch is not None:
            result = batch.result
        result.update(status="error", eligible=False)
        result["issues"].append(str(error))
    if batch is not None:
        candidates.write_json(batch.output / "result.json", result)
    return result
