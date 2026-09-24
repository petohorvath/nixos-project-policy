"""Member files and central records, independent of unittest lifecycles."""

import contextlib
import json
from pathlib import Path
import tempfile

from tests.fixtures.cli import invoke
from tests.fixtures.data import (
    PAIR,
    POLICY_REPO,
    RELEASE,
    lockfile,
)
from tools import policy


class ProjectFixture:
    @contextlib.contextmanager
    def prepared(self):
        with contextlib.ExitStack() as self.resources:
            self.prepare()
            yield self

    def prepare(self):
        self.temp = tempfile.TemporaryDirectory()
        self.resources.enter_context(self.temp)
        self.root = Path(self.temp.name) / "example"
        self.config = {
            "schemaVersion": 1,
            "policyRepository": POLICY_REPO,
            "ci": json.loads(
                (policy.SOURCE_ROOT / "policy/requirements.json").read_text()
            )["ci"],
        }
        self.pins = {
            "stableBranch": "nixos-26.05",
            "schemaVersion": 1,
            "approved": PAIR,
        }
        self.members = {"example": "owner/example"}
        self.config["_members"] = self.members
        self.write("flake.nix", "{}")
        self.write(".envrc", "use flake\n")
        self.write("LICENSE", "MIT")
        self.write("README.md", "# Example\n\nPurpose.\n")
        for file in ["CONTRIBUTING.md", "AGENTS.md"]:
            self.write(
                file,
                f"[Rules](https://github.com/{POLICY_REPO}/blob/{RELEASE}/POLICY.md)\n",
            )
        self.workflow = {
            "on": {
                "pull_request": {
                    "types": ["opened", "synchronize", "reopened", "edited"]
                }
            },
            "jobs": {
                "policy": {
                    "name": "Policy",
                    "uses": f"{POLICY_REPO}/.github/workflows/check.yml@{RELEASE}",
                    "with": {
                        "policy_version": RELEASE,
                        "project": "example",
                        "required_architectures": '["x86_64-linux", "aarch64-linux"]',
                    },
                }
            },
        }
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.write("flake.lock", json.dumps(lockfile()))

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def declare(self, **inputs):
        self.workflow["jobs"]["policy"]["with"].update(inputs)
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))

    def inspect(self):
        return self.run_policy("check", str(self.root), "--project", "example")[1]

    def write_records(self):
        records = Path(self.temp.name) / "records"
        (records / "policy").mkdir(parents=True, exist_ok=True)
        (records / "policy/pins.json").write_text(json.dumps(self.pins))
        (records / "policy/members.json").write_text(
            json.dumps({"schemaVersion": 1, "members": self.members})
        )
        return records

    def run_policy(self, *args):
        if args[0] == "ci" and (len(args) == 1 or args[1].startswith("--")):
            args = ("ci", str(self.root), *args[1:])
        records = self.write_records()
        return invoke("--policy-root", str(records), *args)


def enabled_enforcement(repository):
    return {
        f"{repository}/actions/permissions": {"enabled": True},
        f"{repository}/actions/workflows/policy.yml": {
            "path": ".github/workflows/policy.yml",
            "state": "active",
        },
    }
