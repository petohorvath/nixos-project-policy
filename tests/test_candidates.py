"""Public candidate coordination with exact Git sources and supplied execution seams."""

import base64
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import yaml

from tests.test_audits import invoke, published
from tests.test_policy import (
    CHECKER,
    NEW_PAIR,
    PAIR,
    POLICY_REPO,
    ProjectFixture,
    RELEASE,
)
from tools import candidates, policy, records, releases, support


REAL_RUN = subprocess.run
REAL_OUTPUT = subprocess.check_output


class Services:
    def __init__(self, root):
        self.root = root
        self.host = "x86_64-linux"
        self.commands = []
        self.failure = None
        self.mutation = None
        self.report_mutation = None
        self.additional = "success"

    def lookup(self, path):
        if "/git/commits/" in path:
            return {"sha": policy.git_revision(self.root)}
        if "/contents/" in path:
            requirements = records.read_json(
                policy.SOURCE_ROOT / "policy/requirements.json"
            )
            if declarations_version(self.root) == "v0.1.1":
                requirements.pop("ci")
            return {
                "encoding": "base64",
                "content": base64.b64encode(json.dumps(requirements).encode()).decode(),
            }
        if "/check-runs?" in path:
            return {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "Member / Extra",
                        "head_sha": policy.git_revision(self.root),
                        "status": "completed",
                        "conclusion": self.additional,
                    }
                ]
            }
        return published(path)

    def output(self, command, **kwargs):
        if command[0] == "nix":
            return self.host if kwargs.get("text") else self.host.encode()
        return REAL_OUTPUT(command, **kwargs)

    def run(self, command, **kwargs):
        if command[0] != "nix":
            return REAL_RUN(command, **kwargs)
        command = list(map(str, command))
        self.commands.append(command)
        if command[1] == "run":
            arguments = command[command.index("--") + 1 :]
            operation = arguments[2]
            if declarations_version(self.root) == RELEASE:
                code, report = invoke(*arguments)
            else:
                code, report = self.legacy(arguments)
            if self.report_mutation:
                self.report_mutation(operation, report)
            return subprocess.CompletedProcess(command, code, json.dumps(report), "")
        status = 0
        output = ""
        if command[1:3] == ["flake", "metadata"]:
            graph = records.read_json(self.root / "flake.lock")
            selected = policy.selected_nixpkgs(policy.LockGraph(graph))
            if "--override-input" in command:
                graph["nodes"][selected]["locked"]["rev"] = command[-1].rsplit("/", 1)[
                    1
                ]
            output = json.dumps({"locks": graph})
        elif command[1] == "eval":
            output = self.host if "--impure" in command else '["behavior"]'
        elif command[1:3] == ["flake", "check"]:
            if self.mutation:
                self.mutation(command)
            if self.failure == "tests" and "--override-input" not in command:
                status = 1
            if self.failure == "compatibility" and "--override-input" in command:
                status = 1
        elif command[1] == "fmt" and self.failure == "lint":
            status = 1
        if status and kwargs.get("check"):
            raise subprocess.CalledProcessError(status, command)
        return subprocess.CompletedProcess(
            command, status, output, "controlled failure" if status else ""
        )

    def legacy(self, arguments):
        record_root = Path(arguments[1])
        config, pins = policy.load_policy(record_root)
        version = declarations_version(self.root)
        operation = arguments[2]
        name = "example"
        member = config["projects"][name]
        if operation == "ci":
            report = {
                "status": "planned",
                "project": name,
                "policyVersion": version,
                **policy.ci_plan(member, config["ci"]),
            }
        elif operation == "check":
            report = policy.inspect_project(
                self.root,
                name,
                config,
                pins,
                arguments[arguments.index("--batch") + 1],
                project=member,
            )
        elif operation == "compatibility":
            report = policy.check_compatibility(
                self.root,
                name,
                config,
                pins,
                arguments[arguments.index("--channel") + 1],
                arguments[arguments.index("--batch") + 1],
                Path(arguments[arguments.index("--output") + 1]),
                project={
                    **member,
                    "additionalRequiredChecks": member.get(
                        "additionalRequiredChecks", []
                    ),
                },
            )
        elif operation == "vm":
            report = {"status": "pass", "targets": member["vmTargets"]}
        else:
            report = {"status": "pass", "issues": []}
        report.update(
            checkerVersion=version,
            policyRecordsRevision=policy.git_revision(record_root),
            policyRecordsDigest=records.digest(config, pins, legacy=True),
        )
        return (1 if report["status"] == "fail" else 0), report


def declarations_version(root):
    return policy.declarations.discover(root, POLICY_REPO)[3]


class CandidateTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        lock = records.read_json(self.root / "flake.lock")
        lock["nodes"]["entry"]["inputs"].pop("nixpkgs-unstable")
        lock["nodes"].pop("rolling")
        candidates.write_json(self.root / "flake.lock", lock)
        self.baseline = self.write_records()
        self.proposal = self.root.parent / "proposal"
        self.commit(self.root)
        self.commit(self.baseline)
        shutil.copytree(
            self.baseline, self.proposal, ignore=shutil.ignore_patterns(".git")
        )
        self.propose()
        self.commit(self.proposal)
        self.services = Services(self.root)
        self.output_number = 0
        for adapter in [
            patch.object(releases, "public_get", side_effect=self.services.lookup),
            patch.object(subprocess, "run", side_effect=self.services.run),
            patch.object(subprocess, "check_output", side_effect=self.services.output),
            patch.object(policy, "PACKAGED_REVISION", CHECKER, create=True),
        ]:
            adapter.start()
            self.addCleanup(adapter.stop)

    def commit(self, root):
        if not (root / ".git").exists():
            REAL_RUN(["git", "init", "-q", str(root)], check=True)
        REAL_RUN(["git", "-C", str(root), "add", "."], check=True)
        REAL_RUN(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "test: Capture candidate fixture",
            ],
            check=True,
        )

    def propose(self, *, status="candidate", approved=PAIR):
        pins = copy.deepcopy(self.pins)
        pins["approved"] = approved
        pins["batches"].append(
            {
                "id": "next",
                "status": status,
                "previous": PAIR,
                "pins": NEW_PAIR,
                "projects": {"example": policy.git_revision(self.root)},
            }
        )
        candidates.write_json(self.proposal / "policy/pins.json", pins)

    def call(self, operation, *options):
        output = self.root.parent / f"output-{self.output_number}"
        self.output_number += 1
        code, report = invoke(
            "--policy-root",
            str(self.baseline),
            "pin-batch",
            operation,
            str(self.root),
            "--proposal-root",
            str(self.proposal),
            "--output",
            str(output),
            *options,
        )
        return code, report, output

    def plan(self):
        return self.call(
            "plan", "--project", "example", "--batch", "next", "--attempt", "fixture-1"
        )

    def worker(self, plan, system):
        self.services.host = system
        return self.call(
            "execute", "--plan", str(plan / "plan.json"), "--system", system
        )

    def aggregate(self, plan, workers):
        directory = self.root.parent / f"results-{self.output_number}"
        directory.mkdir()
        for index, worker in enumerate(workers):
            shutil.copytree(worker, directory / str(index))
        return self.call(
            "aggregate", "--plan", str(plan / "plan.json"), "--results", str(directory)
        )

    def test_complete_single_member_runs_both_channels_and_all_gates_without_approval(
        self,
    ):
        before = {
            root: policy.fingerprints(root)
            for root in [self.root, self.baseline, self.proposal]
        }
        code, report, plan = self.plan()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["source"]["revision"], policy.git_revision(self.root))
        self.assertNotEqual(report["baseline"], report["proposal"])
        self.assertEqual(report["release"]["revision"], CHECKER)
        workers = []
        for system in candidates.SYSTEMS:
            code, result, output = self.worker(plan, system)
            self.assertEqual(code, 0, result)
            self.assertEqual(len(result["results"]), 5)
            compatibility = [
                item["report"]
                for item in result["results"]
                if item["job"]["kind"] == "compatibility"
            ]
            self.assertEqual(
                {item["resolvedRevision"] for item in compatibility},
                set(NEW_PAIR.values()),
            )
            self.assertTrue(
                all(item["pinStatus"] == "candidate" for item in compatibility)
            )
            workers.append(output)
        code, result, _ = self.aggregate(plan, workers)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "candidate-pass")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["scope"], "single-member")
        for root, fingerprint in before.items():
            self.assertEqual(policy.fingerprints(root), fingerprint)
        checks = [
            command
            for command in self.services.commands
            if command[1:3] == ["flake", "check"]
        ]
        self.assertTrue(
            any(
                "--no-update-lock-file" in command and "--override-input" not in command
                for command in checks
            )
        )
        released = [
            command for command in self.services.commands if command[1] == "run"
        ]
        self.assertTrue(
            all(f"github:{POLICY_REPO}/{CHECKER}" in command for command in released)
        )

    def test_proposed_approval_is_normalized_without_becoming_authority(self):
        self.propose(status="approved", approved=NEW_PAIR)
        code, report, plan = self.plan()
        self.assertEqual(code, 0, report)
        execution = records.read_json(plan / "records/policy/pins.json")
        self.assertEqual(execution["approved"], PAIR)
        self.assertEqual(execution["batches"][-1]["status"], "candidate")
        (self.proposal / "tools").mkdir()
        (self.proposal / "tools/policy.py").write_text(
            "raise RuntimeError('proposal code must never execute')"
        )
        code, report, _ = self.plan()
        self.assertEqual(code, 0, report)

    def test_proposals_cannot_change_roster_settings_support_or_policy_identity(self):
        for file, change in [
            ("members.json", lambda data: data.update(members={})),
            (
                "projects.json",
                lambda data: data.update(policyRepository="attacker/policy"),
            ),
            (
                "projects.json",
                lambda data: data["projects"]["example"].update(
                    requiredArchitectures=["x86_64-linux"]
                ),
            ),
            (
                "support.json",
                lambda data: data.update(
                    retirements={
                        "v0.3.0": {
                            "decision": "https://example.invalid/decision",
                            "reason": "changed",
                            "migrationStartsAt": "2030-01-01T00:00:00Z",
                            "retiresAt": "2030-02-01T00:00:00Z",
                        }
                    }
                ),
            ),
        ]:
            with self.subTest(file=file):
                path = self.proposal / "policy" / file
                original = path.read_text()
                data = json.loads(original)
                change(data)
                candidates.write_json(path, data)
                code, report, _ = self.plan()
                self.assertEqual(code, 2, report)
                path.write_text(original)

    def test_missing_or_wrong_registration_and_dirty_sources_fail(self):
        path = self.proposal / "policy/pins.json"
        for projects in [{}, {"example": CHECKER}]:
            self.propose()
            pins = records.read_json(path)
            pins["batches"][-1]["projects"] = projects
            candidates.write_json(path, pins)
            self.assertEqual(self.plan()[0], 2)
        self.propose()
        (self.root / "README.md").write_text("changed")
        self.assertEqual(self.plan()[0], 2)

    def test_native_host_is_checked_and_partial_evidence_cannot_complete_member(self):
        code, report, plan = self.plan()
        self.assertEqual(code, 0, report)
        code, result, worker = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 0, result)
        code, result, _ = self.aggregate(plan, [worker])
        self.assertEqual(code, 1, result)
        self.assertTrue(any("aarch64-linux" in issue for issue in result["issues"]))
        code, result, _ = self.call(
            "execute", "--plan", str(plan / "plan.json"), "--system", "aarch64-linux"
        )
        self.assertEqual(code, 2, result)
        self.assertIn("native architecture", " ".join(result["issues"]))

    def test_failure_does_not_suppress_other_independent_execution(self):
        _, _, plan = self.plan()
        self.services.failure = "lint"
        code, result, output = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 1, result)
        by_kind = {item["job"]["kind"]: item["status"] for item in result["results"]}
        self.assertEqual(by_kind["lint"], "fail")
        self.assertEqual(by_kind["tests"], "pass")
        self.assertEqual(by_kind["compatibility"], "pass")
        self.assertEqual(records.read_json(output / "result.json"), result)

    def test_source_mutation_retains_failure_results_and_execution_logs(self):
        _, _, plan = self.plan()
        self.services.mutation = lambda command: (self.root / "flake.lock").write_text(
            "changed"
        )
        code, result, output = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 1, result)
        self.assertEqual(len(result["results"]), 5)
        self.assertTrue(any(item["status"] == "fail" for item in result["results"]))
        self.assertTrue(list(output.glob("job-*/execution/stdout.log")))

    def test_changed_plan_or_retirement_deadline_invalidates_execution(self):
        _, _, plan = self.plan()
        saved = records.read_json(plan / "plan.json")
        changed = copy.deepcopy(saved)
        changed["jobs"] = changed["jobs"][:1]
        changed["planDigest"] = candidates.digest(
            {key: value for key, value in changed.items() if key != "planDigest"}
        )
        candidates.write_json(plan / "plan.json", changed)
        self.assertEqual(self.worker(plan, "x86_64-linux")[0], 2)
        candidates.write_json(plan / "plan.json", saved)
        self.support["retirements"][RELEASE] = {
            "decision": "https://example.invalid/review",
            "reason": "fixture",
            "migrationStartsAt": "2030-01-01T00:00:00Z",
            "retiresAt": "2030-02-01T00:00:00Z",
        }
        self.write_records()
        candidates.write_json(self.proposal / "policy/support.json", self.support)
        with patch.object(
            support, "now", return_value=datetime(2030, 1, 15, tzinfo=timezone.utc)
        ):
            code, result, plan = self.plan()
            self.assertEqual(code, 0, result)
        with patch.object(
            support, "now", return_value=datetime(2030, 2, 1, tzinfo=timezone.utc)
        ):
            self.assertEqual(self.worker(plan, "x86_64-linux")[0], 2)

    def test_wrong_release_reports_and_missing_compatibility_execution_fail(self):
        for field, value in [
            ("checkerVersion", "v9.0.0"),
            ("memberSettings", {}),
            ("revision", CHECKER),
            ("matrix", {"include": []}),
        ]:
            with self.subTest(field=field):
                self.services.report_mutation = lambda operation, report: (
                    report.update({field: value}) if operation == "ci" else None
                )
                self.assertEqual(self.plan()[0], 2)
        self.services.report_mutation = None
        _, _, plan = self.plan()
        self.services.report_mutation = lambda operation, report: (
            report.update(commands=[]) if operation == "compatibility" else None
        )
        code, report, _ = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 1, report)

    def test_aggregation_rejects_substituted_reports_attempts_and_missing_jobs(self):
        _, _, plan = self.plan()
        _, _, x86 = self.worker(plan, "x86_64-linux")
        _, _, arm = self.worker(plan, "aarch64-linux")
        original = records.read_json(x86 / "result.json")
        for mode in [
            "checker",
            "records",
            "source",
            "outcome",
            "attempt",
            "missing",
            "duplicate",
            "malformed",
            "no-build",
        ]:
            with self.subTest(mode=mode):
                result = copy.deepcopy(original)
                if mode in {"checker", "records", "source", "outcome"}:
                    item = result["results"][0]
                    field, value = {
                        "checker": ("checkerVersion", "v9.0.0"),
                        "records": ("policyRecordsDigest", "0" * 64),
                        "source": ("revision", CHECKER),
                        "outcome": ("status", "candidate-pass"),
                    }[mode]
                    item["report"][field] = value
                    item["commands"][-1]["stdout"] = json.dumps(item["report"])
                elif mode == "attempt":
                    result["attempt"] = "another-attempt"
                elif mode == "missing":
                    result["results"].pop()
                elif mode == "duplicate":
                    result["results"].append(result["results"][0])
                elif mode == "malformed":
                    result["results"] = [None]
                else:
                    result["results"][2]["commands"][-1]["command"].append("--no-build")
                candidates.write_json(x86 / "result.json", result)
                code, report, _ = self.aggregate(plan, [x86, arm])
                self.assertEqual(code, 1, report)
        candidates.write_json(x86 / "result.json", original)
        self.assertEqual(self.aggregate(plan, [x86, arm])[0], 0)

    def test_changed_candidate_and_member_settings_require_a_new_plan(self):
        _, _, plan = self.plan()
        path = self.proposal / "policy/pins.json"
        original = path.read_text()
        for channel in ("stable", "unstable"):
            pins = json.loads(original)
            pins["batches"][-1]["pins"][channel] = "f" * 40
            candidates.write_json(path, pins)
            code, report, _ = self.worker(plan, "x86_64-linux")
            self.assertEqual(code, 2, report)
        path.write_text(original)
        self.declare(required_architectures='["x86_64-linux"]')
        self.commit(self.root)
        self.propose()
        self.assertEqual(self.worker(plan, "x86_64-linux")[0], 2)

    def test_commit_statuses_can_supply_an_additional_gate_at_the_exact_source(self):
        self.declare(additional_required_checks='["Member / Extra"]')
        self.commit(self.root)
        self.propose()
        lookup = self.services.lookup

        def statuses(path):
            if "/check-runs?" in path:
                return {"check_runs": []}
            if "/statuses?" in path:
                self.assertIn(
                    f"repos/owner/example/commits/{policy.git_revision(self.root)}/",
                    path,
                )
                return [
                    {
                        "context": "Member / Extra",
                        "state": "success",
                        "target_url": "https://example.invalid/evidence",
                    }
                ]
            return lookup(path)

        with patch.object(releases, "public_get", side_effect=statuses):
            _, _, plan = self.plan()
            code, report, _ = self.worker(plan, "x86_64-linux")
            self.assertEqual(code, 0, report)
            gate = next(
                item
                for item in report["results"]
                if item["job"]["kind"] == "additional"
            )
            self.assertEqual(gate["evidence"]["status"]["state"], "success")

    def test_unavailable_enrolled_commit_and_mutable_release_are_rejected(self):
        lookup = self.services.lookup
        for failure in ("source", "release"):

            def unavailable(path):
                if failure == "source" and "/git/commits/" in path:
                    return {"sha": CHECKER}
                result = lookup(path)
                if failure == "release" and "/releases/tags/" in path:
                    result["immutable"] = False
                return result

            with (
                self.subTest(failure=failure),
                patch.object(releases, "public_get", side_effect=unavailable),
            ):
                self.assertEqual(self.plan()[0], 2)

    def test_additional_checks_and_vm_are_required_on_their_own_hosts(self):
        self.declare(
            required_architectures='["aarch64-linux"]',
            vm_targets='["vm-test"]',
            additional_required_checks='["Member / Extra"]',
        )
        self.commit(self.root)
        self.propose()
        code, report, plan = self.plan()
        self.assertEqual(code, 0, report)
        self.assertEqual(
            {job["system"] for job in report["jobs"] if job["kind"] == "vm"},
            {"x86_64-linux"},
        )
        _, _, arm = self.worker(plan, "aarch64-linux")
        _, _, x86 = self.worker(plan, "x86_64-linux")
        self.assertEqual(self.aggregate(plan, [arm, x86])[0], 0)
        self.services.additional = "neutral"
        self.assertEqual(self.aggregate(plan, [arm, x86])[0], 1)

    def test_legacy_contracts_use_the_selected_checker_and_keep_pin_bound_scopes(self):
        for version in ["v0.1.1", "v0.2.0", "v0.3.0"]:
            with self.subTest(version=version):
                caller = self.workflow["jobs"]["policy"]
                caller["uses"] = f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
                caller["with"] = {"project": "example", "policy_version": version}
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                for file in ["CONTRIBUTING.md", "AGENTS.md"]:
                    self.write(
                        file,
                        f"[Rules](https://github.com/{POLICY_REPO}/blob/{version}/POLICY.md)\n",
                    )
                member = self.config["projects"]["example"]
                member["policyVersion"] = version
                if version == "v0.1.1":
                    member["requiredChecks"] = [
                        "Policy / Policy records",
                        "Policy / Policy (x86_64-linux)",
                        "Policy / Policy (aarch64-linux)",
                    ]
                else:
                    member["requiredChecks"] = policy.ci_plan(
                        member, self.config["ci"]
                    )["requiredChecks"]
                self.write_records()
                candidates.write_json(
                    self.proposal / "policy/projects.json",
                    records.read_json(self.baseline / "policy/projects.json"),
                )
                self.commit(self.root)
                self.propose()
                code, report, plan = self.plan()
                self.assertEqual(code, 0, report)
                code, result, _ = self.worker(plan, "x86_64-linux")
                if version == "v0.1.1":
                    self.assertEqual(code, 1, result)
                    self.assertTrue(
                        any(
                            "allowed pin pair" in str(item)
                            for item in result["results"]
                        )
                    )
                    lock = records.read_json(self.root / "flake.lock")
                    lock["nodes"]["arbitrary-node"]["locked"]["rev"] = NEW_PAIR[
                        "stable"
                    ]
                    candidates.write_json(self.root / "flake.lock", lock)
                    self.commit(self.root)
                    self.propose()
                    _, _, plan = self.plan()
                    code, missing, _ = self.worker(plan, "x86_64-linux")
                    self.assertEqual(code, 1, missing)
                    self.assertIn("Historical unstable coverage", str(missing))
                    lock["nodes"]["rolling"] = copy.deepcopy(
                        lock["nodes"]["arbitrary-node"]
                    )
                    lock["nodes"]["rolling"]["locked"]["rev"] = NEW_PAIR["unstable"]
                    lock["nodes"]["rolling"]["original"]["ref"] = "nixos-unstable"
                    lock["nodes"]["entry"]["inputs"]["nixpkgs-unstable"] = "rolling"
                    candidates.write_json(self.root / "flake.lock", lock)
                    self.commit(self.root)
                    self.propose()
                    _, _, plan = self.plan()
                    code, result, _ = self.worker(plan, "x86_64-linux")
                    self.assertEqual(code, 0, result)
                else:
                    self.assertEqual(code, 0, result)

    def test_manual_workflow_invokes_the_same_plan_and_worker_interface(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/pin-candidate.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["jobs"]["execute"]["strategy"]["fail-fast"], "false")
        step = next(
            step
            for step in workflow["jobs"]["plan"]["steps"]
            if step.get("id") == "plan"
        )
        workspace = self.root.parent / "workflow"
        workspace.mkdir()
        for name, root in [
            ("authority", self.baseline),
            ("proposal", self.proposal),
            ("member", self.root),
        ]:
            (workspace / name).symlink_to(root, target_is_directory=True)
        binary = workspace / "bin"
        binary.mkdir()
        stub = binary / "nix"
        stub.write_text(
            f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom tests.test_candidates import workflow_adapter\nworkflow_adapter()\n"
        )
        stub.chmod(0o755)
        output = workspace / "github-output"
        environment = {
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(workspace),
            "GITHUB_OUTPUT": str(output),
            "PROJECT": "example",
            "BATCH": "next",
            "ATTEMPT": "workflow-1",
            "CANDIDATE_FIXTURE_ROOT": str(self.root),
        }
        result = REAL_RUN(
            ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=workspace,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("matrix=", output.read_text())
        plan = records.read_json(workspace / "candidate-plan/plan.json")
        self.assertEqual(plan["repository"], "owner/example")
        self.assertFalse(plan["eligible"])
        execute = next(
            step
            for step in workflow["jobs"]["execute"]["steps"]
            if "pin-batch execute" in step.get("run", "")
        )
        results = workspace / "candidate-results"
        results.mkdir()
        for system in candidates.SYSTEMS:
            process = REAL_RUN(
                ["bash", "-e", "-o", "pipefail", "-c", execute["run"]],
                cwd=workspace,
                env={**environment, "SYSTEM": system},
                text=True,
                capture_output=True,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            (workspace / "candidate-result").rename(results / system)
        aggregate = next(
            step
            for step in workflow["jobs"]["aggregate"]["steps"]
            if "pin-batch aggregate" in step.get("run", "")
        )
        process = REAL_RUN(
            ["bash", "-e", "-o", "pipefail", "-c", aggregate["run"]],
            cwd=workspace,
            env={**environment, "EXECUTION_RESULT": "success"},
            text=True,
            capture_output=True,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertFalse(
            records.read_json(workspace / "candidate-summary/result.json")["eligible"]
        )
        process = REAL_RUN(
            ["bash", "-e", "-o", "pipefail", "-c", execute["run"]],
            cwd=workspace,
            env={
                **environment,
                "SYSTEM": "x86_64-linux",
                "CANDIDATE_FIXTURE_FAILURE": "lint",
            },
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(
            records.read_json(workspace / "candidate-result/result.json")["status"],
            "fail",
        )
        for job in ("plan", "execute", "aggregate"):
            upload = next(
                step
                for step in workflow["jobs"][job]["steps"]
                if "upload-artifact" in step.get("uses", "")
            )
            self.assertEqual(upload["if"], "always()")


def workflow_adapter():
    services = Services(Path(os.environ["CANDIDATE_FIXTURE_ROOT"]))
    services.host = os.environ.get("SYSTEM", "x86_64-linux")
    services.failure = os.environ.get("CANDIDATE_FIXTURE_FAILURE")
    with (
        patch.object(releases, "public_get", side_effect=services.lookup),
        patch.object(subprocess, "run", side_effect=services.run),
        patch.object(subprocess, "check_output", side_effect=services.output),
        patch.object(policy, "PACKAGED_REVISION", CHECKER, create=True),
    ):
        sys.exit(policy.main(sys.argv[sys.argv.index("--") + 1 :]))
