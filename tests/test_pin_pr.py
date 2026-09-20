"""Trusted proposal-head reporting through the public CLI and GitHub transport."""

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from tests.test_audits import invoke
from tests import test_candidates as fixture
from tests.test_policy import CHECKER, NEW_PAIR, PAIR, POLICY_REPO, ProjectFixture
from tools import candidates, policy, records, releases, support
from tests.test_support import BEFORE, EFFECTIVE, retirement


class PinPRTests(ProjectFixture):
    commit = fixture.CandidateTests.commit

    def setUp(self):
        super().setUp()
        lock = records.read_json(self.root / "flake.lock")
        lock["nodes"]["entry"]["inputs"].pop("nixpkgs-unstable")
        lock["nodes"].pop("rolling")
        candidates.write_json(self.root / "flake.lock", lock)
        self.commit(self.root)
        self.baseline = self.write_records()
        self.commit(self.baseline)
        self.proposal = self.root.parent / "proposal"
        shutil.copytree(
            self.baseline, self.proposal, ignore=shutil.ignore_patterns(".git")
        )
        pins = copy.deepcopy(self.pins)
        pins["approved"] = NEW_PAIR
        pins["batches"].append(
            {
                "id": "next",
                "status": "complete",
                "pins": NEW_PAIR,
                "previous": PAIR,
                "projects": {"example": policy.git_revision(self.root)},
            }
        )
        candidates.write_json(self.proposal / "policy/pins.json", pins)
        self.commit(self.proposal)
        self.head = policy.git_revision(self.proposal)
        self.base = policy.git_revision(self.baseline)
        self.requests = []
        self.checks = []
        self.github = {
            f"repos/{POLICY_REPO}": {
                "full_name": POLICY_REPO,
                "default_branch": "main",
            },
            f"repos/{POLICY_REPO}/pulls/7": {
                "number": 7,
                "state": "open",
                "head": {"sha": self.head},
                "base": {
                    "sha": self.base,
                    "ref": "main",
                    "repo": {"full_name": POLICY_REPO},
                },
            },
            f"repos/{POLICY_REPO}/git/ref/heads/main": {"object": {"sha": self.base}},
            f"repos/{POLICY_REPO}/actions/runs/91": {
                "id": 91,
                "run_attempt": 2,
                "event": "pull_request_target",
                "path": ".github/workflows/pin-pr.yml",
                "head_sha": self.base,
                "repository": {"full_name": POLICY_REPO},
            },
        }
        self.number = 0
        self.archives = {}

    def transport(self, request, **kwargs):
        path = request.full_url.removeprefix("https://api.github.com/").split("?")[0]
        method = request.get_method()
        payload = json.loads(request.data) if request.data else None
        self.requests.append((method, path, payload))
        if method == "POST" and path.endswith("/check-runs"):
            value = {
                **payload,
                "id": len(self.checks) + 1,
                "app": {"slug": "github-actions"},
            }
            self.checks.append(value)
        elif "/check-runs/" in path:
            value = self.checks[int(path.rsplit("/", 1)[1]) - 1]
            if method == "PATCH":
                value.update(payload)
        elif path.endswith("/check-runs"):
            value = {"check_runs": self.checks}
        else:
            value = self.github[path]
        if isinstance(value, bytes):
            return io.BytesIO(value)
        return io.BytesIO(json.dumps(value).encode())

    def evidence(self):
        workspace = self.root.parent / "members"
        workspace.mkdir()
        (workspace / "example").symlink_to(self.root, target_is_directory=True)
        services = fixture.Services(self.root)

        def execute(command, **kwargs):
            if command[:2] == ["nix", "run"]:
                with patch.object(policy, "PACKAGED_REVISION", CHECKER, create=True):
                    return services.run(command, **kwargs)
            return services.run(command, **kwargs)

        common = ["--policy-root", str(self.baseline), "pin-batch"]
        with (
            patch.object(releases, "public_get", side_effect=services.lookup),
            patch.object(subprocess, "run", side_effect=execute),
            patch.object(subprocess, "check_output", side_effect=services.output),
            patch.object(policy, "PACKAGED_REVISION", self.base, create=True),
        ):
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
        import hashlib

        artifacts = []
        for name, directory in [
            ("pin-plan-91-2", plan_dir),
            ("pin-summary-91-2", summary),
            *[
                (f"pin-result-91-2-{row['worker']}", workers / row["worker"])
                for row in plan["matrix"]["include"]
            ],
        ]:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                for path in directory.rglob("*"):
                    if path.is_file():
                        archive.writestr(
                            str(path.relative_to(directory)), path.read_bytes()
                        )
            data = stream.getvalue()
            identity = len(artifacts) + 1
            artifacts.append(
                {
                    "id": identity,
                    "name": name,
                    "expired": False,
                    "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
                    "workflow_run": {"id": 91, "head_sha": self.base},
                }
            )
            self.github[f"repos/{POLICY_REPO}/actions/artifacts/{identity}/zip"] = data
        self.github[f"repos/{POLICY_REPO}/actions/runs/91/artifacts"] = {
            "artifacts": artifacts
        }
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
        self.github[f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"] = {
            "jobs": jobs
        }
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
            patch("urllib.request.urlopen", side_effect=self.transport),
            patch.dict("os.environ", {"GH_TOKEN": "fixture-read-or-report-token"}),
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
        self.github[f"repos/{POLICY_REPO}/pulls/7"]["head"]["sha"] = self.head
        code, report, _ = self.call("capture")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["classification"], "candidate")
        self.assertEqual(report["batch"], "next")
        self.assertFalse(report["eligible"])
        self.assertEqual(self.checks[0]["head_sha"], self.head)
        self.assertEqual(self.checks[0]["status"], "in_progress")
        self.assertEqual(self.checks[0]["name"], "Pin batch / Complete candidate")
        self.assertEqual(
            records.read_json(self.baseline / "policy/pins.json")["approved"], PAIR
        )

    def test_candidate_classification_rejects_changed_authority_and_invalid_batches(
        self,
    ):
        for file, keys, value in (
            ("members.json", ("members",), {}),
            ("projects.json", ("policyRepository",), "attacker/policy"),
            (
                "projects.json",
                ("projects", "example", "requiredArchitectures"),
                ["aarch64-linux", "x86_64-linux"],
            ),
            ("support.json", ("retirements",), {"v0.3.0": retirement()}),
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
                self.github[f"repos/{POLICY_REPO}/pulls/7"]["head"]["sha"] = self.head
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
        jobs = self.github[f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"][
            "jobs"
        ]
        jobs[-1]["conclusion"] = "cancelled"
        code, report, output = self.call("collect")
        self.assertNotEqual(code, 0, report)
        self.assertIn("example", str(report["issues"]))
        self.assertTrue((output / "workers").exists())

    def test_finish_reports_only_complete_current_evidence_on_the_proposal_head(self):
        _, captured, _ = self.call("capture")
        self.evidence()
        code, report, _ = self.call("finish", "--check", str(captured["check"]))
        self.assertEqual(code, 0, report)
        self.assertTrue(report["eligible"])
        self.assertEqual(self.checks[0]["conclusion"], "success")
        self.assertEqual(self.checks[0]["head_sha"], self.head)
        self.github[f"repos/{POLICY_REPO}/pulls/7"]["head"]["sha"] = "f" * 40
        code, report, _ = self.call("finish", "--check", str(captured["check"]))
        self.assertNotEqual(code, 0, report)
        self.assertEqual(self.checks[0]["conclusion"], "failure")
        self.assertIn("head", str(report["issues"]))

    def test_old_target_workflow_cannot_attest_newer_baseline_on_rerun(self):
        self.github[f"repos/{POLICY_REPO}/actions/runs/91"]["head_sha"] = "f" * 40
        code, report, _ = self.call("capture")
        self.assertNotEqual(code, 0, report)
        self.assertFalse(self.checks)

    def test_hosted_failures_and_unavailable_artifacts_never_publish_success(self):
        _, captured, _ = self.call("capture")
        self.evidence()
        original = copy.deepcopy(self.github)
        job_path = f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"
        artifact_path = f"repos/{POLICY_REPO}/actions/runs/91/artifacts"
        mutations = {
            "missing artifact": lambda: self.github[artifact_path]["artifacts"].pop(),
            "expired artifact": lambda: self.github[artifact_path]["artifacts"][
                -1
            ].update(expired=True),
            "wrong run": lambda: self.github[artifact_path]["artifacts"][-1][
                "workflow_run"
            ].update(id=92),
            "wrong attempt": lambda: self.github[
                f"repos/{POLICY_REPO}/actions/runs/91"
            ].update(run_attempt=3),
            "manual workflow": lambda: self.github[
                f"repos/{POLICY_REPO}/actions/runs/91"
            ].update(event="workflow_dispatch"),
            "substituted workflow": lambda: self.github[
                f"repos/{POLICY_REPO}/actions/runs/91"
            ].update(path=".github/workflows/untrusted.yml"),
            "skipped worker": lambda: self.github[job_path]["jobs"][-1].update(
                conclusion="skipped"
            ),
            "missing worker": lambda: self.github[job_path]["jobs"].pop(),
            "wrong native runner": lambda: self.github[job_path]["jobs"][-1].update(
                labels=["ubuntu-24.04"]
            ),
            "failed aggregate upload": lambda: self.github[job_path]["jobs"][2].update(
                conclusion="failure"
            ),
            "duplicate job": lambda: self.github[job_path]["jobs"].append(
                self.github[job_path]["jobs"][-1]
            ),
            "duplicate artifact": lambda: self.github[artifact_path][
                "artifacts"
            ].append(self.github[artifact_path]["artifacts"][-1]),
        }
        for name, mutate in mutations.items():
            self.github = copy.deepcopy(original)
            mutate()
            with self.subTest(change=name):
                code, result, _ = self.call("finish", "--check", str(captured["check"]))
                self.assertNotEqual(code, 0, result)
                self.assertEqual(self.checks[0]["conclusion"], "failure")

    def test_artifact_archives_cannot_escape_or_redirect_evidence_extraction(self):
        self.call("capture")
        self.evidence()
        import hashlib

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
            self.github[f"repos/{POLICY_REPO}/actions/artifacts/1/zip"] = data
            self.github[f"repos/{POLICY_REPO}/actions/runs/91/artifacts"]["artifacts"][
                0
            ]["digest"] = "sha256:" + (
                "0" * 64 if mode == "wrong-digest" else hashlib.sha256(data).hexdigest()
            )
            with self.subTest(mode=mode):
                code, result, output = self.call("collect")
                self.assertNotEqual(code, 0, result)
                self.assertFalse((output / "outside").exists())

    def test_state_only_recovery_and_non_pin_changes_do_not_require_a_new_batch(self):
        self.github[f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"] = {
            "jobs": [
                {
                    "id": 1,
                    "name": "Capture pin proposal",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
        self.github[f"repos/{POLICY_REPO}/actions/runs/91/artifacts"] = {
            "artifacts": []
        }
        pins = records.read_json(self.proposal / "policy/pins.json")
        pins["batches"][-1]["status"] = "approved"
        candidates.write_json(self.baseline / "policy/pins.json", pins)
        self.commit(self.baseline)
        self.base = policy.git_revision(self.baseline)
        self.github[f"repos/{POLICY_REPO}/git/ref/heads/main"]["object"]["sha"] = (
            self.base
        )
        self.github[f"repos/{POLICY_REPO}/actions/runs/91"]["head_sha"] = self.base
        for state in ("approved", "paused", "rolling", "complete", "withdrawn"):
            pins["batches"][-1]["status"] = state
            candidates.write_json(self.proposal / "policy/pins.json", pins)
            self.commit(self.proposal)
            self.head = policy.git_revision(self.proposal)
            self.github[f"repos/{POLICY_REPO}/pulls/7"]["head"]["sha"] = self.head
            self.checks = []
            with self.subTest(state=state):
                code, result, _ = self.call("capture")
                self.assertEqual(code, 0, result)
                self.assertIn(
                    result["classification"], {"state-only", "not-applicable"}
                )
                code, result, _ = self.call("finish", "--check", "1")
                self.assertEqual(code, 0, result)
                self.assertFalse(result["eligible"])
                self.assertEqual(self.checks[0]["conclusion"], "success")
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
        self.github[f"repos/{POLICY_REPO}/pulls"] = [
            self.github[f"repos/{POLICY_REPO}/pulls/7"]
        ]
        (self.baseline / "reviewed.txt").write_text("New trusted baseline\n")
        self.commit(self.baseline)
        current = policy.git_revision(self.baseline)
        self.github[f"repos/{POLICY_REPO}/git/ref/heads/main"]["object"]["sha"] = (
            current
        )
        code, result, _ = self.call("invalidate")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.checks[0]["conclusion"], "failure")
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
        self.github[f"repos/{POLICY_REPO}/pulls/7"]["head"]["sha"] = self.head
        self.github[f"repos/{POLICY_REPO}/git/ref/heads/main"]["object"]["sha"] = (
            self.base
        )
        self.github[f"repos/{POLICY_REPO}/actions/runs/91"]["head_sha"] = self.base
        with patch.object(support, "now", return_value=support.timestamp(BEFORE)):
            _, captured, _ = self.call("capture")
            self.evidence()
            self.assertEqual(
                self.call("finish", "--check", str(captured["check"]))[0], 0
            )
        self.github[f"repos/{POLICY_REPO}/pulls"] = [
            self.github[f"repos/{POLICY_REPO}/pulls/7"]
        ]
        with patch.object(support, "now", return_value=support.timestamp(EFFECTIVE)):
            code, result, _ = self.call("invalidate")
            self.assertEqual(code, 0, result)
            self.assertEqual(self.checks[0]["conclusion"], "failure")
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
        binary = workspace / "bin"
        binary.mkdir()
        stub = binary / "nix"
        stub.write_text(
            f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom tests.test_pin_pr import workflow_adapter\nworkflow_adapter()\n"
        )
        stub.chmod(0o755)
        state = workspace / "github.json"
        environment = {
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(workspace),
            "GITHUB_OUTPUT": str(workspace / "github-output"),
            "GH_TOKEN": "fixture",
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

        def save():
            value = {
                "github": {
                    key: {"binary": data.hex()} if isinstance(data, bytes) else data
                    for key, data in self.github.items()
                },
                "checks": self.checks,
            }
            candidates.write_json(state, value)

        def shell(job, **extra):
            save()
            step = next(
                step
                for step in workflow["jobs"][job]["steps"]
                if "nix run" in step.get("run", "")
            )
            process = fixture.REAL_RUN(
                ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                cwd=workspace,
                env={**environment, **extra},
                text=True,
                capture_output=True,
            )
            self.checks = records.read_json(state)["checks"]
            return process

        def artifact(name, directory):
            import hashlib

            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                for path in directory.rglob("*"):
                    if path.is_file():
                        archive.writestr(
                            str(path.relative_to(directory)), path.read_bytes()
                        )
            data = stream.getvalue()
            entries = self.github.setdefault(
                f"repos/{POLICY_REPO}/actions/runs/91/artifacts", {"artifacts": []}
            )["artifacts"]
            identity = len(entries) + 1
            entries.append(
                {
                    "id": identity,
                    "name": name,
                    "expired": False,
                    "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
                    "workflow_run": {"id": 91, "head_sha": self.base},
                }
            )
            self.github[f"repos/{POLICY_REPO}/actions/artifacts/{identity}/zip"] = data

        process = shell("capture")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn(
            "classification=candidate", (workspace / "github-output").read_text()
        )
        process = shell("plan")
        self.assertEqual(process.returncode, 0, process.stderr)
        plan = records.read_json(workspace / "pin-plan/plan.json")
        artifact("pin-plan-91-2", workspace / "pin-plan")
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
            artifact(f"pin-result-91-2-{row['worker']}", workspace / "pin-result")
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
        self.github[f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"] = {
            "jobs": jobs
        }
        process = shell("aggregate")
        self.assertEqual(process.returncode, 0, process.stderr)
        artifact("pin-summary-91-2", workspace / "pin-summary")
        process = shell("finish", CHECK_ID="1")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.checks[0]["conclusion"], "success")
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
        self.assertEqual(self.checks[0]["conclusion"], "failure")
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
        binary = workspace / "bin"
        binary.mkdir()
        nix = binary / "nix"
        nix.write_text(
            f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom tests.test_pin_pr import workflow_adapter\nworkflow_adapter()\n"
        )
        nix.chmod(0o755)
        git = binary / "git"
        queries = workspace / "queries"
        git.write_text(
            f"#!{sys.executable}\nimport os, sys\nif sys.argv[1] == 'ls-remote':\n with open({str(queries)!r}, 'a') as stream: stream.write(sys.argv[-1] + '\\n')\n print(({'d' * 40!r} if sys.argv[-1].endswith('nixos-unstable') else {'c' * 40!r}) + '\\t' + sys.argv[-1])\nelse: os.execv({shutil.which('git')!r}, [{shutil.which('git')!r}, *sys.argv[1:]])\n"
        )
        git.chmod(0o755)
        state = workspace / "github.json"
        self.github[f"repos/{POLICY_REPO}/pulls"] = [
            self.github[f"repos/{POLICY_REPO}/pulls/7"]
        ]
        self.checks = [
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
        candidates.write_json(state, {"github": self.github, "checks": self.checks})
        environment = {
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(workspace),
            "GH_TOKEN": "fixture",
            "PR_FIXTURE_ROOT": str(self.root),
            "PR_FIXTURE_STATE": str(state),
            "PR_FIXTURE_BASE": self.base,
            "STABLE_REVISION": "",
            "UNSTABLE_REVISION": "",
        }

        def shell(job, **extra):
            step = next(
                step
                for step in workflow["jobs"][job]["steps"]
                if "nix run" in step.get("run", "")
            )
            return fixture.REAL_RUN(
                ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                cwd=self.baseline,
                env={**environment, **extra},
                text=True,
                capture_output=True,
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


def workflow_adapter():
    if sys.argv[1:3] != ["run", "--no-update-lock-file"] or sys.argv[3] not in {
        "./authority",
        ".#",
    }:
        raise AssertionError("Workflow substituted the trusted coordinator")
    state = Path(os.environ["PR_FIXTURE_STATE"])
    saved = records.read_json(state)
    github = {
        key: bytes.fromhex(value["binary"])
        if isinstance(value, dict) and set(value) == {"binary"}
        else value
        for key, value in saved["github"].items()
    }
    context = SimpleNamespace(github=github, checks=saved["checks"], requests=[])
    services = fixture.Services(Path(os.environ["PR_FIXTURE_ROOT"]))
    services.host = os.environ.get("SYSTEM", "x86_64-linux")

    def execute(command, **kwargs):
        if command[:2] == ["nix", "run"]:
            with patch.object(policy, "PACKAGED_REVISION", CHECKER, create=True):
                return services.run(command, **kwargs)
        return services.run(command, **kwargs)

    with (
        patch(
            "urllib.request.urlopen",
            side_effect=lambda request, **kwargs: PinPRTests.transport(
                context, request, **kwargs
            ),
        ),
        patch.object(releases, "public_get", side_effect=services.lookup),
        patch.object(subprocess, "run", side_effect=execute),
        patch.object(subprocess, "check_output", side_effect=services.output),
        patch.object(
            policy, "PACKAGED_REVISION", os.environ["PR_FIXTURE_BASE"], create=True
        ),
    ):
        code = policy.main(sys.argv[sys.argv.index("--") + 1 :])
    saved["checks"] = context.checks
    candidates.write_json(state, saved)
    sys.exit(code)


if __name__ == "__main__":
    unittest.main()
