"""Record storage and identities through the shared records interface."""

import json
from pathlib import Path
import tempfile
import unittest

from tests.fixtures.cli import invoke
from tools import records


# Fixed current-record digest, independent of the producer under test.
FULL_DIGEST = "340569d7bb06105600e773aaa4f90422b5ea9096b5c76a2aee5f7a8b52dd088f"


class RecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace / "source"
        self.documents = {
            "pins.json": {
                "stableBranch": "nixos-26.05",
                "schemaVersion": 1,
                "approved": {"stable": "1" * 40, "unstable": "2" * 40},
            },
            "members.json": {
                "schemaVersion": 1,
                "members": {"member": "owner/member"},
            },
        }
        (self.root / "policy").mkdir(parents=True)
        for name, value in self.documents.items():
            (self.root / "policy" / name).write_text(json.dumps(value))

    def test_records_load_and_validate_without_member_revision_bookkeeping(self):
        config, pins = records.load(self.root)
        self.assertEqual(pins, self.documents["pins.json"])
        self.assertEqual(config["_members"], {"member": "owner/member"})
        self.assertEqual(records.digest(config, pins), FULL_DIGEST)
        code, report = invoke("--policy-root", str(self.root), "validate")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["policyRecordsDigest"], FULL_DIGEST)

    def test_pin_records_reject_unknown_fields(self):
        for field in ["projects", "previous", "state"]:
            with self.subTest(field=field):
                pins = {**self.documents["pins.json"], field: {}}
                (self.root / "policy/pins.json").write_text(json.dumps(pins))
                with self.assertRaisesRegex(ValueError, "Pin records require only"):
                    records.load(self.root)

    def test_current_requirements_cannot_replace_release_identity_or_runners(self):
        expected = records.load(self.root)
        (self.root / "policy/requirements.json").write_text(
            json.dumps(
                {"schemaVersion": 1, "policyRepository": "attacker/policy", "ci": {}}
            )
        )
        self.assertEqual(records.load(self.root), expected)

    def test_stable_update_branch_is_required_and_bound_to_record_identity(self):
        config, pins = records.load(self.root)
        original = records.digest(config, pins)
        pins["stableBranch"] = "nixos-25.11"
        self.assertNotEqual(records.digest(config, pins), original)
        for invalid in [None, "", "nixos-unstable", "main", 1]:
            with self.subTest(branch=invalid):
                pins["stableBranch"] = invalid
                (self.root / "policy/pins.json").write_text(json.dumps(pins))
                with self.assertRaisesRegex(ValueError, "stable NixOS update branch"):
                    records.load(self.root)
