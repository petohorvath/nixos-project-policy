"""Record storage and identities through the shared records interface."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.fixtures.cli import invoke
from tools import records


# Captured before consolidation; shared producer/oracle code must not redefine these.
FULL_DIGEST = "c246bef03b01ea6c2355302a3572820e4518f9f47457a2802aaf66b969acdbc8"
LEGACY_DIGEST = "4bccfa1a9a36c0a5c153e253190c0abc28a7bb5f296b90a2899a983a0a2fcb90"


class RecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace / "source"
        self.documents = {
            "projects.json": {
                "schemaVersion": 2,
                "policyRepository": "owner/policy",
                "stableBranch": "nixos-26.05",
                "description": "Policy café",
                "projects": {
                    "legacy": {
                        "repository": "owner/legacy",
                        "adopted": True,
                        "policyVersion": "v0.1.1",
                        "vmTargets": ["vm-test"],
                        "requiredArchitectures": ["x86_64-linux"],
                        "requiredChecks": ["Legacy / check"],
                    }
                },
            },
            "pins.json": {
                "schemaVersion": 1,
                "approved": {"stable": "1" * 40, "unstable": "2" * 40},
                "batches": [],
            },
            "members.json": {
                "schemaVersion": 1,
                "members": {"legacy": "owner/legacy"},
            },
            "support.json": {"schemaVersion": 1, "retirements": {}},
        }
        (self.root / "policy").mkdir(parents=True)
        for name, value in self.documents.items():
            (self.root / "policy" / name).write_text(json.dumps(value))

    def commit(self, root):
        for arguments in [
            ["init", "-q"],
            ["add", "."],
            ["commit", "-qm", "Record fixture"],
        ]:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Record test",
                    "-c",
                    "user.email=record-test@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    *arguments,
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_existing_full_and_legacy_digest_values_are_preserved(self):
        config, pins = records.load(self.root)
        self.assertEqual(records.digest(config, pins), FULL_DIGEST)
        self.assertEqual(records.digest(config, pins, legacy=True), LEGACY_DIGEST)
        self.assertEqual(
            records.digest(self.documents["projects.json"], pins, legacy=True),
            LEGACY_DIGEST,
        )
        config.update(systems=[], requiredTools=[], readmeSections=[], ci={})
        self.assertEqual(records.digest(config, pins), FULL_DIGEST)
        self.assertEqual(records.digest(config, pins, legacy=True), LEGACY_DIGEST)

    def test_written_records_preserve_the_complete_surface_and_inputs(self):
        config, pins = records.load(self.root)
        before = copy.deepcopy((config, pins))
        destination = self.workspace / "execution"
        records.write(destination, config, pins)
        self.assertEqual((config, pins), before)
        self.assertEqual(
            {
                path.name: json.loads(path.read_text())
                for path in (destination / "policy").iterdir()
            },
            self.documents,
        )
        self.assertEqual(records.load(destination), before)
        code, report = invoke("--policy-root", str(destination), "validate")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["policyRecordsDigest"], FULL_DIGEST)
        self.commit(destination)
        captured_config, captured_pins, identity = records.proposed_snapshot(
            destination
        )
        self.assertEqual((captured_config, captured_pins), before)
        self.assertEqual(identity["digest"], FULL_DIGEST)
        self.assertEqual(identity["legacyDigest"], LEGACY_DIGEST)

    def test_proposal_capture_rejects_indirection_and_missing_committed_records(self):
        for case in ["committed-symlink", "directory-symlink", "missing-record"]:
            with self.subTest(case=case):
                proposal = self.workspace / case
                shutil.copytree(self.root, proposal)
                if case == "committed-symlink":
                    member_record = proposal / "policy/members.json"
                    member_record.rename(proposal / "members.json")
                    member_record.symlink_to("../members.json")
                elif case == "missing-record":
                    (proposal / "policy/support.json").unlink()
                self.commit(proposal)
                if case == "directory-symlink":
                    (proposal / "policy").rename(proposal / "actual-policy")
                    (proposal / "policy").symlink_to(
                        "actual-policy", target_is_directory=True
                    )
                with self.assertRaises((ValueError, OSError)):
                    records.proposed_snapshot(proposal)
