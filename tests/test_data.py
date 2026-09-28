"""Validation of the pins and repo list bundled with the checker."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.fixtures.cli import invoke
from tools import data


PINS = {"stable": "1" * 40, "unstable": "2" * 40, "stableBranch": "nixos-26.05"}
REPOS = {"repos": ["owner/first", "owner/second"]}


class BundledDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_root = Path(temporary.name)
        self.enterContext(patch.object(data, "DATA_ROOT", self.data_root))

    def validate(self, pins=PINS, repos=REPOS, *, raw=None):
        (self.data_root / "pins.json").write_text(json.dumps(pins))
        (self.data_root / "repos.json").write_text(
            json.dumps(repos) if raw is None else raw
        )
        return invoke("validate")

    def test_valid_data_is_reported(self):
        code, report = self.validate()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["pins"], PINS)
        self.assertEqual(report["repos"], REPOS["repos"])

    def test_an_empty_repo_list_is_valid(self):
        code, report = self.validate(repos={"repos": []})
        self.assertEqual(code, 0, report)

    def test_malformed_pins_fail(self):
        cases = {
            "not an object": [],
            "missing stable": {"unstable": "2" * 40, "stableBranch": "nixos-26.05"},
            "unknown field": {**PINS, "schemaVersion": 1},
            "old record shape": {
                "schemaVersion": 1,
                "stableBranch": "nixos-26.05",
                "approved": {"stable": "1" * 40, "unstable": "2" * 40},
            },
            "short revision": {**PINS, "stable": "1" * 39},
            "long revision": {**PINS, "unstable": "2" * 41},
            "branch name": {**PINS, "stable": "nixos-26.05"},
            "uppercase revision": {**PINS, "stable": "A" * 40},
            "null revision": {**PINS, "unstable": None},
            "unstable branch": {**PINS, "stableBranch": "nixos-unstable"},
            "missing branch": {**PINS, "stableBranch": None},
        }
        for name, pins in cases.items():
            with self.subTest(name):
                code, report = self.validate(pins=pins)
                self.assertIn(code, {1, 2}, report)
                self.assertEqual(report["status"], "error")

    def test_malformed_repo_lists_fail(self):
        cases = {
            "not an object": ["owner/first"],
            "unknown field": {**REPOS, "schemaVersion": 1},
            "old roster shape": {"schemaVersion": 1, "members": {"a": "owner/a"}},
            "not a list": {"repos": {"first": "owner/first"}},
            "missing owner": {"repos": ["first"]},
            "url": {"repos": ["https://github.com/owner/first"]},
            "extra segment": {"repos": ["owner/first/extra"]},
            "not a string": {"repos": [1]},
            "duplicate": {"repos": ["owner/first", "owner/first"]},
            "case-insensitive duplicate": {"repos": ["owner/first", "Owner/First"]},
        }
        for name, repos in cases.items():
            with self.subTest(name):
                code, report = self.validate(repos=repos)
                self.assertIn(code, {1, 2}, report)
                self.assertEqual(report["status"], "error")

    def test_duplicate_json_keys_fail(self):
        code, report = self.validate(raw='{"repos": [], "repos": ["owner/first"]}')
        self.assertEqual(code, 2, report)
        self.assertIn("Duplicate JSON key", report["error"])

    def test_missing_files_fail(self):
        code, report = invoke("validate")
        self.assertEqual(code, 2, report)
