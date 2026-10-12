"""`ci` plans the reusable workflow's jobs and required statuses."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

from tests.fixtures.cases import RepoTestCase
from tools import pin_bump, policy


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
    def ci(self, *options):
        return self.run_policy("ci", *options)

    def test_default_systems_require_exactly_eight_statuses(self):
        code, report = self.ci()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "planned")
        self.assertEqual(report["systems"], ["x86_64-linux", "aarch64-linux"])
        self.assertEqual(report["requiredChecks"], TWO_SYSTEMS)
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
        self.assertEqual(set(self.jobs), {"plan", "check", "tests"})
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                checkouts = {
                    step["with"]["path"]: step["with"]
                    for step in job["steps"]
                    if step.get("uses", "").startswith("actions/checkout@")
                }
                # The plan depends only on the systems, not on the repo.
                self.assertEqual(
                    set(checkouts),
                    {"policy"} if name == "plan" else {"policy", "repo"},
                )
                self.assertEqual(
                    checkouts["policy"]["repository"], "${{ job.workflow_repository }}"
                )
                self.assertEqual(checkouts["policy"]["ref"], "${{ job.workflow_sha }}")
                if "repo" in checkouts:
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

    def test_job_matrices_come_from_the_plan_job(self):
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
        self.assertEqual(set(outputs), {"check_matrix", "test_matrix"})

    def test_tests_job_keeps_kvm_enabled_for_vm_tests(self):
        install = next(
            step
            for step in self.jobs["tests"]["steps"]
            if "install-nix-action" in step.get("uses", "")
        )
        # The action enables KVM by default where the runner has it.
        self.assertEqual(install["with"].get("enable_kvm", "true"), "true")

    def test_plan_step_outputs_match_ci_and_name_the_required_statuses(self):
        step = next(
            step for step in self.jobs["plan"]["steps"] if step.get("id") == "ci"
        )
        self.assertEqual(step["env"], {"SYSTEMS": "${{ inputs.systems }}"})
        caller = load("templates/policy-caller.yml")["jobs"]["policy"]
        default = self.workflow["on"]["workflow_call"]["inputs"]["systems"]["default"]
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            stub = workspace / "nix"
            stub.write_text(
                f"#!{sys.executable}\nimport os, sys\n"
                "if sys.argv[1] == 'run':\n"
                f"    os.execv(sys.executable, [sys.executable, {str(policy.SOURCE_ROOT / 'tools/policy.py')!r}, *sys.argv[sys.argv.index('--') + 1:]])\n"
                "sys.exit(1)\n"
            )
            stub.chmod(0o755)
            output = workspace / "output"
            summary = workspace / "summary"
            for systems, succeeds in [
                (default, True),
                ('["x86_64-linux"]', True),
                ('["riscv64-linux", "aarch64-linux"]', True),
                ("[]", False),
                ('"x86_64-linux"', False),
            ]:
                with self.subTest(systems=systems):
                    output.write_text("")
                    summary.write_text("")
                    process = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                        cwd=workspace,
                        env={
                            **os.environ,
                            "PATH": f"{workspace}:{os.environ['PATH']}",
                            "SYSTEMS": systems,
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
                    self.assertEqual(set(outputs), {"check_matrix", "test_matrix"})
                    self.assertEqual(
                        json.loads(outputs["check_matrix"]), report["checkMatrix"]
                    )
                    self.assertEqual(
                        json.loads(outputs["test_matrix"]), report["testMatrix"]
                    )
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
        return [f"{caller} / {name}" for name in names]

    def test_caller_template_is_the_minimal_caller_in_the_readme(self):
        caller = load("templates/policy-caller.yml")["jobs"]["policy"]
        version = pin_bump.read_version(policy.SOURCE_ROOT)
        series_tag = pin_bump.series(version)["seriesTag"]
        self.assertEqual(
            caller,
            {
                "name": "Policy",
                "uses": f"petohorvath/nixos-project-policy/.github/workflows/check.yml@{series_tag}",
            },
        )
        document = (policy.SOURCE_ROOT / "README.md").read_text()
        example = re.search(r"```yaml\n(.*?)```", document, re.DOTALL)[1]
        self.assertEqual(
            yaml.load(example, Loader=yaml.BaseLoader)["jobs"]["policy"], caller
        )
