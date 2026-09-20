"""Audit the public CLI with controlled Git, release, process, and GitHub seams."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import yaml

from tests.test_policy import (
    CHECKER,
    POLICY_REPO,
    ProjectFixture,
    RELEASE,
    REQUIRED_CHECKS,
    SOURCE,
)
from tools import policy, records, releases


def invoke(*arguments):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = policy.main(list(arguments))
    return code, json.loads(output.getvalue() or errors.getvalue())


def published(path):
    if "/releases/tags/" in path:
        return {
            "tag_name": path.rsplit("/", 1)[1],
            "immutable": True,
            "draft": False,
            "prerelease": False,
        }
    return {"object": {"type": "commit", "sha": CHECKER}}


def checked_process(command, **kwargs):
    arguments = command[command.index("--") + 1 :]
    root = Path(arguments[arguments.index("check") + 1])
    record_root = Path(arguments[arguments.index("--policy-root") + 1])
    name = arguments[arguments.index("--project") + 1]
    _, _, _, version = policy.declarations.discover(root, POLICY_REPO)
    if version == RELEASE:
        code, report = invoke(*arguments)
    else:
        config, pins = policy.load_policy(record_root)
        report = {
            "project": name,
            "policyVersion": version,
            "checkerVersion": version,
            "revision": SOURCE,
            "policyRecordsRevision": SOURCE,
            "policyRecordsDigest": records.digest(config, pins, legacy=True),
            "status": "pass",
            "issues": [],
            "dependencies": [],
            "requiredChecks": config["projects"][name]["requiredChecks"],
        }
        code = 0
    return subprocess.CompletedProcess(
        command,
        code,
        json.dumps(report) if code != 2 else "",
        json.dumps(report) if code == 2 else "",
    )


class AuditTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.config["projects"]["example"]["requiredChecks"] = list(REQUIRED_CHECKS)
        self.release_requests = []
        self.commands = []
        self.process = checked_process
        self.release_response = published

        def lookup(path):
            self.release_requests.append(path)
            return self.release_response(path)

        def execute(command, **kwargs):
            self.commands.append(command)
            return self.process(command, **kwargs)

        for adapter in [
            patch.object(policy, "git_revision", return_value=SOURCE),
            patch.object(policy, "git_dirty", return_value=False),
            patch.object(releases, "public_get", side_effect=lookup),
            patch.object(releases.subprocess, "run", side_effect=execute),
        ]:
            adapter.start()
            self.addCleanup(adapter.stop)

    def audit(self, *options):
        return self.run_policy("audit", str(self.root.parent), *options)

    def add_member(self, name, version):
        root = self.root.parent / name
        shutil.copytree(self.root, root)
        workflow = copy.deepcopy(self.workflow)
        caller = workflow["jobs"]["policy"]
        caller["uses"] = f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
        caller["with"] = {"project": name, "policy_version": version}
        if releases.version_at_least(version, (0, 4, 0)):
            caller["with"]["required_architectures"] = '["x86_64-linux"]'
        (root / ".github/workflows/policy.yml").write_text(json.dumps(workflow))
        self.members[name] = f"owner/{name}"
        self.config["projects"][name] = {
            "repository": f"owner/{name}",
            "adopted": True,
            "policyVersion": version,
            "vmTargets": [],
            "requiredArchitectures": ["x86_64-linux"],
            "requiredChecks": ["Historical / Release-specific tests"],
        }
        return root

    def test_mixed_selections_use_verified_commits_and_separate_gate_contracts(self):
        self.add_member("legacy", "v0.1.1")
        self.add_member("derived", "v0.3.0")
        project = self.config["projects"]["derived"]
        project["requiredChecks"] = policy.ci_plan(project, self.config["ci"])[
            "requiredChecks"
        ]
        self.config["projects"]["example"].update(policyVersion="v0.3.0", adopted=False)
        inspected = []

        def gates(project, checks):
            inspected.append((project["repository"], checks))
            return []

        with patch.object(policy, "check_github", side_effect=gates):
            code, report = self.audit("--github")
        self.assertEqual(code, 0, report)
        by_name = {member["project"]: member for member in report["projects"]}
        self.assertEqual(by_name["example"]["policyVersion"], RELEASE)
        self.assertEqual(by_name["legacy"]["policyVersion"], "v0.1.1")
        for member in by_name.values():
            self.assertEqual(member["revision"], SOURCE)
            self.assertEqual(member["checkerRevision"], CHECKER)
            self.assertEqual(member["records"]["revision"], SOURCE)
            self.assertEqual(member["enrollment"], "enrolled")
            self.assertEqual(member["assessment"], "pass")
        self.assertEqual(
            dict(inspected)["owner/legacy"], ["Historical / Release-specific tests"]
        )
        self.assertEqual(dict(inspected)["owner/example"], REQUIRED_CHECKS)
        for command in self.commands:
            self.assertIn(f"github:{POLICY_REPO}/{CHECKER}", command)
            self.assertEqual(
                command[command.index("--policy-root") + 1],
                str(self.root.parent / "records"),
            )
        self.assertIn(
            f"repos/{POLICY_REPO}/releases/tags/v0.1.1", self.release_requests
        )

    def test_roster_removal_changes_coverage_and_digest_without_pruning_legacy_data(
        self,
    ):
        before_config = copy.deepcopy(self.config)
        code, before = self.audit()
        self.assertEqual(code, 0, before)
        self.members.clear()
        code, after = self.audit("--github")
        self.assertEqual(code, 0, after)
        self.assertEqual(after["projects"], [])
        self.assertNotEqual(before["policyRecordsDigest"], after["policyRecordsDigest"])
        self.assertEqual(
            before["records"]["legacyDigest"], after["records"]["legacyDigest"]
        )
        self.assertEqual(self.config["projects"], before_config["projects"])

    def test_new_enrolled_member_does_not_need_copied_legacy_settings(self):
        self.config["projects"] = {}
        code, report = self.audit()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["projects"][0]["policyVersion"], RELEASE)
        self.assertEqual(report["projects"][0]["enrollment"], "enrolled")

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

    def test_candidate_report_is_visible_but_not_approved_compliance(self):
        def candidate(command, **kwargs):
            process = checked_process(command, **kwargs)
            report = json.loads(process.stdout)
            report["status"] = "candidate-ready"
            return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

        self.process = candidate
        code, report = self.audit()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["projects"][0]["assessment"], "candidate-ready")

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


class RosterTests(ProjectFixture):
    def test_changed_records_are_an_error_even_with_no_enrolled_members(self):
        self.members.clear()
        load = policy.load_policy
        calls = 0

        def changing(root):
            nonlocal calls
            config, pins = load(root)
            calls += 1
            if calls == 2:
                pins["approved"]["stable"] = "c" * 40
            return config, pins

        with patch.object(policy, "load_policy", side_effect=changing):
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
            {"schemaVersion": 1, "members": {"example": "owner/other"}},
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

    def test_whole_transitional_snapshot_retains_historical_batch_identities(self):
        root = self.write_records()
        source = policy.SOURCE_ROOT / "policy"
        config = records.read_json(source / "projects.json")
        pins = records.read_json(source / "pins.json")
        roster = records.read_json(source / "members.json")
        self.assertEqual(
            roster["members"],
            {
                name: project["repository"]
                for name, project in config["projects"].items()
                if project["adopted"]
            },
        )
        config["projects"]["modern"] = {
            "repository": "owner/modern",
            "adopted": False,
            "policyVersion": None,
            "vmTargets": [],
        }
        for state in ["complete", "withdrawn"]:
            pins["batches"].append(
                {
                    "id": state,
                    "status": state,
                    "pins": pins["approved"],
                    "projects": {name: SOURCE for name in config["projects"]},
                }
            )
        for enrolled in [{"modern": "owner/modern"}, {}]:
            (root / "policy/projects.json").write_text(json.dumps(config))
            (root / "policy/pins.json").write_text(json.dumps(pins))
            (root / "policy/members.json").write_text(
                json.dumps({"schemaVersion": 1, "members": enrolled})
            )
            self.assertEqual(invoke("--policy-root", str(root), "validate")[0], 0)
        del config["projects"]["modern"]
        (root / "policy/projects.json").write_text(json.dumps(config))
        code, report = invoke("--policy-root", str(root), "validate")
        self.assertEqual(code, 2, report)
        self.assertIn("Unknown project in batch", report["error"])

    def test_actual_maintenance_adapter_retains_mixed_member_failure_reports(self):
        root = self.write_records()
        workspace = self.root.parent
        runner = workspace / "runner"
        projects = runner / "projects"
        projects.mkdir(parents=True)
        shutil.copytree(self.root, projects / "example")
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/maintenance.yml").read_text(),
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
            f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom tests.test_audits import maintenance_adapter\nmaintenance_adapter()\n"
        )
        stub.chmod(0o755)
        original = (projects / "example/.github/workflows/policy.yml").read_text()
        for mode, expected in [
            ("pass", 0),
            ("missing", 1),
            ("identity", 1),
            ("github-error", 2),
            ("mixed", 0),
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
                if mode == "mixed":
                    shutil.copytree(projects / "example", projects / "legacy")
                    caller["jobs"]["policy"]["uses"] = (
                        f"{POLICY_REPO}/.github/workflows/check.yml@v0.1.1"
                    )
                    caller["jobs"]["policy"]["with"] = {
                        "project": "legacy",
                        "policy_version": "v0.1.1",
                    }
                    (projects / "legacy/.github/workflows/policy.yml").write_text(
                        json.dumps(caller)
                    )
                    self.members["legacy"] = "owner/legacy"
                    self.config["projects"]["example"]["requiredChecks"] = (
                        REQUIRED_CHECKS
                    )
                    self.config["projects"]["legacy"] = {
                        "repository": "owner/legacy",
                        "adopted": True,
                        "policyVersion": "v0.1.1",
                        "vmTargets": [],
                        "requiredChecks": ["Historical / Tests"],
                    }
                    self.write_records()
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


def maintenance_adapter():
    """Controlled external services for the actual maintenance shell invocation."""

    def gates(project, checks):
        if os.environ["AUDIT_TEST_MODE"] == "github-error":
            raise ValueError("GitHub inspection unavailable; settings are unknown")
        if project["repository"] not in {"owner/example", "owner/legacy"}:
            raise AssertionError("Member declaration redirected GitHub inspection")
        return []

    with (
        patch.object(policy, "git_revision", return_value=SOURCE),
        patch.object(policy, "git_dirty", return_value=False),
        patch.object(releases, "public_get", side_effect=published),
        patch.object(releases.subprocess, "run", side_effect=checked_process),
        patch.object(policy, "check_github", side_effect=gates),
    ):
        sys.exit(policy.main(sys.argv[sys.argv.index("--") + 1 :]))
