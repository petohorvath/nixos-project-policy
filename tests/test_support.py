"""Public support decisions, retirement boundaries, and legacy cleanup review."""

import json
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

import yaml

from tests.fixtures.audits import AuditFixture, checked_process
from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.cli import invoke
from tests.fixtures.compatibility import CompatibilityFixture
from tests.fixtures.data import (
    AFTER,
    BEFORE,
    EFFECTIVE,
    POLICY_REPO,
    RELEASE,
    SOURCE,
    START,
    retirement,
)
from tests.fixtures.services import published
from tools import policy, records, releases, support


class SupportTests(ProjectTestCase):
    def test_support_identity_is_stable_until_retirement_becomes_effective(self):
        self.support["retirements"][RELEASE] = retirement()
        reports = []
        for instant in ["2029-12-31T00:00:00Z", START, BEFORE, EFFECTIVE, AFTER]:
            with patch.object(support, "now", return_value=support.timestamp(instant)):
                code, report = self.run_policy(
                    "ci", str(self.root), "--project", "example"
                )
            reports.append(report)
            retired = instant >= EFFECTIVE
            self.assertEqual(code, 1 if retired else 0, report)
            self.assertEqual(
                report["selectionStatus"], "retired" if retired else "supported"
            )
            self.assertEqual(report["support"]["retirement"], retirement())
            self.assertEqual("matrix" in report, not retired)
        self.assertEqual(reports[0]["support"], reports[2]["support"])
        self.assertEqual(reports[3]["support"], reports[4]["support"])
        self.assertNotEqual(reports[2]["support"], reports[3]["support"])
        self.assertEqual(len({report["policyRecordsDigest"] for report in reports}), 1)

    def test_retired_commands_stop_before_shell_vm_or_compatibility_execution(self):
        self.support["retirements"][RELEASE] = retirement()
        self.declare(vm_targets='["vm-tests"]')
        for command, options in [
            ("check", ["--shell"]),
            ("ci", []),
            ("vm", []),
            ("compatibility", ["--channel", "stable"]),
        ]:
            with (
                self.subTest(command=command),
                patch.object(support, "now", return_value=support.timestamp(EFFECTIVE)),
                patch.object(policy, "git_revision", return_value=SOURCE),
                patch.object(policy.subprocess, "run") as execute,
            ):
                code, report = self.run_policy(
                    command, str(self.root), "--project", "example", *options
                )
                self.assertEqual(code, 1, report)
                self.assertEqual(report["selectionStatus"], "retired")
                self.assertEqual(report["support"]["retirement"], retirement())
                self.assertEqual(report["enrollment"], "enrolled")
                self.assertNotIn("matrix", report)
                execute.assert_not_called()

    def test_compatibility_execution_uses_the_same_decision_before_and_after_retirement(
        self,
    ):
        with CompatibilityFixture().prepared() as fixture:
            fixture.support["retirements"][RELEASE] = retirement()
            with patch.object(support, "now", return_value=support.timestamp(BEFORE)):
                code, before = fixture.compatibility()
            self.assertEqual(code, 0, before)
            self.assertEqual(before["support"]["status"], "supported")
            count = len(fixture.commands)
            with patch.object(
                support, "now", return_value=support.timestamp(EFFECTIVE)
            ):
                code, after = fixture.compatibility()
            self.assertEqual(code, 1, after)
            self.assertEqual(after["support"]["status"], "retired")
            self.assertEqual(len(fixture.commands), count)

    def test_retirement_taking_effect_during_planning_cannot_return_a_usable_plan(self):
        self.support["retirements"][RELEASE] = retirement()
        with patch.object(
            support,
            "now",
            side_effect=[support.timestamp(BEFORE), support.timestamp(EFFECTIVE)],
        ):
            code, report = self.run_policy("ci", str(self.root), "--project", "example")
        self.assertEqual(code, 1, report)
        self.assertEqual(report["support"]["status"], "retired")
        self.assertNotIn("matrix", report)
        self.assertNotIn("compatibilityMatrix", report)

    def test_invalid_selection_and_uninspectable_support_have_distinct_reports(self):
        self.declare(policy_version="main")
        code, invalid = self.run_policy("ci", str(self.root), "--project", "example")
        self.assertEqual(code, 2, invalid)
        self.assertEqual(invalid["selectionStatus"], "invalid")
        self.declare(policy_version=RELEASE)
        root = self.write_records()
        (root / "policy/support.json").unlink()
        code, unknown = invoke(
            "--policy-root", str(root), "ci", str(self.root), "--project", "example"
        )
        self.assertEqual(code, 2, unknown)
        self.assertEqual(unknown["selectionStatus"], "unknown")

    def test_support_decisions_change_full_identity_but_preserve_the_legacy_digest(
        self,
    ):
        self.write_records()
        before = records.identity(self.config, self.pins, SOURCE)
        self.support["retirements"][RELEASE] = retirement()
        after = records.identity(self.config, self.pins, SOURCE)
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertEqual(before["legacyDigest"], after["legacyDigest"])

    def test_malformed_decisions_cannot_supply_support(self):
        root = self.write_records()
        path = root / "policy/support.json"
        cases = [
            None,
            {},
            {"schemaVersion": True, "retirements": {}},
            {"schemaVersion": 1, "retirements": []},
        ]
        for key, value in [
            ("migrationStartsAt", EFFECTIVE),
            ("migrationStartsAt", AFTER),
            ("migrationStartsAt", "2030-01-01"),
            ("retiresAt", "invalid"),
            ("decision", ""),
            ("decision", "http://example.com/decision"),
            ("reason", ""),
        ]:
            cases.append(
                {
                    "schemaVersion": 1,
                    "retirements": {RELEASE: {**retirement(), key: value}},
                }
            )
        for missing in retirement():
            cases.append(
                {
                    "schemaVersion": 1,
                    "retirements": {
                        RELEASE: {
                            key: value
                            for key, value in retirement().items()
                            if key != missing
                        }
                    },
                }
            )
        for record in cases:
            with self.subTest(record=record):
                path.write_text(json.dumps(record))
                code, report = invoke("--policy-root", str(root), "validate")
                self.assertEqual(code, 2, report)
                self.assertEqual(report["selectionStatus"], "unknown")
        path.write_text('{"schemaVersion":1,"retirements":{},"retirements":{}}')
        self.assertEqual(invoke("--policy-root", str(root), "validate")[0], 2)

    def test_new_publication_preserves_older_support_and_retired_members_stay_visible(
        self,
    ):
        with AuditFixture().prepared() as fixture:
            fixture.add_member("legacy", "v0.1.1")
            for instant in [BEFORE, EFFECTIVE]:
                with patch.object(
                    support, "now", return_value=support.timestamp(instant)
                ):
                    code, report = fixture.audit()
                self.assertEqual(code, 0, report)
                self.assertEqual(
                    {item["policyVersion"] for item in report["projects"]},
                    {RELEASE, "v0.1.1"},
                )
                self.assertTrue(
                    all(
                        item["support"]["status"] == "supported"
                        for item in report["projects"]
                    )
                )
            fixture.support["retirements"]["v0.1.1"] = retirement()
            for instant, expected in [(BEFORE, 0), (EFFECTIVE, 1), (AFTER, 1)]:
                with patch.object(
                    support, "now", return_value=support.timestamp(instant)
                ):
                    code, report = fixture.audit()
                self.assertEqual(code, expected, report)
                self.assertEqual(len(report["projects"]), 2)
                legacy = next(
                    member
                    for member in report["projects"]
                    if member["project"] == "legacy"
                )
                self.assertEqual(
                    legacy["selectionStatus"], "retired" if expected else "supported"
                )
                self.assertEqual(legacy["enrollment"], "enrolled")
                self.assertIn("legacy", fixture.config["projects"])

    def test_audit_rechecks_earlier_members_after_later_members_finish(self):
        with AuditFixture().prepared() as fixture:
            fixture.add_member("legacy", "v0.1.1")
            fixture.support["retirements"][RELEASE] = retirement()
            current_time = support.timestamp(BEFORE)
            execute = fixture.process

            def finish_later_member(command, **kwargs):
                nonlocal current_time
                response = execute(command, **kwargs)
                if command[command.index("--project") + 1] == "legacy":
                    current_time = support.timestamp(EFFECTIVE)
                return response

            fixture.process = finish_later_member
            with patch.object(support, "now", side_effect=lambda: current_time):
                code, report = fixture.audit()
            self.assertEqual(code, 1, report)
            earlier = next(
                member
                for member in report["projects"]
                if member["project"] == "example"
            )
            self.assertEqual(earlier["selectionStatus"], "retired")
            self.assertEqual(earlier["status"], "fail")
            self.assertIn("migration period has ended", " ".join(earlier["issues"]))

    def test_actual_hosted_planning_adapter_stops_at_effective_retirement(self):
        self.support["retirements"][RELEASE] = retirement()
        root = self.write_records()
        workspace = self.root.parent
        (workspace / "project").symlink_to(self.root, target_is_directory=True)
        (workspace / "policy-state").symlink_to(root, target_is_directory=True)
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        steps = [
            next(
                step
                for step in workflow["jobs"]["records"]["steps"]
                if step.get("id") == "ci"
            ),
            next(
                step
                for step in workflow["jobs"]["policy"]["steps"]
                if step.get("name") == "Recheck policy support after execution"
            ),
        ]
        stub = workspace / "nix"
        stub.write_text(
            f"#!{sys.executable}\nimport os, sys\nsys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\nfrom unittest.mock import patch\nfrom tools import policy, support\nwith patch.object(support, 'now', return_value=support.timestamp(os.environ['SUPPORT_TEST_NOW'])):\n    sys.exit(policy.main(sys.argv[sys.argv.index('--') + 1:]))\n"
        )
        stub.chmod(0o755)
        output = workspace / "outputs"
        for step in steps:
            for instant, expected in [(BEFORE, 0), (EFFECTIVE, 1)]:
                output.write_text("")
                process = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                    cwd=workspace,
                    env={
                        **os.environ,
                        "PATH": f"{workspace}:{os.environ['PATH']}",
                        "PROJECT": "example",
                        "WORKFLOW_INPUTS": json.dumps(
                            self.workflow["jobs"]["policy"]["with"]
                        ),
                        "GITHUB_OUTPUT": str(output),
                        "SUPPORT_TEST_NOW": instant,
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(process.returncode, expected, process.stderr)
                report = json.loads(
                    (
                        workspace
                        / ("ci.json" if step.get("id") == "ci" else "support.json")
                    ).read_text()
                )
                self.assertEqual(
                    report["selectionStatus"], "retired" if expected else "supported"
                )
                if expected:
                    self.assertEqual(output.read_text(), "")


class LegacyCleanupTests(ProjectTestCase):
    def prepare_removal(self):
        self.pins["batches"] = [
            {
                "id": status,
                "status": status,
                "pins": self.pins["approved"],
                "projects": {"example": SOURCE},
            }
            for status in ["complete", "withdrawn"]
        ]
        current = self.write_records()
        previous = self.root.parent / "previous"
        shutil.copytree(current, previous)
        # The proposed removal must be judged against the prior roster as well.
        self.config["projects"] = {}
        self.pins["batches"] = []
        self.members.clear()
        return previous

    def validate_removal(self, previous, *options):
        return self.run_policy(
            "validate", "--previous-policy-root", str(previous), *options
        )

    def retire_legacy(self):
        self.support["retirements"].update(
            {version: retirement() for version in support.KNOWN_LEGACY_RELEASES}
        )

    def test_supported_or_scheduled_legacy_releases_prevent_whole_surface_cleanup(self):
        previous = self.prepare_removal()
        for planned in [False, True]:
            if planned:
                self.retire_legacy()
            with patch.object(support, "now", return_value=support.timestamp(BEFORE)):
                code, report = self.validate_removal(previous)
            self.assertEqual(code, 1, report)
            self.assertEqual(
                set(report["legacyRemovals"]),
                {"legacy project example", "pin batch complete", "pin batch withdrawn"},
            )
            self.assertIn("v0.1.1", " ".join(report["issues"]))

    def test_retirement_does_not_remove_still_needed_lists(self):
        self.retire_legacy()
        del self.config["projects"]["example"]["requiredChecks"]
        with patch.object(support, "now", return_value=support.timestamp(AFTER)):
            code, report = self.run_policy("validate")
        self.assertEqual(code, 2, report)
        self.assertIn("needs legacy requiredChecks", report["error"])

    def test_retired_releases_still_require_exact_migration_evidence(self):
        previous = self.prepare_removal()
        self.retire_legacy()
        with (
            patch.object(support, "now", return_value=support.timestamp(AFTER)),
            patch.object(
                releases,
                "published_versions",
                return_value=list(support.KNOWN_LEGACY_RELEASES),
            ),
        ):
            code, report = self.validate_removal(previous)
            self.assertEqual(code, 2, report)
            self.assertIn("--workspace", report["error"])
            with (
                patch.object(policy, "git_revision", return_value=SOURCE),
                patch.object(policy, "git_dirty", return_value=False),
                patch.object(releases, "public_get", side_effect=published),
                patch.object(releases.subprocess, "run", side_effect=checked_process),
            ):
                code, migrated = self.validate_removal(
                    previous, "--workspace", str(self.root.parent)
                )
                self.assertEqual(code, 0, migrated)
                self.assertEqual(migrated["migrationEvidence"][0]["revision"], SOURCE)
                self.workflow["jobs"]["policy"]["uses"] = (
                    f"{POLICY_REPO}/.github/workflows/check.yml@v0.1.1"
                )
                self.declare(policy_version="v0.1.1")
                code, old = self.validate_removal(
                    previous, "--workspace", str(self.root.parent)
                )
                self.assertEqual(code, 1, old)
                self.assertIn("remain needed", " ".join(old["issues"]))
                self.write(".github/workflows/policy.yml", "{}")
                code, missing = self.validate_removal(
                    previous, "--workspace", str(self.root.parent)
                )
                self.assertEqual(code, 2, missing)

    def test_unretired_immutable_patch_in_published_inventory_prevents_cleanup(self):
        previous = self.prepare_removal()
        self.retire_legacy()
        inventory = [
            published(f"/releases/tags/{version}")
            for version in [*support.KNOWN_LEGACY_RELEASES, "v0.1.2", RELEASE]
        ]
        with (
            patch.object(support, "now", return_value=support.timestamp(AFTER)),
            patch.object(releases, "public_get", return_value=inventory) as lookup,
        ):
            code, report = self.validate_removal(previous)
        self.assertEqual(code, 1, report)
        self.assertIn("v0.1.2", " ".join(report["issues"]))
        self.assertIn("releases?per_page=100&page=1", lookup.call_args.args[0])

    def test_uninspectable_release_inventory_cannot_authorize_cleanup(self):
        previous = self.prepare_removal()
        self.retire_legacy()
        for inventory in [
            {},
            [],
            [
                {
                    "tag_name": "v0.1.0",
                    "draft": False,
                    "prerelease": False,
                    "immutable": True,
                }
            ],
        ]:
            with (
                self.subTest(inventory=inventory),
                patch.object(support, "now", return_value=support.timestamp(AFTER)),
                patch.object(releases, "public_get", return_value=inventory),
            ):
                code, report = self.validate_removal(previous)
            self.assertEqual(code, 2, report)
            self.assertIn("inventory", report["error"])

    def test_historical_participant_removal_is_guarded_even_when_legacy_entry_remains(
        self,
    ):
        self.pins["batches"] = [
            {
                "id": "historical",
                "status": "withdrawn",
                "pins": self.pins["approved"],
                "projects": {"example": SOURCE},
            }
        ]
        current = self.write_records()
        previous = self.root.parent / "previous"
        shutil.copytree(current, previous)
        self.pins["batches"][0]["projects"] = {}
        code, report = self.validate_removal(previous)
        self.assertEqual(code, 1, report)
        self.assertIn("pin batch historical project example", report["legacyRemovals"])
