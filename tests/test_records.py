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


# Fixed current-record digest, independent of the producer under test.
FULL_DIGEST = "10979ea2f825aafc0e56686e19a990ef40e98dc839212f93137dba5bd62d68f5"


class RecordTests(unittest.TestCase):
    def test_batch_history_survives_roster_removal(self):
        pins = self.documents["pins.json"]
        pins["batches"] = [
            {
                "id": "completed",
                "status": "complete",
                "pins": pins["approved"],
                "projects": {"removed-member": "3" * 40},
            }
        ]
        (self.root / "policy/pins.json").write_text(json.dumps(pins))
        config, loaded = records.load(self.root)
        self.assertNotIn("removed-member", config["_members"])
        self.assertEqual(loaded, pins)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace / "source"
        self.documents = {
            "config.json": {
                "schemaVersion": 1,
                "policyRepository": "owner/policy",
                "stableBranch": "nixos-26.05",
            },
            "pins.json": {
                "schemaVersion": 1,
                "approved": {"stable": "1" * 40, "unstable": "2" * 40},
                "batches": [],
            },
            "members.json": {
                "schemaVersion": 1,
                "members": {"member": "owner/member"},
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
