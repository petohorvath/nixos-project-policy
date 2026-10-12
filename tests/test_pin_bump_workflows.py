"""The pin-bump workflows take their jobs and revisions from the helper."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from tests.fixtures.process import commit, git, isolated_git
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
GH_STUB = """#!{python}
import json, os, sys
entry = {{"arguments": sys.argv[1:]}}
if sys.argv[1:3] == ["release", "view"]:
    sys.exit(0 if os.environ.get("RELEASE_EXISTS") else 1)
if sys.argv[1:3] == ["release", "create"]:
    entry["stdin"] = sys.stdin.read()
with open(os.environ["GH_LOG"], "a") as stream:
    stream.write(json.dumps(entry) + "\\n")
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
        for task in ["stable", "unstable"]:
            with self.subTest(task=task):
                self.log.write_text("")
                process = self.run_step(script["run"], self.workspace, TASK=task)
                self.assertEqual(process.returncode, 0, process.stderr)
                test = ["run", "--no-update-lock-file", "./policy", "--", "test"]
                self.assertEqual(
                    self.commands(),
                    [
                        [*test, "./repo", "--nixpkgs", task],
                        [*test, "./repo", "--nixpkgs", task, "--vm"],
                    ],
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


class ReleaseWorkflowTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(isolated_git(self.workspace))
        gh = self.workspace / "bin/gh"
        gh.write_text(GH_STUB.format(python=sys.executable))
        gh.chmod(0o755)
        self.gh_log = self.workspace / "gh.jsonl"
        self.gh_log.write_text("")
        self.root = self.workspace / "policy"
        shutil.copytree(
            pin_bump.SOURCE_ROOT / "tools",
            self.root / "tools",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (self.root / "VERSION").write_text("0.5.0\n")
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## 0.5.0\n\n- Initial series.\n"
        )
        commit(self.root)
        git(self.root, "tag", "v0.5.0")
        self.origin = self.workspace / "origin.git"
        git(self.workspace, "init", "--quiet", "--bare", str(self.origin))
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "--quiet", "origin", "HEAD:refs/heads/main", "v0.5.0")
        (self.root / "VERSION").write_text("0.5.1\n")
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## 0.5.1\n\n- Update the [pins](data/pins.json).\n\n"
            "## 0.5.0\n\n- Initial series.\n"
        )
        self.revision = commit(self.root)

    def publish(self, **environment):
        script = step(
            "pin-bump-release.yml",
            "tag",
            "Tag the next patch release, move the series tag, and publish it",
        )
        return self.run_step(
            script["run"],
            self.root,
            GH_REPO="owner/policy",
            GH_LOG=str(self.gh_log),
            **environment,
        )

    def gh_calls(self):
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def origin_tag(self, name):
        return git(self.origin, "rev-list", "-n", "1", f"refs/tags/{name}")

    def test_merged_bump_is_tagged_and_published_with_its_changelog_section(self):
        process = self.publish()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.origin_tag("v0.5.1"), self.revision)
        self.assertEqual(self.origin_tag("v0.5"), self.revision)
        self.assertEqual(
            self.gh_calls(),
            [
                {"arguments": ["auth", "setup-git"]},
                {
                    "arguments": [
                        "release",
                        "create",
                        "v0.5.1",
                        "--title",
                        "v0.5.1",
                        "--verify-tag",
                        "--notes-file",
                        "-",
                    ],
                    "stdin": "- Update the [pins]"
                    "(https://github.com/owner/policy/blob/v0.5.1/data/pins.json).\n",
                },
            ],
        )

    def test_rerun_publishes_a_missing_release_without_moving_the_tag(self):
        git(self.root, "tag", "v0.5.1")
        git(self.root, "push", "--quiet", "origin", "v0.5.1")
        process = self.publish()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.origin_tag("v0.5"), self.revision)
        self.assertEqual(
            [call["arguments"][:2] for call in self.gh_calls()],
            [["auth", "setup-git"], ["release", "create"]],
        )

    def test_existing_release_is_not_created_again(self):
        process = self.publish(RELEASE_EXISTS="1")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.origin_tag("v0.5.1"), self.revision)
        self.assertEqual(self.gh_calls(), [{"arguments": ["auth", "setup-git"]}])


if __name__ == "__main__":
    unittest.main()
