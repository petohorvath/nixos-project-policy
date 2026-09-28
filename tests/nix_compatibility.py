"""Host-level Nix integration test; run explicitly outside the build sandbox."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.fixtures.cli import checker_command
from tools import policy, records


class NixCompatibilityTests(unittest.TestCase):
    def test_locked_mode_requires_nonempty_checks_and_builds_them(self):
        system = self.run_command(
            ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"]
        ).strip()
        graph = policy.LockGraph(records.read_json(policy.SOURCE_ROOT / "flake.lock"))
        revision = graph.nodes[graph.resolve(["nixpkgs"])]["locked"]["rev"]
        with tempfile.TemporaryDirectory(prefix="policy-checks-fixture-") as temporary:
            workspace = Path(temporary)
            data = self.write_data(
                workspace, {"stable": revision, "unstable": revision}
            )
            root = workspace / "project"
            root.mkdir()
            flake = root / "flake.nix"
            build = (
                'nixpkgs.legacyPackages."SYSTEM".runCommand "behavior" {} "touch $out"'
            ).replace("SYSTEM", system)
            for output, outcome in [
                ("", "does not evaluate"),
                (f'checks."{system}" = {{}};', "at least one check"),
                (
                    'checks."another-system" = { behavior = null; };',
                    "does not evaluate",
                ),
                # The name guard passes; nix flake check still validates values.
                (f'checks."{system}" = {{ behavior = null; }};', "flake check"),
                (f'checks."{system}" = {{ behavior = {build}; }};', None),
            ]:
                with self.subTest(output=output):
                    flake.write_text(
                        '{ inputs.nixpkgs.url = "github:NixOS/nixpkgs/REVISION";'
                        " outputs = { nixpkgs, ... }: { OUTPUT }; }".replace(
                            "REVISION", revision
                        ).replace("OUTPUT", output)
                    )
                    self.run_command(["nix", "flake", "lock", str(root)])
                    before = policy.fingerprints(root)
                    process = subprocess.run(
                        [
                            *checker_command(data),
                            "test",
                            str(root),
                            "--nixpkgs",
                            "locked",
                        ],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    report = json.loads(process.stdout)
                    self.assertEqual(
                        process.returncode, 0 if outcome is None else 1, report
                    )
                    self.assertEqual(report["system"], system)
                    self.assertEqual(report["resolvedRevision"], revision)
                    self.assertEqual(policy.fingerprints(root), before)
                    if outcome is None:
                        self.assertEqual(report["checks"], ["behavior"])
                    elif outcome == "flake check":
                        self.assertEqual(report["checks"], ["behavior"])
                        self.assertEqual(
                            report["commands"][-1]["command"][:3],
                            ["nix", "flake", "check"],
                        )
                        self.assertNotEqual(report["commands"][-1]["returncode"], 0)
                    else:
                        self.assertIn(outcome, " ".join(report["issues"]))

    @staticmethod
    def write_data(workspace, pins):
        data = workspace / "data"
        data.mkdir()
        (data / "pins.json").write_text(
            json.dumps({"stableBranch": "nixos-26.05", **pins})
        )
        (data / "repos.json").write_text(json.dumps({"repos": []}))
        return data

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
            data = self.write_data(workspace, pins)
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
            # The fixture locks the stable pin, so the locked mode resolves it too.
            for mode, revision in [("locked", pins["stable"]), *pins.items()]:
                with self.subTest(nixpkgs=mode):
                    report = json.loads(
                        self.run_command(
                            [
                                *checker_command(data),
                                "test",
                                str(project),
                                "--nixpkgs",
                                mode,
                            ]
                        )
                    )
                    self.assertEqual(report["status"], "pass", report)
                    self.assertEqual(report["expectedRevision"], revision)
                    self.assertEqual(report["resolvedRevision"], revision)
                    self.assertEqual(report["checks"], ["selectedInput"])
                    self.assertEqual(report["commands"][-1]["returncode"], 0)
                    self.assertEqual((project / "flake.lock").read_bytes(), lock_before)
                    self.assertEqual(
                        self.selected_input(project, report["system"], mode, pins),
                        revision,
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
            stale = subprocess.run(
                [*checker_command(data), "test", str(project), "--nixpkgs", "locked"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            self.assertEqual(stale.returncode, 1, stale.stdout)
            self.assertEqual((project / "flake.lock").read_bytes(), lock_before)

    def selected_input(self, project, system, mode, pins):
        """Return the nixpkgs revision that the built check recorded."""
        flags = (
            ["--no-update-lock-file"]
            if mode == "locked"
            else ["--override-input", "nixpkgs", f"github:NixOS/nixpkgs/{pins[mode]}"]
        )
        output = self.run_command(
            [
                "nix",
                "build",
                "--no-link",
                "--print-out-paths",
                f"{project}#checks.{system}.selectedInput",
                *flags,
            ]
        ).strip()
        return Path(output).read_text()


if __name__ == "__main__":
    unittest.main()
