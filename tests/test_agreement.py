"""Agreement at committed member revisions through the public checker CLI."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import yaml

from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.data import RELEASE, lockfile
from tests.fixtures.process import commit, git, initialize, isolated_git
from tools import agreement, policy


GATE = "Integration / Policy agreement"


class AgreementTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.workflow["jobs"]["integration"] = {
            "name": "Integration",
            "needs": "policy",
            "uses": f"{self.config['policyRepository']}/.github/workflows/agreement.yml@{RELEASE}",
            "with": {
                "project": "example",
                "policy_version": RELEASE,
                "project_revision": "${{ needs.policy.outputs.project_revision }}",
                "records_revision": "${{ needs.policy.outputs.records_revision }}",
            },
        }
        self.declare(additional_required_checks=json.dumps([GATE]))
        self.enterContext(isolated_git(Path(self.temp.name)))
        initialize(self.root)
        self.repositories = {}
        self.revisions = {}
        for name in ["alpha", "beta"]:
            repository = Path(self.temp.name) / name
            initialize(repository)
            self.repositories[name] = repository
            self.members[name] = f"owner/{name}"
            git(
                self.root,
                "config",
                "--global",
                f"url.{repository.as_uri()}.insteadOf",
                f"https://github.com/owner/{name}.git",
            )
            self.revisions[name] = self.member_revision(name, RELEASE)

    def member_revision(self, name, version):
        root = self.repositories[name]
        workflow = copy.deepcopy(self.workflow)
        job = workflow["jobs"]["policy"]
        job["uses"] = job["uses"].rsplit("@", 1)[0] + "@" + version
        job["with"].update(
            project=name, policy_version=version, additional_required_checks="[]"
        )
        path = root / ".github/workflows/policy.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(workflow))
        return commit(root)

    def member_node(self, name, revision=None):
        return {
            "locked": {
                "type": "github",
                "owner": "owner",
                "repo": name,
                "rev": revision or self.revisions[name],
            },
            "original": {"type": "github", "owner": "owner", "repo": name},
        }

    def locked_set(self):
        lock = lockfile()
        for name in self.repositories:
            lock["nodes"]["entry"]["inputs"][name] = name
            lock["nodes"][name] = self.member_node(name)
        return lock

    def agreement(self, lock=None):
        self.write("flake.lock", json.dumps(lock or self.locked_set()))
        commit(self.root)
        return self.run_policy("agreement", str(self.root), "--project", "example")

    def test_matching_locked_set_passes_after_member_heads_upgrade(self):
        self.member_revision("alpha", "v0.5.0")
        self.member_revision("beta", "v0.5.0")
        code, report = self.agreement()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["revision"], git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(
            {member["project"]: member["revision"] for member in report["members"]},
            self.revisions,
        )
        self.assertEqual(
            {member["policyVersion"] for member in report["members"]}, {RELEASE}
        )
        self.assertEqual(report["behavioralIntegration"], "not-run")
        self.assertEqual(len(report["dependencySetDigest"]), 64)

    def test_partial_upgrade_fails_and_complete_matching_upgrade_passes(self):
        next_alpha = self.member_revision("alpha", "v0.5.0")
        next_beta = self.member_revision("beta", "v0.5.0")
        lock = self.locked_set()
        lock["nodes"]["alpha"] = self.member_node("alpha", next_alpha)
        code, partial = self.agreement(lock)
        self.assertEqual(code, 1, partial)
        self.assertEqual(
            {member["policyVersion"] for member in partial["members"]},
            {RELEASE, "v0.5.0"},
        )
        lock["nodes"]["beta"] = self.member_node("beta", next_beta)
        self.workflow["jobs"]["policy"]["uses"] = (
            self.workflow["jobs"]["policy"]["uses"].rsplit("@", 1)[0] + "@v0.5.0"
        )
        self.workflow["jobs"]["integration"]["uses"] = (
            self.workflow["jobs"]["integration"]["uses"].rsplit("@", 1)[0] + "@v0.5.0"
        )
        self.workflow["jobs"]["integration"]["with"]["policy_version"] = "v0.5.0"
        self.declare(policy_version="v0.5.0")
        self.write("flake.lock", json.dumps(lock))
        revision = commit(self.root)
        checker = Path(self.temp.name) / "next-checker"
        shutil.copytree(policy.SOURCE_ROOT / "tools", checker / "tools")
        (checker / "policy").mkdir()
        shutil.copyfile(
            policy.SOURCE_ROOT / "policy/requirements.json",
            checker / "policy/requirements.json",
        )
        (checker / "VERSION").write_text("0.5.0\n")
        result = subprocess.run(
            [
                sys.executable,
                str(checker / "tools/policy.py"),
                "--policy-root",
                str(self.write_records()),
                "agreement",
                str(self.root),
                "--project",
                "example",
            ],
            capture_output=True,
            text=True,
        )
        complete = json.loads(result.stdout or result.stderr)
        self.assertEqual(result.returncode, 0, complete)
        self.assertEqual(complete["revision"], revision)
        self.assertEqual(complete["policyVersion"], "v0.5.0")
        self.assertEqual(
            {member["policyVersion"] for member in complete["members"]}, {"v0.5.0"}
        )
        self.assertNotEqual(
            partial["dependencySetDigest"], complete["dependencySetDigest"]
        )

    def test_transitive_members_and_follows_use_the_integration_lock_graph(self):
        lock = self.locked_set()
        del lock["nodes"]["entry"]["inputs"]["beta"]
        lock["nodes"]["alpha"]["inputs"] = {"inner": "wrapper"}
        lock["nodes"]["wrapper"] = {"inputs": {"dependency": "beta"}}
        lock["nodes"]["entry"]["inputs"]["alias"] = ["alpha", "inner", "dependency"]
        lock["nodes"]["entry"]["inputs"]["second-alias"] = ["alias"]
        lock["nodes"]["unreachable"] = self.member_node("alpha", "0" * 40)
        # A member's standalone lock has a different context; the consumed graph wins.
        (self.repositories["alpha"] / "flake.lock").write_text(
            json.dumps({"invalid": "standalone lock"})
        )
        lock["nodes"]["alpha"]["locked"]["rev"] = commit(self.repositories["alpha"])
        code, report = self.agreement(lock)
        self.assertEqual(code, 0, report)
        self.assertEqual(
            {member["project"] for member in report["members"]}, {"alpha", "beta"}
        )
        self.assertEqual(len(report["members"]), 2)
        self.assertEqual(
            {
                reference["node"]
                for member in report["members"]
                for reference in member["references"]
            },
            {"alpha", "beta"},
        )

    def test_locked_member_cycles_cannot_pass(self):
        lock = self.locked_set()
        lock["nodes"]["alpha"]["inputs"] = {"member": "beta"}
        lock["nodes"]["beta"]["inputs"] = {"member": "alpha"}
        code, report = self.agreement(lock)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["cycles"], [["alpha", "beta", "alpha"]])

    def test_unavailable_locked_source_retains_exact_member_identity(self):
        lock = self.locked_set()
        lock["nodes"]["alpha"]["locked"]["rev"] = "0" * 40
        code, report = self.agreement(lock)
        self.assertEqual(code, 2, report)
        member = next(
            member for member in report["members"] if member["project"] == "alpha"
        )
        self.assertEqual(member["revision"], "0" * 40)
        self.assertEqual(member["repository"], "owner/alpha")
        self.assertEqual(member["selectionStatus"], "unknown")
        self.assertEqual(member["status"], "error")
        self.assertTrue(member["issues"])

    def test_missing_ambiguous_and_disabled_locked_callers_cannot_pass(self):
        root = self.repositories["alpha"]
        caller = root / ".github/workflows/policy.yml"
        original = caller.read_text()
        for invalid in ["missing", "ambiguous", "disabled"]:
            caller.write_text(original)
            extra = caller.with_name("other.yaml")
            extra.unlink(missing_ok=True)
            if invalid == "missing":
                caller.unlink()
            elif invalid == "ambiguous":
                extra.write_text(original)
            else:
                workflow = json.loads(original)
                workflow["jobs"]["policy"]["if"] = "false"
                caller.write_text(json.dumps(workflow))
            lock = self.locked_set()
            lock["nodes"]["alpha"]["locked"]["rev"] = commit(root)
            code, report = self.agreement(lock)
            self.assertEqual(code, 1, report)
            member = next(
                member for member in report["members"] if member["project"] == "alpha"
            )
            self.assertEqual(member["selectionStatus"], "invalid")

    def test_changed_trusted_records_invalidate_the_assessment(self):
        process = subprocess.run

        def change_roster(command, **kwargs):
            result = process(command, **kwargs)
            if "fetch" in command:
                path = Path(self.temp.name) / "records/policy/members.json"
                path.write_text(json.dumps({"schemaVersion": 1, "members": {}}))
            return result

        with patch.object(agreement.subprocess, "run", side_effect=change_roster):
            code, report = self.agreement()
        self.assertEqual(code, 2, report)
        self.assertIn("Central records changed", " ".join(report["issues"]))

    def test_independent_lock_scopes_count_and_vendor_locks_do_not(self):
        newer = self.member_revision("alpha", "v0.5.0")
        extra = self.locked_set()
        extra["nodes"]["alpha"]["locked"]["rev"] = newer
        self.write("vendor/example/flake.lock", json.dumps(extra))
        self.assertEqual(self.agreement()[0], 0)
        self.write("examples/minimal/flake.lock", json.dumps(extra))
        code, report = self.agreement()
        self.assertEqual(code, 1, report)
        self.assertEqual(
            {scope["path"] for scope in report["lockfiles"]},
            {"flake.lock", "examples/minimal/flake.lock"},
        )
        self.assertEqual(
            {
                member["revision"]
                for member in report["members"]
                if member["project"] == "alpha"
            },
            {self.revisions["alpha"], newer},
        )

    def test_consumed_source_cannot_borrow_identity_from_original(self):
        for locked in [
            {
                "type": "git",
                "url": "https://elsewhere.invalid/owner/alpha.git",
                "rev": self.revisions["alpha"],
            },
            {
                "type": "github",
                "owner": "untrusted",
                "repo": "alpha",
                "rev": self.revisions["alpha"],
            },
            {"type": "path", "path": str(self.repositories["alpha"])},
        ]:
            lock = self.locked_set()
            lock["nodes"]["alpha"]["locked"] = locked
            code, report = self.agreement(lock)
            self.assertEqual(code, 2, report)
            self.assertIn("contradicts", report["error"])

    def test_unsupported_member_archive_cannot_disappear_from_coverage(self):
        lock = self.locked_set()
        source = {
            "type": "tarball",
            "url": f"https://github.com/owner/alpha/archive/{self.revisions['alpha']}.tar.gz",
        }
        lock["nodes"]["alpha"] = {"locked": source, "original": source}
        code, report = self.agreement(lock)
        self.assertEqual(code, 2, report)
        self.assertIn("alpha", report["error"])

    def test_dirty_or_changed_integration_commits_cannot_pass(self):
        self.write("flake.lock", json.dumps(self.locked_set()))
        commit(self.root)
        self.write("uncommitted.txt", "Changed source")
        code, report = self.run_policy(
            "agreement", str(self.root), "--project", "example"
        )
        self.assertEqual(code, 2, report)
        self.assertIn("clean committed", report["error"])
        commit(self.root)
        execute = subprocess.run

        def change_during_fetch(command, **kwargs):
            response = execute(command, **kwargs)
            if "fetch" in command:
                self.write("uncommitted.txt", "Changed during source inspection")
            return response

        with patch.object(agreement.subprocess, "run", side_effect=change_during_fetch):
            code, report = self.run_policy(
                "agreement", str(self.root), "--project", "example"
            )
        self.assertEqual(code, 2, report)
        self.assertIn("Integration source changed", " ".join(report["issues"]))

    def test_root_follows_cycles_and_missing_paths_cannot_pass(self):
        lock = self.locked_set()
        lock["nodes"]["alpha"]["inputs"] = {"integration": []}
        code, report = self.agreement(lock)
        self.assertEqual(code, 1, report)
        self.assertIn(["alpha", "example", "alpha"], report["cycles"])
        lock["nodes"]["alpha"]["inputs"] = {"missing": ["absent"]}
        code, report = self.agreement(lock)
        self.assertEqual(code, 2, report)
        self.assertIn("Missing follows", report["error"])

    def test_template_supplies_the_gate_and_empty_member_sets_do_not_pass(self):
        template = (policy.SOURCE_ROOT / "templates/integration-caller.yml").read_text()
        self.write(
            ".github/workflows/policy.yml",
            template.replace("POLICY_VERSION", RELEASE).replace(
                "PROJECT_NAME", "example"
            ),
        )
        code, report = self.agreement()
        self.assertEqual(code, 0, report)
        code, empty = self.agreement(lockfile())
        self.assertEqual(code, 1, empty)
        self.assertEqual(empty["members"], [])
        self.assertIn("No enrolled member dependencies", " ".join(empty["issues"]))

    def test_git_transport_reads_the_locked_flake_subdirectory(self):
        root = self.repositories["alpha"]
        caller = root / ".github/workflows/policy.yml"
        nested = root / "flake/.github/workflows/policy.yml"
        nested.parent.mkdir(parents=True)
        nested.write_text(caller.read_text())
        caller.write_text("{}")
        revision = commit(root)
        lock = self.locked_set()
        lock["nodes"]["alpha"]["locked"] = {
            "type": "git",
            "url": "https://github.com/owner/alpha.git",
            "dir": "flake",
            "rev": revision,
        }
        code, report = self.agreement(lock)
        self.assertEqual(code, 0, report)
        member = next(
            member for member in report["members"] if member["project"] == "alpha"
        )
        self.assertEqual(member["source"]["dir"], "flake")
        self.assertEqual(member["revision"], revision)
        del lock["nodes"]["alpha"]["locked"]["dir"]
        self.assertEqual(self.agreement(lock)[0], 1)

    def test_removed_identity_is_not_implicitly_enrolled(self):
        del self.members["alpha"]
        lock = self.locked_set()
        lock["nodes"]["alpha"]["locked"]["rev"] = "0" * 40
        code, report = self.agreement(lock)
        self.assertEqual(code, 0, report)
        self.assertEqual([member["project"] for member in report["members"]], ["beta"])

    def test_required_agreement_gate_needs_matching_unconditional_caller(self):
        original = copy.deepcopy(self.workflow["jobs"]["integration"])
        for invalid in [
            "missing",
            "disabled",
            "wrong-release",
            "wrong-identity",
            "live-records",
            "different-source",
        ]:
            job = copy.deepcopy(original)
            self.workflow["jobs"]["integration"] = job
            if invalid == "missing":
                del self.workflow["jobs"]["integration"]
            elif invalid == "disabled":
                job["if"] = "false"
            elif invalid == "wrong-release":
                job["uses"] = job["uses"].rsplit("@", 1)[0] + "@v0.5.0"
            elif invalid == "wrong-identity":
                job["with"]["project"] = "alpha"
            elif invalid == "live-records":
                job["with"]["records_revision"] = "main"
            else:
                job["with"]["project_revision"] = "${{ github.sha }}"
            self.declare()
            code, report = self.run_policy("ci", str(self.root), "--project", "example")
            self.assertEqual(code, 2, report)
            self.assertEqual(report["selectionStatus"], "invalid")

    def test_actual_agreement_workflow_adapter_retains_pass_and_failure_reports(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/agreement.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        job = workflow["jobs"]["agreement"]
        step = next(step for step in job["steps"] if step.get("id") == "agreement")
        code, plan = self.run_policy("ci", str(self.root), "--project", "example")
        self.assertEqual(code, 0, plan)
        self.assertIn("Integration / " + job["name"], plan["requiredChecks"])
        self.assertIn(GATE, plan["requiredChecks"])
        self.assertEqual(
            {entry["check"] for entry in plan["matrix"]["include"]},
            {"Compliance", "Project tests"},
        )
        workspace = Path(self.temp.name) / "hosted"
        workspace.mkdir()
        (workspace / "project").symlink_to(self.root, target_is_directory=True)
        (workspace / "policy-state").symlink_to(
            self.write_records(), target_is_directory=True
        )
        initialize(workspace / "policy")
        (workspace / "policy/VERSION").write_text(RELEASE.removeprefix("v") + "\n")
        commit(workspace / "policy")
        initialize(self.write_records())
        records_revision = commit(self.write_records())
        executable = workspace / "nix"
        executable.write_text(
            f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom tools import policy\nsys.exit(policy.main(sys.argv[sys.argv.index('--') + 1:]))\n"
        )
        executable.chmod(0o755)
        newer = self.member_revision("alpha", "v0.5.0")
        for mismatch, expected in [(False, 0), (True, 1)]:
            lock = self.locked_set()
            if mismatch:
                lock["nodes"]["alpha"]["locked"]["rev"] = newer
            self.write("flake.lock", json.dumps(lock))
            revision = commit(self.root)
            snapshot = next(
                step
                for step in job["steps"]
                if step.get("name") == "Record exact inspection sources"
            )
            for expected_records, expected_code in [
                (records_revision, 0),
                ("0" * 40, 1),
            ]:
                inspected = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", snapshot["run"]],
                    cwd=workspace,
                    env={
                        **os.environ,
                        "POLICY_VERSION": RELEASE,
                        "PROJECT_REVISION": revision,
                        "RECORDS_REVISION": expected_records,
                    },
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(inspected.returncode, expected_code, inspected.stderr)
            process = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                cwd=workspace,
                env={
                    **os.environ,
                    "PATH": f"{workspace}:{os.environ['PATH']}",
                    "PROJECT": "example",
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, expected, process.stderr)
            report = json.loads((workspace / "agreement.json").read_text())
            self.assertEqual(report["status"], "fail" if mismatch else "pass")
            self.assertEqual(len(report["members"]), 2)
