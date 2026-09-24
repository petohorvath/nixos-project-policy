"""Complete candidate batches through the public CLI and native workflow seam."""

import copy
import json
import shutil
from unittest.mock import patch

import yaml

from tests.fixtures import workflows
from tests.fixtures.candidates import BatchFixture, invalid_batch_plans
from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.cli import invoke
from tests.fixtures.data import (
    BEFORE,
    CHECKER,
    EFFECTIVE,
    NEW_PAIR,
    PAIR,
    RELEASE,
    retirement,
)
from tests.fixtures.services import BatchServices
from tools import batches, candidates, policy, records, releases, support


def outcome(report):
    return {
        "status": report.get("status"),
        "issues": report.get("issues"),
        "members": {
            name: member.get("issues")
            for name, member in report.get("members", {}).items()
        },
    }


class BatchTests(BatchFixture, ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.services = self.enterContext(BatchServices(self.roots).installed())
        self.output_number = 0

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
        self.commit(self.proposal)
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

    def test_execute_and_aggregate_reject_forged_plans_with_recomputed_digests(self):
        _, captured, plan = self.plan()
        workers = self.workers(plan)
        for name, value in invalid_batch_plans(captured):
            candidates.write_json(plan / "plan.json", value)
            with self.subTest(change=name, operation="execute"):
                code, result, _ = self.worker(plan, "example", "x86_64-linux")
                self.assertNotEqual(code, 0, result)
                self.assertFalse(result["eligible"])
            with self.subTest(change=name, operation="aggregate"):
                code, result, _ = self.aggregate(plan, workers)
                self.assertNotEqual(code, 0, result)
                self.assertFalse(result["eligible"])

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

    def test_two_routine_approvals_finish_without_member_or_bookkeeping_changes(self):
        before = {name: policy.fingerprints(root) for name, root in self.roots.items()}
        for batch, pair in (("next", NEW_PAIR), ("following", PAIR)):
            pins = records.read_json(self.baseline / "policy/pins.json")
            previous = copy.deepcopy(pins["approved"])
            pins["approved"] = pair
            pins["batches"].append(
                {
                    "id": batch,
                    "status": "complete",
                    "previous": previous,
                    "pins": pair,
                    "projects": {
                        name: policy.git_revision(root)
                        for name, root in self.roots.items()
                    },
                }
            )
            candidates.write_json(self.proposal / "policy/pins.json", pins)
            self.commit(self.proposal)
            code, saved, plan = self.plan("--batch", batch)
            self.assertEqual(code, 0, saved)
            workers = self.workers(plan)
            code, result, _ = self.aggregate(plan, workers)
            self.assertEqual(code, 0, outcome(result))
            self.assertTrue(result["eligible"])
            self.assertEqual(result["approval"], "not-granted")
            self.assertEqual(
                records.read_json(self.baseline / "policy/pins.json")["approved"],
                previous,
            )
            for member in saved["members"].values():
                self.assertEqual(member["plan"]["compatibilityMode"], "root-overrides")
            # Simulate the separate human merge, then prepare the next routine PR.
            shutil.copyfile(
                self.proposal / "policy/pins.json", self.baseline / "policy/pins.json"
            )
            self.commit(self.baseline)
            self.assertEqual(records.load(self.baseline)[1]["approved"], pair)
        self.assertEqual(
            {name: policy.fingerprints(root) for name, root in self.roots.items()},
            before,
        )
        self.assertNotEqual(self.plan("--batch", "next")[0], 0)

    def test_full_workflow_executes_native_matrix_and_retains_partial_outcomes(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/pin-batch.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        workspace = self.workspace.parent / "workflow"
        workspace.mkdir()
        for name, root in (("authority", self.baseline), ("proposal", self.proposal)):
            (workspace / name).symlink_to(root, target_is_directory=True)
        output = workspace / "github-output"
        environment = {
            **workflows.environment(workspace, adapter="batch_adapter"),
            "BATCH": "next",
            "ATTEMPT": "batch-1",
            "BATCH_FIXTURE_ROOTS": json.dumps(
                {name: str(root) for name, root in self.roots.items()}
            ),
        }

        def shell(job, **extra):
            return workflows.run_step(
                workflow, job, workspace, environment, match=f"pin-batch {job}", **extra
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
            BATCH_FIXTURE_FAILURE="tests",
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
        self.commit(self.proposal)
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
