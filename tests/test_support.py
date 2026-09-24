"""Public support decisions, retirement boundaries."""

import json
import os
import subprocess
import sys
from unittest.mock import patch

import yaml

from tests.fixtures.audits import AuditFixture
from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.cli import invoke
from tests.fixtures.compatibility import CompatibilityFixture
from tests.fixtures.data import (
    AFTER,
    BEFORE,
    EFFECTIVE,
    RELEASE,
    SOURCE,
    START,
    retirement,
)
from tools import policy, records, support


class SupportTests(ProjectTestCase):
    def test_support_identity_is_stable_until_retirement_becomes_effective(self):
        self.support["retirements"][RELEASE] = retirement()
        reports = []
        for instant in ["2029-12-31T00:00:00Z", START, BEFORE, EFFECTIVE, AFTER]:
            with patch.object(support, "now", return_value=support.timestamp(instant)):
                code, report = self.run_policy(
                    "ci", str(self.root), "--project", "example"
                )
            reports.append(report)
            retired = instant >= EFFECTIVE
            self.assertEqual(code, 1 if retired else 0, report)
            self.assertEqual(
                report["selectionStatus"], "retired" if retired else "supported"
            )
            self.assertEqual(report["support"]["retirement"], retirement())
            self.assertEqual("matrix" in report, not retired)
        self.assertEqual(reports[0]["support"], reports[2]["support"])
        self.assertEqual(reports[3]["support"], reports[4]["support"])
        self.assertNotEqual(reports[2]["support"], reports[3]["support"])
        self.assertEqual(len({report["policyRecordsDigest"] for report in reports}), 1)

    def test_retired_commands_stop_before_shell_vm_or_compatibility_execution(self):
        self.support["retirements"][RELEASE] = retirement()
        self.declare(vm_targets='["vm-tests"]')
        for command, options in [
            ("check", ["--shell"]),
            ("ci", []),
            ("vm", []),
            ("compatibility", ["--channel", "stable"]),
        ]:
            with (
                self.subTest(command=command),
                patch.object(support, "now", return_value=support.timestamp(EFFECTIVE)),
                patch.object(policy, "git_revision", return_value=SOURCE),
                patch.object(policy.subprocess, "run") as execute,
            ):
                code, report = self.run_policy(
                    command, str(self.root), "--project", "example", *options
                )
                self.assertEqual(code, 1, report)
                self.assertEqual(report["selectionStatus"], "retired")
                self.assertEqual(report["support"]["retirement"], retirement())
                self.assertEqual(report["enrollment"], "enrolled")
                self.assertNotIn("matrix", report)
                execute.assert_not_called()

    def test_compatibility_execution_uses_the_same_decision_before_and_after_retirement(
        self,
    ):
        with CompatibilityFixture().prepared() as fixture:
            fixture.support["retirements"][RELEASE] = retirement()
            with patch.object(support, "now", return_value=support.timestamp(BEFORE)):
                code, before = fixture.compatibility()
            self.assertEqual(code, 0, before)
            self.assertEqual(before["support"]["status"], "supported")
            count = len(fixture.commands)
            with patch.object(
                support, "now", return_value=support.timestamp(EFFECTIVE)
            ):
                code, after = fixture.compatibility()
            self.assertEqual(code, 1, after)
            self.assertEqual(after["support"]["status"], "retired")
            self.assertEqual(len(fixture.commands), count)

    def test_retirement_taking_effect_during_planning_cannot_return_a_usable_plan(self):
        self.support["retirements"][RELEASE] = retirement()
        with patch.object(
            support,
            "now",
            side_effect=[support.timestamp(BEFORE), support.timestamp(EFFECTIVE)],
        ):
            code, report = self.run_policy("ci", str(self.root), "--project", "example")
        self.assertEqual(code, 1, report)
        self.assertEqual(report["support"]["status"], "retired")
        self.assertNotIn("matrix", report)
        self.assertNotIn("compatibilityMatrix", report)

    def test_invalid_selection_and_uninspectable_support_have_distinct_reports(self):
        self.declare(policy_version="main")
        code, invalid = self.run_policy("ci", str(self.root), "--project", "example")
        self.assertEqual(code, 2, invalid)
        self.assertEqual(invalid["selectionStatus"], "invalid")
        self.declare(policy_version=RELEASE)
        root = self.write_records()
        (root / "policy/support.json").unlink()
        code, unknown = invoke(
            "--policy-root", str(root), "ci", str(self.root), "--project", "example"
        )
        self.assertEqual(code, 2, unknown)
        self.assertEqual(unknown["selectionStatus"], "unknown")

    def test_support_decisions_change_record_identity(
        self,
    ):
        self.write_records()
        before = records.identity(self.config, self.pins, SOURCE)
        self.support["retirements"][RELEASE] = retirement()
        after = records.identity(self.config, self.pins, SOURCE)
        self.assertNotEqual(before["digest"], after["digest"])

    def test_malformed_decisions_cannot_supply_support(self):
        root = self.write_records()
        path = root / "policy/support.json"
        cases = [
            None,
            {},
            {"schemaVersion": True, "retirements": {}},
            {"schemaVersion": 1, "retirements": []},
        ]
        for key, value in [
            ("migrationStartsAt", EFFECTIVE),
            ("migrationStartsAt", AFTER),
            ("migrationStartsAt", "2030-01-01"),
            ("retiresAt", "invalid"),
            ("decision", ""),
            ("decision", "http://example.com/decision"),
            ("reason", ""),
        ]:
            cases.append(
                {
                    "schemaVersion": 1,
                    "retirements": {RELEASE: {**retirement(), key: value}},
                }
            )
        for missing in retirement():
            cases.append(
                {
                    "schemaVersion": 1,
                    "retirements": {
                        RELEASE: {
                            key: value
                            for key, value in retirement().items()
                            if key != missing
                        }
                    },
                }
            )
        for record in cases:
            with self.subTest(record=record):
                path.write_text(json.dumps(record))
                code, report = invoke("--policy-root", str(root), "validate")
                self.assertEqual(code, 2, report)
                self.assertEqual(report["selectionStatus"], "unknown")
        path.write_text('{"schemaVersion":1,"retirements":{},"retirements":{}}')
        self.assertEqual(invoke("--policy-root", str(root), "validate")[0], 2)

    def test_audit_rechecks_earlier_members_after_later_members_finish(self):
        with AuditFixture().prepared() as fixture:
            fixture.add_member("beta", RELEASE)
            fixture.support["retirements"][RELEASE] = retirement()
            current_time = support.timestamp(BEFORE)
            execute = fixture.process

            def finish_later_member(command, **kwargs):
                nonlocal current_time
                response = execute(command, **kwargs)
                if command[command.index("--project") + 1] == "beta":
                    current_time = support.timestamp(EFFECTIVE)
                return response

            fixture.process = finish_later_member
            with patch.object(support, "now", side_effect=lambda: current_time):
                code, report = fixture.audit()
            self.assertEqual(code, 1, report)
            earlier = next(
                member
                for member in report["projects"]
                if member["project"] == "example"
            )
            self.assertEqual(earlier["selectionStatus"], "retired")
            self.assertEqual(earlier["status"], "fail")
            self.assertIn("migration period has ended", " ".join(earlier["issues"]))

    def test_actual_hosted_planning_adapter_stops_at_effective_retirement(self):
        self.support["retirements"][RELEASE] = retirement()
        root = self.write_records()
        workspace = self.root.parent
        (workspace / "project").symlink_to(self.root, target_is_directory=True)
        (workspace / "policy-state").symlink_to(root, target_is_directory=True)
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        steps = [
            next(
                step
                for step in workflow["jobs"]["records"]["steps"]
                if step.get("id") == "ci"
            ),
            next(
                step
                for step in workflow["jobs"]["policy"]["steps"]
                if step.get("name") == "Recheck policy support after execution"
            ),
        ]
        stub = workspace / "nix"
        stub.write_text(
            f"#!{sys.executable}\nimport os, sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom unittest.mock import patch\nfrom tools import policy, support\nwith patch.object(support, 'now', return_value=support.timestamp(os.environ['SUPPORT_TEST_NOW'])):\n    sys.exit(policy.main(sys.argv[sys.argv.index('--') + 1:]))\n"
        )
        stub.chmod(0o755)
        output = workspace / "outputs"
        for step in steps:
            for instant, expected in [(BEFORE, 0), (EFFECTIVE, 1)]:
                output.write_text("")
                process = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                    cwd=workspace,
                    env={
                        **os.environ,
                        "PATH": f"{workspace}:{os.environ['PATH']}",
                        "PROJECT": "example",
                        "WORKFLOW_INPUTS": json.dumps(
                            self.workflow["jobs"]["policy"]["with"]
                        ),
                        "GITHUB_OUTPUT": str(output),
                        "SUPPORT_TEST_NOW": instant,
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(process.returncode, expected, process.stderr)
                report = json.loads(
                    (
                        workspace
                        / ("ci.json" if step.get("id") == "ci" else "support.json")
                    ).read_text()
                )
                self.assertEqual(
                    report["selectionStatus"], "retired" if expected else "supported"
                )
                if expected:
                    self.assertEqual(output.read_text(), "")
