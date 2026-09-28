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
from tests.fixtures.compatibility import CompatibilityFixture
from tests.fixtures.data import (
    CHECKER,
    COMPATIBILITY_CHECKS,
    NEW_PAIR,
    NEW_STABLE,
    PAIR,
    POLICY_REPO,
    RELEASE,
    REQUIRED_CHECKS,
    SOURCE,
    STABLE,
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

    def test_ci_plan_requires_policy_and_additional_project_gates(self):
        checks = [*REQUIRED_CHECKS, "Project-specific integration tests"]
        self.declare(
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

    def test_host_check_probe_rejects_empty_malformed_and_failed_evaluations(self):
        for output in ["[]", "{}", "null", "[1]", "invalid", '["behavior"]']:
            with (
                self.subTest(output=output),
                patch.object(
                    policy.subprocess, "check_output", return_value="aarch64-linux\n"
                ),
                patch.object(
                    policy.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, output),
                ),
            ):
                code, report = self.run_policy("host-checks", str(self.root))
                self.assertEqual(code, 0 if output == '["behavior"]' else 1, report)
                self.assertEqual(report["system"], "aarch64-linux")
        with (
            patch.object(
                policy.subprocess, "check_output", return_value="x86_64-linux"
            ),
            patch.object(
                policy.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(1, "nix"),
            ),
        ):
            code, report = self.run_policy("host-checks", str(self.root))
            self.assertEqual(code, 1, report)


class CompatibilityTests(CompatibilityFixture, ProjectTestCase):
    def test_unenrolled_compatibility_uses_member_settings_and_approved_pins(self):
        self.repos.clear()
        code, report = self.compatibility()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["enrollment"], "not-enrolled")
        self.assertEqual(
            report["memberSettings"]["requiredArchitectures"],
            ["x86_64-linux", "aarch64-linux"],
        )

    def test_both_channels_execute_full_checks_at_the_recorded_pin(self):
        for channel, revision in PAIR.items():
            with self.subTest(channel=channel):
                self.metadata["locks"]["nodes"]["arbitrary-node"]["locked"]["rev"] = (
                    revision
                )
                code, report = self.compatibility(channel)
                self.assertEqual(code, 0, report)
                self.assertEqual(report["status"], "pass")
                self.assertEqual(report["expectedRevision"], revision)
                self.assertEqual(report["resolvedRevision"], revision)
                self.assertEqual(report["revision"], SOURCE)
                for removed in [
                    "artifacts",
                    "checkerRevision",
                    "checkerSourceDigest",
                    "policyRecordsDigest",
                    "policyRecordsRevision",
                    "sourceDigest",
                    "sourceDirty",
                ]:
                    self.assertNotIn(removed, report)
                self.assertEqual(report["system"], self.host)
                self.assertEqual(report["pinStatus"], "approved")
                check = next(
                    command
                    for command in reversed(self.commands)
                    if command[:3] == ["nix", "flake", "check"]
                )
                self.assertEqual(
                    check[-3:],
                    ["--override-input", "nixpkgs", f"github:NixOS/nixpkgs/{revision}"],
                )
                self.assertNotIn("--no-build", check)
                self.assertNotIn("--no-update-lock-file", check)

    def test_resolved_input_must_be_present_exact_and_from_nixos(self):
        for failure in [
            "missing",
            "ignored",
            "wrong-owner",
            "custom-host",
            "git-owner-spoof",
            "lookalike-host",
            "mutable",
            "nonflake",
            "malformed",
        ]:
            with self.subTest(failure=failure):
                self.metadata = {"locks": lockfile()}
                node = self.metadata["locks"]["nodes"]["arbitrary-node"]
                if failure == "missing":
                    del self.metadata["locks"]["nodes"]["entry"]["inputs"]["nixpkgs"]
                elif failure == "ignored":
                    node["locked"]["rev"] = NEW_STABLE
                elif failure == "wrong-owner":
                    node["locked"]["owner"] = "someone-else"
                elif failure == "custom-host":
                    node["locked"]["host"] = "github.example.org"
                elif failure == "git-owner-spoof":
                    node["locked"].update(
                        type="git", url="https://example.org/NixOS/nixpkgs"
                    )
                elif failure == "lookalike-host":
                    node["locked"] = {
                        "type": "git",
                        "url": "https://evilgithub.com/NixOS/nixpkgs",
                        "rev": STABLE,
                    }
                elif failure == "mutable":
                    del node["locked"]["rev"]
                elif failure == "nonflake":
                    node["flake"] = False
                else:
                    self.metadata["locks"] = {"version": 6}
                self.commands.clear()
                code, report = self.compatibility()
                self.assertEqual(code, 1, report)
                self.assertEqual(report["status"], "fail")
                self.assertFalse(
                    any(
                        command[:3] == ["nix", "flake", "check"]
                        for command in self.commands
                    )
                )

    def test_follows_and_arbitrary_node_names_verify_the_root_selection(self):
        graph = self.metadata["locks"]
        graph["nodes"]["entry"]["inputs"].update(
            library="library", nixpkgs=["library", "pkgs"]
        )
        graph["nodes"]["library"] = {"inputs": {"pkgs": "arbitrary-node"}}
        code, report = self.compatibility()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["resolvedRevision"], STABLE)

    def test_metadata_does_not_replace_host_evaluation_or_builds(self):
        for stage in [
            ["nix", "flake", "metadata"],
            ["nix", "eval", "--json"],
            ["nix", "flake", "check"],
        ]:
            with self.subTest(stage=stage):
                self.fail_stage = stage
                code, report = self.compatibility()
                self.assertEqual(code, 1, report)
                self.assertEqual(report["commands"][-1]["returncode"], 1)
        self.fail_stage = None
        for checks in [[], {}, None]:
            with self.subTest(checks=checks):
                self.host_checks = checks
                code, report = self.compatibility()
                self.assertEqual(code, 1, report)
                self.assertIn("nonempty host checks", " ".join(report["issues"]))
        self.command_error = FileNotFoundError("nix unavailable")
        code, report = self.compatibility()
        self.assertEqual(code, 1, report)
        self.assertIn("nix unavailable", report["commands"][-1]["error"])

    def test_native_compatibility_hosts_are_not_limited_to_default_runners(self):
        for host in [
            "x86_64-linux",
            "aarch64-linux",
            "aarch64-darwin",
            "riscv64-linux",
        ]:
            with self.subTest(host=host):
                self.host = host
                code, report = self.compatibility()
                self.assertEqual(code, 0, report)
                self.assertEqual(report["system"], host)

    def test_invalid_native_compatibility_host_fails(self):
        self.host = "../invalid"
        code, report = self.compatibility()
        self.assertEqual(code, 1, report)
        self.assertIn("Invalid compatibility host", " ".join(report["issues"]))

    def test_lock_changes_are_reported_as_failure(self):
        original = self.run_command

        def mutate(command, **kwargs):
            if command[:3] == ["nix", "flake", "check"]:
                self.write("flake.lock", "changed")
            return original(command, **kwargs)

        with patch.object(policy.subprocess, "run", side_effect=mutate):
            code, report = self.compatibility()
        self.assertEqual(code, 1, report)
        self.assertIn("Compatibility changed the project lockfile", report["issues"])

    def test_source_inspection_errors_fail_the_run(self):
        original = self.run_command

        def mutate(command, **kwargs):
            if command[:3] == ["nix", "flake", "check"]:
                outside = Path(self.temp.name) / "outside"
                outside.write_text("external")
                (self.root / "generated").symlink_to(outside)
            return original(command, **kwargs)

        with patch.object(policy.subprocess, "run", side_effect=mutate):
            code, report = self.compatibility()
        self.assertEqual(code, 1, report)
        self.assertIn("escapes", " ".join(report["issues"]))

    def test_record_snapshot_is_captured_before_test_execution(self):
        original = self.run_command

        def change_records(command, **kwargs):
            if command[:3] == ["nix", "flake", "metadata"]:
                pins = Path(self.temp.name) / "data/pins.json"
                pins.write_text(json.dumps({**self.pins, **NEW_PAIR}))
            return original(command, **kwargs)

        code, before = self.compatibility()
        self.assertEqual(code, 0, before)
        with patch.object(policy.subprocess, "run", side_effect=change_records):
            code, after = self.compatibility()
        self.assertEqual(code, 0, after)
        self.assertEqual(after["expectedRevision"], STABLE)

    def test_compatibility_rejects_an_evidence_directory(self):
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            self.compatibility("stable", "--output", str(self.root.parent / "out"))
        self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.root.parent / "out").exists())

    def test_compatibility_requires_the_committed_root_lock(self):
        self.committed_lock = b"{}"
        code, report = self.compatibility()
        self.assertEqual(code, 1, report)
        self.assertIn("committed root lock", " ".join(report["issues"]))


class RecordTests(unittest.TestCase):
    def test_policy_root_option_is_removed(self):
        for args in [
            ["--policy-root", ".", "validate"],
            ["--policy-root", ".", "check", "."],
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
            {"requiredArchitectures": list(requirements["ci"]["runners"])},
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
            "nix flake check ./project": "Project tests",
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
            for architectures in [
                ["x86_64-linux"],
                ["aarch64-linux"],
                ["x86_64-linux", "aarch64-linux"],
                [],
                ["unsupported"],
            ]:
                with self.subTest(architectures=architectures):
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
                        self.assertNotIn("vm_job", outputs)
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
            step
            for step in job["steps"]
            if "compatibility ./project" in step.get("run", "")
        )
        self.assertNotIn("if", step)
        self.assertNotIn("continue-on-error", step)
        self.assertIn('--channel "$CHANNEL"', step["run"])
        self.assertEqual(step["env"]["CHANNEL"], "${{ matrix.channel }}")
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
        self.assertTrue(
            any(
                "nix flake check ./project --no-update-lock-file --print-build-logs"
                in step.get("run", "")
                for step in default
            )
        )
        tests_step = next(
            step for step in default if step.get("name") == "Run project tests"
        )
        commands = tests_step["run"].strip().splitlines()
        self.assertEqual(
            commands[0],
            "nix run --no-update-lock-file ./policy -- host-checks ./project",
        )
        self.assertEqual(
            commands[1],
            "nix flake check ./project --no-update-lock-file --print-build-logs",
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
