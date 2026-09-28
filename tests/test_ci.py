"""`ci PATH` plans the reusable workflow's jobs and required statuses."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from tests.fixtures.cases import RepoTestCase
from tools import policy


TWO_SYSTEMS = [
    "Policy / Check (x86_64-linux)",
    "Policy / Check (aarch64-linux)",
    "Policy / Tests (locked, x86_64-linux)",
    "Policy / Tests (locked, aarch64-linux)",
    "Policy / Tests (stable, x86_64-linux)",
    "Policy / Tests (stable, aarch64-linux)",
    "Policy / Tests (unstable, x86_64-linux)",
    "Policy / Tests (unstable, aarch64-linux)",
]
RUNNERS = {"x86_64-linux": "ubuntu-24.04", "aarch64-linux": "ubuntu-24.04-arm"}


def load(path):
    return yaml.load((policy.SOURCE_ROOT / path).read_text(), Loader=yaml.BaseLoader)


class CiTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.vm_tests = []
        self.discovery_status = 0
        self.discovered = []
        self.enterContext(
            patch.object(policy.subprocess, "run", side_effect=self.run_command)
        )

    def run_command(self, command, **kwargs):
        if command[:2] != ["nix", "eval"]:
            raise AssertionError(command)
        env = kwargs["env"]
        self.assertEqual(env[policy.vm.FLAKE_VARIABLE], f"path:{self.root.resolve()}")
        self.discovered.append(env[policy.vm.SYSTEM_VARIABLE])
        if self.discovery_status:
            raise subprocess.CalledProcessError(self.discovery_status, command)
        output = json.dumps({"system": "x86_64-linux", "names": self.vm_tests})
        return subprocess.CompletedProcess(command, 0, output)

    def ci(self, *options):
        return self.run_policy("ci", str(self.root), *options)

    def test_default_systems_without_vm_tests_require_exactly_eight_statuses(self):
        code, report = self.ci()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "planned")
        self.assertEqual(report["systems"], ["x86_64-linux", "aarch64-linux"])
        self.assertEqual(report["requiredChecks"], TWO_SYSTEMS)
        self.assertFalse(report["vmJob"])
        self.assertEqual(
            report["checkMatrix"]["include"],
            [{"system": system, "runner": RUNNERS[system]} for system in RUNNERS],
        )
        self.assertEqual(
            report["testMatrix"]["include"],
            [
                {"nixpkgs": mode, "system": system, "runner": RUNNERS[system]}
                for mode in ["locked", "stable", "unstable"]
                for system in RUNNERS
            ],
        )
        self.assertEqual(self.discovered, ["x86_64-linux"])

    def test_custom_systems_select_jobs_and_runners(self):
        for systems, runners in [
            (["x86_64-linux"], ["ubuntu-24.04"]),
            (["aarch64-linux"], ["ubuntu-24.04-arm"]),
            (["riscv64-linux"], [["self-hosted", "riscv64-linux"]]),
        ]:
            with self.subTest(systems=systems):
                code, report = self.ci("--systems", json.dumps(systems))
                self.assertEqual(code, 0, report)
                (system,) = systems
                self.assertEqual(
                    report["requiredChecks"],
                    [
                        f"Policy / Check ({system})",
                        f"Policy / Tests (locked, {system})",
                        f"Policy / Tests (stable, {system})",
                        f"Policy / Tests (unstable, {system})",
                    ],
                )
                jobs = (
                    report["checkMatrix"]["include"] + report["testMatrix"]["include"]
                )
                self.assertEqual(
                    {json.dumps(job["runner"]) for job in jobs},
                    {json.dumps(runners[0])},
                )

    def test_invalid_systems_fail_before_any_nix_call(self):
        for systems in [
            "[",
            "null",
            '"x86_64-linux"',
            "{}",
            "[]",
            "[null]",
            '[""]',
            '["x86_64-linux", "x86_64-linux"]',
            '["../linux"]',
            '["x86_64 linux"]',
        ]:
            with self.subTest(systems=systems):
                code, report = self.ci("--systems", systems)
                self.assertEqual(code, 2, report)
                self.assertEqual(report["status"], "error")
                self.assertIn("systems", report["error"])
                self.assertNotIn("requiredChecks", report)
        self.assertEqual(self.discovered, [])

    def test_vm_tests_add_the_vm_job_on_x86_64_linux_for_any_systems(self):
        self.vm_tests = ["boot", "upgrade"]
        for systems in [None, '["aarch64-linux"]']:
            with self.subTest(systems=systems):
                self.discovered.clear()
                options = () if systems is None else ("--systems", systems)
                code, report = self.ci(*options)
                self.assertEqual(code, 0, report)
                self.assertTrue(report["vmJob"])
                self.assertEqual(report["vmTests"], ["boot", "upgrade"])
                self.assertEqual(report["requiredChecks"][-1], "Policy / VM tests")
                self.assertEqual(report["requiredChecks"].count("Policy / VM tests"), 1)
                self.assertEqual(self.discovered, ["x86_64-linux"])
        code, report = self.ci()
        self.assertEqual(report["requiredChecks"], [*TWO_SYSTEMS, "Policy / VM tests"])

    def test_vm_discovery_failure_is_an_error(self):
        self.discovery_status = 1
        code, report = self.ci()
        self.assertEqual(code, 2, report)
        self.assertNotIn("requiredChecks", report)

    def test_ci_takes_no_caller_settings(self):
        code, report = self.ci()
        self.assertEqual(code, 0, report)
        for field in ["project", "policyVersion", "enrollment", "memberSettings"]:
            self.assertNotIn(field, report)
        for options in [("--project", "example"), ("--inputs-json", "{}")]:
            with (
                self.subTest(options=options),
                patch("sys.stderr"),
                self.assertRaises(SystemExit) as raised,
            ):
                self.ci(*options)
            self.assertEqual(raised.exception.code, 2)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load(".github/workflows/check.yml")
        self.jobs = self.workflow["jobs"]

    def test_systems_is_the_only_input_and_defaults_to_both_linux_systems(self):
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(set(inputs), {"systems"})
        self.assertEqual(
            json.loads(inputs["systems"]["default"]),
            ["x86_64-linux", "aarch64-linux"],
        )
        self.assertEqual(inputs["systems"]["type"], "string")

    def test_jobs_run_the_checker_from_the_called_workflow_commit(self):
        self.assertEqual(set(self.jobs), {"plan", "check", "tests", "vm"})
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                checkouts = {
                    step["with"]["path"]: step["with"]
                    for step in job["steps"]
                    if step.get("uses", "").startswith("actions/checkout@")
                }
                self.assertEqual(set(checkouts), {"policy", "repo"})
                self.assertEqual(
                    checkouts["policy"]["repository"], "${{ job.workflow_repository }}"
                )
                self.assertEqual(checkouts["policy"]["ref"], "${{ job.workflow_sha }}")
                self.assertNotIn("repository", checkouts["repo"])
                self.assertNotIn("ref", checkouts["repo"])
                runs = [step["run"] for step in job["steps"] if "run" in step]
                self.assertTrue(runs)
                for run in runs:
                    for line in run.splitlines():
                        if "nix run" in line:
                            self.assertRegex(
                                line, r"nix run --no-update-lock-file \./policy -- "
                            )
        check = next(
            step["run"] for step in self.jobs["check"]["steps"] if "run" in step
        )
        self.assertEqual(
            check.strip(), "nix run --no-update-lock-file ./policy -- check ./repo"
        )

    def test_check_job_fetches_the_full_history_of_the_repo(self):
        # Reason: a shallow checkout has no release tag, so check could not
        # compare public outputs with the last release.
        [checkout] = [
            step["with"]
            for step in self.jobs["check"]["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
            and step["with"]["path"] == "repo"
        ]
        self.assertEqual(checkout["fetch-depth"], "0")

    def test_job_matrices_and_vm_gate_come_from_the_plan_job(self):
        outputs = self.jobs["plan"]["outputs"]
        for name, output in [("check", "check_matrix"), ("tests", "test_matrix")]:
            with self.subTest(job=name):
                job = self.jobs[name]
                self.assertEqual(job["needs"], "plan")
                self.assertEqual(job["strategy"]["fail-fast"], "false")
                self.assertEqual(
                    job["strategy"]["matrix"],
                    f"${{{{ fromJSON(needs.plan.outputs.{output}) }}}}",
                )
                self.assertEqual(
                    outputs[output], f"${{{{ steps.ci.outputs.{output} }}}}"
                )
                self.assertEqual(job["runs-on"], "${{ matrix.runner }}")
                self.assertNotIn("if", job)
        vm = self.jobs["vm"]
        self.assertEqual(vm["needs"], "plan")
        self.assertEqual(vm["if"], "needs.plan.outputs.vm_job == 'true'")
        self.assertEqual(outputs["vm_job"], "${{ steps.ci.outputs.vm_job }}")
        self.assertEqual(vm["runs-on"], "ubuntu-24.04")
        install = next(
            step for step in vm["steps"] if "install-nix-action" in step.get("uses", "")
        )
        self.assertEqual(install["with"]["enable_kvm"], "true")

    def test_plan_step_outputs_match_ci_and_name_the_required_statuses(self):
        step = next(
            step for step in self.jobs["plan"]["steps"] if step.get("id") == "ci"
        )
        self.assertEqual(step["env"], {"SYSTEMS": "${{ inputs.systems }}"})
        caller = load("templates/policy-caller.yml")["jobs"]["policy"]
        default = self.workflow["on"]["workflow_call"]["inputs"]["systems"]["default"]
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "repo").mkdir()
            stub = workspace / "nix"
            stub.write_text(
                f"#!{sys.executable}\nimport json, os, sys\n"
                "if sys.argv[1] == 'run':\n"
                f"    os.execv(sys.executable, [sys.executable, {str(policy.SOURCE_ROOT / 'tools/policy.py')!r}, *sys.argv[sys.argv.index('--') + 1:]])\n"
                "if sys.argv[1] == 'eval':\n"
                "    names = json.loads(os.environ['VM_TESTS'])\n"
                "    print(json.dumps({'system': 'x86_64-linux', 'names': names}))\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n"
            )
            stub.chmod(0o755)
            output = workspace / "output"
            summary = workspace / "summary"
            for systems, vm_tests, succeeds in [
                (default, [], True),
                ('["x86_64-linux"]', ["boot"], True),
                ('["riscv64-linux", "aarch64-linux"]', [], True),
                ("[]", [], False),
                ('"x86_64-linux"', [], False),
            ]:
                with self.subTest(systems=systems, vm_tests=vm_tests):
                    output.write_text("")
                    summary.write_text("")
                    process = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                        cwd=workspace,
                        env={
                            **os.environ,
                            "PATH": f"{workspace}:{os.environ['PATH']}",
                            "SYSTEMS": systems,
                            "VM_TESTS": json.dumps(vm_tests),
                            "GITHUB_OUTPUT": str(output),
                            "GITHUB_STEP_SUMMARY": str(summary),
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if not succeeds:
                        self.assertNotEqual(process.returncode, 0)
                        self.assertEqual(output.read_text(), "")
                        continue
                    self.assertEqual(process.returncode, 0, process.stderr)
                    report = json.loads((workspace / "ci.json").read_text())
                    outputs = dict(
                        line.split("=", 1) for line in output.read_text().splitlines()
                    )
                    self.assertEqual(
                        set(outputs), {"check_matrix", "test_matrix", "vm_job"}
                    )
                    self.assertEqual(
                        json.loads(outputs["check_matrix"]), report["checkMatrix"]
                    )
                    self.assertEqual(
                        json.loads(outputs["test_matrix"]), report["testMatrix"]
                    )
                    self.assertEqual(outputs["vm_job"], "true" if vm_tests else "false")
                    self.assertEqual(
                        [job["system"] for job in report["checkMatrix"]["include"]],
                        json.loads(systems),
                    )
                    statuses = self.statuses(caller["name"], outputs)
                    self.assertEqual(statuses, report["requiredChecks"])
                    for status in statuses:
                        self.assertIn(f"`{status}`", summary.read_text())

    def statuses(self, caller, outputs):
        """Expand the workflow's job names over the planned matrices."""

        def expand(name, values):
            return re.sub(
                r"\$\{\{ matrix\.(\w+) \}\}", lambda match: values[match[1]], name
            )

        names = [
            expand(self.jobs[job]["name"], values)
            for job, output in [("check", "check_matrix"), ("tests", "test_matrix")]
            for values in json.loads(outputs[output])["include"]
        ]
        if outputs["vm_job"] == "true":
            names.append(self.jobs["vm"]["name"])
        return [f"{caller} / {name}" for name in names]

    def test_caller_template_is_the_minimal_caller_in_the_readme(self):
        caller = load("templates/policy-caller.yml")["jobs"]["policy"]
        self.assertEqual(
            caller,
            {
                "name": "Policy",
                "uses": "petohorvath/nixos-project-policy/.github/workflows/check.yml@v0.5",
            },
        )
        document = (policy.SOURCE_ROOT / "README.md").read_text()
        example = re.search(r"```yaml\n(.*?)```", document, re.DOTALL)[1]
        self.assertEqual(
            yaml.load(example, Loader=yaml.BaseLoader)["jobs"]["policy"], caller
        )
