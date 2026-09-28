"""The pin-bump workflows take their jobs and revisions from the helper."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from tools import pin_bump


WORKFLOWS = pin_bump.SOURCE_ROOT / ".github/workflows"
STUB = """#!{python}
import json, os, sys
with open(os.environ["COMMAND_LOG"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "develop":
    command = sys.argv[sys.argv.index("--command") + 1 :]
    if command[0] == "python":
        command[0] = sys.executable
    os.execv(command[0], command)
"""


def step(workflow, job, name):
    document = yaml.load((WORKFLOWS / workflow).read_text(), Loader=yaml.BaseLoader)
    return next(
        item for item in document["jobs"][job]["steps"] if item.get("name") == name
    )


class WorkflowTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        stub = self.workspace / "bin/nix"
        stub.parent.mkdir()
        stub.write_text(STUB.format(python=sys.executable))
        stub.chmod(0o755)
        self.log = self.workspace / "commands.jsonl"
        self.log.write_text("")
        self.output = self.workspace / "output"
        self.output.write_text("")

    def run_step(self, script, cwd, **environment):
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=cwd,
            env={
                **os.environ,
                "PATH": f"{self.workspace / 'bin'}:{os.environ['PATH']}",
                "COMMAND_LOG": str(self.log),
                "GITHUB_OUTPUT": str(self.output),
                **environment,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def outputs(self):
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines())

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]


class PinBumpTestsWorkflowTests(WorkflowTestCase):
    def test_matrix_comes_from_the_helper_for_the_bundled_repo_list(self):
        script = step("pin-bump-tests.yml", "plan", "List jobs for every listed repo")
        process = self.run_step(script["run"], pin_bump.SOURCE_ROOT)
        self.assertEqual(process.returncode, 0, process.stderr)
        outputs = self.outputs()
        repos = json.loads((pin_bump.SOURCE_ROOT / "data/repos.json").read_text())
        expected = pin_bump.matrix(repos["repos"])
        self.assertEqual(json.loads(outputs["matrix"]), expected)
        self.assertEqual(outputs["count"], str(len(expected["include"])))

    def test_each_job_runs_the_proposed_checker_on_the_repo_main(self):
        script = step(
            "pin-bump-tests.yml",
            "test",
            "Run the listed repo's tests with the proposed pins",
        )
        for task, expected in [
            ("stable", ["test", "./repo", "--nixpkgs", "stable"]),
            ("unstable", ["test", "./repo", "--nixpkgs", "unstable"]),
            ("vm", ["vm", "./repo"]),
        ]:
            with self.subTest(task=task):
                self.log.write_text("")
                process = self.run_step(script["run"], self.workspace, TASK=task)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(
                    self.commands(),
                    [["run", "--no-update-lock-file", "./policy", "--", *expected]],
                )


class ProposeWorkflowTests(WorkflowTestCase):
    def resolve(self, stable, unstable):
        script = step("pins.yml", "propose", "Resolve the proposed revisions")
        return self.run_step(
            script["run"],
            self.workspace,
            STABLE_REVISION=stable,
            UNSTABLE_REVISION=unstable,
        )

    def test_manual_revisions_must_be_supplied_together(self):
        for stable, unstable in [("a" * 40, ""), ("", "b" * 40), ("a" * 40, "B" * 40)]:
            with self.subTest(stable=stable, unstable=unstable):
                process = self.resolve(stable, unstable)
                self.assertEqual(process.returncode, 1, process.stdout)
                self.assertIn("Supply both", process.stdout)

    def test_manual_revisions_are_proposed_without_resolving_branches(self):
        process = self.resolve("a" * 40, "b" * 40)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.outputs(), {"stable": "a" * 40, "unstable": "b" * 40})


if __name__ == "__main__":
    unittest.main()
