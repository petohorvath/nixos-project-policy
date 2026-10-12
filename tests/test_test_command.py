"""`test PATH --nixpkgs locked|stable|unstable` through the CLI."""

import contextlib
import io
import itertools
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from tests.fixtures.cases import RepoTestCase
from tests.fixtures.data import NEW_PAIR, NEW_STABLE, NEW_UNSTABLE, STABLE, lockfile
from tests.fixtures.nix_runs import NixRunFixture
from tools import policy


OVERRIDE = "--override-input"


class TestCommandTests(NixRunFixture, RepoTestCase):
    def setUp(self):
        super().setUp()
        # Pins differ from the locked root nixpkgs, so every report shows
        # which revision the run used.
        self.pins.update(NEW_PAIR)

    def test_locked_mode_runs_checks_with_the_committed_lock(self):
        code, report = self.run_test("locked")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["nixpkgs"], "locked")
        self.assertEqual(report["system"], "x86_64-linux")
        self.assertEqual(report["expectedRevision"], STABLE)
        self.assertEqual(report["resolvedRevision"], STABLE)
        self.assertEqual(report["checks"], ["behavior"])
        self.assertEqual(report["issues"], [])
        for command in (
            self.nix(["nix", "flake"])
            + self.nix(["nix", "eval", "--json"])
            + self.nix(["nix", "build"])
        ):
            self.assertIn("--no-update-lock-file", command)
            self.assertNotIn(OVERRIDE, command)
        self.assertEqual(len(self.nix(["nix", "flake", "check"])), 1)

    def test_entries_needing_missing_system_features_are_skipped_by_name(self):
        self.checks = {
            "behavior": [],
            "needs-kvm": ["kvm"],
            "with.dot": ["big-parallel"],
            "large": ["big-parallel", "gpu", "kvm"],
        }
        code, report = self.run_test("locked")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(
            report["checks"], ["behavior", "large", "needs-kvm", "with.dot"]
        )
        self.assertEqual(
            report["skipped"], {"large": ["gpu", "kvm"], "needs-kvm": ["kvm"]}
        )
        (check,) = self.nix(["nix", "flake", "check"])
        self.assertIn("--no-build", check)
        (build,) = self.nix(["nix", "build"])
        root = self.root.resolve()
        self.assertEqual(
            [argument for argument in build if argument.startswith(str(root))],
            [
                f'{root}#checks.x86_64-linux."behavior"',
                f'{root}#checks.x86_64-linux."with.dot"',
            ],
        )
        self.assertIn("--no-update-lock-file", build)

    def test_vm_tests_build_only_with_vm_and_other_entries_only_without(self):
        self.checks = {
            "behavior": [],
            "vm-boot": ["kvm", "nixos-test"],
            "vm-reads-boot": ["nixos-test"],
        }
        self.features = "kvm nixos-test"
        root = self.root.resolve()
        # The Tests job evaluates every output; the VM job builds only.
        for options, entries, evaluations in [
            ((), ["behavior"], 1),
            (("--vm",), ["vm-boot", "vm-reads-boot"], 0),
        ]:
            with self.subTest(options=options):
                self.commands.clear()
                code, report = self.run_test("locked", *options)
                self.assertEqual(code, 0, report)
                self.assertEqual(report["checks"], entries)
                self.assertEqual(len(self.nix(["nix", "flake", "check"])), evaluations)
                (build,) = self.nix(["nix", "build"])
                self.assertEqual(
                    [argument for argument in build if argument.startswith(str(root))],
                    [f'{root}#checks.x86_64-linux."{name}"' for name in entries],
                )
        self.checks = {"behavior": []}
        self.commands.clear()
        code, report = self.run_test("locked", "--vm")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["checks"], [])
        self.assertEqual(self.nix(["nix", "build"]), [])

    def test_vm_tests_without_kvm_pass_as_skipped_without_a_build(self):
        self.checks = {"behavior": [], "vm-boot": ["kvm", "nixos-test"]}
        code, report = self.run_test("stable", "--vm")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["skipped"], {"vm-boot": ["kvm"]})
        self.assertEqual(self.nix(["nix", "build"]), [])
        self.features = "kvm nixos-test"
        code, report = self.run_test("stable", "--vm")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["skipped"], {})
        self.assertEqual(len(self.nix(["nix", "build"])), 1)

    def test_pinned_modes_override_root_nixpkgs_with_the_bundled_pin(self):
        # VM tests run against each pin like the other entries.
        self.checks = {"behavior": [], "vm-boot": ["kvm", "nixos-test"]}
        self.features = "kvm nixos-test"
        for (mode, revision), options in itertools.product(
            NEW_PAIR.items(), [(), ("--vm",)]
        ):
            with self.subTest(nixpkgs=mode, options=options):
                self.commands.clear()
                code, report = self.run_test(mode, *options)
                self.assertEqual(code, 0, report)
                self.assertEqual(report["nixpkgs"], mode)
                self.assertEqual(report["expectedRevision"], revision)
                self.assertEqual(report["resolvedRevision"], revision)
                self.assertEqual(report["skipped"], {})
                override = [OVERRIDE, "nixpkgs", f"github:NixOS/nixpkgs/{revision}"]
                for prefix in [
                    ["nix", "flake", "metadata"],
                    ["nix", "eval", "--json"],
                    *([] if options else [["nix", "flake", "check"]]),
                    ["nix", "build"],
                ]:
                    (command,) = self.nix(prefix)
                    self.assertEqual(command[-3:], override)
                    self.assertNotIn("--no-update-lock-file", command)

    def test_a_mode_with_the_revision_of_an_earlier_mode_passes_without_nix(self):
        # The fixture lock holds STABLE as root nixpkgs.
        for pins, mode, same in [
            ({"stable": STABLE, "unstable": NEW_UNSTABLE}, "stable", "locked"),
            ({"stable": NEW_STABLE, "unstable": STABLE}, "unstable", "locked"),
            ({"stable": NEW_STABLE, "unstable": NEW_STABLE}, "unstable", "stable"),
        ]:
            for options in [(), ("--vm",)]:
                with self.subTest(pins=pins, nixpkgs=mode, options=options):
                    self.pins.update(pins)
                    self.commands.clear()
                    code, report = self.run_test(mode, *options)
                    self.assertEqual(code, 0, report)
                    self.assertEqual(report["status"], "pass")
                    self.assertEqual(report["sameAs"], same)
                    self.assertEqual(report["expectedRevision"], pins[mode])
                    self.assertEqual(self.commands, [])
        self.pins.update(stable=STABLE)
        for mode in ["locked", "unstable"]:
            with self.subTest(nixpkgs=mode):
                self.commands.clear()
                code, report = self.run_test(mode)
                self.assertEqual(code, 0, report)
                self.assertIsNone(report["sameAs"])
                self.assertEqual(len(self.nix(["nix", "build"])), 1)

    def test_follows_and_renamed_nodes_resolve_root_nixpkgs(self):
        graph = lockfile()
        graph["nodes"]["entry"]["inputs"].update(
            library="library", nixpkgs=["library", "pkgs"]
        )
        graph["nodes"]["library"] = {"inputs": {"pkgs": "arbitrary-node"}}
        self.write("flake.lock", json.dumps(graph))
        for mode in ["locked", "stable"]:
            with self.subTest(nixpkgs=mode):
                code, report = self.run_test(mode)
                self.assertEqual(code, 0, report)
                self.assertEqual(
                    report["resolvedRevision"],
                    STABLE if mode == "locked" else NEW_PAIR["stable"],
                )

    def test_test_takes_no_project_name(self):
        code, report = self.run_test("stable")
        self.assertEqual(code, 0, report)
        for options in [["--project", "example"], ["--nixpkgs", "other"], []]:
            with (
                self.subTest(options=options),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                arguments = ["test", str(self.root)]
                if options != ["--nixpkgs", "other"] and options:
                    arguments += ["--nixpkgs", "stable"]
                self.run_policy(*arguments, *options)
            self.assertEqual(error.exception.code, 2)

    def test_empty_or_missing_checks_fail_before_building(self):
        for checks, problem in [
            ([], "at least one check"),
            ({}, "at least one check"),
            (None, "at least one check"),
            (["behavior"], "at least one check"),
            ("behavior", "at least one check"),
            ({"behavior": "kvm"}, "requiredSystemFeatures"),
            ({"behavior": [1]}, "requiredSystemFeatures"),
        ]:
            for mode in ["locked", "unstable"]:
                with self.subTest(checks=checks, nixpkgs=mode):
                    self.commands.clear()
                    self.checks = checks
                    code, report = self.run_test(mode)
                    self.assertEqual(code, 1, report)
                    issues = " ".join(report["issues"])
                    self.assertIn("checks.x86_64-linux", issues)
                    self.assertIn(problem, issues)
                    self.assertEqual(self.nix(["nix", "flake", "check"]), [])
        self.checks = {"behavior": []}
        self.fail_stage = ["nix", "eval", "--json"]
        code, report = self.run_test("locked")
        self.assertEqual(code, 1, report)
        self.assertIn("checks.x86_64-linux", " ".join(report["issues"]))
        self.assertEqual(self.nix(["nix", "flake", "check"]), [])

    def test_an_ignored_override_fails_before_building(self):
        self.metadata = lockfile()
        for mode in ["stable", "unstable"]:
            with self.subTest(nixpkgs=mode):
                self.commands.clear()
                code, report = self.run_test(mode)
                self.assertEqual(code, 1, report)
                self.assertEqual(report["expectedRevision"], NEW_PAIR[mode])
                self.assertEqual(report["resolvedRevision"], STABLE)
                self.assertIn("does not match", " ".join(report["issues"]))
                self.assertEqual(self.nix(["nix", "flake", "check"]), [])

    def test_a_wrong_or_foreign_resolved_nixpkgs_fails(self):
        for failure in [
            "wrong-revision",
            "missing",
            "wrong-owner",
            "custom-host",
            "lookalike-host",
            "mutable",
            "malformed",
        ]:
            for mode in ["locked", "stable"]:
                with self.subTest(failure=failure, nixpkgs=mode):
                    self.metadata = lockfile()
                    graph = self.metadata
                    node = graph["nodes"]["arbitrary-node"]
                    if mode == "stable":
                        node["locked"]["rev"] = NEW_PAIR["stable"]
                    if failure == "wrong-revision":
                        node["locked"]["rev"] = "9" * 40
                    elif failure == "missing":
                        del graph["nodes"]["entry"]["inputs"]["nixpkgs"]
                    elif failure == "wrong-owner":
                        node["locked"]["owner"] = "someone-else"
                    elif failure == "custom-host":
                        node["locked"]["host"] = "github.example.org"
                    elif failure == "lookalike-host":
                        node["locked"] = {
                            "type": "git",
                            "url": "https://evilgithub.com/NixOS/nixpkgs",
                            "rev": node["locked"]["rev"],
                        }
                    elif failure == "mutable":
                        del node["locked"]["rev"]
                    else:
                        graph["version"] = 6
                    self.commands.clear()
                    code, report = self.run_test(mode)
                    self.assertEqual(code, 1, report)
                    self.assertEqual(report["status"], "fail")
                    self.assertTrue(report["issues"])
                    self.assertEqual(self.nix(["nix", "flake", "check"]), [])

    def test_a_repo_without_a_locked_root_nixpkgs_fails(self):
        graph = lockfile()
        del graph["nodes"]["entry"]["inputs"]["nixpkgs"]
        self.write("flake.lock", json.dumps(graph))
        code, report = self.run_test("locked")
        self.assertEqual(code, 1, report)
        self.assertIn("root nixpkgs", " ".join(report["issues"]))
        (self.root / "flake.lock").unlink()
        code, report = self.run_test("stable")
        self.assertEqual(code, 1, report)
        self.assertIn("flake.lock", " ".join(report["issues"]))
        self.assertEqual(self.nix(["nix", "flake", "check"]), [])

    def test_a_changed_lock_fails_every_mode(self):
        original = self.run_command

        def mutate(command, **kwargs):
            if command[:3] == ["nix", "flake", "check"]:
                self.write("flake.lock", json.dumps(lockfile(), indent=1))
            return original(command, **kwargs)

        for mode in ["locked", "stable", "unstable"]:
            with (
                self.subTest(nixpkgs=mode),
                patch.object(policy.subprocess, "run", side_effect=mutate),
            ):
                self.write("flake.lock", json.dumps(lockfile()))
                code, report = self.run_test(mode)
                self.assertEqual(code, 1, report)
                self.assertIn("flake.lock changed during the run", report["issues"])

    def test_changed_or_escaping_sources_fail(self):
        original = self.run_command

        def mutate(command, **kwargs):
            if command[:3] == ["nix", "flake", "check"]:
                self.write("generated.nix", "{}")
            return original(command, **kwargs)

        with patch.object(policy.subprocess, "run", side_effect=mutate):
            code, report = self.run_test("stable")
        self.assertEqual(code, 1, report)
        self.assertIn("Repo sources changed during the run", report["issues"])

        def escape(command, **kwargs):
            if command[:3] == ["nix", "flake", "check"]:
                outside = Path(self.temp.name) / "outside"
                outside.write_text("external")
                (self.root / "link").symlink_to(outside)
            return original(command, **kwargs)

        with patch.object(policy.subprocess, "run", side_effect=escape):
            code, report = self.run_test("locked")
        self.assertEqual(code, 1, report)
        self.assertIn("escapes", " ".join(report["issues"]))

    def test_failed_nix_commands_fail_with_the_command_log(self):
        for stage in [
            ["nix", "eval", "--raw"],
            ["nix", "flake", "metadata"],
            ["nix", "flake", "check"],
            ["nix", "config", "show"],
            ["nix", "build"],
        ]:
            with self.subTest(stage=stage):
                self.fail_stage = stage
                code, report = self.run_test("unstable")
                self.assertEqual(code, 1, report)
                command = report["commands"][-1]["command"]
                self.assertEqual(command[: len(stage)], stage)
                self.assertEqual(report["commands"][-1]["returncode"], 1)
        self.fail_stage = None
        self.command_error = FileNotFoundError("nix unavailable")
        code, report = self.run_test("locked")
        self.assertEqual(code, 1, report)
        self.assertIn("nix unavailable", report["commands"][-1]["error"])

    def test_any_valid_host_system_is_used_and_invalid_ones_fail(self):
        for host in ["aarch64-linux", "aarch64-darwin", "riscv64-linux"]:
            with self.subTest(host=host):
                self.host = host
                code, report = self.run_test("stable")
                self.assertEqual(code, 0, report)
                self.assertEqual(report["system"], host)
                self.assertEqual(report["checks"], ["behavior"])
        self.host = "../invalid"
        code, report = self.run_test("stable")
        self.assertEqual(code, 1, report)
        self.assertIn("Invalid host system", " ".join(report["issues"]))

    def test_malformed_bundled_pins_are_an_error(self):
        self.pins["approved"] = NEW_PAIR
        code, report = self.run_test("stable")
        self.assertEqual(code, 2, report)
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
