import copy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from tools import policy, records


from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.cli import invoke
from tests.fixtures.data import (
    CHECKER,
    COMPATIBILITY_CHECKS,
    NEW_PAIR,
    NEW_STABLE,
    NEW_UNSTABLE,
    PAIR,
    POLICY_REPO,
    RELEASE,
    REQUIRED_CHECKS,
    SOURCE,
    STABLE,
    UNSTABLE,
    VM_CHECK,
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
    def test_pending_enrollment_does_not_prevent_full_checks(self):
        self.repos.clear()

        status, report = self.run_policy(
            "check", str(self.root), "--project", "example"
        )
        self.assertEqual(status, 0, report)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["enrollment"], "not-enrolled")

    def test_root_selection_is_independent_of_shared_pins_and_update_channel(self):
        lock = lockfile()
        del lock["nodes"]["entry"]["inputs"]["nixpkgs-unstable"]
        for branch in ["nixos-26.05", "nixos-unstable", "custom-branch"]:
            with self.subTest(branch=branch):
                lock["nodes"]["arbitrary-node"] = nixpkgs(NEW_STABLE, branch)
                self.write("flake.lock", json.dumps(lock))
                code, report = self.run_policy(
                    "check", str(self.root), "--project", "example"
                )
                self.assertEqual(code, 0, report)
                self.assertEqual(report["compatibility"], "not-run")
                self.assertEqual(report["pins"][0]["selection"], "independent")

    def test_independent_selection_does_not_waive_lock_validation(self):
        for failure in ["missing", "malformed", "mutable"]:
            with self.subTest(failure=failure):
                lock = lockfile()
                if failure == "mutable":
                    lock["nodes"]["arbitrary-node"]["locked"]["rev"] = "nixos-unstable"
                self.write(
                    "flake.lock", "[]" if failure == "malformed" else json.dumps(lock)
                )
                if failure == "missing":
                    (self.root / "flake.lock").unlink()
                code, report = self.run_policy(
                    "check", str(self.root), "--project", "example"
                )
                self.assertNotEqual(code, 0, report)

    def test_independent_root_does_not_exempt_distinct_transitive_nixpkgs(self):
        lock = lockfile()
        lock["nodes"]["arbitrary-node"]["locked"]["rev"] = NEW_STABLE
        lock["nodes"]["entry"]["inputs"]["library"] = "library"
        lock["nodes"]["library"] = {"inputs": {"pkgs": "transitive"}}
        lock["nodes"]["transitive"] = nixpkgs(NEW_STABLE, "nixos-26.05")
        self.write("flake.lock", json.dumps(lock))
        code, report = self.run_policy("check", str(self.root), "--project", "example")
        self.assertEqual(code, 1, report)
        self.assertIn("allowed pin pair", " ".join(report["issues"]))

    def test_checks_report_enrollment_separately(self):
        for enrolled in [False, True]:
            with self.subTest(enrolled=enrolled):
                self.repos.clear()
                if enrolled:
                    self.repos.append("owner/example")
                status, report = self.run_policy(
                    "check", str(self.root), "--project", "example"
                )
                self.assertEqual(status, 0)
                self.assertEqual(report["status"], "pass")
                self.assertEqual(report["issues"], [])
                self.assertEqual(
                    report["enrollment"], "enrolled" if enrolled else "not-enrolled"
                )

    def test_pin_update_uses_current_records_without_changing_policy_version(self):
        command = ("check", str(self.root), "--project", "example")
        status, before = self.run_policy(*command)
        self.assertEqual(status, 0)
        self.pins.update(NEW_PAIR)
        status, stale = self.run_policy(*command)
        self.assertEqual(status, 1)
        self.assertTrue(any("allowed pin pair" in issue for issue in stale["issues"]))
        lock = lockfile()
        lock["nodes"]["arbitrary-node"]["locked"]["rev"] = NEW_STABLE
        lock["nodes"]["rolling"]["locked"]["rev"] = NEW_UNSTABLE
        self.write("flake.lock", json.dumps(lock))
        status, updated = self.run_policy(*command)
        self.assertEqual(status, 0)
        self.assertEqual(updated["status"], "pass")
        for report in [before, stale, updated]:
            self.assertEqual(report["policyVersion"], RELEASE)
            self.assertEqual(report["checkerVersion"], RELEASE)

    def test_caller_version_must_match_selected_release(self):
        for version in ["v9.0.0", "main", CHECKER]:
            with self.subTest(version=version):
                self.workflow["jobs"]["policy"]["with"]["policy_version"] = version
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["status"], "error")

    def test_caller_cannot_select_a_different_release_or_commit(self):
        for version in ["v9.0.0", CHECKER]:
            with self.subTest(version=version):
                self.workflow["jobs"]["policy"]["uses"] = (
                    f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
                )
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["status"], "error")

    def test_shared_rule_links_require_the_selected_release_and_policy_document(self):
        for target in [
            "v9.0.0/POLICY.md",
            f"{CHECKER}/POLICY.md",
            "v0.1.0/README.md",
            "v0.1.0/POLICY.md.other",
        ]:
            with self.subTest(target=target):
                self.write(
                    "AGENTS.md",
                    f"[Rules](https://github.com/{POLICY_REPO}/blob/{target})",
                )
                self.assertTrue(
                    any("AGENTS.md" in issue for issue in self.inspect()["issues"])
                )

    def test_enrolled_members_derive_checks_without_recording_mandatory_names(self):
        self.assertEqual(self.run_policy("validate")[0], 0)
        status, report = self.run_policy(
            "check", str(self.root), "--project", "example"
        )
        self.assertEqual(status, 0, report)
        self.assertEqual(report["requiredChecks"], REQUIRED_CHECKS)

    def test_vm_gate_is_mandatory_only_when_targets_are_declared(self):
        self.assertEqual(self.run_policy("validate")[0], 0)
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0, report)
        self.assertEqual(report["requiredChecks"], REQUIRED_CHECKS)
        self.declare(vm_targets='["vm-tests", "vm-tests-unstable"]')
        self.assertEqual(self.run_policy("validate")[0], 0)
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0, report)
        self.assertEqual(report["requiredChecks"], [*REQUIRED_CHECKS, VM_CHECK])

    def test_ci_plan_uses_the_members_required_architectures_for_jobs_and_gates(self):
        for architectures in [
            ["x86_64-linux"],
            ["aarch64-linux"],
            ["x86_64-linux", "aarch64-linux"],
        ]:
            with self.subTest(architectures=architectures):
                expected = [
                    check
                    for check in REQUIRED_CHECKS
                    if check == REQUIRED_CHECKS[0]
                    or any(
                        check.endswith(f"{architecture})")
                        for architecture in architectures
                    )
                ]
                self.declare(required_architectures=json.dumps(architectures))
                status, report = self.run_policy("ci", "--project", "example")
                self.assertEqual(status, 0)
                self.assertEqual(report["status"], "planned")
                self.assertEqual(report["requiredChecks"], expected)
                self.assertEqual(report["policyVersion"], RELEASE)
                jobs = report["matrix"]["include"]
                compatibility_jobs = report["compatibilityMatrix"]["include"]
                self.assertEqual(len(jobs), 2 * len(architectures))
                self.assertEqual(len(compatibility_jobs), 2 * len(architectures))
                self.assertEqual(
                    {(job["channel"], job["system"]) for job in compatibility_jobs},
                    {
                        (channel, architecture)
                        for channel in ["stable", "unstable"]
                        for architecture in architectures
                    },
                )
                self.assertEqual(
                    {(job["check"], job["system"]) for job in jobs},
                    {
                        (check, system)
                        for check in [
                            "Compliance",
                            "Project tests",
                        ]
                        for system in architectures
                    },
                )
                for job in [*jobs, *compatibility_jobs]:
                    self.assertEqual(
                        job["runner"],
                        {
                            "x86_64-linux": "ubuntu-24.04",
                            "aarch64-linux": "ubuntu-24.04-arm",
                        }[job["system"]],
                    )

    def test_single_architecture_compatibility_gates_are_enforced(self):
        for architecture in ["x86_64-linux", "aarch64-linux"]:
            checks = [
                check
                for check in REQUIRED_CHECKS
                if check == REQUIRED_CHECKS[0] or check.endswith(f"{architecture})")
            ]
            self.declare(required_architectures=json.dumps([architecture]))
            with self.subTest(architecture=architecture):
                self.assertEqual(self.inspect()["status"], "pass")
                status, report = self.run_policy("ci", "--project", "example")
                self.assertEqual(status, 0, report)
                self.assertEqual(report["requiredChecks"], checks)
                self.assertTrue(
                    {check for check in checks if check in COMPATIBILITY_CHECKS}
                )

    def test_invalid_architecture_selections_cannot_produce_a_ci_matrix(self):
        for invalid in [
            None,
            [],
            "x86_64-linux",
            {},
            [None],
            [""],
            [" "],
            ["x86_64-linux", "x86_64-linux"],
            ["../linux"],
            ["x86_64 linux"],
        ]:
            with self.subTest(architectures=invalid):
                self.declare(required_architectures=json.dumps(invalid))
                status, report = self.run_policy("ci", "--project", "example")
                self.assertEqual(status, 2)
                self.assertIn("required_architectures", report["error"])
                self.assertNotIn("matrix", report)

    def test_current_release_requires_architectures_even_before_enrollment(self):
        del self.workflow["jobs"]["policy"]["with"]["required_architectures"]
        self.declare()
        for enrolled in [False, True]:
            self.repos.clear()
            if enrolled:
                self.repos.append("owner/example")
            status, report = self.run_policy("ci", "--project", "example")
            self.assertEqual(status, 2, report)
            self.assertIn("required_architectures", report["error"])

    def test_vm_gate_keeps_its_platform_when_regular_ci_requires_only_arm(self):
        checks = [
            check
            for check in REQUIRED_CHECKS
            if check == REQUIRED_CHECKS[0] or check.endswith("aarch64-linux)")
        ]
        self.declare(
            required_architectures='["aarch64-linux"]', vm_targets='["vm-tests"]'
        )
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0)
        self.assertEqual(report["requiredChecks"], [*checks, VM_CHECK])
        self.assertEqual(
            {job["system"] for job in report["matrix"]["include"]}, {"aarch64-linux"}
        )
        self.assertEqual(
            {job["system"] for job in report["compatibilityMatrix"]["include"]},
            {"aarch64-linux"},
        )

    def test_ci_planning_requires_the_members_selected_release(self):
        for version in [None, "v0.1.1"]:
            with self.subTest(version=version):
                self.workflow["jobs"]["policy"]["uses"] = (
                    f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
                )
                self.declare(policy_version=version)
                status, report = self.run_policy("ci", "--project", "example")
                self.assertEqual(status, 2)
                self.assertNotIn("matrix", report)

    def test_ci_plan_does_not_claim_pin_approval_or_adoption(self):
        self.repos.clear()
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "planned")

    def test_additional_checks_cannot_replace_or_duplicate_mandatory_gates(self):
        self.declare(
            additional_required_checks=json.dumps([REQUIRED_CHECKS[0], "Integration"])
        )
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0, report)
        self.assertEqual(report["requiredChecks"], [*REQUIRED_CHECKS, "Integration"])

    def test_ci_plan_requires_policy_vm_and_additional_project_gates(self):
        checks = [*REQUIRED_CHECKS, VM_CHECK, "Project-specific integration tests"]
        self.declare(
            vm_targets='["vm-tests"]',
            additional_required_checks='["Project-specific integration tests"]',
        )
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0, report)
        self.assertEqual(report["requiredChecks"], checks)

    def test_malformed_bundled_data_fails_every_command(self):
        self.pins["approved"] = PAIR
        for command in [
            ("validate",),
            ("ci", "--project", "example"),
            ("check", str(self.root), "--project", "example"),
        ]:
            with self.subTest(command=command[0]):
                status, report = self.run_policy(*command)
                self.assertEqual(status, 2, report)
                self.assertIn("pins.json requires only", report["error"])

    def test_local_check_cannot_use_a_different_policy_release(self):
        self.workflow["jobs"]["policy"]["uses"] = (
            f"{POLICY_REPO}/.github/workflows/check.yml@v9.0.0"
        )
        self.declare(policy_version="v9.0.0")
        for options in [[]]:
            with self.subTest(options=options):
                status, report = self.run_policy(
                    "check", str(self.root), "--project", "example", *options
                )
                self.assertEqual(status, 2)
                self.assertIn("selects policy v9.0.0", report["error"])

    def test_shell_probe_preserves_pre_enrollment_and_fails_on_probe_errors(self):
        for probe_error in [None, subprocess.CalledProcessError(1, "nix")]:
            with (
                self.subTest(probe_error=probe_error),
                patch.object(policy.subprocess, "run", side_effect=probe_error),
                patch.object(
                    policy.subprocess, "check_output", return_value="x86_64-linux"
                ),
            ):
                status, report = self.run_policy(
                    "check",
                    str(self.root),
                    "--project",
                    "example",
                    "--shell",
                )
                self.assertEqual(status, 1 if probe_error else 0)
                self.assertEqual(report["status"], "fail" if probe_error else "pass")

    def test_pre_enrollment_cannot_waive_missing_policy_version(self):
        del self.workflow["jobs"]["policy"]["with"]["policy_version"]
        self.declare()
        status, report = self.run_policy(
            "check", str(self.root), "--project", "example"
        )
        self.assertEqual(status, 2, report)

    def test_complete_static_contract_passes(self):
        self.assertEqual(self.inspect()["issues"], [])

    def test_nonstandard_nixpkgs_input_names_fail(self):
        for channel, expected_name, wrong_name, node in [
            ("stable", "nixpkgs", "stable", "arbitrary-node"),
            ("unstable", "nixpkgs-unstable", "unstable", "rolling"),
        ]:
            with self.subTest(channel=channel):
                lock = lockfile()
                inputs = lock["nodes"]["entry"]["inputs"]
                del inputs[expected_name]
                inputs[wrong_name] = node
                self.write("flake.lock", json.dumps(lock))
                self.assertIn(
                    f"flake.lock: {channel} nixpkgs input {wrong_name!r} "
                    f"must be named {expected_name!r}",
                    self.inspect()["issues"],
                )

    def test_swapped_nixpkgs_channels_fail(self):
        lock = lockfile()
        lock["nodes"]["entry"]["inputs"] = {
            "nixpkgs": "rolling",
            "nixpkgs-unstable": "arbitrary-node",
        }
        self.write("flake.lock", json.dumps(lock))
        self.assertEqual(self.inspect()["status"], "fail")
        self.assertIn(
            "flake.lock: stable nixpkgs input 'nixpkgs-unstable' must be named 'nixpkgs'",
            self.inspect()["issues"],
        )

    def test_only_the_selected_root_input_is_required(self):
        for input_name in ["nixpkgs", "nixpkgs-unstable"]:
            with self.subTest(input_name=input_name):
                lock = lockfile()
                del lock["nodes"]["entry"]["inputs"][input_name]
                self.write("flake.lock", json.dumps(lock))
                self.assertEqual(
                    self.inspect()["issues"],
                    ["flake.lock: missing root nixpkgs input"]
                    if input_name == "nixpkgs"
                    else [],
                )

    def test_canonical_follows_can_use_third_party_input_names(self):
        lock = lockfile()
        lock["nodes"]["entry"]["inputs"].update(
            library="library", nixpkgs=["library", "pkgs"]
        )
        lock["nodes"]["library"] = {"inputs": {"pkgs": "arbitrary-node"}}
        self.write("flake.lock", json.dumps(lock))
        self.assertEqual(self.inspect()["issues"], [])

    def test_noncanonical_root_nixpkgs_alias_fails(self):
        lock = lockfile()
        lock["nodes"]["entry"]["inputs"]["pkgs"] = ["nixpkgs"]
        self.write("flake.lock", json.dumps(lock))
        self.assertIn(
            "flake.lock: stable nixpkgs input 'pkgs' must be named 'nixpkgs'",
            self.inspect()["issues"],
        )

    def test_example_nixpkgs_input_names_are_checked(self):
        lock = lockfile()
        inputs = lock["nodes"]["entry"]["inputs"]
        inputs["unstable"] = inputs.pop("nixpkgs-unstable")
        self.write("examples/flake.lock", json.dumps(lock))
        self.assertIn(
            "examples/flake.lock: unstable nixpkgs input 'unstable' "
            "must be named 'nixpkgs-unstable'",
            self.inspect()["issues"],
        )

    def test_changed_example_lock_fails(self):
        lock = lockfile()
        lock["nodes"]["rolling"]["locked"]["rev"] = NEW_UNSTABLE
        self.write("examples/flake.lock", json.dumps(lock))
        result = self.inspect()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any("examples/flake.lock" in issue for issue in result["issues"])
        )

    def test_exact_input_declarations_are_accepted(self):
        lock = lockfile()
        lock["nodes"]["arbitrary-node"]["original"].pop("ref")
        lock["nodes"]["arbitrary-node"]["original"]["rev"] = STABLE
        self.write("flake.lock", json.dumps(lock))
        self.assertEqual(self.inspect()["status"], "pass")

    def test_mixed_shared_channels_cannot_pass(self):
        self.pins.update(NEW_PAIR)
        lock = lockfile()
        lock["nodes"]["rolling"]["locked"]["rev"] = NEW_UNSTABLE
        self.write("examples/flake.lock", json.dumps(lock))
        self.assertEqual(self.inspect()["status"], "fail")

    def test_separate_locks_cannot_select_different_pairs(self):
        self.pins.update(NEW_PAIR)
        lock = lockfile()
        lock["nodes"]["arbitrary-node"]["locked"]["rev"] = NEW_STABLE
        lock["nodes"]["rolling"]["locked"]["rev"] = NEW_UNSTABLE
        self.write("examples/flake.lock", json.dumps(lock))
        self.assertIn(
            "pins: project lockfiles do not share one allowed pair",
            self.inspect()["issues"],
        )

    def test_vendor_locks_are_excluded(self):
        self.write("vendor/flake.lock", "not a lockfile")
        self.assertEqual(self.inspect()["status"], "pass")

    def test_transitive_policy_input_is_rejected(self):
        lock = lockfile()
        lock["nodes"]["rolling"]["inputs"] = {"policy": "policy"}
        lock["nodes"]["policy"] = {
            "locked": {"owner": "petohorvath", "repo": "nixos-project-policy"}
        }
        self.write("flake.lock", json.dumps(lock))
        self.assertTrue(
            any(
                "policy repository is a flake dependency" in issue
                for issue in self.inspect()["issues"]
            )
        )

    def test_trailing_slash_git_sources_retain_policy_pin_and_dependency_checks(self):
        self.repos.append("owner/other")
        for repository in [POLICY_REPO, "NixOS/nixpkgs", "owner/other"]:
            with self.subTest(repository=repository):
                lock = lockfile()
                lock["nodes"]["entry"]["inputs"]["library"] = "library"
                lock["nodes"]["library"] = {
                    "locked": {
                        "type": "git",
                        "url": f"https://github.com/{repository}/",
                        "rev": NEW_STABLE,
                    },
                    "original": {
                        "type": "git",
                        "url": f"https://github.com/{repository}/",
                        "ref": "nixos-26.05",
                    },
                }
                self.write("flake.lock", json.dumps(lock))
                code, report = self.run_policy(
                    "check", str(self.root), "--project", "example"
                )
                if repository == "owner/other":
                    self.assertEqual(report["dependencies"], ["other"])
                else:
                    self.assertEqual(code, 1, report)

    def test_conditional_or_unpinned_callers_fail(self):
        self.workflow["jobs"]["policy"]["if"] = "false"
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertEqual(self.inspect()["status"], "error")
        self.workflow["jobs"]["policy"]["uses"] = (
            f"{POLICY_REPO}/.github/workflows/check.yml@main"
        )
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertEqual(self.inspect()["status"], "error")

    def test_caller_name_must_produce_the_standard_status_prefix(self):
        for name in [None, "policy", "Project validation", "Policy (${{ matrix.os }})"]:
            with self.subTest(name=name):
                job = self.workflow["jobs"]["policy"]
                if name is None:
                    job.pop("name", None)
                else:
                    job["name"] = name
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertIn(
                    "Policy caller job must be named 'Policy'",
                    self.inspect()["error"],
                )

    def test_caller_matrix_cannot_change_or_duplicate_required_status_names(self):
        self.workflow["jobs"]["policy"]["strategy"] = {
            "matrix": {"system": ["x86_64-linux", "aarch64-linux"]}
        }
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertIn("strategy", self.inspect()["error"])

    def test_policy_caller_cannot_depend_on_a_skipped_job(self):
        self.workflow["jobs"]["optional"] = {
            "if": False,
            "runs-on": "ubuntu-latest",
            "steps": [{"run": "true"}],
        }
        for needs in ["optional", ["optional"]]:
            with self.subTest(needs=needs):
                self.workflow["jobs"]["policy"]["needs"] = needs
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                result = self.inspect()
                self.assertEqual(result["status"], "error")
                self.assertIn("needs", result["error"])

    def test_caller_cannot_disable_or_supply_compatibility_selection(self):
        for setting in [
            {"strategy": {"matrix": {"skip": []}}},
            {"continue-on-error": True},
            {
                "with": {
                    "project": "example",
                    "policy_version": RELEASE,
                    "revision": NEW_STABLE,
                }
            },
        ]:
            with self.subTest(setting=setting):
                workflow = copy.deepcopy(self.workflow)
                workflow["jobs"]["policy"].update(setting)
                self.write(".github/workflows/policy.yml", json.dumps(workflow))
                code, report = self.run_policy(
                    "check", str(self.root), "--project", "example"
                )
                self.assertEqual(code, 2, report)

    def test_policy_caller_edit_event_is_optional(self):
        for activities in [
            ["opened", "synchronize", "reopened"],
            ["opened", "synchronize", "reopened", "edited"],
        ]:
            with self.subTest(activities=activities):
                self.workflow["on"]["pull_request"] = {"types": activities}
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["issues"], [])

    def test_policy_caller_requires_every_supported_pr_activity(self):
        activities = ["opened", "synchronize", "reopened"]
        triggers = ["pull_request", ["pull_request"], {"pull_request": None}]
        triggers.extend(
            {
                "pull_request": {
                    "types": [item for item in activities if item != missing]
                }
            }
            for missing in activities
        )
        triggers.extend(
            {"pull_request": {"types": [*activities, extra]}}
            for extra in ["opened", "closed"]
        )
        for trigger in triggers:
            with self.subTest(trigger=trigger):
                self.workflow["on"] = trigger
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["status"], "error")

    def test_policy_caller_rejects_path_and_branch_filters(self):
        for restriction in ["paths", "paths-ignore", "branches", "branches-ignore"]:
            with self.subTest(restriction=restriction):
                self.workflow["on"]["pull_request"] = {
                    "types": ["opened", "synchronize", "reopened", "edited"],
                    restriction: ["main"],
                }
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["status"], "error")

    def test_dev_flake_file_does_not_fail_structure_check(self):
        self.write("dev/flake.nix", "{}")
        self.write("dev/flake.lock", json.dumps(lockfile()))
        code, report = self.run_policy("check", str(self.root), "--project", "example")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")

    def test_readme_contents_are_unrestricted(self):
        for contents in ["", "Example project.\n", "# Custom title\n\n## Usage\n"]:
            with self.subTest(contents=contents):
                self.write("README.md", contents)
                code, report = self.run_policy(
                    "check", str(self.root), "--project", "example"
                )
                self.assertEqual(code, 0, report)
                self.assertEqual(report["status"], "pass")

    def test_missing_readme_file_is_reported(self):
        (self.root / "README.md").unlink()
        code, report = self.run_policy("check", str(self.root), "--project", "example")
        self.assertEqual(code, 1, report)
        self.assertIn("structure: missing README.md", report["issues"])

    def test_stale_rule_links_fail(self):
        self.write(
            "AGENTS.md", f"https://github.com/{POLICY_REPO}/blob/{SOURCE}/POLICY.md"
        )
        self.assertTrue(any("AGENTS.md" in issue for issue in self.inspect()["issues"]))

    def test_wrong_project_cannot_select_other_vm_requirements(self):
        self.workflow["jobs"]["policy"]["with"]["project"] = "another-project"
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertIn("own project identity", self.inspect()["error"])


class RecordTests(unittest.TestCase):
    def test_policy_root_option_is_removed(self):
        for args in [
            ["--policy-root", ".", "validate"],
            ["--policy-root", ".", "check", ".", "--project", "example"],
            ["ci", ".", "--project", "example", "--policy-root", "."],
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
    def test_caller_and_workflow_produce_the_mandatory_status_names(self):
        source = policy.SOURCE_ROOT
        requirements = json.loads((source / "policy/requirements.json").read_text())
        caller = yaml.load(
            (source / "templates/policy-caller.yml").read_text(), Loader=yaml.BaseLoader
        )["jobs"]["policy"]
        jobs = yaml.load(
            (source / ".github/workflows/check.yml").read_text(), Loader=yaml.BaseLoader
        )["jobs"]
        self.assertEqual(caller["name"], "Policy")
        self.assertEqual(requirements["ci"]["callerJobName"], caller["name"])
        self.assertNotIn("if", caller)
        self.assertNotIn("needs", caller)
        self.assertNotIn("strategy", caller)
        plan = policy.ci_plan(
            {
                "requiredArchitectures": list(requirements["ci"]["runners"]),
                "vmTargets": [],
            },
            requirements["ci"],
        )
        names = {f"{caller['name']} / {jobs['records']['name']}"}
        for job in plan["matrix"]["include"]:
            name = jobs["policy"]["name"].replace("${{ matrix.check }}", job["check"])
            name = name.replace("${{ matrix.system }}", job["system"])
            names.add(f"{caller['name']} / {name}")
        compatibility = jobs["compatibility"]
        for job in plan["compatibilityMatrix"]["include"]:
            name = compatibility["name"].replace("${{ matrix.check }}", job["check"])
            names.add(f"{caller['name']} / {name}")
        self.assertEqual(names, set(REQUIRED_CHECKS))
        self.assertEqual(set(plan["requiredChecks"]), names)
        self.assertEqual(
            requirements["ci"]["requiredChecks"],
            [REQUIRED_CHECKS[0]],
        )
        self.assertEqual(
            jobs["vm"]["name"], "${{ fromJSON(needs.records.outputs.vm_job).check }}"
        )
        self.assertEqual(
            jobs["vm"]["runs-on"],
            "${{ fromJSON(needs.records.outputs.vm_job).runner }}",
        )
        self.assertEqual(f"{caller['name']} / {plan['vmJob']['check']}", VM_CHECK)
        self.assertEqual(plan["vmJob"]["runner"], "ubuntu-24.04")
        self.assertEqual(
            requirements["ci"]["vmCheck"].format(architecture="x86_64-linux"), VM_CHECK
        )

    def test_check_categories_run_independently_on_both_linux_architectures(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        job = workflow["jobs"]["policy"]
        self.assertEqual(job["needs"], "records")
        self.assertNotIn("if", job)
        self.assertNotIn("continue-on-error", job)
        self.assertEqual(job["strategy"]["fail-fast"], "false")
        self.assertEqual(
            job["strategy"]["matrix"], "${{ fromJSON(needs.records.outputs.matrix) }}"
        )
        self.assertEqual(
            workflow["jobs"]["records"]["outputs"]["matrix"],
            "${{ steps.ci.outputs.matrix }}",
        )
        self.assertEqual(job["runs-on"], "${{ matrix.runner }}")
        categories = {
            "-- check ./project": "Compliance",
            "-- test ./project --nixpkgs locked": "Project tests",
        }
        for command, category in categories.items():
            with self.subTest(category=category):
                steps = [
                    step for step in job["steps"] if command in step.get("run", "")
                ]
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0]["if"], f"matrix.check == '{category}'")
                self.assertIn("--no-update-lock-file", steps[0]["run"])
                self.assertNotIn("continue-on-error", steps[0])

    def test_snapshot_job_generates_matrix_from_member_inputs_and_checkout(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        records_job = workflow["jobs"]["records"]
        self.assertNotIn("strategy", records_job)
        self.assertEqual(
            records_job["outputs"]["vm_required"], "${{ steps.ci.outputs.vm_required }}"
        )
        self.assertEqual(
            workflow["jobs"]["vm"]["if"], "needs.records.outputs.vm_required == 'true'"
        )
        step = next(step for step in records_job["steps"] if step.get("id") == "ci")
        self.assertEqual(step["env"]["PROJECT"], "${{ inputs.project }}")
        self.assertIn("--no-update-lock-file ./policy", step["run"])
        self.assertNotIn("--policy-root", step["run"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = root / "nix"
            stub.write_text(
                f"#!{sys.executable}\nimport os, sys\n"
                f"os.execv(sys.executable, [sys.executable, {str(policy.SOURCE_ROOT / 'tools/policy.py')!r}, "
                "*sys.argv[sys.argv.index('--') + 1:]])\n"
            )
            stub.chmod(0o755)
            output = root / "output"
            for architectures, targets in [
                (["x86_64-linux"], []),
                (["aarch64-linux"], []),
                (["aarch64-linux"], ["vm-tests"]),
                (["x86_64-linux", "aarch64-linux"], []),
                ([], []),
                (["unsupported"], []),
            ]:
                with self.subTest(architectures=architectures, targets=targets):
                    caller = yaml.load(
                        (
                            policy.SOURCE_ROOT / "templates/policy-caller.yml"
                        ).read_text(),
                        Loader=yaml.BaseLoader,
                    )
                    job = caller["jobs"]["policy"]
                    job["uses"] = f"{POLICY_REPO}/.github/workflows/check.yml@{RELEASE}"
                    job["with"] = {
                        "project": "example",
                        "policy_version": RELEASE,
                        "required_architectures": json.dumps(architectures),
                        "vm_architecture": "aarch64-linux",
                        "vm_targets": json.dumps(targets),
                    }
                    member = root / "project/.github/workflows"
                    member.mkdir(parents=True, exist_ok=True)
                    (member / "policy.yml").write_text(json.dumps(caller))
                    output.write_text("")
                    process = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                        cwd=root,
                        env={
                            **os.environ,
                            "PATH": f"{temporary}:{os.environ['PATH']}",
                            "PROJECT": "example",
                            "WORKFLOW_INPUTS": json.dumps(
                                {
                                    **job["with"],
                                    "additional_required_checks": "[]",
                                }
                            ),
                            "GITHUB_OUTPUT": str(output),
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if architectures and set(architectures) <= {
                        "x86_64-linux",
                        "aarch64-linux",
                    }:
                        self.assertEqual(process.returncode, 0, process.stderr)
                        outputs = dict(
                            line.split("=", 1)
                            for line in output.read_text().splitlines()
                        )
                        self.assertEqual(
                            outputs["vm_required"], "true" if targets else "false"
                        )
                        self.assertEqual(
                            json.loads(outputs["vm_job"])["runner"],
                            ["self-hosted", "aarch64-linux"],
                        )
                        matrix = json.loads(outputs["matrix"])
                        compatibility_matrix = json.loads(
                            outputs["compatibility_matrix"]
                        )
                        self.assertEqual(len(matrix["include"]), 2 * len(architectures))
                        self.assertEqual(
                            len(compatibility_matrix["include"]), 2 * len(architectures)
                        )
                        self.assertEqual(
                            {job["system"] for job in matrix["include"]},
                            set(architectures),
                        )
                        self.assertEqual(
                            {
                                (job["channel"], job["system"])
                                for job in compatibility_matrix["include"]
                            },
                            {
                                (channel, architecture)
                                for channel in ["stable", "unstable"]
                                for architecture in architectures
                            },
                        )
                    else:
                        self.assertNotEqual(process.returncode, 0)
                        self.assertEqual(output.read_text(), "")

    def test_release_snapshot_requires_matching_version_and_records_both_commits(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        script = next(
            step["run"]
            for step in workflow["jobs"]["records"]["steps"]
            if step.get("id") == "snapshot"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "policy").mkdir()
            stub = root / "git"
            stub.write_text(
                f"#!{sys.executable}\nimport sys\n"
                f"print({CHECKER!r} if sys.argv[2] == 'policy' else {SOURCE!r})\n"
            )
            stub.chmod(0o755)
            output = root / "output"
            for version, passes in [
                (RELEASE.removeprefix("v"), True),
                ("9.0.0", False),
            ]:
                with self.subTest(version=version):
                    (root / "policy/VERSION").write_text(version + "\n")
                    output.write_text("")
                    result = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", script],
                        cwd=root,
                        env={
                            **os.environ,
                            "PATH": f"{temporary}:{os.environ['PATH']}",
                            "POLICY_VERSION": RELEASE,
                            "GITHUB_OUTPUT": str(output),
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, passes, result.stderr)
                    self.assertEqual(
                        output.read_text(),
                        f"checker_revision={CHECKER}\nproject_revision={SOURCE}\n"
                        if passes
                        else "",
                    )

    def test_all_member_jobs_use_one_release_without_a_data_checkout(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        jobs = workflow["jobs"]
        for job in ["policy", "vm", "compatibility"]:
            with self.subTest(job=job):
                self.assertEqual(jobs[job]["needs"], "records")
                checkouts = {
                    step["with"]["path"]: step["with"].get("ref")
                    for step in jobs[job]["steps"]
                    if step.get("uses", "").startswith("actions/checkout@")
                }
                self.assertEqual(
                    checkouts["policy"], "${{ needs.records.outputs.checker_revision }}"
                )
                self.assertEqual(set(checkouts), {"policy", "project"})
                for step in jobs[job]["steps"]:
                    if "nix run" in step.get("run", ""):
                        self.assertIn(
                            "nix run --no-update-lock-file ./policy -- ", step["run"]
                        )
                        self.assertNotIn("--policy-root", step["run"])

    def test_compatibility_jobs_use_the_generated_member_matrix(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        job = workflow["jobs"]["compatibility"]
        self.assertEqual(job["name"], "${{ matrix.check }}")
        self.assertEqual(
            job["strategy"]["matrix"],
            "${{ fromJSON(needs.records.outputs.compatibility_matrix) }}",
        )
        self.assertEqual(
            workflow["jobs"]["records"]["outputs"]["compatibility_matrix"],
            "${{ steps.ci.outputs.compatibility_matrix }}",
        )
        self.assertEqual(job["runs-on"], "${{ matrix.runner }}")
        self.assertEqual(job["needs"], "records")
        self.assertEqual(job["strategy"]["fail-fast"], "false")
        self.assertNotIn("if", job)
        self.assertNotIn("continue-on-error", job)
        step = next(
            step for step in job["steps"] if "test ./project" in step.get("run", "")
        )
        self.assertNotIn("if", step)
        self.assertNotIn("continue-on-error", step)
        self.assertEqual(
            step["run"].strip(),
            'nix run --no-update-lock-file ./policy -- test ./project --nixpkgs "$CHANNEL"',
        )
        self.assertEqual(step["env"], {"CHANNEL": "${{ matrix.channel }}"})
        self.assertNotIn("--policy-root", step["run"])
        self.assertNotIn("||", step["run"])
        self.assertNotIn("--output", step["run"])
        self.assertFalse(
            any(
                step.get("uses", "").startswith("actions/upload-artifact@")
                for step in job["steps"]
            )
        )
        default = workflow["jobs"]["policy"]["steps"]
        tests_step = next(
            step for step in default if step.get("name") == "Run project tests"
        )
        self.assertEqual(
            tests_step["run"].strip(),
            "nix run --no-update-lock-file ./policy -- test ./project --nixpkgs locked",
        )

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
