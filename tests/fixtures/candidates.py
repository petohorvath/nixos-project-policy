"""Committed member repositories and exact candidate record snapshots."""

import copy
import json
from pathlib import Path
import shutil

from tests.fixtures.data import NEW_PAIR, PAIR, POLICY_REPO, RELEASE
from tests.fixtures.process import commit, git, isolated_git
from tests.fixtures.projects import ProjectFixture
from tools import agreement, candidates, policy, records


def invalid_batch_plans(plan):
    """Alter captured subjects and coverage without relying on stale digests."""
    mutations = {
        "schema": lambda value: value.update(schemaVersion=True),
        "missing member": lambda value: value["members"].pop("example"),
        "malformed member": lambda value: value["members"].update(example=[]),
        "substituted member": lambda value: value["members"]["example"]["plan"].update(
            project="different"
        ),
        "substituted source": lambda value: value["members"]["example"]["plan"][
            "source"
        ].update(revision="f" * 40),
        "missing worker": lambda value: value["matrix"]["include"].pop(),
        "duplicate worker": lambda value: value["matrix"]["include"].append(
            value["matrix"]["include"][0]
        ),
        "forged worker": lambda value: value["matrix"]["include"][0].update(
            worker="example--different-linux"
        ),
        "forged job": lambda value: value["matrix"]["include"][0].update(
            job="Substituted candidate job"
        ),
        "malformed matrix": lambda value: value.update(matrix={"include": None}),
    }

    def substitute_runner(value):
        child = value["members"]["example"]["plan"]
        child["matrix"]["include"][0]["runner"] = "untrusted-runner"
        system = child["matrix"]["include"][0]["system"]
        for row in value["matrix"]["include"]:
            if row["project"] == "example" and row["system"] == system:
                row["runner"] = "untrusted-runner"

    def duplicate_native_worker(value):
        child = value["members"]["example"]["plan"]
        child["matrix"]["include"].append(child["matrix"]["include"][0])
        row = next(
            row for row in value["matrix"]["include"] if row["project"] == "example"
        )
        value["matrix"]["include"].append(row)

    mutations.update(
        {
            "native runner": substitute_runner,
            "native duplication": duplicate_native_worker,
        }
    )
    for name, mutate in mutations.items():
        value = copy.deepcopy(plan)
        mutate(value)
        for member in value["members"].values():
            if isinstance(member, dict) and member.get("status") == "planned":
                child = member["plan"]
                child["planDigest"] = candidates.digest(
                    {key: item for key, item in child.items() if key != "planDigest"}
                )
        value["planDigest"] = candidates.digest(
            {key: item for key, item in value.items() if key != "planDigest"}
        )
        yield name, value


class CandidateFixture(ProjectFixture):
    def prepare(self):
        super().prepare()
        self.roots = {"example": self.root}
        self.prepare_members()
        self.commit(self.root)
        self.baseline = self.write_records()
        self.commit(self.baseline)
        self.proposal = Path(self.temp.name) / "proposal"
        shutil.copytree(
            self.baseline, self.proposal, ignore=shutil.ignore_patterns(".git")
        )
        self.propose()
        self.commit(self.proposal)

    def prepare_members(self):
        lock = records.read_json(self.root / "flake.lock")
        lock["nodes"]["entry"]["inputs"].pop("nixpkgs-unstable")
        lock["nodes"].pop("rolling")
        candidates.write_json(self.root / "flake.lock", lock)

    commit = staticmethod(commit)

    def propose(self, *, status="candidate", approved=PAIR):
        pins = copy.deepcopy(self.pins)
        pins["approved"] = approved
        pins["batches"].append(
            {
                "id": "next",
                "status": status,
                "previous": PAIR,
                "pins": NEW_PAIR,
                "projects": {
                    name: policy.git_revision(root) for name, root in self.roots.items()
                },
            }
        )
        candidates.write_json(self.proposal / "policy/pins.json", pins)


class BatchFixture(CandidateFixture):
    def prepare_members(self):
        super().prepare_members()
        self.workspace = self.root.parent / "members"
        self.workspace.mkdir()
        original = self.root
        self.root = self.workspace / "example"
        original.rename(self.root)
        lock = records.read_json(self.root / "flake.lock")
        self.roots = {"example": self.root}
        self.locked = {}
        self.resources.enter_context(isolated_git(self.workspace.parent))
        for name in ("alpha", "legacy"):
            root = self.workspace / name
            shutil.copytree(self.root, root)
            self.roots[name] = root
            self.members[name] = f"owner/{name}"
            self.config["projects"][name] = {
                **copy.deepcopy(self.config["projects"]["example"]),
                "repository": f"owner/{name}",
            }
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
        self.declaration("legacy", "v0.3.0")
        self.config["projects"]["legacy"]["policyVersion"] = "v0.3.0"
        self.commit(self.roots["legacy"])
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
        candidates.write_json(self.root / "flake.lock", lock)

    def declaration(self, name, version, **settings):
        root = self.roots[name]
        workflow = copy.deepcopy(self.workflow)
        job = workflow["jobs"]["policy"]
        job["uses"] = f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
        job["with"] = {"project": name, "policy_version": version}
        if version == RELEASE:
            job["with"].update(
                {
                    "required_architectures": '["x86_64-linux", "aarch64-linux"]',
                    **settings,
                }
            )
        candidates.write_json(root / ".github/workflows/policy.yml", workflow)
        for name in ("AGENTS.md", "CONTRIBUTING.md"):
            (root / name).write_text(
                f"[Rules](https://github.com/{POLICY_REPO}/blob/{version}/POLICY.md)\n"
            )
