"""Audit subjects with controlled release and checker process responses."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from tests.fixtures.cli import invoke
from tests.fixtures.data import POLICY_REPO, RELEASE, REQUIRED_CHECKS, SOURCE
from tests.fixtures.projects import ProjectFixture
from tests.fixtures.services import published
from tools import policy, records, releases


def checked_process(command, **kwargs):
    arguments = command[command.index("--") + 1 :]
    root = Path(arguments[arguments.index("check") + 1])
    record_root = Path(arguments[arguments.index("--policy-root") + 1])
    name = arguments[arguments.index("--project") + 1]
    _, _, _, version = policy.declarations.discover(root, POLICY_REPO)
    if version == RELEASE:
        code, report = invoke(*arguments)
    else:
        config, pins = policy.load_policy(record_root)
        report = {
            "project": name,
            "policyVersion": version,
            "checkerVersion": version,
            "revision": SOURCE,
            "policyRecordsRevision": SOURCE,
            "policyRecordsDigest": records.digest(config, pins, legacy=True),
            "status": "pass",
            "issues": [],
            "dependencies": [],
            "requiredChecks": config["projects"][name]["requiredChecks"],
        }
        code = 0
    return subprocess.CompletedProcess(
        command,
        code,
        json.dumps(report) if code != 2 else "",
        json.dumps(report) if code == 2 else "",
    )


class AuditFixture(ProjectFixture):
    def prepare(self):
        super().prepare()
        self.config["projects"]["example"]["requiredChecks"] = list(REQUIRED_CHECKS)
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
        if releases.version_at_least(version, (0, 4, 0)):
            caller["with"]["required_architectures"] = '["x86_64-linux"]'
        (root / ".github/workflows/policy.yml").write_text(json.dumps(workflow))
        self.members[name] = f"owner/{name}"
        self.config["projects"][name] = {
            "repository": f"owner/{name}",
            "adopted": True,
            "policyVersion": version,
            "vmTargets": [],
            "requiredArchitectures": ["x86_64-linux"],
            "requiredChecks": ["Historical / Release-specific tests"],
        }
        return root
