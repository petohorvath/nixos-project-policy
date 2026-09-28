import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from tools import policy, records


from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.cli import invoke
from tests.fixtures.data import (
    NEW_STABLE,
    PAIR,
    UNSTABLE,
    lockfile,
    nixpkgs,
)


class LockTests(unittest.TestCase):
    def test_malformed_lock_shapes_fail_with_a_clear_error(self):
        malformed = [
            [],
            {"version": 6},
            {"version": 7, "nodes": [], "root": "root"},
            {"version": 7, "nodes": {"root": "bad"}, "root": "root"},
        ]
        for lock in malformed:
            with self.subTest(lock=lock), self.assertRaises(ValueError):
                policy.LockGraph(lock)

    def test_follows_resolves_from_nonstandard_root(self):
        lock = lockfile()
        lock["nodes"]["entry"]["inputs"]["alias"] = ["nixpkgs"]
        graph = policy.LockGraph(lock)
        self.assertEqual(graph.resolve(["alias"]), "arbitrary-node")
        self.assertEqual(len(graph.reachable()), 3)

    def test_nested_follows(self):
        lock = lockfile()
        lock["nodes"]["entry"]["inputs"]["library"] = "library"
        lock["nodes"]["library"] = {"inputs": {"pkgs": ["nixpkgs"]}}
        self.assertEqual(
            policy.LockGraph(lock).resolve(["library", "pkgs"]), "arbitrary-node"
        )

    def test_alias_cycles_fail(self):
        lock = lockfile()
        lock["nodes"]["entry"]["inputs"].update(nixpkgs=["alias"], alias=["nixpkgs"])
        with self.assertRaisesRegex(ValueError, "Cyclic follows"):
            policy.LockGraph(lock).reachable()

    def test_missing_node_and_missing_alias_fail(self):
        for reference in ["missing", ["missing"]]:
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                policy.LockGraph(lockfile()).resolve(reference)

    def test_unreachable_old_nodes_are_ignored(self):
        lock = lockfile()
        lock["nodes"]["unused"] = nixpkgs(NEW_STABLE, "nixos-26.05")
        self.assertNotIn("unused", policy.LockGraph(lock).reachable())

    def test_git_transport_identity(self):
        self.assertEqual(
            policy.repository_identity(
                {"locked": {"url": "https://github.com/NixOS/nixpkgs.git"}}
            ),
            "nixos/nixpkgs",
        )


class PinStateTests(unittest.TestCase):
    def test_floating_revisions_fail(self):
        with self.assertRaises(ValueError):
            records.validate_pair({"stable": "nixos-26.05", "unstable": UNSTABLE})


class ProjectTests(ProjectTestCase):
    def test_malformed_bundled_data_fails_every_command(self):
        self.pins["approved"] = PAIR
        for command in [
            ("validate",),
            ("ci", str(self.root)),
            ("check", str(self.root)),
        ]:
            with self.subTest(command=command[0]):
                status, report = self.run_policy(*command)
                self.assertEqual(status, 2, report)
                self.assertIn("pins.json requires only", report["error"])

    def test_shell_probe_fails_check_on_probe_errors(self):
        for probe_error in [None, subprocess.CalledProcessError(1, "nix")]:
            with (
                self.subTest(probe_error=probe_error),
                patch.object(policy.subprocess, "run", side_effect=probe_error),
                patch.object(
                    policy.subprocess, "check_output", return_value="x86_64-linux"
                ),
            ):
                status, report = self.run_policy("check", str(self.root), "--shell")
                self.assertEqual(status, 1 if probe_error else 0)
                self.assertEqual(report["status"], "fail" if probe_error else "pass")
                self.assertEqual(
                    report["rules"]["shell"], "fail" if probe_error else "pass"
                )


class RecordTests(unittest.TestCase):
    def test_policy_root_option_is_removed(self):
        for args in [
            ["--policy-root", ".", "validate"],
            ["--policy-root", ".", "check", "."],
            ["ci", ".", "--policy-root", "."],
        ]:
            with (
                self.subTest(args=args),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                policy.main(args)
            self.assertEqual(error.exception.code, 2)

    def test_validate_passes_on_the_committed_bundled_data(self):
        code, report = invoke("validate")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(
            report["repos"],
            ["petohorvath/nixos-cross-config", "petohorvath/nixos-registry"],
        )
        self.assertEqual(set(report["pins"]), {"stable", "unstable", "stableBranch"})

    def test_cli_version_matches_release_version_file(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            policy.main(["--version"])
        self.assertEqual(error.exception.code, 0)
        version = (policy.SOURCE_ROOT / "VERSION").read_text().strip()
        self.assertEqual(output.getvalue().strip(), f"nixos-project-policy {version}")

    def test_audit_and_agreement_commands_do_not_exist(self):
        for command in ["audit", "agreement"]:
            with (
                self.subTest(command=command),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                policy.main([command, "."])
            self.assertEqual(error.exception.code, 2)


class WorkflowTests(unittest.TestCase):
    def test_pr_workflows_cover_source_changes_without_edit_events(self):
        source = Path(__file__).resolve().parents[1]
        for path in [
            ".github/workflows/ci.yml",
            "templates/policy-caller.yml",
        ]:
            with self.subTest(path=path):
                workflow = yaml.load(
                    (source / path).read_text(), Loader=yaml.BaseLoader
                )
                self.assertEqual(
                    workflow["on"]["pull_request"],
                    {"types": ["opened", "synchronize", "reopened"]},
                )

    def test_fingerprints_notice_added_and_changed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "file.nix"
            file.write_text("before")
            before = policy.fingerprints(root)
            file.write_text("after")
            (root / "new.md").write_text("new")
            after = policy.fingerprints(root)
            self.assertNotEqual(before["file.nix"], after["file.nix"])
            self.assertIn("new.md", after)


if __name__ == "__main__":
    unittest.main()
