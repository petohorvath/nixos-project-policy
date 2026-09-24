"""Public candidate coordination with exact Git sources and supplied execution seams."""

import copy
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
from unittest.mock import patch

import yaml

from tests.fixtures import workflows
from tests.fixtures.candidates import CandidateFixture
from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.cli import invoke
from tests.fixtures.data import (
    LEGACY_REQUIRED_CHECKS,
    CHECKER,
    NEW_PAIR,
    PAIR,
    POLICY_REPO,
    RELEASE,
)
from tests.fixtures.services import Services
from tools import batches, candidates, policy, records, releases, support


class CandidateTests(CandidateFixture, ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.services = self.enterContext(Services(self.root).installed())
        self.output_number = 0

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
        self.commit(self.proposal)
        return self.call(
            "plan", "--project", "example", "--batch", "next", "--attempt", "fixture-1"
        )

    def test_current_release_executes_without_policy_formatting_or_lint(self):
        code, report, plan = self.plan()
        self.assertEqual(code, 0, report)
        code, report, _ = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 0, report)
        self.assertEqual(
            {item["job"]["kind"] for item in report["results"]},
            {"compliance", "tests", "compatibility"},
        )

    def test_proposal_records_must_be_regular_files_at_the_exact_commit(self):
        path = self.proposal / "policy/pins.json"
        external = self.root.parent / "outside-pins.json"
        external.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(external)
        self.assertNotEqual(self.plan()[0], 0)
        path.unlink()
        path.write_bytes(external.read_bytes())
        self.commit(self.proposal)
        code, _, plan = self.plan()
        self.assertEqual(code, 0)
        path.write_text(path.read_text() + "\n")
        self.assertNotEqual(self.worker(plan, "x86_64-linux")[0], 0)

    def test_member_execution_cannot_inherit_workflow_control_credentials(self):
        _, _, plan = self.plan()
        observed = []

        def execute(command, **kwargs):
            if command[0] == "nix" and "env" in kwargs:
                observed.append(kwargs["env"])
            return self.services.run(command, **kwargs)

        controlled = {
            name: "workflow-secret-or-command-file"
            for name in (
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "ACTIONS_RUNTIME_TOKEN",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                "GITHUB_ENV",
                "GITHUB_OUTPUT",
                "GITHUB_PATH",
                "GITHUB_STEP_SUMMARY",
                "GITHUB_STATE",
            )
        }
        with (
            patch.dict(os.environ, controlled),
            patch.object(subprocess, "run", side_effect=execute),
        ):
            self.assertEqual(self.worker(plan, "x86_64-linux")[0], 0)
        self.assertTrue(observed)
        for environment in observed:
            self.assertFalse(set(environment) & set(controlled))

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

    def test_custom_architecture_executes_and_replays_every_candidate_gate(self):
        system = "riscv64-linux"
        self.declare(required_architectures=json.dumps([system]))
        self.commit(self.root)
        self.propose()
        code, report, plan = self.plan()
        self.assertEqual(code, 0, report)
        self.assertEqual(
            report["matrix"]["include"],
            [{"system": system, "runner": ["self-hosted", system]}],
        )
        members = {"example": {"status": "planned", "plan": report}}
        self.assertEqual(
            batches.matrix(members)["include"][0]["runner"], ["self-hosted", system]
        )
        forged = copy.deepcopy(members)
        forged["example"]["plan"]["matrix"]["include"][0]["runner"] = "ubuntu-24.04"
        with self.assertRaisesRegex(ValueError, "Unsafe or conflicting"):
            batches.matrix(forged)
        code, result, output = self.worker(plan, system)
        self.assertEqual(code, 0, result)
        self.assertEqual(len(result["results"]), 4)
        code, result, _ = self.aggregate(plan, [output])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "candidate-pass")

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
            self.assertEqual(len(result["results"]), 4)
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
        self.services.failure = "tests"
        code, result, output = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 1, result)
        by_kind = {item["job"]["kind"]: item["status"] for item in result["results"]}
        self.assertEqual(by_kind["compliance"], "pass")
        self.assertEqual(by_kind["tests"], "fail")
        self.assertEqual(by_kind["compatibility"], "pass")
        self.assertEqual(records.read_json(output / "result.json"), result)

    def test_empty_committed_checks_fail_even_when_compatibility_checks_pass(self):
        _, _, plan = self.plan()
        self.services.failure = "empty-host-checks"
        code, result, output = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 1, result)
        tests = next(
            item for item in result["results"] if item["job"]["kind"] == "tests"
        )
        self.assertEqual(tests["status"], "fail")
        self.assertIn("nonempty host checks", " ".join(tests["issues"]))
        self.assertEqual(len(tests["commands"]), 1)
        self.assertTrue(
            all(
                item["status"] == "pass"
                for item in result["results"]
                if item["job"]["kind"] == "compatibility"
            )
        )
        self.assertEqual(self.aggregate(plan, [output])[0], 1)

    def test_replay_rejects_missing_committed_host_check_probe(self):
        _, _, plan = self.plan()
        code, result, output = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 0, result)
        tests = next(
            item for item in result["results"] if item["job"]["kind"] == "tests"
        )
        tests["commands"].pop(0)
        (output / "result.json").write_text(json.dumps(result))
        code, report, _ = self.aggregate(plan, [output])
        self.assertEqual(code, 1, report)
        self.assertTrue(
            any("substituted" in issue for issue in report["issues"]), report
        )

    def test_source_mutation_retains_failure_results_and_execution_logs(self):
        _, _, plan = self.plan()
        self.services.mutation = lambda command: (self.root / "flake.lock").write_text(
            "changed"
        )
        code, result, output = self.worker(plan, "x86_64-linux")
        self.assertEqual(code, 1, result)
        self.assertEqual(len(result["results"]), 4)
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

    def test_historical_workers_use_selected_checkers_and_require_committed_channels(
        self,
    ):
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
                    member["requiredChecks"] = list(LEGACY_REQUIRED_CHECKS)
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
                    self.assertIn("Historical stable coverage", str(result))
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
        workspace = self.root.parent / "workflow"
        workspace.mkdir()
        for name, root in [
            ("authority", self.baseline),
            ("proposal", self.proposal),
            ("member", self.root),
        ]:
            (workspace / name).symlink_to(root, target_is_directory=True)
        output = workspace / "github-output"
        environment = {
            **workflows.environment(workspace, adapter="candidate_adapter"),
            "PROJECT": "example",
            "BATCH": "next",
            "ATTEMPT": "workflow-1",
            "CANDIDATE_FIXTURE_ROOT": str(self.root),
        }

        def shell(job, **extra):
            return workflows.run_step(
                workflow, job, workspace, environment, match=f"pin-batch {job}", **extra
            )

        result = shell("plan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("matrix=", output.read_text())
        plan = records.read_json(workspace / "candidate-plan/plan.json")
        self.assertEqual(plan["repository"], "owner/example")
        self.assertFalse(plan["eligible"])
        results = workspace / "candidate-results"
        results.mkdir()
        for system in candidates.SYSTEMS:
            process = shell("execute", SYSTEM=system)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            (workspace / "candidate-result").rename(results / system)
        process = shell("aggregate", EXECUTION_RESULT="success")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertFalse(
            records.read_json(workspace / "candidate-summary/result.json")["eligible"]
        )
        process = shell(
            "execute",
            SYSTEM="x86_64-linux",
            CANDIDATE_FIXTURE_FAILURE="tests",
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
