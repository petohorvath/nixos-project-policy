"""Committed member repositories for packaged checks."""

import copy
import json
import shutil

from tests.fixtures.data import POLICY_REPO, RELEASE
from tests.fixtures.process import commit, isolated_git
from tests.fixtures.projects import ProjectFixture


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


class FamilyFixture(ProjectFixture):
    def prepare(self):
        super().prepare()
        self.workspace = self.root.parent / "members"
        self.workspace.mkdir()
        original = self.root
        self.root = self.workspace / "example"
        original.rename(self.root)
        self.resources.enter_context(isolated_git(self.workspace.parent))
        self.roots = {"example": self.root}
        alpha = self.workspace / "alpha"
        shutil.copytree(self.root, alpha)
        self.roots["alpha"] = alpha
        self.repos.append("owner/alpha")
        self.declaration(
            "alpha",
            RELEASE,
            required_architectures='["aarch64-linux"]',
            vm_targets='["vm-test"]',
            additional_required_checks='["Member / Extra"]',
        )
        for root in self.roots.values():
            self.commit(root)

    commit = staticmethod(commit)

    def declaration(self, name, version, **settings):
        root = self.roots[name]
        workflow = copy.deepcopy(self.workflow)
        job = workflow["jobs"]["policy"]
        job["uses"] = f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
        job["with"] = {"project": name, "policy_version": version}
        job["with"].update(
            {
                "required_architectures": '["x86_64-linux", "aarch64-linux"]',
                **settings,
            }
        )
        write_json(root / ".github/workflows/policy.yml", workflow)
        for file in ("AGENTS.md", "CONTRIBUTING.md"):
            (root / file).write_text(
                f"[Rules](https://github.com/{POLICY_REPO}/blob/{version}/POLICY.md)\n"
            )
