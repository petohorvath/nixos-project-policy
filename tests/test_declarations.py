"""Public member declaration, planning, and execution boundaries."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import yaml

from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.data import POLICY_REPO, RELEASE, REQUIRED_CHECKS, VM_CHECK
from tools import policy


class DeclarationTests(ProjectTestCase):
    def test_actual_vm_workflow_step_uses_defaults_targets_and_failure_status(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        script = next(
            step["run"]
            for step in workflow["jobs"]["vm"]["steps"]
            if step.get("name") == "Run applicable VM suites"
        )
        workspace = Path(self.temp.name)
        (workspace / "project").symlink_to(self.root, target_is_directory=True)
        (workspace / "policy-state").symlink_to(
            workspace / "records", target_is_directory=True
        )
        stub = workspace / "nix"
        stub.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "from pathlib import Path\n"
            "if sys.argv[1] == 'run':\n"
            f"    os.execv(sys.executable, [sys.executable, {str(policy.SOURCE_ROOT / 'tools/policy.py')!r}, *sys.argv[sys.argv.index('--') + 1:]])\n"
            "with Path(os.environ['COMMAND_LOG']).open('a') as stream:\n"
            "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "sys.exit(int(os.environ['BUILD_STATUS']))\n"
        )
        stub.chmod(0o755)
        log = workspace / "commands.jsonl"
        for targets, build_status in [
            ([], 0),
            (["vm-tests", "vm-tests-unstable"], 0),
            (["vm-tests"], 1),
        ]:
            with self.subTest(targets=targets, build_status=build_status):
                self.declare(vm_targets=json.dumps(targets))
                self.assertEqual(self.run_policy("ci", "--project", "example")[0], 0)
                log.write_text("")
                process = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", script],
                    cwd=workspace,
                    env={
                        **os.environ,
                        "PATH": f"{workspace}:{os.environ['PATH']}",
                        "PROJECT": "example",
                        "BUILD_STATUS": str(build_status),
                        "COMMAND_LOG": str(log),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    process.returncode, 2 if build_status else 0, process.stderr
                )
                commands = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(
                    [command[-1] for command in commands],
                    [f"path:{self.root}#{target}" for target in targets],
                )
                if not build_status:
                    self.assertEqual(
                        json.loads(process.stdout)["status"],
                        "pass" if targets else "not-applicable",
                    )

    def test_pre_enrollment_checks_and_planning_do_not_change_records(self):
        self.config["projects"] = {}
        self.members.clear()
        for command in ["ci", "check", "vm"]:
            with self.subTest(command=command):
                code, report = self.run_policy(
                    command, str(self.root), "--project", "example"
                )
                self.assertEqual(code, 0, report)
                self.assertEqual(report["enrollment"], "not-enrolled")
                self.assertEqual(report["policyVersion"], RELEASE)
                self.assertEqual(report["memberSettings"]["vmTargets"], [])
                records = Path(self.temp.name) / "records/policy"
                self.assertEqual(
                    json.loads((records / "projects.json").read_text())["projects"], {}
                )
                self.assertEqual(
                    json.loads((records / "pins.json").read_text()), self.pins
                )

    def test_legacy_selection_and_settings_do_not_control_new_members(self):
        self.config["projects"]["example"].update(
            policyVersion="v0.3.0",
            adopted=False,
            requiredArchitectures=["x86_64-linux"],
            vmTargets=["legacy-vm"],
            additionalRequiredChecks=["Legacy gate"],
        )
        project = self.config["projects"]["example"]
        project["requiredChecks"] = policy.ci_plan(project, self.config["ci"])[
            "requiredChecks"
        ]
        for command in ["check", "ci", "vm"]:
            code, report = self.run_policy(
                command, str(self.root), "--project", "example"
            )
            self.assertEqual(code, 0, report)
            self.assertEqual(report["policyVersion"], RELEASE)
            self.assertEqual(
                report["memberSettings"],
                {
                    "requiredArchitectures": ["x86_64-linux", "aarch64-linux"],
                    "vmTargets": [],
                    "additionalRequiredChecks": [],
                },
            )

    def test_hosted_defaults_and_local_literals_produce_the_same_plan(self):
        local_code, local = self.run_policy("ci", "--project", "example")
        inputs = {
            **self.workflow["jobs"]["policy"]["with"],
            "vm_targets": "[]",
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

    def test_invalid_declarations_cannot_plan_check_or_execute(self):
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
                '["aarch64-darwin"]',
                ["x86_64-linux"],
                "${{ inputs.architectures }}",
            ]
        ]
        cases += [
            (field, value)
            for field in ["vm_targets", "additional_required_checks"]
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
            ("vm_targets", '["../escape"]'),
            ("vm_targets", '["target#other"]'),
            ("additional_required_checks", '["bad\\ncheck"]'),
            ("additional_required_checks", '[" leading"]'),
            ("policy_root", "candidate"),
            ("revision", "a" * 40),
            ("project", "other"),
        ]
        for field, value in cases:
            self.workflow = copy.deepcopy(original)
            self.declare(**{field: value})
            for command in ["ci", "check", "vm", "compatibility"]:
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
        code, report = self.run_policy("check", str(self.root), "--project", "example")
        self.assertEqual(code, 2, report)
        self.assertIn("found 2", report["error"])

    def test_matching_link_does_not_hide_a_contradictory_policy_link(self):
        for document in ["AGENTS.md", "CONTRIBUTING.md"]:
            with self.subTest(document=document):
                self.write(
                    document,
                    f"[Rules](https://github.com/{POLICY_REPO}/blob/{RELEASE}/POLICY.md)\n[Other](https://github.com/{POLICY_REPO}/blob/v0.3.0/POLICY.md)\n",
                )
                code, report = self.run_policy(
                    "check", str(self.root), "--project", "example"
                )
                self.assertEqual(code, 1, report)
                self.assertTrue(any(document in issue for issue in report["issues"]))

    def test_vm_execution_and_gates_follow_the_same_arm_only_declaration(self):
        self.config["projects"] = {}
        self.members.clear()
        self.declare(
            required_architectures='["aarch64-linux"]',
            vm_targets='["vm-tests", "vm-tests-unstable"]',
            additional_required_checks='["Integration"]',
        )
        code, plan = self.run_policy("ci", "--project", "example")
        self.assertEqual(code, 0, plan)
        self.assertEqual(
            plan["requiredChecks"],
            [check for check in REQUIRED_CHECKS if "x86_64" not in check]
            + [VM_CHECK, "Integration"],
        )
        self.assertEqual(
            {job["system"] for job in plan["matrix"]["include"]}, {"aarch64-linux"}
        )
        with (
            patch.object(policy.subprocess, "run") as execute,
            patch.object(policy, "git_revision", return_value=None),
        ):
            code, report = self.run_policy("vm", str(self.root), "--project", "example")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["targets"], plan["vmTargets"])
        self.assertEqual(
            [call.args[0][-1] for call in execute.call_args_list],
            [f"path:{self.root}#{target}" for target in plan["vmTargets"]],
        )
        with patch.object(
            policy.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "nix"),
        ):
            self.assertEqual(
                self.run_policy("vm", str(self.root), "--project", "example")[0], 2
            )
