"""Host fixture: the built release package with controlled Nix commands.

The package is built from a copy whose bundled data holds fixture pins that
differ from the committed ones, so every report proves which data it used.
"""

import json
import os
from pathlib import Path
import shutil
import sys
import unittest


from tests.fixtures.family import FamilyFixture, write_json
from tests.fixtures.data import PAIR
from tests.fixtures.process import REAL_RUN
from tools import policy, records


class PackagedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(FamilyFixture().prepared())
        fixture = self.fixture
        self.workspace = fixture.workspace.parent
        authority = self.workspace / "authority"
        shutil.copytree(
            policy.SOURCE_ROOT,
            authority,
            ignore=shutil.ignore_patterns(
                ".git", ".direnv", "__pycache__", ".ruff_cache"
            ),
        )
        committed = records.read_json(policy.SOURCE_ROOT / "data/pins.json")
        self.assertNotEqual({channel: committed[channel] for channel in PAIR}, PAIR)
        fixture.write_data(authority / "data")
        self.authority = authority
        fixture.commit(authority)
        built = json.loads(
            self.command(
                [
                    "nix",
                    "build",
                    "--no-update-lock-file",
                    "--no-link",
                    "--json",
                    str(authority) + "#default",
                ]
            )
        )
        self.program = str(
            Path(built[0]["outputs"]["out"]) / "bin/nixos-project-policy"
        )
        self.config = self.workspace / "adapter.json"
        write_json(
            self.config,
            {
                "commands": str(self.workspace / "nix-commands.jsonl"),
                "system": "x86_64-linux",
            },
        )
        startup = self.workspace / "startup"
        startup.mkdir()
        (startup / "sitecustomize.py").write_text(
            "import runpy, sys\nsys.path.insert(0, "
            + repr(str(policy.SOURCE_ROOT))
            + ")\nrunpy.run_path("
            + repr(str(policy.SOURCE_ROOT / "tests/packaged_adapter.py"))
            + ")\n"
        )
        self.environment = {
            **os.environ,
            "PYTHONPATH": str(startup),
            "POLICY_PACKAGE_FIXTURE": str(self.config),
        }

    def command(
        self, arguments, *, binary=False, environment=None, cwd=None, expected=0
    ):
        result = REAL_RUN(
            arguments,
            text=not binary,
            capture_output=True,
            env=environment,
            cwd=cwd,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            expected,
            str(result.stdout)[-6000:] + str(result.stderr)[-12000:],
        )
        return result.stdout

    def call(self, *arguments, system="x86_64-linux"):
        result = json.loads(
            self.command(
                [self.program, *map(str, arguments)],
                environment={**self.environment, "POLICY_PACKAGE_SYSTEM": system},
                # No checkout of the policy repository is reachable from here.
                cwd=self.fixture.workspace,
            )
        )
        self.assertNotIn(result.get("status"), {"error", "fail"}, result)
        return result

    def gates(self, root, name, *, enrolled):
        planned = self.call("ci", root, "--project", name)
        self.assertEqual(planned["enrollment"], enrolled)
        report = self.call("vm", root)
        self.assertEqual(report["vmTests"], ["boot"])
        for system in records.load_requirements()["ci"]["runners"]:
            self.call("check", root, "--shell", system=system)
            for channel in PAIR:
                report = self.call(
                    "compatibility",
                    root,
                    "--project",
                    name,
                    "--channel",
                    channel,
                    system=system,
                )
                self.assertEqual(report["expectedRevision"], PAIR[channel])
                self.assertEqual(report["resolvedRevision"], PAIR[channel])
                self.assertNotIn("artifacts", report)
                self.assertNotIn("policyRecordsDigest", report)
            # The separate real-Nix fixture establishes actual host builds.
            self.command(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys; subprocess.run(['nix','flake','check',sys.argv[1],'--no-update-lock-file'], check=True)",
                    str(root),
                ],
                environment={**self.environment, "POLICY_PACKAGE_SYSTEM": system},
            )

    def test_packaged_checker_uses_bundled_data_without_a_data_checkout(self):
        fixture = self.fixture
        self.assertIn("0.4.0", self.command([self.program, "--version"]))
        self.command([self.program, "--help"])
        report = self.call("validate")
        self.assertEqual(report["pins"], fixture.pins)
        self.assertEqual(report["repos"], ["owner/example", "owner/alpha"])
        baseline = policy.fingerprints(self.authority)
        alpha = fixture.roots["alpha"]
        self.gates(alpha, "alpha", enrolled="enrolled")
        pending = fixture.workspace / "pending"
        shutil.copytree(alpha, pending, ignore=shutil.ignore_patterns(".git"))
        workflow = records.read_json(pending / ".github/workflows/policy.yml")
        workflow["jobs"]["policy"]["with"].update(
            project="pending", additional_required_checks="[]"
        )
        write_json(pending / ".github/workflows/policy.yml", workflow)
        fixture.commit(pending)
        self.gates(pending, "pending", enrolled="not-enrolled")
        self.assertEqual(policy.fingerprints(self.authority), baseline)
