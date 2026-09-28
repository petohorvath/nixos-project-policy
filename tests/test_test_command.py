"""`test PATH --nixpkgs locked|stable|unstable` through the CLI."""

import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from tests.fixtures.cases import RepoTestCase
from tests.fixtures.data import NEW_PAIR, STABLE, lockfile
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
        for command in self.nix(["nix", "flake"]) + self.nix(["nix", "eval", "--json"]):
            self.assertIn("--no-update-lock-file", command)
            self.assertNotIn(OVERRIDE, command)
        self.assertEqual(len(self.nix(["nix", "flake", "check"])), 1)

    def test_pinned_modes_override_root_nixpkgs_with_the_bundled_pin(self):
        for mode, revision in NEW_PAIR.items():
            with self.subTest(nixpkgs=mode):
                self.commands.clear()
                code, report = self.run_test(mode)
                self.assertEqual(code, 0, report)
                self.assertEqual(report["nixpkgs"], mode)
                self.assertEqual(report["expectedRevision"], revision)
                self.assertEqual(report["resolvedRevision"], revision)
                override = [OVERRIDE, "nixpkgs", f"github:NixOS/nixpkgs/{revision}"]
                for prefix in [
                    ["nix", "flake", "metadata"],
                    ["nix", "eval", "--json"],
                    ["nix", "flake", "check"],
                ]:
                    (command,) = self.nix(prefix)
                    self.assertEqual(command[-3:], override)
                    self.assertNotIn("--no-update-lock-file", command)

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
        for checks in [[], {}, None, [1], "behavior"]:
            for mode in ["locked", "unstable"]:
                with self.subTest(checks=checks, nixpkgs=mode):
                    self.commands.clear()
                    self.checks = checks
                    code, report = self.run_test(mode)
                    self.assertEqual(code, 1, report)
                    self.assertIn("checks.x86_64-linux", " ".join(report["issues"]))
                    self.assertEqual(self.nix(["nix", "flake", "check"]), [])
        self.checks = ["behavior"]
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
        ]:
            with self.subTest(stage=stage):
                self.fail_stage = stage
                code, report = self.run_test("unstable")
                self.assertEqual(code, 1, report)
                self.assertEqual(report["commands"][-1]["command"][:3], stage)
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

    def test_compatibility_and_host_checks_are_removed(self):
        for arguments in [
            ["compatibility", ".", "--project", "example", "--channel", "stable"],
            ["host-checks", "."],
        ]:
            with (
                self.subTest(command=arguments[0]),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                policy.main(arguments)
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
