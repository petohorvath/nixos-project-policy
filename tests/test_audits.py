"""Audit the public CLI with controlled Git, release, process, and GitHub seams."""

import copy
import io
import json
import os
import shutil
import subprocess
import sys
from unittest.mock import patch
from urllib.error import HTTPError

import yaml

from tests.fixtures.audits import AuditFixture, checked_process
from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.cli import invoke
from tests.fixtures.data import (
    CHECKER,
    POLICY_REPO,
    RELEASE,
    REQUIRED_CHECKS,
)
from tests.fixtures.services import published
from tools import policy, records


class AuditTests(AuditFixture, ProjectTestCase):
    def test_unsupported_selection_remains_an_enrolled_failure(self):
        self.add_member("unsupported", "v0.3.99")
        code, report = self.audit()
        self.assertEqual(code, 1, report)
        member = next(p for p in report["projects"] if p["project"] == "unsupported")
        self.assertEqual(member["enrollment"], "enrolled")
        self.assertEqual(member["status"], "fail")
        self.assertIn("v0.4.0 or later", " ".join(member["issues"]))
        self.assertFalse(any("v0.3.99" in path for path in self.release_requests))

    def test_roster_removal_changes_coverage_and_digest(
        self,
    ):
        code, before = self.audit()
        self.assertEqual(code, 0, before)
        self.members.clear()
        code, after = self.audit("--github")
        self.assertEqual(code, 0, after)
        self.assertEqual(after["projects"], [])
        self.assertNotEqual(before["policyRecordsDigest"], after["policyRecordsDigest"])

    def test_new_enrolled_member_uses_member_settings(self):
        code, report = self.audit()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["projects"][0]["policyVersion"], RELEASE)
        self.assertEqual(report["projects"][0]["enrollment"], "enrolled")

    def test_vm_architecture_is_bound_to_the_audited_declaration(self):
        self.declare(
            required_architectures='["aarch64-linux"]',
            vm_architecture="aarch64-linux",
            vm_targets='["vm-tests"]',
        )
        code, report = self.audit()
        self.assertEqual(code, 0, report)
        member = report["projects"][0]
        self.assertEqual(member["memberSettings"]["vmArchitecture"], "aarch64-linux")
        self.assertIn("Policy / VM tests (aarch64-linux)", member["requiredChecks"])

        def changed(command, **kwargs):
            process = checked_process(command, **kwargs)
            result = json.loads(process.stdout)
            result["memberSettings"].pop("vmArchitecture")
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        self.process = changed
        code, report = self.audit()
        self.assertEqual(code, 2, report)
        self.assertIn("incompatible report", " ".join(report["projects"][0]["issues"]))

    def test_audit_accepts_older_reports_with_implicit_x86_vm_architecture(self):
        def older(command, **kwargs):
            process = checked_process(command, **kwargs)
            result = json.loads(process.stdout)
            result["memberSettings"].pop("vmArchitecture")
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        self.process = older
        code, report = self.audit()
        self.assertEqual(code, 0, report)

    def test_missing_or_disabled_caller_stays_enrolled(self):
        original = copy.deepcopy(self.workflow)
        for change in [
            "missing",
            "conditional",
            "filtered",
            "ambiguous",
            "identity",
            "invalid-release",
        ]:
            with self.subTest(change=change):
                self.workflow = copy.deepcopy(original)
                if change == "missing":
                    self.workflow["jobs"] = {}
                elif change == "conditional":
                    self.workflow["jobs"]["policy"]["if"] = "false"
                elif change == "filtered":
                    self.workflow["on"]["pull_request"]["paths"] = ["docs/**"]
                elif change == "ambiguous":
                    self.workflow["jobs"]["other"] = self.workflow["jobs"]["policy"]
                elif change == "identity":
                    self.workflow["jobs"]["policy"]["with"]["project"] = "other"
                else:
                    self.workflow["jobs"]["policy"]["uses"] = (
                        f"{POLICY_REPO}/.github/workflows/check.yml@main"
                    )
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                code, report = self.audit()
                self.assertIn(code, [1, 2], report)
                self.assertEqual(len(report["projects"]), 1)
                self.assertEqual(report["projects"][0]["enrollment"], "enrolled")
                self.assertNotIn("pending", report["projects"][0]["status"])

    def test_missing_checkout_and_uninspectable_commit_remain_distinct(self):
        self.members["missing"] = "owner/missing"
        with patch.object(policy, "git_revision", return_value=None):
            code, report = self.audit()
        self.assertEqual(code, 2, report)
        entries = {entry["project"]: entry for entry in report["projects"]}
        self.assertEqual(entries["missing"]["status"], "fail")
        self.assertEqual(entries["missing"]["issues"], ["checkout missing"])
        self.assertEqual(entries["example"]["status"], "error")

    def test_dirty_or_changed_member_cannot_claim_an_exact_assessment(self):
        for dirty in [True, False]:
            with self.subTest(dirty=dirty):
                with patch.object(
                    policy,
                    "git_dirty",
                    side_effect=[dirty, True] if not dirty else None,
                    return_value=dirty,
                ):
                    code, report = self.audit()
                self.assertEqual(code, 2, report)
                self.assertIn(
                    "member", " ".join(report["projects"][0]["issues"]).lower()
                )

    def test_unpublished_mutable_or_malformed_release_never_runs_a_checker(self):
        valid = published(f"releases/tags/{RELEASE}")
        for value in [
            None,
            {},
            {**valid, "tag_name": "v9.0.0"},
            {**valid, "immutable": False},
            {**valid, "immutable": "true"},
            {**valid, "draft": True},
            {**valid, "prerelease": True},
        ]:
            with self.subTest(value=value):
                self.release_response = lambda path: value
                self.commands.clear()
                code, report = self.audit()
                self.assertEqual(code, 2, report)
                self.assertEqual(self.commands, [])
        self.release_response = lambda path: (_ for _ in ()).throw(
            OSError("Release unavailable")
        )
        code, report = self.audit()
        self.assertEqual(code, 2)
        self.assertIn("Release unavailable", " ".join(report["projects"][0]["issues"]))

    def test_annotated_release_tags_are_peeled_and_cycles_fail(self):
        tag = "a" * 40
        self.release_response = lambda path: (
            published(path)
            if "/releases/" in path
            else {
                "object": {
                    "type": "commit" if "/git/tags/" in path else "tag",
                    "sha": CHECKER if "/git/tags/" in path else tag,
                }
            }
        )
        self.assertEqual(self.audit()[0], 0)
        self.assertIn(f"repos/{POLICY_REPO}/git/tags/{tag}", self.release_requests)
        self.release_response = lambda path: (
            published(path)
            if "/releases/" in path
            else {"object": {"type": "tag", "sha": tag}}
        )
        code, report = self.audit()
        self.assertEqual(code, 2, report)

    def test_checker_identity_shape_and_outcome_substitutions_are_errors(self):
        mutations = [
            ("project", "other"),
            ("repository", "owner/other"),
            ("revision", "a" * 40),
            ("policyVersion", "v0.3.0"),
            ("checkerVersion", "v0.3.0"),
            ("checkerRevision", "a" * 40),
            ("policyRecordsRevision", None),
            ("policyRecordsDigest", "a" * 64),
            ("memberSettings", {}),
            ("requiredChecks", []),
            ("requiredChecks", ["Tests", "Tests"]),
            ("dependencies", [None]),
            ("issues", ["Unexpected issue"]),
            ("status", "fail"),
        ]
        for key, value in mutations:
            with self.subTest(field=key, value=value):

                def changed(command, **kwargs):
                    process = checked_process(command, **kwargs)
                    result = json.loads(process.stdout)
                    result[key] = value
                    return subprocess.CompletedProcess(
                        command, 0, json.dumps(result), ""
                    )

                self.process = changed
                with patch.object(policy, "github_get") as github:
                    code, report = self.audit("--github")
                self.assertEqual(code, 2, report)
                self.assertIn(
                    "incompatible report", " ".join(report["projects"][0]["issues"])
                )
                github.assert_not_called()
        for body in ["not json", "[]", '{"project":"other","project":"example"}']:
            self.process = lambda command, **kwargs: subprocess.CompletedProcess(
                command, 0, body, ""
            )
            self.assertEqual(self.audit()[0], 2)

    def test_github_uses_roster_identity_and_keeps_unknown_metadata_as_errors(self):
        info = {
            "default_branch": "main",
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
        }
        cases = [
            [{}, []],
            [info, {}],
            [info, [], {}],
            [info, [], {"protected": None}],
            [info, [{"type": "required_status_checks", "parameters": {}}]],
        ]
        for responses in cases:
            with (
                self.subTest(responses=responses),
                patch.object(policy, "github_get", side_effect=responses) as github,
            ):
                code, report = self.audit("--github")
                self.assertEqual(code, 2, report)
                issues = report["projects"][0]["issues"]
                self.assertIn("settings are unknown", " ".join(issues))
                self.assertFalse(
                    any("missing required check" in issue for issue in issues)
                )
                self.assertTrue(
                    all(
                        call.args[0].startswith("repos/owner/example")
                        for call in github.call_args_list
                    )
                )

    def hosted_audit(self, permissions, workflow, *, filename="policy.yml"):
        repository = "https://api.github.com/repos/owner/example"
        data = {
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
                            {"context": check} for check in REQUIRED_CHECKS
                        ]
                    },
                },
            ],
            f"{repository}/actions/permissions": permissions,
            f"{repository}/actions/workflows/{filename}": workflow,
        }
        requests = []

        def response(request, **kwargs):
            self.assertEqual(request.get_method(), "GET")
            requests.append(request.full_url)
            value = data[request.full_url]
            if isinstance(value, Exception):
                raise value
            return io.StringIO(json.dumps(value))

        with (
            patch.dict(os.environ, {"GH_TOKEN": "read-only-audit-fixture"}),
            patch.object(policy, "urlopen", side_effect=response),
        ):
            code, report = self.audit("--github")
        self.assertTrue(all(url.startswith(repository) for url in requests))
        return code, report, requests

    def test_hosted_audit_requires_actions_and_the_discovered_caller_to_be_active(self):
        filename = "member-checks.yaml"
        path = f".github/workflows/{filename}"
        (self.root / ".github/workflows/policy.yml").rename(self.root / path)
        for enabled, state, expected, issue in [
            (True, "active", 0, None),
            (False, "active", 1, "repository Actions are disabled"),
            (True, "disabled_manually", 1, "not active (disabled_manually)"),
            (True, "disabled_inactivity", 1, "not active (disabled_inactivity)"),
            (True, "disabled_fork", 1, "not active (disabled_fork)"),
            (True, "deleted", 1, "not active (deleted)"),
        ]:
            with self.subTest(enabled=enabled, state=state):
                code, report, requests = self.hosted_audit(
                    {"enabled": enabled},
                    {"path": path, "state": state},
                    filename=filename,
                )
                self.assertEqual(code, expected, report)
                member = report["projects"][0]
                self.assertEqual(member["enrollment"], "enrolled")
                self.assertEqual(member["status"], "pass" if expected == 0 else "fail")
                self.assertIn(
                    f"https://api.github.com/repos/owner/example/actions/workflows/{filename}",
                    requests,
                )
                if issue is None:
                    self.assertEqual(member["issues"], [])
                else:
                    self.assertIn(issue, " ".join(member["issues"]))

    def test_hosted_audit_keeps_missing_or_malformed_enablement_metadata_as_errors(
        self,
    ):
        active = {"path": ".github/workflows/policy.yml", "state": "active"}
        for permissions, workflow in [
            ({}, active),
            ({"enabled": "true"}, active),
            ({"enabled": 1}, active),
            ({"enabled": True}, None),
            ({"enabled": True}, {}),
            ({"enabled": True}, {"path": active["path"]}),
            ({"enabled": True}, {**active, "state": None}),
            ({"enabled": True}, {**active, "state": ""}),
            ({"enabled": True}, {**active, "path": ".github/workflows/other.yml"}),
        ]:
            with self.subTest(permissions=permissions, workflow=workflow):
                code, report, _ = self.hosted_audit(permissions, workflow)
                self.assertEqual(code, 2, report)
                member = report["projects"][0]
                self.assertEqual(member["status"], "error")
                self.assertIn("enforcement is unknown", " ".join(member["issues"]))

    def test_hosted_audit_cannot_treat_inaccessible_enablement_as_active_or_disabled(
        self,
    ):
        for endpoint in ["permissions", "workflows/policy.yml"]:
            for status in [401, 403, 404]:
                with self.subTest(endpoint=endpoint, status=status):
                    permissions = {"enabled": True}
                    workflow = {
                        "path": ".github/workflows/policy.yml",
                        "state": "active",
                    }
                    url = (
                        f"https://api.github.com/repos/owner/example/actions/{endpoint}"
                    )
                    error = HTTPError(url, status, "Unavailable", {}, None)
                    if endpoint == "permissions":
                        permissions = error
                    else:
                        workflow = error
                    code, report, _ = self.hosted_audit(permissions, workflow)
                    self.assertEqual(code, 2, report)
                    member = report["projects"][0]
                    self.assertEqual(member["enrollment"], "enrolled")
                    self.assertEqual(member["status"], "error")
                    self.assertIn(f"HTTP {status}", " ".join(member["issues"]))
                    self.assertFalse(
                        any(issue.startswith("github:") for issue in member["issues"])
                    )


class RosterTests(ProjectTestCase):
    def test_changed_records_are_an_error_even_with_no_enrolled_members(self):
        self.members.clear()
        load = records.load
        calls = 0

        def changing(root):
            nonlocal calls
            config, pins = load(root)
            calls += 1
            if calls == 2:
                pins["approved"]["stable"] = "c" * 40
            return config, pins

        with patch.object(records, "load", side_effect=changing):
            code, report = self.run_policy("audit", str(self.root.parent))
        self.assertEqual(code, 2, report)
        self.assertEqual(report["projects"], [])
        self.assertIn("Central records changed", " ".join(report["issues"]))

    def test_roster_requires_only_unique_repository_identities(self):
        root = self.write_records()
        path = root / "policy/members.json"
        values = [
            None,
            {},
            {"schemaVersion": 1, "members": []},
            {
                "schemaVersion": 1,
                "members": {
                    "example": {"repository": "owner/example", "adopted": True}
                },
            },
            {"schemaVersion": 1, "members": {"one": "owner/same", "two": "OWNER/SAME"}},
        ]
        for value in values:
            path.write_text(json.dumps(value))
            code, report = invoke("--policy-root", str(root), "validate")
            self.assertEqual(code, 2, report)
        path.write_text(
            '{"schemaVersion":1,"members":{"example":"owner/other","example":"owner/example"}}'
        )
        self.assertEqual(invoke("--policy-root", str(root), "validate")[0], 2)
        path.unlink()
        self.assertEqual(invoke("--policy-root", str(root), "validate")[0], 2)

    def test_actual_audit_workflow_retains_mixed_member_failure_reports(self):
        root = self.write_records()
        workspace = self.root.parent
        runner = workspace / "runner"
        projects = runner / "projects"
        projects.mkdir(parents=True)
        shutil.copytree(self.root, projects / "example")
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/audit.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        steps = workflow["jobs"]["audit"]["steps"]
        step = next(step for step in steps if "--github" in step.get("run", ""))
        upload = next(
            step for step in steps if "upload-artifact" in step.get("uses", "")
        )
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(step["env"], {"GH_TOKEN": "${{ secrets.MEMBER_AUDIT_TOKEN }}"})
        stub = workspace / "nix"
        stub.write_text(
            f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom tests.fixtures.workflows import audit_adapter\naudit_adapter()\n"
        )
        stub.chmod(0o755)
        original = (projects / "example/.github/workflows/policy.yml").read_text()
        for mode, expected in [
            ("pass", 0),
            ("missing", 1),
            ("identity", 1),
            ("github-error", 2),
        ]:
            with self.subTest(mode=mode):
                caller = json.loads(original)
                if mode == "missing":
                    caller["jobs"] = {}
                if mode == "identity":
                    caller["jobs"]["policy"]["with"]["project"] = "other"
                (projects / "example/.github/workflows/policy.yml").write_text(
                    json.dumps(caller)
                )
                result = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                    cwd=root,
                    env={
                        **os.environ,
                        "PATH": f"{workspace}:{os.environ['PATH']}",
                        "RUNNER_TEMP": str(runner),
                        "AUDIT_TEST_MODE": mode,
                        "GH_TOKEN": "read-only-fixture",
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                report = json.loads((root / "audit.json").read_text())
                self.assertEqual(len(report["projects"]), 2 if mode == "mixed" else 1)
                self.assertEqual(report["projects"][0]["repository"], "owner/example")
                self.assertEqual(
                    report["projects"][0]["status"],
                    {0: "pass", 1: "fail", 2: "error"}[expected],
                )
