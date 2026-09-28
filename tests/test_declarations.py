"""Public member declaration, planning, and execution boundaries."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.data import POLICY_REPO, RELEASE
from tools import policy


class DeclarationTests(ProjectTestCase):
    def test_release_tags_accept_current_and_future_versions_only(self):
        for version in ["v0.4.0", "v0.4.1", "v0.10.0", "v1.0.0"]:
            policy.declarations.require_policy_version(version)
        for version in [
            "main",
            "v0.4",
            "0.4.0",
            "v00.4.0",
            "v0.4.0-rc.1",
            "v0.4.0+build",
        ]:
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(ValueError, "release tags"),
            ):
                policy.declarations.require_policy_version(version)

    def test_release_floor_rejects_old_selections_before_execution(self):
        for version in ["v0.0.0", "v0.1.1", "v0.2.9", "v0.3.99"]:
            self.workflow["jobs"]["policy"]["uses"] = (
                f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
            )
            self.declare(policy_version=version)
            for command, options in [
                ("ci", []),
                ("compatibility", ["--channel", "stable"]),
            ]:
                with self.subTest(version=version, command=command):
                    code, report = self.run_policy(
                        command, str(self.root), "--project", "example", *options
                    )
                    self.assertEqual(code, 2, report)
                    self.assertIn("v0.4.0 or later", report["error"])
                    self.assertNotIn("matrix", report)

    def test_any_named_system_can_be_selected_and_planned(self):
        for systems in [
            ["riscv64-linux"],
            ["aarch64-darwin"],
            ["x86_64-linux", "riscv64-linux"],
        ]:
            with self.subTest(systems=systems):
                self.declare(required_architectures=json.dumps(systems))
                code, report = self.run_policy("ci", "--project", "example")
                self.assertEqual(code, 0, report)
                self.assertEqual(
                    report["memberSettings"]["requiredArchitectures"], systems
                )
                jobs = (
                    report["matrix"]["include"]
                    + report["compatibilityMatrix"]["include"]
                )
                self.assertEqual(len(jobs), 4 * len(systems))
                self.assertEqual({job["system"] for job in jobs}, set(systems))
                for job in jobs:
                    runner = (
                        "ubuntu-24.04"
                        if job["system"] == "x86_64-linux"
                        else ["self-hosted", job["system"]]
                    )
                    self.assertEqual(job["runner"], runner)
                for system in systems:
                    self.assertIn(
                        f"Policy / Project tests ({system})", report["requiredChecks"]
                    )
                    for channel in ["stable", "unstable"]:
                        self.assertIn(
                            f"Policy / Compatibility ({channel}, {system})",
                            report["requiredChecks"],
                        )

    def test_pre_enrollment_checks_and_planning_do_not_change_data(self):
        self.repos.clear()
        for command in ["ci"]:
            with self.subTest(command=command):
                code, report = self.run_policy(
                    command, str(self.root), "--project", "example"
                )
                self.assertEqual(code, 0, report)
                self.assertEqual(report["enrollment"], "not-enrolled")
                self.assertEqual(report["policyVersion"], RELEASE)
                self.assertEqual(
                    report["memberSettings"]["additionalRequiredChecks"], []
                )
                data = Path(self.temp.name) / "data"
                self.assertEqual(
                    json.loads((data / "repos.json").read_text()), {"repos": []}
                )
                self.assertEqual(
                    json.loads((data / "pins.json").read_text()), self.pins
                )

    def test_hosted_defaults_and_local_literals_produce_the_same_plan(self):
        local_code, local = self.run_policy("ci", "--project", "example")
        inputs = {
            **self.workflow["jobs"]["policy"]["with"],
            "additional_required_checks": "[]",
        }
        code, hosted = self.run_policy(
            "ci", "--project", "example", "--inputs-json", json.dumps(inputs)
        )
        self.assertEqual(local_code, 0, local)
        self.assertEqual(code, 0, hosted)
        self.assertEqual(hosted, local)
        inputs["required_architectures"] = '["aarch64-linux"]'
        code, report = self.run_policy(
            "ci", "--project", "example", "--inputs-json", json.dumps(inputs)
        )
        self.assertEqual(code, 2, report)
        self.assertIn("disagree", report["error"])

    def test_invalid_declarations_cannot_plan_or_execute(self):
        original = copy.deepcopy(self.workflow)
        cases = [
            ("required_architectures", value)
            for value in [
                "[",
                "null",
                "true",
                "42",
                "{}",
                '"x86_64-linux"',
                "[]",
                "[null]",
                '[""]',
                '["x86_64-linux", "x86_64-linux"]',
                '["../darwin"]',
                ["x86_64-linux"],
                "${{ inputs.architectures }}",
            ]
        ]
        cases += [
            (field, value)
            for field in ["additional_required_checks"]
            for value in [
                "{",
                "null",
                "42",
                "{}",
                "[null]",
                '[""]',
                '["duplicate", "duplicate"]',
                "${{ inputs.targets }}",
                '["${{ github.sha }}"]',
            ]
        ]
        cases += [
            ("vm_targets", '["vm-tests"]'),
            ("vm_architecture", "x86_64-linux"),
            ("additional_required_checks", '["bad\\ncheck"]'),
            ("additional_required_checks", '[" leading"]'),
            ("policy_root", "candidate"),
            ("revision", "a" * 40),
            ("project", "other"),
        ]
        for field, value in cases:
            self.workflow = copy.deepcopy(original)
            self.declare(**{field: value})
            for command in ["ci", "compatibility"]:
                with self.subTest(field=field, value=value, command=command):
                    options = (
                        ["--channel", "stable"] if command == "compatibility" else []
                    )
                    with patch.object(policy.subprocess, "run") as execute:
                        code, report = self.run_policy(
                            command, str(self.root), "--project", "example", *options
                        )
                    self.assertEqual(code, 2, report)
                    self.assertNotIn("matrix", report)
                    execute.assert_not_called()

    def test_missing_multiple_and_duplicate_key_callers_fail(self):
        caller = self.root / ".github/workflows/policy.yml"
        original = caller.read_text()
        cases = [
            "{}",
            original.replace(
                '"jobs": {',
                '"jobs": {"duplicate": '
                + json.dumps(self.workflow["jobs"]["policy"])
                + ", ",
                1,
            ),
            original.replace(
                '"project": "example"', '"project": "other", "project": "example"'
            ),
        ]
        for source in cases:
            caller.write_text(source)
            code, report = self.run_policy("ci", "--project", "example")
            self.assertEqual(code, 2, report)
        caller.write_text(original)
        self.write(".github/workflows/second.yaml", original)
        code, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(code, 2, report)
        self.assertIn("found 2", report["error"])
