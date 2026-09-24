"""Committed member repositories for packaged checks and integration agreement."""

import copy
import json
import shutil

from tests.fixtures.data import (
    POLICY_REPO,
    RELEASE,
)
from tests.fixtures.process import commit, git, isolated_git
from tests.fixtures.projects import ProjectFixture
from tools import agreement, policy, records


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


class FamilyFixture(ProjectFixture):
    def prepare(self):
        super().prepare()
        self.roots = {"example": self.root}
        self.prepare_members()
        self.commit(self.root)
        self.baseline = self.write_records()
        self.commit(self.baseline)

    commit = staticmethod(commit)

    def prepare_members(self):
        lock = records.read_json(self.root / "flake.lock")
        lock["nodes"]["entry"]["inputs"].pop("nixpkgs-unstable")
        lock["nodes"].pop("rolling")
        write_json(self.root / "flake.lock", lock)
        self.workspace = self.root.parent / "members"
        self.workspace.mkdir()
        original = self.root
        self.root = self.workspace / "example"
        original.rename(self.root)
        lock = records.read_json(self.root / "flake.lock")
        self.roots = {"example": self.root}
        self.locked = {}
        self.resources.enter_context(isolated_git(self.workspace.parent))
        for name in ("alpha", "beta"):
            root = self.workspace / name
            shutil.copytree(self.root, root)
            self.roots[name] = root
            self.members[name] = f"owner/{name}"
            self.declaration(name, RELEASE)
            self.commit(root)
            self.locked[name] = policy.git_revision(root)
        for name, root in self.roots.items():
            git(
                root,
                "config",
                "--global",
                f"url.{root.as_uri()}.insteadOf",
                f"https://github.com/owner/{name}.git",
            )
        (self.roots["beta"] / "README.md").write_text("# Updated member\n")
        self.commit(self.roots["beta"])
        self.declaration(
            "alpha",
            RELEASE,
            required_architectures='["aarch64-linux"]',
            vm_targets='["vm-test"]',
            additional_required_checks='["Member / Extra"]',
        )
        self.commit(self.roots["alpha"])
        self.workflow["jobs"]["integration"] = {
            "name": "Integration",
            "needs": "policy",
            "uses": f"{POLICY_REPO}/.github/workflows/agreement.yml@{RELEASE}",
            "with": {
                "project": "example",
                "policy_version": RELEASE,
                "project_revision": "${{ needs.policy.outputs.project_revision }}",
                "records_revision": "${{ needs.policy.outputs.records_revision }}",
            },
        }
        self.declare(additional_required_checks=json.dumps([agreement.GATE]))
        for name, revision in self.locked.items():
            lock["nodes"]["entry"]["inputs"][name] = name
            lock["nodes"][name] = {
                "locked": {
                    "type": "github",
                    "owner": "owner",
                    "repo": name,
                    "rev": revision,
                },
                "original": {"type": "github", "owner": "owner", "repo": name},
                "inputs": {"nixpkgs": ["nixpkgs"]},
            }
        write_json(self.root / "flake.lock", lock)

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
        for name in ("AGENTS.md", "CONTRIBUTING.md"):
            (root / name).write_text(
                f"[Rules](https://github.com/{POLICY_REPO}/blob/{version}/POLICY.md)\n"
            )
