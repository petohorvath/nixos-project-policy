"""Host-level Nix integration test; run explicitly outside the build sandbox."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import policy


class NixCompatibilityTests(unittest.TestCase):
    def run_command(self, command, **kwargs):
        result = subprocess.run(
            command, check=False, text=True, capture_output=True, **kwargs
        )
        self.assertEqual(
            result.returncode, 0, f"{command}:\n{result.stdout}\n{result.stderr}"
        )
        return result.stdout

    def test_real_overrides_build_checks_preserve_locks_and_reject_default_updates(
        self,
    ):
        graph = policy.LockGraph(policy.read_json(policy.SOURCE_ROOT / "flake.lock"))
        pins = {
            channel: graph.nodes[graph.resolve([name])]["locked"]["rev"]
            for channel, name in [
                ("stable", "nixpkgs"),
                ("unstable", "nixpkgs-unstable"),
            ]
        }
        with tempfile.TemporaryDirectory(prefix="policy-nix-fixture-") as temporary:
            workspace = Path(temporary)
            project = workspace / "project"
            project.mkdir()
            flake = project / "flake.nix"
            flake.write_text(
                """{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/STABLE";
  outputs = { nixpkgs, ... }: {
    checks = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (system: {
      selectedInput = nixpkgs.legacyPackages.${system}.runCommand "selected-input" {} ''
        test -n "${nixpkgs.rev}"
        printf '%s' "${nixpkgs.rev}" > "$out"
      '';
    });
  };
}
""".replace("STABLE", pins["stable"])
            )
            self.run_command(["git", "init", "-q", str(project)])
            self.run_command(["git", "-C", str(project), "add", "flake.nix"])
            self.run_command(["nix", "flake", "lock", str(project)])
            self.run_command(["git", "-C", str(project), "add", "flake.lock"])
            self.run_command(
                [
                    "git",
                    "-C",
                    str(project),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-qm",
                    "test: Create compatibility fixture",
                ]
            )
            records = workspace / "records/policy"
            records.mkdir(parents=True)
            (records / "projects.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "policyRepository": "petohorvath/nixos-project-policy",
                        "projects": {
                            "fixture": {
                                "repository": "example/fixture",
                                "adopted": False,
                                "policyVersion": "v"
                                + (policy.SOURCE_ROOT / "VERSION").read_text().strip(),
                                "vmTargets": [],
                                "requiredArchitectures": [
                                    "x86_64-linux",
                                    "aarch64-linux",
                                ],
                            }
                        },
                    }
                )
            )
            (records / "pins.json").write_text(
                json.dumps({"schemaVersion": 1, "approved": pins, "batches": []})
            )
            lock_before = (project / "flake.lock").read_bytes()
            self.run_command(
                [
                    "nix",
                    "flake",
                    "check",
                    str(project),
                    "--no-update-lock-file",
                    "--print-build-logs",
                ]
            )
            for channel, revision in pins.items():
                with self.subTest(channel=channel):
                    evidence = workspace / channel
                    report = json.loads(
                        self.run_command(
                            [
                                sys.executable,
                                str(policy.SOURCE_ROOT / "tools/policy.py"),
                                "--policy-root",
                                str(records.parent),
                                "compatibility",
                                str(project),
                                "--project",
                                "fixture",
                                "--channel",
                                channel,
                                "--output",
                                str(evidence),
                            ]
                        )
                    )
                    self.assertEqual(report["status"], "pass", report)
                    self.assertEqual(report["resolvedRevision"], revision)
                    self.assertEqual(report["commands"][-1]["returncode"], 0)
                    self.assertEqual((project / "flake.lock").read_bytes(), lock_before)
                    self.assertTrue((evidence / "metadata.json").is_file())
            flake.write_text(
                flake.read_text().replace(pins["stable"], pins["unstable"])
            )
            default = subprocess.run(
                ["nix", "flake", "check", str(project), "--no-update-lock-file"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(default.returncode, 0)
            self.assertIn("lock file", default.stderr)
            self.assertEqual((project / "flake.lock").read_bytes(), lock_before)


if __name__ == "__main__":
    unittest.main()
