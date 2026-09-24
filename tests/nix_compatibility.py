"""Host-level Nix integration test; run explicitly outside the build sandbox."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools import policy, records


class NixCompatibilityTests(unittest.TestCase):
    def test_committed_lock_requires_nonempty_host_checks(self):
        system = self.run_command(
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"]
        ).strip()
        with tempfile.TemporaryDirectory(prefix="policy-checks-fixture-") as temporary:
            root = Path(temporary)
            flake = root / "flake.nix"
            for output, passes in [
                ("", False),
                (f'checks."{system}" = {{}};', False),
                ('checks."another-system" = { behavior = null; };', False),
                (f'checks."{system}" = {{ behavior = null; }};', True),
            ]:
                with self.subTest(output=output):
                    flake.write_text("{ outputs = { self }: { " + output + " }; }")
                    self.run_command(["nix", "flake", "lock", str(root)])
                    before = policy.fingerprints(root)
                    if output == "":
                        # A bare flake check accepts a flake without tests.
                        self.run_command(
                            [
                                "nix",
                                "flake",
                                "check",
                                str(root),
                                "--no-update-lock-file",
                            ]
                        )
                    process = subprocess.run(
                        [
                            sys.executable,
                            str(policy.SOURCE_ROOT / "tools/policy.py"),
                            "host-checks",
                            str(root),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    report = json.loads(process.stdout)
                    self.assertEqual(process.returncode, 0 if passes else 1, report)
                    self.assertEqual(report["system"], system)
                    self.assertEqual(policy.fingerprints(root), before)
                    if passes:
                        self.assertEqual(report["checks"], ["behavior"])
                        # The guard only requires names; flake check still validates values.
                        build = subprocess.run(
                            [
                                "nix",
                                "flake",
                                "check",
                                str(root),
                                "--no-update-lock-file",
                            ],
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                        self.assertNotEqual(build.returncode, 0)

    def test_shell_probe_requires_working_default_shell_without_fixed_tools(self):
        graph = policy.LockGraph(records.read_json(policy.SOURCE_ROOT / "flake.lock"))
        revision = graph.nodes[graph.resolve(["nixpkgs"])]["locked"]["rev"]
        with tempfile.TemporaryDirectory(prefix="policy-shell-fixture-") as temporary:
            root = Path(temporary)
            flake = root / "flake.nix"
            source = """{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/REVISION";
  outputs = { nixpkgs, ... }: let
    systems = [ "x86_64-linux" "aarch64-linux" ];
    forSystems = nixpkgs.lib.genAttrs systems;
    shell = system: nixpkgs.legacyPackages.${system}.mkShellNoCC {
      shellHook = "SHELL_HOOK";
    };
  in {
    packages = forSystems (system: { default = shell system; });
    formatter = forSystems (system: nixpkgs.legacyPackages.${system}.hello);
    SHELL_OUTPUT
  };
}
""".replace("REVISION", revision)
            flake.write_text(
                source.replace("SHELL_OUTPUT", "").replace("SHELL_HOOK", "")
            )
            self.run_command(["nix", "flake", "lock", str(root)])
            lock_before = (root / "flake.lock").read_bytes()
            self.run_command(
                [
                    "nix",
                    "develop",
                    "--no-update-lock-file",
                    "--ignore-environment",
                    f"path:{root}",
                    "--command",
                    "bash",
                    "--help",
                ]
            )
            for name, hook, passes in [
                (None, "", False),
                ("other", "", False),
                ("default", "", True),
                ("default", "exit 23", False),
            ]:
                with self.subTest(shell=name, hook=hook):
                    output = (
                        "devShells = forSystems (system: { "
                        + name
                        + " = shell system; });"
                        if name
                        else ""
                    )
                    flake.write_text(
                        source.replace("SHELL_OUTPUT", output).replace(
                            "SHELL_HOOK", hook
                        )
                    )
                    issues = policy.check_shell(root)
                    if passes:
                        self.assertEqual(issues, [])
                        self.run_command(
                            [
                                "nix",
                                "develop",
                                "--no-update-lock-file",
                                "--ignore-environment",
                                f"path:{root}",
                                "--command",
                                "bash",
                                "-c",
                                "! command -v nil && ! command -v nixfmt",
                            ]
                        )
                    else:
                        self.assertTrue(
                            issues,
                            "Missing or broken default shells must fail the probe",
                        )
                    self.assertEqual((root / "flake.lock").read_bytes(), lock_before)

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
        graph = policy.LockGraph(records.read_json(policy.SOURCE_ROOT / "flake.lock"))
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
            release = "v" + (policy.SOURCE_ROOT / "VERSION").read_text().strip()
            caller = project / ".github/workflows/policy.yml"
            caller.parent.mkdir(parents=True)
            caller.write_text(
                json.dumps(
                    {
                        "on": {
                            "pull_request": {
                                "types": ["opened", "synchronize", "reopened", "edited"]
                            }
                        },
                        "jobs": {
                            "policy": {
                                "name": "Policy",
                                "uses": f"petohorvath/nixos-project-policy/.github/workflows/check.yml@{release}",
                                "with": {
                                    "project": "fixture",
                                    "policy_version": release,
                                    "required_architectures": '["x86_64-linux", "aarch64-linux"]',
                                },
                            }
                        },
                    }
                )
            )
            self.run_command(["git", "init", "-q", str(project)])
            self.run_command(["git", "-C", str(project), "add", "flake.nix", ".github"])
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
            record_directory = workspace / "records/policy"
            record_directory.mkdir(parents=True)
            (record_directory / "config.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "policyRepository": "petohorvath/nixos-project-policy",
                        "stableBranch": "nixos-26.05",
                    }
                )
            )
            (record_directory / "pins.json").write_text(
                json.dumps({"schemaVersion": 1, "approved": pins, "batches": []})
            )
            (record_directory / "members.json").write_text(
                json.dumps({"schemaVersion": 1, "members": {}})
            )
            (record_directory / "support.json").write_text(
                json.dumps({"schemaVersion": 1, "retirements": {}})
            )
            lock_before = (project / "flake.lock").read_bytes()
            caller_before = caller.read_bytes()
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
                                str(record_directory.parent),
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
            proposal = workspace / "proposal"
            shutil.copytree(record_directory.parent, proposal)
            approved_before = (record_directory / "pins.json").read_bytes()
            (proposal / "policy/pins.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "approved": None,
                        "batches": [
                            {
                                "id": "unmerged",
                                "status": "candidate",
                                "pins": pins,
                                "projects": {"fixture": policy.git_revision(project)},
                            }
                        ],
                    }
                )
            )
            for channel, revision in pins.items():
                with self.subTest(candidate_channel=channel):
                    report = json.loads(
                        self.run_command(
                            [
                                sys.executable,
                                str(policy.SOURCE_ROOT / "tools/policy.py"),
                                "--policy-root",
                                str(proposal),
                                "compatibility",
                                str(project),
                                "--project",
                                "fixture",
                                "--batch",
                                "unmerged",
                                "--channel",
                                channel,
                                "--output",
                                str(workspace / f"candidate-{channel}"),
                            ]
                        )
                    )
                    self.assertEqual(report["status"], "candidate-pass", report)
                    self.assertEqual(report["resolvedRevision"], revision)
                    self.assertEqual(report["pinStatus"], "candidate")
                    self.assertEqual(caller.read_bytes(), caller_before)
                    self.assertEqual((project / "flake.lock").read_bytes(), lock_before)
                    self.assertEqual(
                        (record_directory / "pins.json").read_bytes(), approved_before
                    )
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
            self.assertEqual(policy.check_host_checks(project)["status"], "fail")
            self.assertEqual((project / "flake.lock").read_bytes(), lock_before)


if __name__ == "__main__":
    unittest.main()
