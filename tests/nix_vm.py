"""Host-level VM test discovery with real Nix; run outside the build sandbox."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import policy


def derivation(name, script):
    return (
        f'derivation {{ name = "{name}"; inherit system; builder = "/bin/sh"; '
        f'args = [ "-c" "{script}" ]; }}'
    )


class NixVmTests(unittest.TestCase):
    def setUp(self):
        self.system = subprocess.run(
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        temporary = tempfile.TemporaryDirectory(prefix="policy-vm-fixture-")
        self.root = Path(self.enterContext(temporary))

    def write_flake(self, outputs):
        (self.root / "flake.nix").write_text(
            "{ outputs = { self }: let system = "
            f'"{self.system}"; in {{ {outputs} }}; }}\n'
        )
        subprocess.run(["nix", "flake", "lock", str(self.root)], check=True)

    def vm(self):
        before = policy.fingerprints(self.root)
        process = subprocess.run(
            [sys.executable, str(policy.SOURCE_ROOT / "tools/policy.py"), "vm"]
            + [str(self.root)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(policy.fingerprints(self.root), before)
        return process.returncode, json.loads(process.stdout or process.stderr)

    def test_vm_tests_are_discovered_and_built_but_not_by_flake_check(self):
        passing = derivation("policy-vm-boot", "echo booted > $out")
        failing = derivation("policy-vm-broken", "exit 1")
        check = derivation("policy-vm-check", "echo checked > $out")
        self.write_flake(
            f"checks.${{system}}.behavior = {check}; "
            "legacyPackages.${system}.vmTests = "
            f"{{ boot = {passing}; broken = {failing}; }};"
        )
        # The broken VM test would fail this if flake check built vmTests.
        subprocess.run(
            ["nix", "flake", "check", str(self.root), "--no-update-lock-file"],
            check=True,
        )
        code, report = self.vm()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["system"], self.system)
        self.assertEqual(report["vmTests"], ["boot", "broken"])
        self.assertEqual(report["failed"], ["broken"])

    def test_missing_or_empty_vm_tests_are_not_applicable(self):
        for outputs in [
            "",
            "legacyPackages.${system} = { };",
            "legacyPackages.${system}.vmTests = { };",
        ]:
            with self.subTest(outputs=outputs):
                self.write_flake(outputs)
                code, report = self.vm()
                self.assertEqual(code, 0, report)
                self.assertEqual(report["status"], "not-applicable")
                self.assertEqual(report["vmTests"], [])
