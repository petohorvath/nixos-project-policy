"""Host fixture: the built release package with controlled external services."""

import json
import os
from pathlib import Path
import shutil
import sys
import unittest


from tests.fixtures.family import FamilyFixture, write_json
from tests.fixtures.data import (
    PAIR,
    POLICY_REPO,
    RELEASE,
)
from tests.fixtures.process import REAL_RUN
from tests.fixtures.projects import enabled_enforcement
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
        shutil.copytree(
            fixture.baseline / "policy", authority / "policy", dirs_exist_ok=True
        )
        fixture.baseline = authority
        fixture.commit(authority)
        self.base = policy.git_revision(authority)
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
        self.state = self.workspace / "github.json"
        self.save({"github": {}})
        self.config = self.workspace / "adapter.json"
        write_json(
            self.config,
            {
                "state": str(self.state),
                "commands": str(self.workspace / "nix-commands.jsonl"),
                "members": {name: str(root) for name, root in fixture.roots.items()},
                "system": "x86_64-linux",
                "policyRepository": POLICY_REPO,
                "releases": {
                    RELEASE: {
                        "revision": self.base,
                        "program": self.program,
                        "requirements": str(authority / "policy/requirements.json"),
                    },
                },
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
            "GH_TOKEN": "fixture-token",
        }
        self.counter = 0

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
                [
                    self.program,
                    "--policy-root",
                    str(self.fixture.baseline),
                    *map(str, arguments),
                ],
                environment={**self.environment, "POLICY_PACKAGE_SYSTEM": system},
            )
        )
        self.assertNotIn(result.get("status"), {"error", "fail"}, result)
        return result

    def save(self, value):
        write_json(self.state, value)

    def gates(self, root, name, *, enrolled):
        planned = self.call("ci", root, "--project", name)
        self.assertEqual(planned["enrollment"], enrolled)
        self.call("vm", root, "--project", name)
        for system in records.load_requirements()["ci"]["runners"]:
            checked = self.call(
                "check", root, "--project", name, "--shell", system=system
            )
            self.assertEqual(checked["enrollment"], enrolled)
            for channel in PAIR:
                output = self.workspace / f"compatibility-{self.counter}"
                self.counter += 1
                report = self.call(
                    "compatibility",
                    root,
                    "--project",
                    name,
                    "--channel",
                    channel,
                    "--output",
                    output,
                    system=system,
                )
                self.assertEqual(report["checkerRevision"], self.base)
                self.assertEqual(report["pinStatus"], "approved")
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

    def test_packaged_member_checks_audit_and_integration(self):
        fixture = self.fixture
        self.assertIn("0.4.0", self.command([self.program, "--version"]))
        self.command([self.program, "--help"])
        self.call("validate")
        baseline = policy.fingerprints(fixture.baseline)
        before = self.call("audit", fixture.workspace)
        self.assertEqual(
            {project["policyVersion"] for project in before["projects"]},
            {RELEASE},
        )
        alpha = fixture.roots["alpha"]
        self.gates(alpha, "alpha", enrolled="enrolled")
        pending = fixture.workspace / "pending"
        shutil.copytree(alpha, pending, ignore=shutil.ignore_patterns(".git"))
        workflow = records.read_json(pending / ".github/workflows/policy.yml")
        workflow["jobs"]["policy"]["with"].update(
            project="pending", vm_targets="[]", additional_required_checks="[]"
        )
        write_json(pending / ".github/workflows/policy.yml", workflow)
        fixture.commit(pending)
        self.gates(pending, "pending", enrolled="not-enrolled")
        audited = self.call("audit", fixture.workspace)
        self.assertEqual(
            {project["policyVersion"] for project in audited["projects"]},
            {RELEASE},
        )
        self.hosted_audit(audited)
        integration = self.call("agreement", fixture.root, "--project", "example")
        self.assertEqual(
            {
                member["project"]: member["revision"]
                for member in integration["members"]
            },
            fixture.locked,
        )
        self.assertEqual(integration["behavioralIntegration"], "not-run")
        self.assertTrue(integration["dependencySetDigest"])
        self.assertEqual(policy.fingerprints(fixture.baseline), baseline)

    def hosted_audit(self, audited):
        state = records.read_json(self.state)
        for member in audited["projects"]:
            repository = f"repos/{member['repository']}"
            state["github"].update(
                {
                    repository: {
                        "default_branch": "main",
                        "allow_squash_merge": True,
                        "allow_merge_commit": False,
                        "allow_rebase_merge": False,
                    },
                    f"{repository}/rules/branches/main": [
                        {"type": "pull_request"},
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [
                                    {"context": check}
                                    for check in member["requiredChecks"]
                                ]
                            },
                        },
                    ],
                    **enabled_enforcement(repository),
                }
            )
        self.save(state)
        passing = self.call("audit", self.fixture.workspace, "--github")
        self.assertTrue(
            all(member["status"] == "pass" for member in passing["projects"])
        )
        state = records.read_json(self.state)
        path = (
            f"repos/{audited['projects'][0]['repository']}/actions/workflows/policy.yml"
        )
        state["github"][path]["state"] = "disabled_manually"
        self.save(state)
        failed = json.loads(
            self.command(
                [
                    self.program,
                    "--policy-root",
                    str(self.fixture.baseline),
                    "audit",
                    str(self.fixture.workspace),
                    "--github",
                ],
                environment=self.environment,
                expected=1,
            )
        )
        self.assertEqual(failed["projects"][0]["status"], "fail")
        self.assertIn("disabled_manually", " ".join(failed["projects"][0]["issues"]))
        state = records.read_json(self.state)
        state["github"][path]["state"] = "active"
        self.save(state)
