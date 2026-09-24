"""Trusted proposal-head reporting through the public CLI and GitHub transport."""

import copy
import hashlib
import io
import json
import shutil
import sys
import zipfile
import unittest
from unittest.mock import patch

import yaml

from tests.fixtures import workflows
from tests.fixtures.candidates import CandidateFixture, invalid_batch_plans
from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.cli import invoke
from tests.fixtures.data import (
    RELEASE,
    BEFORE,
    EFFECTIVE,
    NEW_PAIR,
    PAIR,
    POLICY_REPO,
    retirement,
)
from tests.fixtures.github import GitHub
from tests.fixtures.services import Services
from tools import candidates, policy, records, support


class PinPRTests(CandidateFixture, ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.propose(status="complete", approved=NEW_PAIR)
        self.commit(self.proposal)
        self.head = policy.git_revision(self.proposal)
        self.base = policy.git_revision(self.baseline)
        self.workflow_revision = self.base
        self.hosted = GitHub()
        self.hosted.pin_pr(POLICY_REPO, self.base, self.head)
        self.number = 0

    def evidence(self):
        workspace = self.root.parent / "members"
        workspace.mkdir()
        (workspace / "example").symlink_to(self.root, target_is_directory=True)
        services = Services(self.root)

        common = ["--policy-root", str(self.baseline), "pin-batch"]
        with services.installed(coordinator=self.base):
            plan_dir = self.root.parent / "plan"
            code, plan = invoke(
                *common,
                "plan",
                str(workspace),
                "--all",
                "--proposal-root",
                str(self.proposal),
                "--batch",
                "next",
                "--attempt",
                "91:2",
                "--output",
                str(plan_dir),
            )
            self.assertEqual(code, 0, plan)
            workers = self.root.parent / "workers"
            workers.mkdir()
            for row in plan["matrix"]["include"]:
                services.host = row["system"]
                output = workers / row["worker"]
                code, result = invoke(
                    *common,
                    "execute",
                    str(self.root),
                    "--all",
                    "--proposal-root",
                    str(self.proposal),
                    "--plan",
                    str(plan_dir / "plan.json"),
                    "--project",
                    "example",
                    "--system",
                    row["system"],
                    "--attempt",
                    "91:2",
                    "--output",
                    str(output),
                )
                self.assertEqual(code, 0, result)
            summary = self.root.parent / "summary"
            outcomes = self.root.parent / "worker-outcomes.json"
            candidates.write_json(
                outcomes,
                {
                    "schemaVersion": 1,
                    "planDigest": plan["planDigest"],
                    "attempt": "91:2",
                    "workers": [
                        {
                            "id": row["worker"],
                            "status": "completed",
                            "conclusion": "success",
                        }
                        for row in plan["matrix"]["include"]
                    ],
                },
            )
            code, result = invoke(
                *common,
                "aggregate",
                str(workspace),
                "--all",
                "--proposal-root",
                str(self.proposal),
                "--plan",
                str(plan_dir / "plan.json"),
                "--results",
                str(workers),
                "--attempt",
                "91:2",
                "--worker-outcomes",
                str(outcomes),
                "--output",
                str(summary),
            )
            self.assertEqual(code, 0, result)
        for name, directory in [
            ("pin-plan-91-2", plan_dir),
            ("pin-summary-91-2", summary),
            *[
                (f"pin-result-91-2-{row['worker']}", workers / row["worker"])
                for row in plan["matrix"]["include"]
            ],
        ]:
            self.hosted.artifact(POLICY_REPO, self.head, name, directory)
        jobs = [
            {
                "id": 1,
                "name": "Capture pin proposal",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 2,
                "name": "Plan pin candidate",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 3,
                "name": "Aggregate pin candidate",
                "status": "completed",
                "conclusion": "success",
            },
        ]
        jobs += [
            {
                "id": 4 + index,
                "name": row["job"],
                "status": "completed",
                "conclusion": "success",
                "labels": [row["runner"]],
            }
            for index, row in enumerate(plan["matrix"]["include"])
        ]
        self.hosted.responses[
            f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"
        ] = {"jobs": jobs}
        return plan

    def call(self, operation, *options):
        output = self.root.parent / f"pr-output-{self.number}"
        self.number += 1
        arguments = [
            "--policy-root",
            str(self.baseline),
            "pin-pr",
            operation,
            "--output",
            str(output),
        ]
        if operation != "invalidate":
            arguments += [
                "--proposal-root",
                str(self.proposal),
                "--number",
                "7",
                "--head",
                self.head,
                "--run",
                "91",
                "--attempt",
                "2",
            ]
        with (
            patch("urllib.request.urlopen", side_effect=self.hosted.transport),
            patch.dict(
                "os.environ",
                {
                    "GH_TOKEN": "fixture-read-or-report-token",
                    "GITHUB_WORKFLOW_SHA": self.workflow_revision,
                },
            ),
        ):
            code, report = invoke(*arguments, *options)
        return code, report, output

    def test_capture_uses_exact_proposal_data_and_starts_an_unapproved_head_gate(self):
        (self.proposal / "tools").mkdir()
        (self.proposal / "tools/policy.py").write_text(
            'raise AssertionError("proposal code executed")\n'
        )
        self.commit(self.proposal)
        self.head = policy.git_revision(self.proposal)
        self.hosted.pin_pr(POLICY_REPO, self.base, self.head)
        code, report, _ = self.call("capture")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["workflowHead"], self.base)
        self.assertNotEqual(report["workflowHead"], report["head"])
        self.assertEqual(report["classification"], "candidate")
        self.assertEqual(report["batch"], "next")
        self.assertFalse(report["eligible"])
        self.assertEqual(self.hosted.checks[0]["head_sha"], self.head)
        self.assertEqual(self.hosted.checks[0]["status"], "in_progress")
        self.assertEqual(
            self.hosted.checks[0]["name"], "Pin batch / Complete candidate"
        )
        self.assertEqual(
            records.read_json(self.baseline / "policy/pins.json")["approved"], PAIR
        )

    def test_candidate_classification_rejects_changed_authority_and_invalid_batches(
        self,
    ):
        for file, keys, value in (
            ("members.json", ("members",), {}),
            ("config.json", ("policyRepository",), "attacker/policy"),
            (
                "config.json",
                ("stableBranch",),
                "nixos-unstable",
            ),
            ("support.json", ("retirements",), {RELEASE: retirement()}),
            ("pins.json", ("batches", -1, "id"), "../next"),
            ("pins.json", ("batches", -1, "status"), "withdrawn"),
        ):
            with self.subTest(file=file, keys=keys):
                path = self.proposal / "policy" / file
                original = path.read_text()
                data = json.loads(original)
                target = data
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                candidates.write_json(path, data)
                self.commit(self.proposal)
                self.head = policy.git_revision(self.proposal)
                self.hosted.pin_pr(POLICY_REPO, self.base, self.head)
                code, result, _ = self.call("capture")
                self.assertEqual(code, 2, result)
                self.assertFalse(result["eligible"])
                path.write_text(original)

    def test_collect_requires_exact_attempt_jobs_and_downloads_bound_complete_evidence(
        self,
    ):
        self.call("capture")
        plan = self.evidence()
        code, report, output = self.call("collect")
        self.assertEqual(code, 0, report)
        manifest = records.read_json(output / "worker-outcomes.json")
        self.assertEqual(manifest["attempt"], "91:2")
        self.assertEqual(manifest["planDigest"], plan["planDigest"])
        self.assertEqual(len(list((output / "workers").glob("*/result.json"))), 2)
        jobs = self.hosted.responses[
            f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"
        ]["jobs"]
        jobs[-1]["conclusion"] = "cancelled"
        code, report, output = self.call("collect")
        self.assertNotEqual(code, 0, report)
        self.assertIn("example", str(report["issues"]))
        self.assertTrue((output / "workers").exists())

    def test_collect_and_finish_reject_forged_plans_with_recomputed_digests(self):
        _, captured, _ = self.call("capture")
        plan = self.evidence()
        for name, value in invalid_batch_plans(plan):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("plan.json", json.dumps(value))
            data = stream.getvalue()
            self.hosted.responses[f"repos/{POLICY_REPO}/actions/artifacts/1/zip"] = data
            self.hosted.responses[f"repos/{POLICY_REPO}/actions/runs/91/artifacts"][
                "artifacts"
            ][0]["digest"] = f"sha256:{hashlib.sha256(data).hexdigest()}"
            with self.subTest(change=name, operation="collect"):
                code, report, _ = self.call("collect")
                self.assertNotEqual(code, 0, report)
                self.assertFalse(report["eligible"])
            with self.subTest(change=name, operation="finish"):
                code, report, _ = self.call("finish", "--check", str(captured["check"]))
                self.assertNotEqual(code, 0, report)
                self.assertEqual(self.hosted.checks[0]["conclusion"], "failure")

    def test_finish_reports_only_complete_current_evidence_on_the_proposal_head(self):
        _, captured, _ = self.call("capture")
        self.evidence()
        code, report, _ = self.call("finish", "--check", str(captured["check"]))
        self.assertEqual(code, 0, report)
        self.assertTrue(report["eligible"])
        self.assertEqual(self.hosted.checks[0]["conclusion"], "success")
        self.assertEqual(self.hosted.checks[0]["head_sha"], self.head)
        self.hosted.responses[f"repos/{POLICY_REPO}/pulls/7"]["head"]["sha"] = "f" * 40
        code, report, _ = self.call("finish", "--check", str(captured["check"]))
        self.assertNotEqual(code, 0, report)
        self.assertEqual(self.hosted.checks[0]["conclusion"], "failure")
        self.assertIn("head", str(report["issues"]))

    def test_old_target_workflow_cannot_attest_newer_baseline_on_rerun(self):
        (self.baseline / "reviewed.txt").write_text("New trusted baseline\n")
        self.commit(self.baseline)
        self.base = policy.git_revision(self.baseline)
        self.hosted.pin_pr(POLICY_REPO, self.base, self.head)
        code, report, _ = self.call("capture")
        self.assertNotEqual(code, 0, report)
        self.assertIn("workflow revision", str(report["issues"]))
        self.assertFalse(self.hosted.checks)

    def test_missing_workflow_revision_cannot_establish_trusted_authority(self):
        self.workflow_revision = ""
        code, report, _ = self.call("capture")
        self.assertNotEqual(code, 0, report)
        self.assertIn("workflow revision", str(report["issues"]))
        self.assertFalse(self.hosted.checks)

    def test_documentation_pr_accepts_distinct_proposal_and_workflow_commits(self):
        shutil.copyfile(
            self.baseline / "policy/pins.json", self.proposal / "policy/pins.json"
        )
        (self.proposal / "README.md").write_text("# Shorter documentation\n")
        self.commit(self.proposal)
        self.head = policy.git_revision(self.proposal)
        self.hosted.pin_pr(POLICY_REPO, self.base, self.head)
        code, captured, _ = self.call("capture")
        self.assertEqual(code, 0, captured)
        self.assertEqual(captured["classification"], "not-applicable")
        self.assertEqual(captured["workflowHead"], self.base)
        self.hosted.responses[
            f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"
        ] = {
            "jobs": [
                {
                    "id": 1,
                    "name": "Capture pin proposal",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
        self.hosted.responses[f"repos/{POLICY_REPO}/actions/runs/91/artifacts"] = {
            "artifacts": []
        }
        code, report, _ = self.call("finish", "--check", str(captured["check"]))
        self.assertEqual(code, 0, report)
        self.assertFalse(report["eligible"])
        self.assertEqual(self.hosted.checks[0]["head_sha"], self.head)
        self.assertEqual(self.hosted.checks[0]["conclusion"], "success")

    def test_hosted_failures_and_unavailable_artifacts_never_publish_success(self):
        _, captured, _ = self.call("capture")
        self.evidence()
        original = copy.deepcopy(self.hosted.responses)
        job_path = f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"
        artifact_path = f"repos/{POLICY_REPO}/actions/runs/91/artifacts"
        mutations = {
            "missing artifact": lambda: self.hosted.responses[artifact_path][
                "artifacts"
            ].pop(),
            "expired artifact": lambda: self.hosted.responses[artifact_path][
                "artifacts"
            ][-1].update(expired=True),
            "wrong run": lambda: self.hosted.responses[artifact_path]["artifacts"][-1][
                "workflow_run"
            ].update(id=92),
            "wrong artifact head": lambda: self.hosted.responses[artifact_path][
                "artifacts"
            ][-1]["workflow_run"].update(head_sha=self.base),
            "wrong proposal head": lambda: self.hosted.responses[
                f"repos/{POLICY_REPO}/actions/runs/91"
            ].update(head_sha="f" * 40),
            "wrong attempt": lambda: self.hosted.responses[
                f"repos/{POLICY_REPO}/actions/runs/91"
            ].update(run_attempt=3),
            "manual workflow": lambda: self.hosted.responses[
                f"repos/{POLICY_REPO}/actions/runs/91"
            ].update(event="workflow_dispatch"),
            "substituted workflow": lambda: self.hosted.responses[
                f"repos/{POLICY_REPO}/actions/runs/91"
            ].update(path=".github/workflows/untrusted.yml"),
            "skipped worker": lambda: self.hosted.responses[job_path]["jobs"][
                -1
            ].update(conclusion="skipped"),
            "missing worker": lambda: self.hosted.responses[job_path]["jobs"].pop(),
            "wrong native runner": lambda: self.hosted.responses[job_path]["jobs"][
                -1
            ].update(labels=["ubuntu-24.04"]),
            "failed aggregate upload": lambda: self.hosted.responses[job_path]["jobs"][
                2
            ].update(conclusion="failure"),
            "duplicate job": lambda: self.hosted.responses[job_path]["jobs"].append(
                self.hosted.responses[job_path]["jobs"][-1]
            ),
            "duplicate artifact": lambda: self.hosted.responses[artifact_path][
                "artifacts"
            ].append(self.hosted.responses[artifact_path]["artifacts"][-1]),
        }
        for name, mutate in mutations.items():
            self.hosted.responses = copy.deepcopy(original)
            mutate()
            with self.subTest(change=name):
                code, result, _ = self.call("finish", "--check", str(captured["check"]))
                self.assertNotEqual(code, 0, result)
                self.assertEqual(self.hosted.checks[0]["conclusion"], "failure")

    def test_artifact_archives_cannot_escape_or_redirect_evidence_extraction(self):
        self.call("capture")
        self.evidence()

        for mode in ("escape", "symlink", "invalid-json", "wrong-digest"):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                if mode == "escape":
                    archive.writestr("../outside", "untrusted")
                elif mode == "symlink":
                    info = zipfile.ZipInfo("plan.json")
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                    archive.writestr(info, "../../outside")
                else:
                    archive.writestr("plan.json", "not json")
            data = stream.getvalue()
            self.hosted.responses[f"repos/{POLICY_REPO}/actions/artifacts/1/zip"] = data
            self.hosted.responses[f"repos/{POLICY_REPO}/actions/runs/91/artifacts"][
                "artifacts"
            ][0]["digest"] = "sha256:" + (
                "0" * 64 if mode == "wrong-digest" else hashlib.sha256(data).hexdigest()
            )
            with self.subTest(mode=mode):
                code, result, output = self.call("collect")
                self.assertNotEqual(code, 0, result)
                self.assertFalse((output / "outside").exists())

    def test_state_only_recovery_and_non_pin_changes_do_not_require_a_new_batch(self):
        self.hosted.responses[
            f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"
        ] = {
            "jobs": [
                {
                    "id": 1,
                    "name": "Capture pin proposal",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
        self.hosted.responses[f"repos/{POLICY_REPO}/actions/runs/91/artifacts"] = {
            "artifacts": []
        }
        pins = records.read_json(self.proposal / "policy/pins.json")
        pins["batches"][-1]["status"] = "approved"
        candidates.write_json(self.baseline / "policy/pins.json", pins)
        self.commit(self.baseline)
        self.base = policy.git_revision(self.baseline)
        self.workflow_revision = self.base
        for state in ("approved", "paused", "rolling", "complete", "withdrawn"):
            pins["batches"][-1]["status"] = state
            candidates.write_json(self.proposal / "policy/pins.json", pins)
            self.commit(self.proposal)
            self.head = policy.git_revision(self.proposal)
            self.hosted.pin_pr(POLICY_REPO, self.base, self.head)
            self.hosted.checks = []
            with self.subTest(state=state):
                code, result, _ = self.call("capture")
                self.assertEqual(code, 0, result)
                self.assertIn(
                    result["classification"], {"state-only", "not-applicable"}
                )
                code, result, _ = self.call("finish", "--check", "1")
                self.assertEqual(code, 0, result)
                self.assertFalse(result["eligible"])
                self.assertEqual(self.hosted.checks[0]["conclusion"], "success")
                code, candidate = invoke(
                    "--policy-root",
                    str(self.baseline),
                    "pin-batch",
                    "plan",
                    str(self.root),
                    "--proposal-root",
                    str(self.proposal),
                    "--project",
                    "example",
                    "--batch",
                    "next",
                    "--output",
                    str(self.root.parent / f"recovery-{state}"),
                )
                self.assertEqual(code, 2, candidate)
                self.assertFalse(candidate["eligible"])

    def test_maintenance_invalidates_open_success_after_base_changes(self):
        _, captured, _ = self.call("capture")
        self.evidence()
        self.assertEqual(self.call("finish", "--check", str(captured["check"]))[0], 0)
        self.hosted.responses[f"repos/{POLICY_REPO}/pulls"] = [
            self.hosted.responses[f"repos/{POLICY_REPO}/pulls/7"]
        ]
        (self.baseline / "reviewed.txt").write_text("New trusted baseline\n")
        self.commit(self.baseline)
        current = policy.git_revision(self.baseline)
        self.hosted.responses[f"repos/{POLICY_REPO}/git/ref/heads/main"]["object"][
            "sha"
        ] = current
        code, result, _ = self.call("invalidate")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.hosted.checks[0]["conclusion"], "failure")
        self.assertEqual(result["invalidated"][0]["number"], 7)
        self.assertIn("base", result["invalidated"][0]["reason"])

    def test_retirement_reassessed_after_artifact_reads_and_by_maintenance(self):
        self.support["retirements"]["v0.4.0"] = retirement()
        self.write_records()
        self.commit(self.baseline)
        shutil.copyfile(
            self.baseline / "policy/support.json", self.proposal / "policy/support.json"
        )
        self.commit(self.proposal)
        self.base, self.head = (
            policy.git_revision(self.baseline),
            policy.git_revision(self.proposal),
        )
        self.workflow_revision = self.base
        self.hosted.pin_pr(POLICY_REPO, self.base, self.head)
        with patch.object(support, "now", return_value=support.timestamp(BEFORE)):
            _, captured, _ = self.call("capture")
            self.evidence()
            self.assertEqual(
                self.call("finish", "--check", str(captured["check"]))[0], 0
            )
        self.hosted.responses[f"repos/{POLICY_REPO}/pulls"] = [
            self.hosted.responses[f"repos/{POLICY_REPO}/pulls/7"]
        ]
        with patch.object(support, "now", return_value=support.timestamp(EFFECTIVE)):
            code, result, _ = self.call("invalidate")
            self.assertEqual(code, 0, result)
            self.assertEqual(self.hosted.checks[0]["conclusion"], "failure")
            code, result, _ = self.call("finish", "--check", str(captured["check"]))
            self.assertNotEqual(code, 0, result)
            self.assertIn("example", str(result["issues"]))

    def test_real_pr_workflow_shell_requires_complete_evidence_and_preserves_failures(
        self,
    ):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/pin-pr.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        workspace = self.root.parent / "workflow"
        workspace.mkdir()
        (workspace / "authority").symlink_to(self.baseline, target_is_directory=True)
        (workspace / "proposal").symlink_to(self.proposal, target_is_directory=True)
        (workspace / "members").mkdir()
        (workspace / "members/example").symlink_to(self.root, target_is_directory=True)
        (workspace / "member").symlink_to(self.root, target_is_directory=True)
        state = workspace / "github.json"
        environment = {
            **workflows.environment(workspace, adapter="pr_adapter"),
            "GH_TOKEN": "fixture",
            "GITHUB_WORKFLOW_SHA": self.workflow_revision,
            "PR_NUMBER": "7",
            "PROPOSAL_HEAD": self.head,
            "RUN_ID": "91",
            "RUN_ATTEMPT": "2",
            "ATTEMPT": "91:2",
            "BATCH": "next",
            "PR_FIXTURE_ROOT": str(self.root),
            "PR_FIXTURE_STATE": str(state),
            "PR_FIXTURE_BASE": self.base,
        }

        def shell(job, **extra):
            self.hosted.save(state)
            process = workflows.run_step(workflow, job, workspace, environment, **extra)
            self.hosted.checks = GitHub.load(state).checks
            return process

        process = shell("capture")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn(
            "classification=candidate", (workspace / "github-output").read_text()
        )
        process = shell("plan")
        self.assertEqual(process.returncode, 0, process.stderr)
        plan = records.read_json(workspace / "pin-plan/plan.json")
        self.hosted.artifact(
            POLICY_REPO, self.head, "pin-plan-91-2", workspace / "pin-plan"
        )
        jobs = [
            {
                "id": index + 1,
                "name": name,
                "status": "completed",
                "conclusion": "success",
            }
            for index, name in enumerate(
                (
                    "Capture pin proposal",
                    "Plan pin candidate",
                    "Aggregate pin candidate",
                )
            )
        ]
        for index, row in enumerate(plan["matrix"]["include"]):
            process = shell("execute", PROJECT=row["project"], SYSTEM=row["system"])
            self.assertEqual(process.returncode, 0, process.stderr)
            self.hosted.artifact(
                POLICY_REPO,
                self.head,
                f"pin-result-91-2-{row['worker']}",
                workspace / "pin-result",
            )
            (workspace / "pin-result").rename(workspace / row["worker"])
            jobs.append(
                {
                    "id": index + 4,
                    "name": row["job"],
                    "status": "completed",
                    "conclusion": "success",
                    "labels": [row["runner"]],
                }
            )
        self.hosted.responses[
            f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"
        ] = {"jobs": jobs}
        process = shell("aggregate")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.hosted.artifact(
            POLICY_REPO, self.head, "pin-summary-91-2", workspace / "pin-summary"
        )
        process = shell("finish", CHECK_ID="1")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.hosted.checks[0]["conclusion"], "success")
        # Existing success files cannot conceal a failed native job or its upload.
        jobs[-1]["conclusion"] = "failure"
        for directory in ("pin-provenance", "pin-summary", "pin-report"):
            (workspace / directory).rename(workspace / f"earlier-{directory}")
        process = shell("aggregate")
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertFalse(
            records.read_json(workspace / "pin-summary/result.json")["eligible"]
        )
        jobs[2]["conclusion"] = "failure"
        process = shell("finish", CHECK_ID="1")
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.hosted.checks[0]["conclusion"], "failure")
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(set(workflow["on"]), {"pull_request_target"})
        self.assertEqual(workflow["jobs"]["finish"]["if"], "always()")
        self.assertEqual(workflow["jobs"]["execute"]["strategy"]["fail-fast"], "false")
        for name, job in workflow["jobs"].items():
            if name not in {"capture", "finish"}:
                self.assertNotIn("write", job.get("permissions", {}).values())
            for step in job["steps"]:
                if "checkout@" in step.get("uses", ""):
                    self.assertEqual(step["with"]["persist-credentials"], "false")
                if "upload-artifact@" in step.get("uses", ""):
                    self.assertEqual(step["if"], "always()")

    def test_maintenance_shell_prepares_exact_unapproved_pairs_and_invalidates(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/maintenance.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        workspace = self.root.parent / "maintenance"
        workspace.mkdir()
        environment = workflows.environment(workspace, adapter="pr_adapter")
        binary = workspace / "bin"
        git = binary / "git"
        queries = workspace / "queries"
        git.write_text(
            f"#!{sys.executable}\nimport os, sys\nif sys.argv[1] == 'ls-remote':\n with open({str(queries)!r}, 'a') as stream: stream.write(sys.argv[-1] + '\\n')\n print(({'d' * 40!r} if sys.argv[-1].endswith('nixos-unstable') else {'c' * 40!r}) + '\\t' + sys.argv[-1])\nelse: os.execv({shutil.which('git')!r}, [{shutil.which('git')!r}, *sys.argv[1:]])\n"
        )
        git.chmod(0o755)
        state = workspace / "github.json"
        self.hosted.responses[f"repos/{POLICY_REPO}/pulls"] = [
            self.hosted.responses[f"repos/{POLICY_REPO}/pulls/7"]
        ]
        self.hosted.checks = [
            {
                "id": 1,
                "name": "Pin batch / Complete candidate",
                "app": {"slug": "github-actions"},
                "head_sha": self.head,
                "external_id": "pin-pr:7:90:1:" + "f" * 40,
                "status": "completed",
                "conclusion": "success",
            }
        ]
        self.hosted.save(state)
        environment = {
            **environment,
            "GH_TOKEN": "fixture",
            "PR_FIXTURE_ROOT": str(self.root),
            "PR_FIXTURE_STATE": str(state),
            "PR_FIXTURE_BASE": self.base,
            "STABLE_REVISION": "",
            "UNSTABLE_REVISION": "",
        }

        def shell(job, **extra):
            return workflows.run_step(
                workflow, job, self.baseline, environment, **extra
            )

        process = shell("candidate")
        self.assertEqual(process.returncode, 0, process.stderr)
        proposal = records.read_json(self.baseline / "candidate.json")
        self.assertEqual(proposal["status"], "proposal")
        self.assertEqual(proposal["pins"], NEW_PAIR)
        self.assertEqual(len(queries.read_text().splitlines()), 2)
        process = shell(
            "candidate",
            STABLE_REVISION=PAIR["stable"],
            UNSTABLE_REVISION=PAIR["unstable"],
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(
            records.read_json(self.baseline / "candidate.json")["pins"], PAIR
        )
        self.assertEqual(len(queries.read_text().splitlines()), 2)
        self.assertNotEqual(
            shell("candidate", STABLE_REVISION=PAIR["stable"]).returncode, 0
        )
        process = shell("invalidate")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(records.read_json(state)["checks"][0]["conclusion"], "failure")
        self.assertEqual(workflow["on"]["push"]["branches"], ["main"])


if __name__ == "__main__":
    unittest.main()
