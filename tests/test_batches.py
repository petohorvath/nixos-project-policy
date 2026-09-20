"""Complete candidate batches through the public CLI and native workflow seam."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import yaml

from tests import test_candidates as fixture
from tests.test_audits import invoke
from tests.test_policy import (
    CHECKER,
    NEW_PAIR,
    PAIR,
    POLICY_REPO,
    ProjectFixture,
    RELEASE,
)
from tests.test_support import BEFORE, EFFECTIVE, retirement
from tools import agreement, batches, candidates, policy, records, releases, support


def outcome(report):
    return {
        "status": report.get("status"),
        "issues": report.get("issues"),
        "members": {
            name: member.get("issues")
            for name, member in report.get("members", {}).items()
        },
    }


class Services(fixture.Services):
    def __init__(self, roots):
        super().__init__(roots["example"])
        self.roots = roots
        self.failed_member = None

    def lookup(self, path):
        if "/git/commits/" in path:
            name = path.split("/")[2]
            self.root = self.roots[name]
        return super().lookup(path)

    def run(self, command, **kwargs):
        previous = self.root
        command = list(map(str, command))
        if command[0] == "nix":
            if command[1] == "run":
                arguments = command[command.index("--") + 1 :]
                if not arguments[3].startswith("--"):
                    self.root = Path(arguments[3])
                elif "--project" in arguments:
                    self.root = self.roots[arguments[arguments.index("--project") + 1]]
            elif command[1:3] in (["flake", "metadata"], ["flake", "check"]):
                self.root = Path(command[3])
        failure = self.failure
        if self.failed_member and self.root.name != self.failed_member:
            self.failure = None
        try:
            return super().run(command, **kwargs)
        finally:
            self.root, self.failure = previous, failure


class BatchTests(ProjectFixture):
    commit = fixture.CandidateTests.commit

    def setUp(self):
        super().setUp()
        self.workspace = self.root.parent / "members"
        self.workspace.mkdir()
        original = self.root
        self.root = self.workspace / "example"
        original.rename(self.root)
        lock = records.read_json(self.root / "flake.lock")
        lock["nodes"]["entry"]["inputs"].pop("nixpkgs-unstable")
        lock["nodes"].pop("rolling")
        candidates.write_json(self.root / "flake.lock", lock)
        self.roots = {"example": self.root}
        self.locked = {}
        configuration = self.root.parent.parent / "gitconfig"
        configuration.write_text("")
        environment = patch.dict(
            os.environ,
            {
                "GIT_CONFIG_GLOBAL": str(configuration),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ALLOW_PROTOCOL": "file:https:ssh",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("alpha", "legacy"):
            root = self.workspace / name
            shutil.copytree(self.root, root)
            self.roots[name] = root
            self.members[name] = f"owner/{name}"
            self.config["projects"][name] = {
                **copy.deepcopy(self.config["projects"]["example"]),
                "repository": f"owner/{name}",
            }
            self.declaration(name, RELEASE)
            self.commit(root)
            self.locked[name] = policy.git_revision(root)
            fixture.REAL_RUN(
                [
                    "git",
                    "config",
                    "--global",
                    f"url.{root.as_uri()}.insteadOf",
                    f"https://github.com/owner/{name}.git",
                ],
                check=True,
            )
        fixture.REAL_RUN(
            [
                "git",
                "config",
                "--global",
                f"url.{self.root.as_uri()}.insteadOf",
                "https://github.com/owner/example.git",
            ],
            check=True,
        )
        self.declaration("legacy", "v0.3.0")
        self.config["projects"]["legacy"]["policyVersion"] = "v0.3.0"
        self.commit(self.roots["legacy"])
        self.declaration(
            "alpha",
            RELEASE,
            required_architectures='["aarch64-linux"]',
            vm_targets='["vm-test"]',
            additional_required_checks='["Member / Extra"]',
        )
        self.commit(self.roots["alpha"])
        self.workflow["jobs"]["integration"] = {
            "name": "Integration",
            "needs": "policy",
            "uses": f"{POLICY_REPO}/.github/workflows/agreement.yml@{RELEASE}",
            "with": {
                "project": "example",
                "policy_version": RELEASE,
                "project_revision": "${{ needs.policy.outputs.project_revision }}",
                "records_revision": "${{ needs.policy.outputs.records_revision }}",
            },
        }
        self.declare(additional_required_checks=json.dumps([agreement.GATE]))
        for name, revision in self.locked.items():
            lock["nodes"]["entry"]["inputs"][name] = name
            lock["nodes"][name] = {
                "locked": {
                    "type": "github",
                    "owner": "owner",
                    "repo": name,
                    "rev": revision,
                },
                "original": {"type": "github", "owner": "owner", "repo": name},
                "inputs": {"nixpkgs": ["nixpkgs"]},
            }
        candidates.write_json(self.root / "flake.lock", lock)
        self.commit(self.root)
        self.baseline = self.write_records()
        self.commit(self.baseline)
        self.proposal = self.workspace.parent / "proposal"
        shutil.copytree(
            self.baseline, self.proposal, ignore=shutil.ignore_patterns(".git")
        )
        self.propose()
        self.commit(self.proposal)
        self.services = Services(self.roots)
        self.output_number = 0
        for adapter in [
            patch.object(releases, "public_get", side_effect=self.services.lookup),
            patch.object(subprocess, "run", side_effect=self.services.run),
            patch.object(subprocess, "check_output", side_effect=self.services.output),
            patch.object(policy, "PACKAGED_REVISION", CHECKER, create=True),
        ]:
            adapter.start()
            self.addCleanup(adapter.stop)

    def declaration(self, name, version, **settings):
        root = self.roots[name]
        workflow = copy.deepcopy(self.workflow)
        job = workflow["jobs"]["policy"]
        job["uses"] = f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
        job["with"] = {"project": name, "policy_version": version}
        if version == RELEASE:
            job["with"].update(
                {
                    "required_architectures": '["x86_64-linux", "aarch64-linux"]',
                    **settings,
                }
            )
        candidates.write_json(root / ".github/workflows/policy.yml", workflow)
        for name in ("AGENTS.md", "CONTRIBUTING.md"):
            (root / name).write_text(
                f"[Rules](https://github.com/{POLICY_REPO}/blob/{version}/POLICY.md)\n"
            )

    def propose(self):
        pins = copy.deepcopy(self.pins)
        pins["batches"].append(
            {
                "id": "next",
                "status": "candidate",
                "previous": PAIR,
                "pins": NEW_PAIR,
                "projects": {
                    name: policy.git_revision(root) for name, root in self.roots.items()
                },
            }
        )
        candidates.write_json(self.proposal / "policy/pins.json", pins)

    def call(self, operation, *options, root=None):
        output = self.workspace.parent / f"batch-output-{self.output_number}"
        self.output_number += 1
        code, report = invoke(
            "--policy-root",
            str(self.baseline),
            "pin-batch",
            operation,
            str(root or self.workspace),
            "--proposal-root",
            str(self.proposal),
            "--output",
            str(output),
            *options,
        )
        return code, report, output

    def plan(self, *options, root=None):
        return self.call(
            "plan",
            "--all",
            "--batch",
            "next",
            "--attempt",
            "batch-1",
            *options,
            root=root,
        )

    def worker(self, plan, name, system, *options):
        self.services.host = system
        return self.call(
            "execute",
            "--plan",
            str(plan / "plan.json"),
            "--project",
            name,
            "--system",
            system,
            "--attempt",
            "batch-1",
            *options,
            root=self.roots[name],
        )

    def workers(self, plan):
        report = records.read_json(plan / "plan.json")
        results = []
        for row in report["matrix"]["include"]:
            code, result, output = self.worker(plan, row["project"], row["system"])
            self.assertEqual(code, 0, outcome(result))
            results.append(output)
        return results

    def aggregate(self, plan, workers, *options):
        inputs = self.workspace.parent / f"batch-inputs-{self.output_number}"
        inputs.mkdir()
        for index, worker in enumerate(workers):
            shutil.copytree(worker, inputs / str(index))
        return self.call(
            "aggregate",
            "--plan",
            str(plan / "plan.json"),
            "--results",
            str(inputs),
            "--attempt",
            "batch-1",
            *options,
        )

    def test_complete_mixed_batch_executes_locked_integration_and_every_native_gate(
        self,
    ):
        before = {
            root: policy.fingerprints(root)
            for root in [*self.roots.values(), self.baseline, self.proposal]
        }
        code, plan, output = self.plan()
        self.assertEqual(code, 0, plan)
        self.assertEqual(set(plan["members"]), set(self.members))
        self.assertEqual(len(plan["matrix"]["include"]), 6)
        integration = plan["members"]["example"]["plan"]["agreement"]
        self.assertEqual(
            {
                member["project"]: member["revision"]
                for member in integration["members"]
            },
            self.locked,
        )
        self.assertNotEqual(
            self.locked["legacy"],
            plan["members"]["legacy"]["plan"]["source"]["revision"],
        )
        self.assertEqual(
            plan["members"]["legacy"]["plan"]["release"]["version"], "v0.3.0"
        )
        workers = self.workers(output)
        code, result, summary = self.aggregate(output, workers)
        self.assertEqual(code, 0, outcome(result))
        self.assertTrue(result["eligible"])
        self.assertEqual(result["approval"], "not-granted")
        for name, member in result["members"].items():
            self.assertFalse(member["eligible"])
            expected = {job["id"] for job in plan["members"][name]["plan"]["jobs"]}
            self.assertEqual(
                {item["job"]["id"] for item in member["results"]}, expected
            )
        executed = result["members"]["example"]["results"]
        check = next(item for item in executed if item["job"]["kind"] == "agreement")
        self.assertEqual(check["report"], integration)
        self.assertEqual(check["report"]["behavioralIntegration"], "not-run")
        compatibility = [
            item for item in executed if item["job"]["kind"] == "compatibility"
        ]
        self.assertEqual(len(compatibility), 4)
        self.assertTrue(
            all(
                str(self.root) in item["commands"][0]["command"]
                for item in compatibility
            )
        )
        self.assertEqual(
            len(list((summary / "evidence").glob("worker-*/result.json"))), 6
        )
        self.assertGreater(len(list((summary / "evidence").glob("**/result.json"))), 6)
        for root, fingerprint in before.items():
            self.assertEqual(policy.fingerprints(root), fingerprint)

    def test_missing_enrollment_registration_preserves_other_plans_and_results(self):
        pins = records.read_json(self.proposal / "policy/pins.json")
        del pins["batches"][-1]["projects"]["legacy"]
        candidates.write_json(self.proposal / "policy/pins.json", pins)
        code, plan, output = self.plan()
        self.assertEqual(code, 1, plan)
        self.assertEqual(plan["members"]["legacy"]["status"], "error")
        workers = self.workers(output)
        code, result, _ = self.aggregate(output, workers)
        self.assertEqual(code, 1, outcome(result))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["members"]["example"]["status"], "candidate-pass")

    def test_partial_duplicate_foreign_and_malformed_workers_never_grant_eligibility(
        self,
    ):
        _, _, plan = self.plan()
        workers = self.workers(plan)
        for invalid in (
            "missing",
            "missing-envelope",
            "duplicate",
            "malformed",
            "foreign",
            "failed",
        ):
            original = (workers[0] / "result.json").read_text()
            inputs = list(workers)
            if invalid == "missing":
                inputs.pop()
            elif invalid == "duplicate":
                inputs.append(workers[0])
            elif invalid == "malformed":
                (workers[0] / "result.json").write_text('{"truncated":')
            elif invalid == "missing-envelope":
                (workers[0] / "result.json").unlink()
            else:
                value = json.loads(original)
                value["worker" if invalid == "foreign" else "status"] = (
                    "foreign" if invalid == "foreign" else "cancelled"
                )
                candidates.write_json(workers[0] / "result.json", value)
            with self.subTest(invalid=invalid):
                code, result, output = self.aggregate(plan, inputs)
                self.assertNotEqual(code, 0, result)
                self.assertFalse(result["eligible"])
                self.assertTrue(list((output / "evidence").iterdir()))
            (workers[0] / "result.json").write_text(original)

    def test_attempt_subject_settings_checker_and_integration_forgery_is_rejected(self):
        _, saved, plan = self.plan()
        workers = self.workers(plan)
        source = next(
            path
            for path in workers
            if records.read_json(path / "result.json")["project"] == "example"
        )
        original = records.read_json(source / "result.json")
        mutations = {
            "attempt": lambda value: value.update(attempt="batch-0"),
            "parent": lambda value: value.update(batchPlanDigest="0" * 64),
            "child": lambda value: value.update(planDigest="0" * 64),
            "system": lambda value: value.update(system="different-linux"),
        }
        for kind in ("agreement", "compatibility"):
            for field, replacement in (
                ("revision", CHECKER),
                ("policyRecordsDigest", "0" * 64),
                ("checkerVersion", "v0.9.0"),
                ("memberSettings", {}),
                ("dependencySetDigest", "0" * 64),
            ):
                if field == "dependencySetDigest" and kind != "agreement":
                    continue

                def mutate(value, kind=kind, field=field, replacement=replacement):
                    item = next(
                        item for item in value["results"] if item["job"]["kind"] == kind
                    )
                    item["report"][field] = replacement
                    item["commands"][-1]["stdout"] = json.dumps(item["report"])

                mutations[f"{kind}-{field}"] = mutate
        for name, mutation in mutations.items():
            value = copy.deepcopy(original)
            mutation(value)
            candidates.write_json(source / "result.json", value)
            with self.subTest(mutation=name):
                self.assertNotEqual(self.aggregate(plan, workers)[0], 0)
        candidates.write_json(source / "result.json", original)
        value = copy.deepcopy(saved)
        del value["members"]["legacy"]
        value["matrix"] = batches.matrix(value["members"])
        value["planDigest"] = candidates.digest(
            {key: item for key, item in value.items() if key != "planDigest"}
        )
        candidates.write_json(plan / "plan.json", value)
        self.assertNotEqual(self.aggregate(plan, workers)[0], 0)

    def test_trusted_attempt_and_native_job_outcomes_cannot_be_spoofed_by_artifacts(
        self,
    ):
        _, captured, plan = self.plan()
        workers = self.workers(plan)
        path = self.workspace.parent / "outcomes.json"
        value = {
            "schemaVersion": 1,
            "planDigest": captured["planDigest"],
            "attempt": "batch-1",
            "workers": [
                {"id": row["worker"], "status": "completed", "conclusion": "success"}
                for row in captured["matrix"]["include"]
            ],
        }
        candidates.write_json(path, value)
        self.assertEqual(
            self.aggregate(plan, workers, "--worker-outcomes", str(path))[0], 0
        )
        for failure in (
            "failure",
            "cancelled",
            "skipped",
            "neutral",
            "missing",
            "duplicate",
            "attempt",
        ):
            changed = copy.deepcopy(value)
            if failure == "missing":
                changed["workers"].pop()
            elif failure == "duplicate":
                changed["workers"].append(changed["workers"][0])
            elif failure == "attempt":
                changed["attempt"] = "batch-0"
            else:
                changed["workers"][0]["conclusion"] = failure
            candidates.write_json(path, changed)
            with self.subTest(failure=failure):
                self.assertNotEqual(
                    self.aggregate(plan, workers, "--worker-outcomes", str(path))[0], 0
                )
        self.assertNotEqual(self.aggregate(plan, workers, "--attempt", "batch-2")[0], 0)
        self.assertNotEqual(
            self.worker(plan, "example", "x86_64-linux", "--attempt", "batch-2")[0], 0
        )
        self.assertEqual(
            self.aggregate(plan, workers, "--execution-status", "failure")[0], 1
        )

    def test_candidate_source_settings_roster_and_support_changes_invalidate_evidence(
        self,
    ):
        _, _, plan = self.plan()
        workers = self.workers(plan)
        changes = [
            (
                self.proposal / "policy/pins.json",
                lambda value: value["batches"][-1]["pins"].update(stable="9" * 40),
            ),
            (
                self.proposal / "policy/pins.json",
                lambda value: value["batches"][-1]["projects"].update(alpha=CHECKER),
            ),
            (
                self.baseline / "policy/members.json",
                lambda value: value["members"].pop("alpha"),
            ),
            (
                self.baseline / "policy/support.json",
                lambda value: value["retirements"].update({RELEASE: retirement()}),
            ),
            (
                self.roots["alpha"] / ".github/workflows/policy.yml",
                lambda value: value["jobs"]["policy"]["with"].update(
                    required_architectures='["x86_64-linux"]'
                ),
            ),
            (
                self.root / "flake.lock",
                lambda value: value["nodes"]["alpha"]["locked"].update(rev=CHECKER),
            ),
        ]
        for path, change in changes:
            original = path.read_text()
            value = json.loads(original)
            change(value)
            candidates.write_json(path, value)
            with self.subTest(path=path):
                code, result, output = self.aggregate(plan, workers)
                self.assertNotEqual(code, 0, result)
                self.assertTrue(
                    list((output / "evidence").glob("worker-*/result.json"))
                )
            path.write_text(original)
        self.support["retirements"][RELEASE] = retirement()
        self.write_records()
        shutil.copyfile(
            self.baseline / "policy/support.json", self.proposal / "policy/support.json"
        )
        with patch.object(support, "now", return_value=support.timestamp(BEFORE)):
            _, _, plan = self.plan()
            workers = self.workers(plan)
        with patch.object(support, "now", return_value=support.timestamp(EFFECTIVE)):
            code, result, _ = self.aggregate(plan, workers)
            self.assertNotEqual(code, 0, result)
            self.assertFalse(result["eligible"])

    def test_locked_mismatch_remains_failed_despite_successful_member_heads(self):
        lock = records.read_json(self.root / "flake.lock")
        lock["nodes"]["legacy"]["locked"]["rev"] = policy.git_revision(
            self.roots["legacy"]
        )
        candidates.write_json(self.root / "flake.lock", lock)
        self.commit(self.root)
        self.propose()
        code, captured, plan = self.plan()
        self.assertEqual(code, 0, captured)
        self.assertEqual(
            captured["members"]["example"]["plan"]["agreement"]["status"], "fail"
        )
        workers = []
        for row in captured["matrix"]["include"]:
            code, report, output = self.worker(plan, row["project"], row["system"])
            if row["project"] == "example" and row["system"] == "x86_64-linux":
                self.assertEqual(code, 1, outcome(report))
                self.assertTrue(
                    any(item["status"] == "pass" for item in report["results"])
                )
            else:
                self.assertEqual(code, 0, outcome(report))
            workers.append(output)
        code, result, _ = self.aggregate(plan, workers)
        self.assertEqual(code, 1, outcome(result))
        self.assertEqual(result["members"]["legacy"]["status"], "candidate-pass")

    def test_fetch_captures_trusted_exact_sources_and_preserves_unavailable_member(
        self,
    ):
        target = self.workspace.parent / "fetched"
        code, captured, _ = self.plan("--fetch", root=target)
        self.assertEqual(code, 0, captured)
        for name, root in self.roots.items():
            self.assertEqual(
                policy.git_revision(target / name), policy.git_revision(root)
            )
        pins = records.read_json(self.proposal / "policy/pins.json")
        pins["batches"][-1]["projects"]["legacy"] = CHECKER
        candidates.write_json(self.proposal / "policy/pins.json", pins)
        code, captured, output = self.plan(
            "--fetch", root=self.workspace.parent / "unavailable"
        )
        self.assertEqual(code, 1, captured)
        self.assertEqual(captured["members"]["legacy"]["status"], "error")
        self.assertEqual(captured["members"]["example"]["status"], "planned")
        self.assertTrue(list((output / "fetch/legacy").glob("**/stderr.log")))

    def test_final_assessment_rechecks_earlier_members_after_later_work(self):
        self.support["retirements"][RELEASE] = retirement()
        self.write_records()
        shutil.copyfile(
            self.baseline / "policy/support.json", self.proposal / "policy/support.json"
        )
        with patch.object(support, "now", return_value=support.timestamp(BEFORE)):
            _, _, plan = self.plan()
            workers = self.workers(plan)
        instant = support.timestamp(BEFORE)

        def lookup(path):
            nonlocal instant
            if "repos/owner/legacy/git/commits/" in path:
                instant = support.timestamp(EFFECTIVE)
            return self.services.lookup(path)

        with (
            patch.object(support, "now", side_effect=lambda: instant),
            patch.object(releases, "public_get", side_effect=lookup),
        ):
            code, result, _ = self.aggregate(plan, workers)
        self.assertEqual(code, 1, outcome(result))
        self.assertEqual(result["members"]["alpha"]["status"], "fail")
        self.assertEqual(result["members"]["legacy"]["status"], "candidate-pass")
        self.assertIn("support changed", str(result["members"]["alpha"]["issues"]))

    def test_untrusted_coverage_and_checker_commands_cannot_replace_required_jobs(self):
        _, saved, plan = self.plan()
        workers = self.workers(plan)
        source = next(
            path
            for path in workers
            if records.read_json(path / "result.json")["project"] == "example"
        )
        original = records.read_json(source / "result.json")
        for invalid in (
            "missing",
            "skipped",
            "checker",
            "root",
            "inner-root",
            "empty-checks",
            "boolean-outcome",
            "worker-issue",
        ):
            changed = copy.deepcopy(original)
            item = next(
                item
                for item in changed["results"]
                if item["job"]["kind"] == "compatibility"
            )
            if invalid == "missing":
                changed["results"].remove(item)
            elif invalid == "skipped":
                item["status"] = "skipped"
            elif invalid == "checker":
                item["commands"][0]["command"][3] = "github:attacker/checker/main"
            elif invalid == "root":
                item["commands"][0]["command"][8] = "/different/source"
            elif invalid == "boolean-outcome":
                item["commands"][0]["returncode"] = False
            elif invalid == "worker-issue":
                changed["issues"].append("Source changed after execution")
            else:
                if invalid == "inner-root":
                    item["report"]["commands"][-1]["command"][3] = "/different/source"
                else:
                    item["report"]["checks"] = []
                item["commands"][0]["stdout"] = json.dumps(item["report"])
            candidates.write_json(source / "result.json", changed)
            with self.subTest(invalid=invalid):
                self.assertEqual(self.aggregate(plan, workers)[0], 1)
        candidates.write_json(source / "result.json", original)
        child = saved["members"]["example"]["plan"]
        child["jobs"] = [
            job for job in child["jobs"] if job.get("channel") != "unstable"
        ]
        child["planDigest"] = candidates.digest(
            {key: value for key, value in child.items() if key != "planDigest"}
        )
        saved["planDigest"] = candidates.digest(
            {key: value for key, value in saved.items() if key != "planDigest"}
        )
        candidates.write_json(plan / "plan.json", saved)
        self.assertEqual(self.aggregate(plan, workers)[0], 1)

    def test_proposal_approval_and_replaced_checker_code_have_no_authority(self):
        pins = records.read_json(self.proposal / "policy/pins.json")
        pins["approved"] = NEW_PAIR
        pins["batches"][-1]["status"] = "approved"
        candidates.write_json(self.proposal / "policy/pins.json", pins)
        (self.proposal / "tools").mkdir()
        (self.proposal / "tools/policy.py").write_text(
            'raise AssertionError("proposal code must not execute")\n'
        )
        code, saved, plan = self.plan()
        self.assertEqual(code, 0, saved)
        workers = self.workers(plan)
        code, result, _ = self.aggregate(plan, workers)
        self.assertEqual(code, 0, outcome(result))
        self.assertTrue(result["eligible"])
        self.assertEqual(result["approval"], "not-granted")
        for member in result["members"].values():
            for item in member["results"]:
                if item["job"]["kind"] == "compatibility":
                    self.assertEqual(item["report"]["pinStatus"], "candidate")
        self.assertEqual(
            records.read_json(self.baseline / "policy/pins.json")["approved"], PAIR
        )

    def test_full_workflow_executes_native_matrix_and_retains_partial_outcomes(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/pin-batch.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        workspace = self.workspace.parent / "workflow"
        workspace.mkdir()
        for name, root in (("authority", self.baseline), ("proposal", self.proposal)):
            (workspace / name).symlink_to(root, target_is_directory=True)
        binary = workspace / "bin"
        binary.mkdir()
        stub = binary / "nix"
        stub.write_text(
            f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom tests.test_batches import workflow_adapter\nworkflow_adapter()\n"
        )
        stub.chmod(0o755)
        output = workspace / "github-output"
        environment = {
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(workspace),
            "GITHUB_OUTPUT": str(output),
            "BATCH": "next",
            "ATTEMPT": "batch-1",
            "BATCH_FIXTURE_ROOTS": json.dumps(
                {name: str(root) for name, root in self.roots.items()}
            ),
        }

        def shell(job, **extra):
            step = next(
                step
                for step in workflow["jobs"][job]["steps"]
                if f"pin-batch {job}" in step.get("run", "")
            )
            return fixture.REAL_RUN(
                ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                cwd=workspace,
                env={**environment, **extra},
                text=True,
                capture_output=True,
            )

        process = shell("plan")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("runnable=true", output.read_text())
        plan = records.read_json(workspace / "batch-plan/plan.json")
        inputs = workspace / "batch-results"
        inputs.mkdir()
        member = workspace / "member"
        for row in plan["matrix"]["include"]:
            member.unlink(missing_ok=True)
            member.symlink_to(
                workspace / "members" / row["project"], target_is_directory=True
            )
            process = shell("execute", PROJECT=row["project"], SYSTEM=row["system"])
            self.assertEqual(process.returncode, 0, process.stderr)
            (workspace / "batch-result").rename(inputs / row["worker"])
        process = shell("aggregate", EXECUTION_STATUS="success")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(
            records.read_json(workspace / "batch-summary/result.json")["eligible"]
        )
        (workspace / "batch-summary").rename(workspace / "successful-summary")
        member.unlink()
        member.symlink_to(workspace / "members/example", target_is_directory=True)
        process = shell(
            "execute",
            PROJECT="example",
            SYSTEM="x86_64-linux",
            BATCH_FIXTURE_FAILURE="lint",
            BATCH_FIXTURE_FAILED_MEMBER="example",
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        result = records.read_json(workspace / "batch-result/result.json")
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(item["status"] == "pass" for item in result["results"]))
        process = shell("aggregate", EXECUTION_STATUS="failure")
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertFalse(
            records.read_json(workspace / "batch-summary/result.json")["eligible"]
        )
        (workspace / "batch-plan").rename(workspace / "successful-plan")
        pins = records.read_json(self.proposal / "policy/pins.json")
        del pins["batches"][-1]["projects"]["legacy"]
        candidates.write_json(self.proposal / "policy/pins.json", pins)
        output.write_text("")
        process = shell("plan")
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertIn("runnable=true", output.read_text())
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["jobs"]["execute"]["strategy"]["fail-fast"], "false")
        self.assertIn("always()", workflow["jobs"]["execute"]["if"])
        self.assertIn("always()", workflow["jobs"]["aggregate"]["if"])
        for job in ("plan", "execute", "aggregate"):
            upload = next(
                step
                for step in workflow["jobs"][job]["steps"]
                if "upload-artifact" in step.get("uses", "")
            )
            self.assertEqual(upload["if"], "always()")


def workflow_adapter():
    services = Services(
        {
            name: Path(root)
            for name, root in json.loads(os.environ["BATCH_FIXTURE_ROOTS"]).items()
        }
    )
    services.host = os.environ.get("SYSTEM", "x86_64-linux")
    services.failure = os.environ.get("BATCH_FIXTURE_FAILURE")
    services.failed_member = os.environ.get("BATCH_FIXTURE_FAILED_MEMBER")
    with (
        patch.object(releases, "public_get", side_effect=services.lookup),
        patch.object(subprocess, "run", side_effect=services.run),
        patch.object(subprocess, "check_output", side_effect=services.output),
        patch.object(policy, "PACKAGED_REVISION", CHECKER, create=True),
    ):
        sys.exit(policy.main(sys.argv[sys.argv.index("--") + 1 :]))
