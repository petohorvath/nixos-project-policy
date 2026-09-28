"""Committed member repositories for packaged checks."""

import json
import shutil

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
        for root in self.roots.values():
            self.commit(root)

    commit = staticmethod(commit)
