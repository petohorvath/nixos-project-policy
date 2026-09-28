"""Member files and bundled checker data, independent of unittest lifecycles."""

import contextlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from tests.fixtures.check_nix import CheckNix
from tests.fixtures.cli import invoke
from tests.fixtures.data import (
    PAIR,
    POLICY_REPO,
    RELEASE,
    lockfile,
)
from tools import policy, records


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
        self.pins = {"stableBranch": "nixos-26.05", **PAIR}
        self.repos = ["owner/example"]
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
        # `check` always evaluates outputs and probes the shell; answer those
        # Nix calls unless a scenario installs its own process fake.
        self.check_nix = CheckNix()
        self.resources.enter_context(
            patch.object(policy.subprocess, "run", side_effect=self.check_nix)
        )

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def declare(self, **inputs):
        self.workflow["jobs"]["policy"]["with"].update(inputs)
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))

    def inspect(self):
        return self.run_policy("check", str(self.root))[1]

    def write_data(self, directory=None):
        """Write the fixture's pins and repo list in the bundled data layout."""
        directory = directory or Path(self.temp.name) / "data"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pins.json").write_text(json.dumps(self.pins))
        (directory / "repos.json").write_text(json.dumps({"repos": self.repos}))
        return directory

    def run_policy(self, *args):
        """Invoke the CLI with the fixture's data in place of the bundled files."""
        if args[0] == "ci" and (len(args) == 1 or args[1].startswith("--")):
            args = ("ci", str(self.root), *args[1:])
        with patch.object(records, "DATA_ROOT", self.write_data()):
            return invoke(*args)
