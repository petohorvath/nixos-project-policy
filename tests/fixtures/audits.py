"""Audit subjects with controlled release and checker process responses."""

import copy
import json
import shutil
import subprocess
from unittest.mock import patch

from tests.fixtures.cli import invoke
from tests.fixtures.data import POLICY_REPO, SOURCE
from tests.fixtures.projects import ProjectFixture
from tests.fixtures.services import published
from tools import policy, releases


def checked_process(command, **kwargs):
    arguments = command[command.index("--") + 1 :]
    code, report = invoke(*arguments)
    return subprocess.CompletedProcess(
        command,
        code,
        json.dumps(report) if code != 2 else "",
        json.dumps(report) if code == 2 else "",
    )


class AuditFixture(ProjectFixture):
    def prepare(self):
        super().prepare()
        self.release_requests = []
        self.commands = []
        self.process = checked_process
        self.release_response = published

        def lookup(path):
            self.release_requests.append(path)
            return self.release_response(path)

        def execute(command, **kwargs):
            self.commands.append(command)
            return self.process(command, **kwargs)

        for adapter in [
            patch.object(policy, "git_revision", return_value=SOURCE),
            patch.object(policy, "git_dirty", return_value=False),
            patch.object(releases, "public_get", side_effect=lookup),
            patch.object(releases.subprocess, "run", side_effect=execute),
        ]:
            self.resources.enter_context(adapter)

    def audit(self, *options):
        return self.run_policy("audit", str(self.root.parent), *options)

    def add_member(self, name, version):
        root = self.root.parent / name
        shutil.copytree(self.root, root)
        workflow = copy.deepcopy(self.workflow)
        caller = workflow["jobs"]["policy"]
        caller["uses"] = f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
        caller["with"] = {"project": name, "policy_version": version}
        caller["with"]["required_architectures"] = '["x86_64-linux"]'
        (root / ".github/workflows/policy.yml").write_text(json.dumps(workflow))
        self.members[name] = f"owner/{name}"
        for file in ("AGENTS.md", "CONTRIBUTING.md"):
            (root / file).write_text(
                f"[Rules](https://github.com/{POLICY_REPO}/blob/{version}/POLICY.md)\n"
            )
        return root
