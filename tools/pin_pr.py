"""Bind complete candidate evidence to a trusted GitHub PR workflow attempt."""

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib import parse, request
import zipfile

if __package__:
    from . import candidates, proposals, records, support
else:
    import candidates
    import proposals
    import records
    import support

GATE = "Pin batch / Complete candidate"
WORKFLOW = ".github/workflows/pin-pr.yml"
CAPTURE_JOB = "Capture pin proposal"
PLAN_JOB = "Plan pin candidate"
AGGREGATE_JOB = "Aggregate pin candidate"


def add_commands(commands):
    command = commands.add_parser(
        "pin-pr", help="Bind candidate evidence to its exact central PR"
    )
    operations = command.add_subparsers(dest="operation", required=True)
    for operation in ("capture", "collect", "finish", "invalidate"):
        parser = operations.add_parser(operation)
        parser.add_argument("--output", type=Path, required=True)
        if operation != "invalidate":
            parser.add_argument("--proposal-root", type=Path, required=True)
            parser.add_argument("--number", type=int, required=True)
            parser.add_argument("--head", required=True)
            parser.add_argument("--run", type=int, required=True)
            parser.add_argument("--attempt", type=int, required=True)
        if operation == "finish":
            parser.add_argument("--check", type=int, required=True)


def api(path, payload=None, *, method=None, binary=False):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GitHub PR evidence inspection requires a token")
    query = request.Request(
        f"https://api.github.com/{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method=method,
    )
    # Artifact downloads redirect to signed storage URLs, which need no API token.
    query.add_unredirected_header("Authorization", f"Bearer {token}")
    with request.urlopen(query, timeout=60) as response:
        data = response.read()
    return (
        data if binary else json.loads(data, object_pairs_hook=records.unique_mapping)
    )


def pages(path, key=None):
    values = []
    for page in range(1, 101):
        response = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        items = response[key] if key else response
        if not isinstance(items, list):
            raise ValueError("GitHub returned malformed paginated evidence")
        values.extend(items)
        if len(items) < 100:
            return values
    raise ValueError("GitHub evidence pagination was incomplete")


class Review:
    def __init__(self, args, load_policy):
        self.args = args
        self.load_policy = load_policy
        self.config, self.pins, self.baseline = records.proposed_snapshot(
            args.policy_root, load_policy
        )
        self.repository = self.config["policyRepository"]
        self.prefix = f"repos/{self.repository}"
        self.output = args.output.resolve()
        for root in (
            args.policy_root,
            getattr(args, "proposal_root", args.policy_root),
        ):
            if self.output.is_relative_to(root.resolve()):
                raise ValueError("PR evidence output must be outside its inputs")
        self.output.mkdir(parents=True, exist_ok=False)
        self.result = {
            "schemaVersion": 1,
            "scope": "pin-pr",
            "status": "error",
            "eligible": False,
            "approval": "not-granted",
            "issues": [],
        }

    def authority(self):
        repository = api(self.prefix)
        if repository["full_name"].lower() != self.repository.lower():
            raise ValueError("GitHub repository disagrees with trusted policy identity")
        branch = repository["default_branch"]
        current = api(f"{self.prefix}/git/ref/heads/{parse.quote(branch, safe='')}")[
            "object"
        ]["sha"]
        if current != self.baseline["revision"]:
            raise ValueError(
                "Trusted base changed; renew validation against current authority"
            )
        return branch

    def context(self):
        args = self.args
        if min(args.number, args.run, args.attempt) < 1 or not re.fullmatch(
            r"[0-9a-f]{40}", args.head
        ):
            raise ValueError(
                "PR evidence requires exact proposal, run, and attempt identities"
            )
        branch = self.authority()
        pull = api(f"{self.prefix}/pulls/{args.number}")
        if (
            pull.get("state") != "open"
            or pull["head"]["sha"] != args.head
            or pull["base"]["ref"] != branch
            or pull["base"]["repo"]["full_name"].lower() != self.repository.lower()
        ):
            raise ValueError("Proposal head or base changed; renew PR validation")
        run = api(f"{self.prefix}/actions/runs/{args.run}")
        if (
            run.get("id") != args.run
            or run.get("run_attempt") != args.attempt
            or run.get("event") != "pull_request_target"
            or run.get("path", "").split("@")[0] != WORKFLOW
            or run["repository"]["full_name"].lower() != self.repository.lower()
            or run.get("head_sha") != self.baseline["revision"]
        ):
            raise ValueError(
                "Evidence belongs to a different trusted workflow run or attempt"
            )
        self.result.update(
            repository=self.repository,
            number=args.number,
            head=args.head,
            baseline=self.baseline,
            run=args.run,
            attempt=f"{args.run}:{args.attempt}",
            workflow=WORKFLOW,
            workflowHead=run["head_sha"],
        )
        return self.result

    def classify(self):
        proposed, pins, snapshot = records.proposed_snapshot(
            self.args.proposal_root, self.load_policy
        )
        if snapshot["revision"] != self.args.head:
            raise ValueError("Proposal checkout does not match the reviewed head")
        self.result["proposal"] = snapshot
        proposal = proposals.Proposal(
            (self.config, self.pins, self.baseline), (proposed, pins, snapshot)
        )
        self.result.update(proposal.classify())
        return self.result

    def external_id(self):
        return f"pin-pr:{self.args.number}:{self.args.run}:{self.args.attempt}:{self.baseline['revision']}"

    def capture(self):
        self.context()
        check = api(
            f"{self.prefix}/check-runs",
            {
                "name": GATE,
                "head_sha": self.args.head,
                "status": "in_progress",
                "external_id": self.external_id(),
                "details_url": f"https://github.com/{self.repository}/actions/runs/{self.args.run}/attempts/{self.args.attempt}",
                "output": {
                    "title": "Candidate validation pending",
                    "summary": "Approval remains unchanged until human review and merge.",
                },
            },
        )
        if type(check.get("id")) is not int or check.get("head_sha") != self.args.head:
            raise ValueError(
                "GitHub did not associate the pending check with the proposal"
            )
        self.result["check"] = check["id"]
        self.classify()
        self.result["status"] = "captured"
        return self.result

    def evidence_index(self):
        self.jobs = pages(
            f"{self.prefix}/actions/runs/{self.args.run}/attempts/{self.args.attempt}/jobs",
            "jobs",
        )
        self.artifacts = pages(
            f"{self.prefix}/actions/runs/{self.args.run}/artifacts", "artifacts"
        )
        self.result.update(jobs=self.jobs, artifacts=self.artifacts)

    def job(self, name, runner=None):
        matches = [job for job in self.jobs if job.get("name") == name]
        if len(matches) != 1:
            self.result["issues"].append(f"Missing or duplicate required job: {name}")
            return {"status": "missing", "conclusion": None}
        job = matches[0]
        if (
            job.get("status") != "completed"
            or job.get("conclusion") != "success"
            or (runner and runner not in job.get("labels", []))
        ):
            self.result["issues"].append(
                f"Required job {name} failed or lacks its native runner: {job.get('status')}/{job.get('conclusion')}"
            )
        return job

    def artifact_entry(self, name):
        matches = [
            artifact for artifact in self.artifacts if artifact.get("name") == name
        ]
        if len(matches) != 1:
            raise ValueError(f"Missing or duplicate required artifact: {name}")
        artifact = matches[0]
        if (
            type(artifact.get("id")) is not int
            or artifact.get("expired") is not False
            or artifact.get("workflow_run", {}).get("id") != self.args.run
            or artifact["workflow_run"].get("head_sha") != self.result["workflowHead"]
        ):
            raise ValueError(
                f"Artifact {name} is unavailable or belongs to another workflow context"
            )
        if not isinstance(artifact.get("digest"), str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", artifact["digest"]
        ):
            raise ValueError(f"Artifact {name} has no verifiable content identity")
        return artifact

    def artifact(self, name, destination):
        artifact = self.artifact_entry(name)
        data = api(f"{self.prefix}/actions/artifacts/{artifact['id']}/zip", binary=True)
        if artifact.get("digest") != f"sha256:{hashlib.sha256(data).hexdigest()}":
            raise ValueError(f"Artifact {name} content disagrees with GitHub metadata")
        destination.mkdir(parents=True, exist_ok=False)
        seen = set()
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for item in archive.infolist():
                    path = PurePosixPath(item.filename)
                    mode = item.external_attr >> 16
                    if (
                        path.is_absolute()
                        or not path.parts
                        or any(part in {".", ".."} for part in path.parts)
                        or "\\" in item.filename
                        or path in seen
                        or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})
                    ):
                        raise ValueError(
                            f"Artifact {name} contains unsafe or duplicate paths"
                        )
                    seen.add(path)
                    target = destination / path
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(item))
        except zipfile.BadZipFile as error:
            raise ValueError(f"Artifact {name} is not a valid archive") from error
        return destination

    def collected_plan(self):
        directory = self.artifact(
            f"pin-plan-{self.args.run}-{self.args.attempt}", self.output / "plan"
        )
        plan = records.read_json(directory / "plan.json")
        if (
            plan.get("scope") != "whole-batch"
            or plan.get("attempt") != self.result["attempt"]
            or plan.get("baseline") != self.baseline
            or plan.get("proposal") != self.result["proposal"]
            or plan.get("batch") != self.result["batch"]
            or plan.get("roster") != self.config["_members"]
            or plan.get("orchestratorRevision") != self.baseline["revision"]
            or plan.get("planDigest")
            != candidates.digest(
                {key: value for key, value in plan.items() if key != "planDigest"}
            )
        ):
            raise ValueError(
                "Hosted plan substituted proposal, authority, attempt, or enrollment"
            )
        seen = set()
        for row in plan["matrix"]["include"]:
            if (
                row["project"] not in self.config["_members"]
                or row["system"] not in candidates.SYSTEMS
                or row["runner"] != candidates.SYSTEMS[row["system"]]
                or row["worker"] != f"{row['project']}--{row['system']}"
                or row["worker"] in seen
                or row["job"] != f"Candidate member ({row['project']}, {row['system']})"
            ):
                raise ValueError(
                    "Hosted plan contains unsafe or conflicting native workers"
                )
            seen.add(row["worker"])
        self.result["planDigest"] = plan["planDigest"]
        return plan

    def worker_jobs(self, plan):
        outcomes = {
            "schemaVersion": 1,
            "attempt": self.result["attempt"],
            "planDigest": plan["planDigest"],
            "workers": [],
        }
        expected = set()
        for row in plan["matrix"]["include"]:
            job = self.job(row["job"], row["runner"])
            outcomes["workers"].append(
                {
                    "id": row["worker"],
                    "status": job.get("status"),
                    "conclusion": job.get("conclusion"),
                }
            )
            name = f"pin-result-{self.args.run}-{self.args.attempt}-{row['worker']}"
            expected.add(name)
            try:
                self.artifact_entry(name)
            except candidates.ERRORS as error:
                self.result["issues"].append(str(error))
        unexpected = {
            item["name"]
            for item in self.artifacts
            if item["name"].startswith(
                f"pin-result-{self.args.run}-{self.args.attempt}-"
            )
        } - expected
        if unexpected:
            self.result["issues"].append(
                f"Unplanned candidate artifacts: {', '.join(sorted(unexpected))}"
            )
        return outcomes

    def collect(self):
        self.context()
        self.classify()
        if self.result["classification"] != "candidate":
            raise ValueError("This PR does not identify a candidate batch")
        self.evidence_index()
        self.job(CAPTURE_JOB)
        self.job(PLAN_JOB)
        plan = self.collected_plan()
        outcomes = self.worker_jobs(plan)
        candidates.write_json(self.output / "worker-outcomes.json", outcomes)
        (self.output / "workers").mkdir()
        for row in plan["matrix"]["include"]:
            try:
                self.artifact(
                    f"pin-result-{self.args.run}-{self.args.attempt}-{row['worker']}",
                    self.output / "workers" / row["worker"],
                )
            except candidates.ERRORS as error:
                self.result["issues"].append(str(error))
        self.context()
        self.result["status"] = "fail" if self.result["issues"] else "collected"
        return self.result

    def finish(self):
        self.context()
        self.classify()
        self.evidence_index()
        self.job(CAPTURE_JOB)
        if self.result["classification"] == "candidate":
            self.job(PLAN_JOB)
            self.job(AGGREGATE_JOB)
            plan = self.collected_plan()
            self.plan = plan
            outcomes = self.worker_jobs(plan)
            directory = self.artifact(
                f"pin-summary-{self.args.run}-{self.args.attempt}",
                self.output / "summary",
            )
            summary = records.read_json(directory / "result.json")
            if (
                summary.get("scope") != "whole-batch"
                or summary.get("status") != "candidate-pass"
                or summary.get("eligible") is not True
                or summary.get("approval") != "not-granted"
                or summary.get("issues")
                or summary.get("workerOutcomes") != outcomes
                or any(
                    summary.get(key) != plan[key]
                    for key in (
                        "planDigest",
                        "attempt",
                        "baseline",
                        "proposal",
                        "roster",
                        "batch",
                        "pins",
                    )
                )
                or set(summary.get("members", {})) != set(self.config["_members"])
            ):
                raise ValueError(
                    "Complete-batch summary lacks successful bound hosted evidence"
                )
            self.result["members"] = {}
            versions = set()
            for name, member in sorted(plan["members"].items()):
                child = member["plan"]
                if (
                    member["status"] != "planned"
                    or summary["members"][name].get("status") != "candidate-pass"
                    or summary["members"][name].get("plan") != child
                ):
                    raise ValueError(
                        f"Member {name} lacks its complete captured assessment"
                    )
                self.result["members"][name] = {
                    "revision": child["source"]["revision"],
                    "policyVersion": child["release"]["version"],
                    "dependencySetDigest": child.get("agreement", {}).get(
                        "dependencySetDigest"
                    ),
                }
                versions.add(child["release"]["version"])
                versions.update(
                    member["policyVersion"]
                    for member in child.get("agreement", {}).get("members", [])
                    if "policyVersion" in member
                )
            self.result["selections"] = sorted(versions)
        # Fetching later artifacts can outlive a support deadline or proposal update.
        self.context()
        if (
            records.proposed_snapshot(self.args.policy_root, self.load_policy)[2]
            != self.baseline
            or records.proposed_snapshot(self.args.proposal_root, self.load_policy)[2]
            != self.result["proposal"]
        ):
            raise ValueError("Captured records changed during PR assessment")
        if self.result["classification"] == "candidate":
            instant = support.now()
            for name, member in plan["members"].items():
                try:
                    candidates.verify_support(member["plan"], self.config, at=instant)
                except candidates.ERRORS as error:
                    self.result["issues"].append(f"{name}: {error}")
        self.result["status"] = "fail" if self.result["issues"] else "pass"
        self.result["eligible"] = (
            self.result["status"] == "pass"
            and self.result["classification"] == "candidate"
        )
        return self.result

    def publish(self):
        if self.args.check == 0:
            if not re.fullmatch(r"[0-9a-f]{40}", self.args.head):
                raise ValueError("Failure reporting requires the exact proposal head")
            self.result.update(status="fail", eligible=False)
            self.result["issues"].append(
                "Proposal capture did not establish a pending check"
            )
            check = api(
                f"{self.prefix}/check-runs",
                {
                    "name": GATE,
                    "head_sha": self.args.head,
                    "status": "completed",
                    "conclusion": "failure",
                    "external_id": self.external_id(),
                    "output": {
                        "title": "Proposal capture failed",
                        "summary": "; ".join(self.result["issues"]),
                    },
                },
            )
            if (
                check.get("head_sha") != self.args.head
                or check.get("conclusion") != "failure"
            ):
                raise ValueError("GitHub did not confirm proposal capture failure")
            self.result["check"] = check["id"]
            return
        check = api(f"{self.prefix}/check-runs/{self.args.check}")
        if (
            check.get("name") != GATE
            or check.get("head_sha") != self.args.head
            or check.get("external_id") != self.external_id()
            or check.get("app", {}).get("slug") != "github-actions"
        ):
            raise ValueError(
                "Reporting check does not belong to this proposal and trusted attempt"
            )
        existing = pages(
            f"{self.prefix}/commits/{self.args.head}/check-runs?filter=all",
            "check_runs",
        )
        newer = [
            item
            for item in existing
            if item.get("name") == GATE
            and item.get("app", {}).get("slug") == "github-actions"
            and item.get("id", 0) > self.args.check
        ]
        if newer:
            self.result.update(status="fail", eligible=False)
            self.result["issues"].append(
                "A newer validation check superseded this attempt"
            )
        if self.result["status"] == "pass":
            try:
                self.context()
                if (
                    records.proposed_snapshot(self.args.policy_root, self.load_policy)[
                        2
                    ]
                    != self.baseline
                    or records.proposed_snapshot(
                        self.args.proposal_root, self.load_policy
                    )[2]
                    != self.result["proposal"]
                ):
                    raise ValueError("Record inputs changed before publication")
                instant = support.now()
                for name, member in (
                    getattr(self, "plan", {}).get("members", {}).items()
                ):
                    try:
                        candidates.verify_support(
                            member["plan"], self.config, at=instant
                        )
                    except candidates.ERRORS as error:
                        raise ValueError(f"{name}: {error}") from error
            except candidates.ERRORS as error:
                self.result.update(status="fail", eligible=False)
                self.result["issues"].append(str(error))
        success = self.result["status"] == "pass"
        binding = {
            "schemaVersion": 1,
            "number": self.args.number,
            "baseline": self.baseline["revision"],
            "head": self.args.head,
            "classification": self.result.get("classification"),
            "planDigest": self.result.get("planDigest"),
            "selections": self.result.get("selections", []),
        }
        response = api(
            f"{self.prefix}/check-runs/{self.args.check}",
            {
                "status": "completed",
                "conclusion": "success" if success else "failure",
                "output": {
                    "title": "Complete candidate evidence"
                    if self.result.get("eligible")
                    else "No candidate approval change"
                    if success
                    else "Renewed candidate validation required",
                    "summary": "Human review and merge remain required. "
                    + (
                        "; ".join(self.result["issues"])
                        or "The exact captured proposal passed its required assessment."
                    ),
                    "text": json.dumps(binding, sort_keys=True),
                },
            },
            method="PATCH",
        )
        if response.get("head_sha") != self.args.head or response.get("conclusion") != (
            "success" if success else "failure"
        ):
            raise ValueError("GitHub did not confirm the proposal-head result")
        self.result["check"] = self.args.check

    def invalidate(self):
        branch = self.authority()
        self.result.update(invalidated=[], inspected=[], baseline=self.baseline)
        pulls = pages(
            f"{self.prefix}/pulls?state=open&base={parse.quote(branch, safe='')}"
        )
        for pull in pulls:
            number, head = pull["number"], pull["head"]["sha"]
            self.result["inspected"].append(number)
            try:
                checks = pages(
                    f"{self.prefix}/commits/{head}/check-runs?filter=all", "check_runs"
                )
                for check in checks:
                    binding = re.fullmatch(
                        r"pin-pr:([1-9][0-9]*):([1-9][0-9]*):([1-9][0-9]*):([0-9a-f]{40})",
                        check.get("external_id", ""),
                    )
                    if (
                        check.get("name") != GATE
                        or check.get("app", {}).get("slug") != "github-actions"
                        or check.get("head_sha") != head
                        or binding is None
                        or int(binding[1]) != number
                        or check.get("conclusion") not in {None, "success"}
                    ):
                        continue
                    reason = None
                    if binding[4] != self.baseline["revision"]:
                        reason = (
                            "Trusted base changed; start a fresh PR validation event"
                        )
                    elif check.get("conclusion") == "success":
                        try:
                            saved = json.loads(
                                check["output"]["text"],
                                object_pairs_hook=records.unique_mapping,
                            )
                            if (
                                saved["baseline"] != self.baseline["revision"]
                                or saved["head"] != head
                                or saved["number"] != number
                                or not isinstance(saved["selections"], list)
                            ):
                                raise ValueError("Invalid saved gate identity")
                            retired = [
                                version
                                for version in saved["selections"]
                                if support.assess(version, self.config["_support"])[
                                    "status"
                                ]
                                != "supported"
                            ]
                            if retired:
                                reason = f"Selected release support changed: {', '.join(retired)}; renew affected member validation"
                        except candidates.ERRORS:
                            reason = "Saved validation identity is unavailable; renew candidate evidence"
                    if reason:
                        response = api(
                            f"{self.prefix}/check-runs/{check['id']}",
                            {
                                "status": "completed",
                                "conclusion": "failure",
                                "output": {
                                    "title": "Renewed candidate validation required",
                                    "summary": reason
                                    + ". Update/rebase the proposal or reopen the PR after a base change; rerunning an older target workflow cannot attest newer authority.",
                                },
                            },
                            method="PATCH",
                        )
                        if (
                            response.get("head_sha") != head
                            or response.get("conclusion") != "failure"
                        ):
                            raise ValueError(
                                "GitHub did not confirm stale-check invalidation"
                            )
                        self.result["invalidated"].append(
                            {
                                "number": number,
                                "head": head,
                                "check": check["id"],
                                "reason": reason,
                            }
                        )
            except candidates.ERRORS as error:
                self.result["issues"].append(f"PR #{number}: {error}")
        self.authority()
        self.result["status"] = "fail" if self.result["issues"] else "inspected"
        return self.result


def run(args, *, load_policy):
    review = None
    result = {
        "schemaVersion": 1,
        "scope": "pin-pr",
        "status": "error",
        "eligible": False,
        "approval": "not-granted",
        "issues": [],
    }
    try:
        review = Review(args, load_policy)
        result = getattr(review, args.operation)()
    except candidates.ERRORS as error:
        if review:
            result = review.result
        result["status"] = "error"
        result["eligible"] = False
        result["issues"].append(str(error))
    if review:
        if args.operation == "finish":
            try:
                review.publish()
            except candidates.ERRORS as error:
                result.update(status="error", eligible=False)
                result["issues"].append(str(error))
        candidates.write_json(review.output / "result.json", result)
    return result
