"""Host fixture: the built release package with controlled external services."""

import copy
import json
import os
from pathlib import Path
import shutil
import sys
import unittest

import yaml

from tests.fixtures import workflows
from tests.fixtures.candidates import BatchFixture
from tests.fixtures.data import (
    NEW_PAIR,
    PAIR,
    POLICY_REPO,
    RELEASE,
)
from tests.fixtures.github import GitHub
from tests.fixtures.process import REAL_RUN
from tests.fixtures.projects import enabled_enforcement
from tools import candidates, policy, records


class PackagedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(BatchFixture().prepared())
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
        self.save({"github": {}, "checks": [], "requests": []})
        self.config = self.workspace / "adapter.json"
        candidates.write_json(
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
        candidates.write_json(self.state, value)

    def gates(self, root, name, *, enrolled):
        planned = self.call("ci", root, "--project", name)
        self.assertEqual(planned["enrollment"], enrolled)
        self.call("vm", root, "--project", name)
        for system in candidates.SYSTEMS:
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

    def test_packaged_member_checks_and_complete_pin_pr(self):
        fixture = self.fixture
        self.assertIn("0.4.0", self.command([self.program, "--version"]))
        for arguments in (("--help",), ("pin-batch", "--help"), ("pin-pr", "--help")):
            self.command([self.program, *arguments])
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
        candidates.write_json(pending / ".github/workflows/policy.yml", workflow)
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
        self.routine_pr()

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

    def artifact(self, name, directory):
        github = GitHub.load(self.state)
        github.artifact(POLICY_REPO, self.head, name, directory)
        github.save(self.state)

    def routine_pr(self):
        fixture = self.fixture
        subjects = {root: policy.fingerprints(root) for root in fixture.roots.values()}
        baseline = policy.fingerprints(fixture.baseline)
        fixture.proposal.rename(self.workspace / "unused-proposal")
        shutil.copytree(
            fixture.baseline, fixture.proposal, ignore=shutil.ignore_patterns(".git")
        )
        pins = copy.deepcopy(fixture.pins)
        pins["approved"] = NEW_PAIR
        pins["batches"].append(
            {
                "id": "next",
                "status": "complete",
                "previous": PAIR,
                "pins": NEW_PAIR,
                "projects": {
                    name: policy.git_revision(root)
                    for name, root in fixture.roots.items()
                },
            }
        )
        candidates.write_json(fixture.proposal / "policy/pins.json", pins)
        fixture.commit(fixture.proposal)
        head = policy.git_revision(fixture.proposal)
        self.head = head
        github = GitHub.load(self.state)
        github.pin_pr(POLICY_REPO, self.base, head)
        github.save(self.state)
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/pin-pr.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        workspace = self.workspace / "workflow"
        workspace.mkdir()
        for name, root in (
            ("authority", fixture.baseline),
            ("proposal", fixture.proposal),
            ("members", fixture.workspace),
        ):
            (workspace / name).symlink_to(root, target_is_directory=True)
        environment = {
            **workflows.environment(
                workspace, program=self.program, inherited=self.environment
            ),
            "PR_NUMBER": "7",
            "PROPOSAL_HEAD": head,
            "GITHUB_WORKFLOW_SHA": self.base,
            "RUN_ID": "91",
            "RUN_ATTEMPT": "2",
            "ATTEMPT": "91:2",
            "BATCH": "next",
        }

        def shell(job, **extra):
            process = workflows.run_step(workflow, job, workspace, environment, **extra)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            return process.stdout

        shell("capture")
        shell("plan")
        plan = records.read_json(workspace / "pin-plan/plan.json")
        self.assertEqual(plan["orchestratorRevision"], self.base)
        self.assertFalse(plan["eligible"])
        self.artifact("pin-plan-91-2", workspace / "pin-plan")
        jobs = [
            {
                "id": index + 1,
                "name": name,
                "status": "completed",
                "conclusion": "success",
            }
            for index, name in enumerate(
                (
                    "Capture pin proposal",
                    "Plan pin candidate",
                    "Aggregate pin candidate",
                )
            )
        ]
        for index, row in enumerate(plan["matrix"]["include"]):
            member = workspace / "member"
            member.unlink(missing_ok=True)
            member.symlink_to(fixture.roots[row["project"]], target_is_directory=True)
            shell(
                "execute",
                PROJECT=row["project"],
                SYSTEM=row["system"],
                POLICY_PACKAGE_SYSTEM=row["system"],
            )
            self.artifact(f"pin-result-91-2-{row['worker']}", workspace / "pin-result")
            (workspace / "pin-result").rename(workspace / row["worker"])
            jobs.append(
                {
                    "id": index + 4,
                    "name": row["job"],
                    "status": "completed",
                    "conclusion": "success",
                    "labels": [row["runner"]],
                }
            )
        state = records.read_json(self.state)
        state["github"][f"repos/{POLICY_REPO}/actions/runs/91/attempts/2/jobs"] = {
            "jobs": jobs
        }
        self.save(state)
        shell("aggregate")
        summary = records.read_json(workspace / "pin-summary/result.json")
        self.assertTrue(summary["eligible"])
        self.assertEqual(summary["approval"], "not-granted")
        for member in summary["members"].values():
            for item in member["results"]:
                if item["job"]["kind"] == "compatibility":
                    self.assertEqual(item["report"]["pinStatus"], "candidate")
        self.artifact("pin-summary-91-2", workspace / "pin-summary")
        shell("finish", CHECK_ID="1")
        check = records.read_json(self.state)["checks"][0]
        self.assertEqual((check["head_sha"], check["conclusion"]), (head, "success"))
        self.assertEqual(policy.fingerprints(fixture.baseline), baseline)
        for root, source in subjects.items():
            self.assertEqual(policy.fingerprints(root), source)
        # Simulate a reviewed merge only in a new isolated record snapshot.
        merged = self.workspace / "simulated-merged-records"
        shutil.copytree(fixture.proposal, merged)
        fixture.baseline = merged
        report = self.call(
            "compatibility",
            fixture.roots["alpha"],
            "--project",
            "alpha",
            "--channel",
            "stable",
            "--output",
            self.workspace / "after-merge",
        )
        self.assertEqual(
            (report["pinStatus"], report["resolvedRevision"]),
            ("approved", NEW_PAIR["stable"]),
        )
        following = self.workspace / "following-proposal"
        shutil.copytree(merged, following, ignore=shutil.ignore_patterns(".git"))
        following_pins = copy.deepcopy(pins)
        pair = {"stable": "6" * 40, "unstable": "7" * 40}
        following_pins["approved"] = pair
        following_pins["batches"].append(
            {
                "id": "following",
                "status": "complete",
                "previous": NEW_PAIR,
                "pins": pair,
                "projects": pins["batches"][-1]["projects"],
            }
        )
        candidates.write_json(following / "policy/pins.json", following_pins)
        fixture.commit(following)
        next_plan = self.call(
            "pin-batch",
            "plan",
            fixture.workspace,
            "--all",
            "--proposal-root",
            following,
            "--batch",
            "following",
            "--attempt",
            "next-review",
            "--output",
            self.workspace / "following-plan",
        )
        self.assertEqual(next_plan["status"], "planned")
        self.assertEqual(next_plan["pins"], pair)
        self.assertFalse(next_plan["eligible"])


if __name__ == "__main__":
    unittest.main()
