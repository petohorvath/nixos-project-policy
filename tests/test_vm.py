"""VM tests are discovered from `legacyPackages.<system>.vmTests` and built."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import yaml

from tests.fixtures.cases import RepoTestCase
from tools import policy


class VmTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.system = "x86_64-linux"
        self.vm_tests = []
        self.failing = set()
        self.discovery_status = 0
        self.commands = []
        self.enterContext(
            patch.object(policy.subprocess, "run", side_effect=self.run_command)
        )

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if command[:2] == ["nix", "eval"]:
            flake = kwargs["env"][policy.vm.FLAKE_VARIABLE]
            self.assertEqual(flake, f"path:{self.root.resolve()}")
            if self.discovery_status:
                raise subprocess.CalledProcessError(self.discovery_status, command)
            output = json.dumps({"system": self.system, "names": self.vm_tests})
            return subprocess.CompletedProcess(command, 0, output)
        if command[:2] == ["nix", "build"]:
            name = command[-1].rsplit(".vmTests.", 1)[1].strip('"')
            return subprocess.CompletedProcess(command, int(name in self.failing))
        raise AssertionError(command)

    def vm(self, *options):
        return self.run_policy("vm", str(self.root), *options)

    def builds(self):
        return [command[-1] for command in self.commands if command[1] == "build"]

    def target(self, name):
        root = self.root.resolve()
        return f'path:{root}#legacyPackages.{self.system}.vmTests."{name}"'

    def test_missing_or_empty_vm_tests_are_not_applicable(self):
        code, report = self.vm()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "not-applicable")
        self.assertEqual(report["vmTests"], [])
        self.assertEqual(self.builds(), [])

    def test_every_entry_is_built_on_the_host_system(self):
        self.system = "aarch64-linux"
        self.vm_tests = ["upgrade", "boot", "with.dot"]
        code, report = self.vm()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["system"], "aarch64-linux")
        self.assertEqual(report["vmTests"], ["boot", "upgrade", "with.dot"])
        self.assertEqual(report["failed"], [])
        self.assertEqual(
            self.builds(), [self.target(name) for name in report["vmTests"]]
        )
        for command in self.commands[1:]:
            self.assertIn("--no-update-lock-file", command)

    def test_failing_entry_is_named_and_later_entries_still_build(self):
        self.vm_tests = ["alpha", "beta", "gamma"]
        self.failing = {"alpha"}
        code, report = self.vm()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["failed"], ["alpha"])
        self.assertEqual(self.builds(), [self.target(name) for name in self.vm_tests])

    def test_discovery_failure_is_an_error(self):
        self.discovery_status = 1
        code, report = self.vm()
        self.assertEqual(code, 2, report)
        self.assertEqual(report["status"], "error")
        self.assertEqual(self.builds(), [])

    def test_vm_takes_no_project_name(self):
        self.vm_tests = ["boot"]
        code, report = self.vm()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        with (
            patch("sys.stderr"),
            self.assertRaises(SystemExit) as raised,
        ):
            self.vm("--project", "example")
        self.assertEqual(raised.exception.code, 2)


class VmWorkflowTests(RepoTestCase):
    def test_workflow_vm_job_runs_the_checker_on_the_repo_checkout(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        self.assertNotIn("vm_targets", workflow["on"]["workflow_call"]["inputs"])
        self.assertNotIn("vm_architecture", workflow["on"]["workflow_call"]["inputs"])
        job = workflow["jobs"]["vm"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        script = next(
            step["run"]
            for step in job["steps"]
            if step.get("name") == "Build discovered VM tests"
        )
        workspace = Path(self.temp.name)
        (workspace / "repo").symlink_to(self.root, target_is_directory=True)
        stub = workspace / "nix"
        stub.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "from pathlib import Path\n"
            "if sys.argv[1] == 'run':\n"
            f"    os.execv(sys.executable, [sys.executable, {str(policy.SOURCE_ROOT / 'tools/policy.py')!r}, *sys.argv[sys.argv.index('--') + 1:]])\n"
            "with Path(os.environ['COMMAND_LOG']).open('a') as stream:\n"
            "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1] == 'eval':\n"
            "    names = json.loads(os.environ['VM_TESTS'])\n"
            "    print(json.dumps({'system': 'x86_64-linux', 'names': names}))\n"
            "    sys.exit(0)\n"
            "sys.exit(int(os.environ['BUILD_STATUS']))\n"
        )
        stub.chmod(0o755)
        log = workspace / "commands.jsonl"
        for names, build_status, code, status in [
            ([], 0, 0, "not-applicable"),
            (["boot", "upgrade"], 0, 0, "pass"),
            (["boot"], 1, 1, "fail"),
        ]:
            with self.subTest(names=names, build_status=build_status):
                log.write_text("")
                process = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", script],
                    cwd=workspace,
                    env={
                        **os.environ,
                        "PATH": f"{workspace}:{os.environ['PATH']}",
                        "VM_TESTS": json.dumps(names),
                        "BUILD_STATUS": str(build_status),
                        "COMMAND_LOG": str(log),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(process.returncode, code, process.stderr)
                self.assertEqual(json.loads(process.stdout)["status"], status)
                builds = [
                    json.loads(line)
                    for line in log.read_text().splitlines()
                    if json.loads(line)[0] == "build"
                ]
                self.assertEqual(len(builds), len(names))
