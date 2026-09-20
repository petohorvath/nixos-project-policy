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
from urllib.error import HTTPError

import yaml

from tools import policy


STABLE = "a" * 40
UNSTABLE = "b" * 40
NEW_STABLE = "c" * 40
NEW_UNSTABLE = "d" * 40
CHECKER = "e" * 40
RELEASE = f"v{(policy.SOURCE_ROOT / 'VERSION').read_text().strip()}"
SOURCE = "f" * 40
PAIR = {"stable": STABLE, "unstable": UNSTABLE}
NEW_PAIR = {"stable": NEW_STABLE, "unstable": NEW_UNSTABLE}
POLICY_REPO = "petohorvath/nixos-project-policy"
COMPATIBILITY_CHECKS = [
    "Policy / Compatibility (stable, x86_64-linux)",
    "Policy / Compatibility (stable, aarch64-linux)",
    "Policy / Compatibility (unstable, x86_64-linux)",
    "Policy / Compatibility (unstable, aarch64-linux)",
]
REQUIRED_CHECKS = [
    "Policy / Verify policy version and load shared pins",
    *COMPATIBILITY_CHECKS,
    "Policy / Compliance (x86_64-linux)",
    "Policy / Compliance (aarch64-linux)",
    "Policy / Formatting and lint (x86_64-linux)",
    "Policy / Formatting and lint (aarch64-linux)",
    "Policy / Project tests (x86_64-linux)",
    "Policy / Project tests (aarch64-linux)",
]
VM_CHECK = "Policy / VM tests (x86_64-linux)"


def nixpkgs(revision, branch):
    return {
        "locked": {
            "type": "github",
            "owner": "NixOS",
            "repo": "nixpkgs",
            "rev": revision,
        },
        "original": {
            "type": "github",
            "owner": "NixOS",
            "repo": "nixpkgs",
            "ref": branch,
        },
    }


def lockfile():
    return {
        "version": 7,
        "root": "entry",
        "nodes": {
            "entry": {
                "inputs": {
                    "nixpkgs": "arbitrary-node",
                    "nixpkgs-unstable": "rolling",
                }
            },
            "arbitrary-node": nixpkgs(STABLE, "nixos-26.05"),
            "rolling": nixpkgs(UNSTABLE, "nixos-unstable"),
        },
    }


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
    def batch(self, state):
        return {
            "id": "batch-1",
            "status": state,
            "pins": NEW_PAIR,
            "previous": PAIR,
            "projects": {"example": SOURCE},
        }

    def test_no_approval_is_not_a_baseline(self):
        self.assertEqual(
            policy.allowed_pairs({"approved": None, "batches": []}, "example", SOURCE),
            [],
        )

    def test_candidate_requires_exact_registered_project_revision(self):
        pins = {"approved": PAIR, "batches": [self.batch("candidate")]}
        self.assertEqual(
            policy.allowed_pairs(pins, "example", SOURCE, "batch-1"), [NEW_PAIR]
        )
        with self.assertRaisesRegex(ValueError, "exact project commit"):
            policy.allowed_pairs(pins, "example", CHECKER, "batch-1")
        self.assertEqual(policy.allowed_pairs(pins, "example", SOURCE), [NEW_PAIR])
        self.assertEqual(policy.allowed_pairs(pins, "example", CHECKER), [PAIR])

    def test_paused_rollout_keeps_old_and_new_pairs_for_affected_projects(self):
        pins = {"approved": NEW_PAIR, "batches": [self.batch("paused")]}
        self.assertIn(PAIR, policy.allowed_pairs(pins, "example", SOURCE))
        self.assertEqual(policy.allowed_pairs(pins, "unrelated", SOURCE), [NEW_PAIR])

    def test_completed_or_withdrawn_batch_cannot_extend_allowed_pins(self):
        for state in ["complete", "withdrawn"]:
            pins = {"approved": NEW_PAIR, "batches": [self.batch(state)]}
            self.assertEqual(policy.allowed_pairs(pins, "example", SOURCE), [NEW_PAIR])

    def test_floating_revisions_fail(self):
        with self.assertRaises(ValueError):
            policy.validate_pair({"stable": "nixos-26.05", "unstable": UNSTABLE})


class ProjectFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "example"
        self.config = {
            "schemaVersion": 2,
            "policyRepository": POLICY_REPO,
            "systems": ["x86_64-linux", "aarch64-linux"],
            "requiredTools": [],
            "ci": json.loads(
                (policy.SOURCE_ROOT / "policy/requirements.json").read_text()
            )["ci"],
            "readmeSections": [
                "Support",
                "Quickstart",
                "Development",
                "Contributing",
                "Documentation",
            ],
            "projects": {
                "example": {
                    "repository": "owner/example",
                    "adopted": True,
                    "policyVersion": RELEASE,
                    "requiredArchitectures": ["x86_64-linux", "aarch64-linux"],
                    "vmTargets": [],
                }
            },
        }
        self.pins = {"schemaVersion": 1, "approved": PAIR, "batches": []}
        self.members = {"example": "owner/example"}
        self.config["_members"] = self.members
        self.write("flake.nix", "{}")
        self.write(".envrc", "use flake\n")
        self.write("LICENSE", "MIT")
        self.write(
            "README.md",
            "# Example\n\nPurpose.\n"
            + "\n".join(f"## {section}\n" for section in self.config["readmeSections"]),
        )
        for file in ["CONTRIBUTING.md", "AGENTS.md"]:
            self.write(
                file,
                f"[Rules](https://github.com/{POLICY_REPO}/blob/{RELEASE}/POLICY.md)\n",
            )
        self.workflow = {
            "on": {
                "pull_request": {
                    "types": ["opened", "synchronize", "reopened", "edited"]
                }
            },
            "jobs": {
                "policy": {
                    "name": "Policy",
                    "uses": f"{POLICY_REPO}/.github/workflows/check.yml@{RELEASE}",
                    "with": {
                        "policy_version": RELEASE,
                        "project": "example",
                        "required_architectures": '["x86_64-linux", "aarch64-linux"]',
                    },
                }
            },
        }
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.write("flake.lock", json.dumps(lockfile()))

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def declare(self, **inputs):
        self.workflow["jobs"]["policy"]["with"].update(inputs)
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))

    def inspect(self):
        return policy.inspect_project(self.root, "example", self.config, self.pins)

    def write_records(self):
        records = Path(self.temp.name) / "records"
        (records / "policy").mkdir(parents=True, exist_ok=True)
        records_config = {
            key: value
            for key, value in self.config.items()
            if key
            not in {"systems", "requiredTools", "readmeSections", "ci", "_members"}
        }
        (records / "policy/projects.json").write_text(json.dumps(records_config))
        (records / "policy/pins.json").write_text(json.dumps(self.pins))
        (records / "policy/members.json").write_text(
            json.dumps({"schemaVersion": 1, "members": self.members})
        )
        return records

    def run_policy(self, *args):
        if args[0] == "ci" and (len(args) == 1 or args[1].startswith("--")):
            args = ("ci", str(self.root), *args[1:])
        records = self.write_records()
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = policy.main(["--policy-root", str(records), *args])
        return code, json.loads(output.getvalue() or errors.getvalue())


class ProjectTests(ProjectFixture):
    def run_policy(self, *args):
        if args[0] != "audit":
            return super().run_policy(*args)

        def assessed(
            release, root, name, repository, revision, records_root, snapshot, **kwargs
        ):
            return {
                **policy.inspect_project(
                    root,
                    name,
                    self.config,
                    self.pins,
                    project=policy.member_project(root, name, self.config),
                ),
                "checkerVersion": release["version"],
                "checkerRevision": release["revision"],
            }

        with (
            patch.object(
                policy.releases,
                "inspect_release",
                side_effect=lambda repository, version: {
                    "repository": repository,
                    "version": version,
                    "revision": CHECKER,
                },
            ),
            patch.object(policy.releases, "check_member", side_effect=assessed),
            patch.object(policy, "git_revision", return_value=SOURCE),
            patch.object(policy, "git_dirty", return_value=False),
        ):
            return super().run_policy(*args)

    def test_pending_enrollment_does_not_prevent_full_checks(self):
        self.members.clear()
        self.config["projects"]["example"]["adopted"] = False
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
        for adopted in [False, True]:
            with self.subTest(adopted=adopted):
                self.config["projects"]["example"]["adopted"] = adopted
                self.members.clear()
                if adopted:
                    self.members["example"] = "owner/example"
                status, report = self.run_policy(
                    "check", str(self.root), "--project", "example", "--readiness"
                )
                self.assertEqual(status, 0)
                self.assertEqual(report["status"], "pass")
                self.assertEqual(report["issues"], [])
                self.assertEqual(
                    report["enrollment"], "enrolled" if adopted else "not-enrolled"
                )

    def test_policy_versions_require_exact_release_tags_even_before_adoption(self):
        for version in [
            "main",
            CHECKER,
            "v0",
            "v0.1",
            "0.1.0",
            "v00.1.0",
            "v0.1.0-rc.1",
            "v0.1.0+build",
        ]:
            self.config["projects"]["example"].update(
                adopted=False, policyVersion=version
            )
            for args in [
                ("validate",),
                ("check", str(self.root), "--project", "example", "--readiness"),
            ]:
                with self.subTest(version=version, command=args[0]):
                    status, report = self.run_policy(*args)
                    self.assertEqual(status, 2)
                    self.assertEqual(report["status"], "error")
                    self.assertIn("release tags", report["error"])

    def test_pin_update_uses_current_records_without_changing_policy_version(self):
        command = ("check", str(self.root), "--project", "example")
        status, before = self.run_policy(*command)
        self.assertEqual(status, 0)
        self.pins["approved"] = NEW_PAIR
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
        self.assertNotEqual(
            before["policyRecordsDigest"], updated["policyRecordsDigest"]
        )

    def test_caller_version_must_match_selected_release(self):
        for version in ["v9.0.0", "main", CHECKER]:
            with self.subTest(version=version):
                self.workflow["jobs"]["policy"]["with"]["policy_version"] = version
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["status"], "fail")

    def test_caller_cannot_select_a_different_release_or_commit(self):
        for version in ["v9.0.0", CHECKER]:
            with self.subTest(version=version):
                self.workflow["jobs"]["policy"]["uses"] = (
                    f"{POLICY_REPO}/.github/workflows/check.yml@{version}"
                )
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["status"], "fail")

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

    def test_live_records_cannot_override_release_requirements(self):
        status, _ = self.run_policy("validate")
        self.assertEqual(status, 0)
        root = Path(self.temp.name) / "records"
        (root / "policy/requirements.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "systems": [],
                    "requiredTools": [],
                    "readmeSections": [],
                }
            )
        )
        config, _ = policy.load_policy(root)
        requirements = json.loads(
            (policy.SOURCE_ROOT / "policy/requirements.json").read_text()
        )
        for field in ["systems", "requiredTools", "readmeSections", "ci"]:
            self.assertEqual(config[field], requirements[field])
            path = root / "policy/projects.json"
            records = json.loads(path.read_text())
            records[field] = []
            path.write_text(json.dumps(records))
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "belongs in the release"),
            ):
                policy.load_policy(root)
            del records[field]
            path.write_text(json.dumps(records))

    def test_adopted_members_derive_checks_without_recording_mandatory_names(self):
        self.assertEqual(self.run_policy("validate")[0], 0)
        status, report = self.run_policy(
            "check", str(self.root), "--project", "example"
        )
        self.assertEqual(status, 0, report)
        self.assertEqual(report["requiredChecks"], REQUIRED_CHECKS)
        self.assertEqual(self.audit_with_checks(REQUIRED_CHECKS)[0], 0)

    def test_vm_gate_is_mandatory_only_when_targets_are_declared(self):
        self.assertEqual(self.run_policy("validate")[0], 0)
        self.declare(vm_targets='["vm-tests", "vm-tests-unstable"]')
        self.assertEqual(self.run_policy("validate")[0], 0)
        status, report = self.audit_with_checks(REQUIRED_CHECKS)
        self.assertEqual(status, 1)
        self.assertEqual(
            report["projects"][0]["issues"],
            [f"github: missing required check '{VM_CHECK}'"],
        )
        self.assertEqual(self.audit_with_checks([*REQUIRED_CHECKS, VM_CHECK])[0], 0)

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
                self.assertEqual(len(jobs), 3 * len(architectures))
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
                            "Formatting and lint",
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
                self.assertEqual(self.audit_with_checks(checks)[0], 0)
                for missing in [
                    check for check in checks if check in COMPATIBILITY_CHECKS
                ]:
                    with self.subTest(missing=missing):
                        incomplete = [check for check in checks if check != missing]
                        status, report = self.audit_with_checks(incomplete)
                        self.assertEqual(status, 1, report)
                        self.assertIn(
                            f"github: missing required check '{missing}'",
                            report["projects"][0]["issues"],
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
            ["unknown-linux"],
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
        for adopted in [False, True]:
            self.config["projects"]["example"]["adopted"] = adopted
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
        project = self.config["projects"]["example"]
        project["adopted"] = False
        self.pins["approved"] = None
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "planned")

    def test_check_names_must_be_a_list_of_unique_nonempty_strings(self):
        project = self.config["projects"]["example"]
        for field in ["requiredChecks", "additionalRequiredChecks"]:
            for invalid in [None, "Policy", {}, [None], [" "], ["Policy", "Policy"]]:
                with self.subTest(field=field, checks=invalid):
                    project[field] = invalid
                    status, report = self.run_policy("validate")
                    self.assertEqual(status, 2)
                    self.assertIn(f"Invalid {field} names", report["error"])
            del project[field]

    def test_pending_members_can_prepare_callers_before_recording_merge_gates(self):
        project = self.config["projects"]["example"]
        project["adopted"] = False
        status, report = self.run_policy(
            "check", str(self.root), "--project", "example", "--readiness"
        )
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "pass")
        project["adopted"] = True
        status, report = self.run_policy("validate")
        self.assertEqual(status, 0, report)

    def test_legacy_check_lists_must_match_generated_gates_including_additional_checks(
        self,
    ):
        project = self.config["projects"]["example"]
        project["policyVersion"] = "v0.3.0"
        project["additionalRequiredChecks"] = ["Integration"]
        checks = [*REQUIRED_CHECKS, "Integration"]
        for recorded in [
            [],
            *([check for check in checks if check != missing] for missing in checks),
            [*checks, "Stale gate"],
        ]:
            with self.subTest(recorded=recorded):
                project["requiredChecks"] = recorded
                status, report = self.run_policy("validate")
                self.assertEqual(status, 2)
                self.assertIn("must match the generated CI checks", report["error"])
        project["requiredChecks"] = checks
        self.assertEqual(self.run_policy("validate")[0], 0)
        del project["requiredChecks"]
        self.assertEqual(self.run_policy("validate")[0], 0)

    def test_older_selected_checkers_need_lists_for_every_adopted_member(self):
        project = self.config["projects"]["example"]
        legacy = copy.deepcopy(project)
        legacy.update(repository="owner/legacy", adopted=False)
        self.config["projects"]["legacy"] = legacy
        for version in ["v0.1.1", "v0.2.0"]:
            with self.subTest(version=version):
                legacy["policyVersion"] = version
                status, report = self.run_policy("validate")
                self.assertEqual(status, 2)
                self.assertIn("needs legacy requiredChecks", report["error"])
                project["requiredChecks"] = list(REQUIRED_CHECKS)
                self.assertEqual(self.run_policy("validate")[0], 0)
                del project["requiredChecks"]
        legacy["policyVersion"] = RELEASE
        self.assertEqual(self.run_policy("validate")[0], 0)

    def test_legacy_members_still_require_recorded_names_and_cannot_use_additional_checks(
        self,
    ):
        project = self.config["projects"]["example"]
        for version in ["v0.1.1", "v0.2.0"]:
            with self.subTest(version=version):
                project["policyVersion"] = version
                status, report = self.run_policy("validate")
                self.assertEqual(status, 2)
                self.assertIn("needs verified required check names", report["error"])
                project["requiredChecks"] = list(REQUIRED_CHECKS)
                self.assertEqual(self.run_policy("validate")[0], 0)
                project["additionalRequiredChecks"] = ["Integration"]
                status, report = self.run_policy("validate")
                self.assertEqual(status, 2)
                self.assertIn("needs policy v0.3.0 or later", report["error"])
                del project["additionalRequiredChecks"]
                del project["requiredChecks"]

    def test_additional_checks_cannot_replace_or_duplicate_mandatory_gates(self):
        self.declare(
            additional_required_checks=json.dumps([REQUIRED_CHECKS[0], "Integration"])
        )
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0, report)
        self.assertEqual(report["requiredChecks"], [*REQUIRED_CHECKS, "Integration"])
        self.assertEqual(self.audit_with_checks(["Integration"])[0], 1)

    def audit_with_checks(self, checks):
        with patch.object(
            policy,
            "github_get",
            side_effect=[
                {
                    "default_branch": "main",
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "allow_rebase_merge": False,
                },
                [
                    {"type": "pull_request"},
                    {
                        "type": "required_status_checks",
                        "parameters": {
                            "required_status_checks": [
                                {"context": check} for check in checks
                            ]
                        },
                    },
                ],
                {"protected": False},
            ],
        ):
            return self.run_policy("audit", str(self.root.parent), "--github")

    def test_github_audit_requires_policy_vm_and_additional_project_gates(self):
        checks = [*REQUIRED_CHECKS, VM_CHECK, "Project-specific integration tests"]
        self.declare(
            vm_targets='["vm-tests"]',
            additional_required_checks='["Project-specific integration tests"]',
        )
        status, report = self.run_policy("ci", "--project", "example")
        self.assertEqual(status, 0, report)
        self.assertEqual(report["requiredChecks"], checks)
        self.assertEqual(self.audit_with_checks(checks)[0], 0)
        for missing in checks:
            with self.subTest(missing=missing):
                status, report = self.audit_with_checks(
                    [check for check in checks if check != missing]
                )
                self.assertEqual(status, 1)
                self.assertEqual(
                    report["projects"][0]["issues"],
                    [f"github: missing required check '{missing}'"],
                )

    def test_legacy_project_records_are_rejected(self):
        self.config["schemaVersion"] = 1
        status, report = self.run_policy("validate")
        self.assertEqual(status, 2)
        self.assertIn("Unsupported policy record schema", report["error"])

    def test_local_check_cannot_use_a_different_policy_release(self):
        self.workflow["jobs"]["policy"]["uses"] = (
            f"{POLICY_REPO}/.github/workflows/check.yml@v9.0.0"
        )
        self.declare(policy_version="v9.0.0")
        for options in [[], ["--readiness"]]:
            with self.subTest(options=options):
                status, report = self.run_policy(
                    "check", str(self.root), "--project", "example", *options
                )
                self.assertEqual(status, 2)
                self.assertIn("selects policy v9.0.0", report["error"])

    def test_missing_checkout_does_not_turn_a_failed_audit_into_an_inspection_error(
        self,
    ):
        workspace = Path(self.temp.name) / "missing-workspace"
        with patch.object(policy, "github_get") as github:
            status, report = self.run_policy("audit", str(workspace), "--github")
        self.assertEqual(status, 1)
        self.assertEqual(report["projects"][0]["issues"], ["checkout missing"])
        github.assert_not_called()

    def test_shell_probe_preserves_readiness_and_fails_on_probe_errors(self):
        self.config["projects"]["example"]["adopted"] = False
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
                    "--readiness",
                    "--shell",
                )
                self.assertEqual(status, 1 if probe_error else 0)
                self.assertEqual(report["status"], "fail" if probe_error else "pass")

    def test_readiness_cannot_waive_missing_approval_or_policy_version(self):
        self.config["projects"]["example"]["adopted"] = False
        self.pins["approved"] = None
        status, report = self.run_policy(
            "check", str(self.root), "--project", "example", "--readiness"
        )
        self.assertEqual(status, 1, report)
        self.pins["approved"] = PAIR
        del self.workflow["jobs"]["policy"]["with"]["policy_version"]
        self.declare()
        status, report = self.run_policy(
            "check", str(self.root), "--project", "example", "--readiness"
        )
        self.assertEqual(status, 2, report)

    def test_legacy_adoption_does_not_change_audit_coverage(self):
        self.config["projects"]["example"]["adopted"] = False
        status, report = self.run_policy("audit", str(self.root.parent))
        self.assertEqual(status, 0)
        self.assertEqual(report["projects"][0]["status"], "pass")
        self.assertEqual(report["projects"][0]["enrollment"], "enrolled")
        self.assertEqual(report["projects"][0]["issues"], [])

    def test_registered_candidate_validation_is_not_approved_compliance(self):
        self.pins.update(
            approved=None,
            batches=[
                {
                    "id": "initial-candidate",
                    "status": "candidate",
                    "pins": PAIR,
                    "projects": {"example": SOURCE},
                }
            ],
        )
        for adopted in [True, False]:
            for options in [[], ["--batch", "initial-candidate"]]:
                with (
                    self.subTest(adopted=adopted, options=options),
                    patch.object(
                        policy.subprocess,
                        "check_output",
                        side_effect=[SOURCE, "", SOURCE],
                    ),
                ):
                    self.config["projects"]["example"]["adopted"] = adopted
                    status, report = self.run_policy(
                        "check",
                        str(self.root),
                        "--project",
                        "example",
                        "--readiness",
                        *options,
                    )
                    self.assertEqual(status, 0)
                    self.assertEqual(report["status"], "candidate-ready")
                    self.assertEqual(report["candidateBatch"], "initial-candidate")

    def test_audit_reports_inaccessible_protection_without_claiming_it_is_absent(self):
        repository = "https://api.github.com/repos/owner/example"
        info = {
            "default_branch": "main",
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
        }
        for code in [401, 403, 404]:
            with self.subTest(code=code):

                def response(request, **kwargs):
                    data = {
                        repository: info,
                        f"{repository}/rules/branches/main": [],
                        f"{repository}/branches/main": {"protected": True},
                    }
                    if request.full_url == f"{repository}/branches/main/protection":
                        raise HTTPError(request.full_url, code, "Unavailable", {}, None)
                    return io.StringIO(json.dumps(data[request.full_url]))

                with (
                    patch.dict(os.environ, {"GH_TOKEN": "test-audit-token"}),
                    patch.object(policy, "urlopen", side_effect=response),
                ):
                    status, report = self.run_policy(
                        "audit", str(self.root.parent), "--github"
                    )
                self.assertEqual(status, 2)
                self.assertEqual(report["status"], "error")
                project = report["projects"][0]
                self.assertEqual(project["status"], "error")
                self.assertIn(f"HTTP {code}", " ".join(project["issues"]))
                self.assertNotIn(
                    "github: pull requests are not required", project["issues"]
                )
                self.assertFalse(
                    any(
                        "missing required check" in issue for issue in project["issues"]
                    )
                )

    def test_github_audit_requires_member_credentials_only_for_enrolled_members(self):
        for adopted in [True, False]:
            with self.subTest(adopted=adopted):
                self.members.clear()
                if adopted:
                    self.members["example"] = "owner/example"
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch.object(
                        policy,
                        "urlopen",
                        side_effect=AssertionError("No token must mean no API request"),
                    ),
                ):
                    status, report = self.run_policy(
                        "audit", str(self.root.parent), "--github"
                    )
                self.assertEqual(status, 2 if adopted else 0)
                if adopted:
                    self.assertIn("token", " ".join(report["projects"][0]["issues"]))
                else:
                    self.assertEqual(report["projects"], [])

    def test_github_audit_recognizes_unprotected_branches_and_ruleset_only_gates(self):
        repository = "https://api.github.com/repos/owner/example"
        rules = [
            {"type": "pull_request"},
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": check} for check in REQUIRED_CHECKS
                    ]
                },
            },
        ]
        for protected in [False, True]:
            with self.subTest(protected=protected):

                def response(request, **kwargs):
                    data = {
                        repository: {
                            "default_branch": "main",
                            "allow_squash_merge": True,
                            "allow_merge_commit": False,
                            "allow_rebase_merge": False,
                        },
                        f"{repository}/rules/branches/main": rules if protected else [],
                        f"{repository}/branches/main": {"protected": protected},
                    }
                    if request.full_url == f"{repository}/branches/main/protection":
                        raise HTTPError(request.full_url, 404, "Not Found", {}, None)
                    return io.StringIO(json.dumps(data[request.full_url]))

                with (
                    patch.dict(os.environ, {"GH_TOKEN": "test-audit-token"}),
                    patch.object(policy, "urlopen", side_effect=response),
                ):
                    status, report = self.run_policy(
                        "audit", str(self.root.parent), "--github"
                    )
                self.assertEqual(status, 0 if protected else 1)
                self.assertEqual(
                    report["projects"][0]["status"], "pass" if protected else "fail"
                )
                if not protected:
                    self.assertIn(
                        "github: pull requests are not required",
                        report["projects"][0]["issues"],
                    )

    def test_github_audit_combines_rulesets_and_classic_protection(self):
        self.config["projects"]["example"]["additionalRequiredChecks"] = [
            "Rules",
            "Classic",
        ]
        repository = "https://api.github.com/repos/owner/example"
        data = {
            repository: {
                "default_branch": "release/main",
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
            },
            f"{repository}/rules/branches/release%2Fmain": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "Rules"}]},
                },
            ],
            f"{repository}/branches/release%2Fmain": {"protected": True},
            f"{repository}/branches/release%2Fmain/protection": {
                "required_pull_request_reviews": {"required_approving_review_count": 0},
                "required_status_checks": {
                    "contexts": REQUIRED_CHECKS,
                    "checks": [{"context": "Classic"}],
                },
            },
        }

        def response(request, **kwargs):
            self.assertEqual(
                request.get_header("Authorization"), "Bearer test-audit-token"
            )
            return io.StringIO(json.dumps(data[request.full_url]))

        with (
            patch.dict(
                os.environ,
                {"GH_TOKEN": "test-audit-token", "GITHUB_TOKEN": "workflow-token"},
            ),
            patch.object(policy, "urlopen", side_effect=response),
        ):
            status, report = self.run_policy("audit", str(self.root.parent), "--github")
        self.assertEqual(status, 0)
        self.assertEqual(report["projects"][0]["status"], "pass")
        self.assertEqual(report["projects"][0]["issues"], [])

    def audit_merge_settings(self, rest_settings, graphql_result):
        repository = "https://api.github.com/repos/owner/example"
        data = {
            repository: {"default_branch": "main", **rest_settings},
            f"{repository}/rules/branches/main": [
                {"type": "pull_request"},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": [
                            {"context": check} for check in REQUIRED_CHECKS
                        ]
                    },
                },
            ],
        }

        def response(request, **kwargs):
            self.assertEqual(request.get_header("Authorization"), "Bearer audit-token")
            if request.full_url == "https://api.github.com/graphql":
                self.assertEqual(request.get_method(), "POST")
                payload = json.loads(request.data)
                self.assertEqual(
                    payload["variables"], {"owner": "owner", "name": "example"}
                )
                for field in [
                    "squashMergeAllowed",
                    "mergeCommitAllowed",
                    "rebaseMergeAllowed",
                ]:
                    self.assertIn(field, payload["query"])
                if isinstance(graphql_result, Exception):
                    raise graphql_result
                return io.StringIO(json.dumps(graphql_result))
            return io.StringIO(json.dumps(data[request.full_url]))

        with (
            patch.dict(os.environ, {"GH_TOKEN": "audit-token"}),
            patch.object(policy, "urlopen", side_effect=response),
        ):
            return self.run_policy("audit", str(self.root.parent), "--github")

    def test_github_audit_resolves_hidden_merge_settings(self):
        squash_only = {
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
        }
        incomplete = [{}, {"allow_squash_merge": True}]
        for field in squash_only:
            incomplete.append(
                {key: value for key, value in squash_only.items() if key != field}
            )
            for invalid in [None, "false", 0]:
                incomplete.append({**squash_only, field: invalid})
        for rest_settings in incomplete:
            for changed in [None, *squash_only]:
                with self.subTest(rest=rest_settings, changed=changed):
                    settings = dict(squash_only)
                    if changed:
                        settings[changed] = not settings[changed]
                    status, report = self.audit_merge_settings(
                        rest_settings, {"data": {"repository": settings}}
                    )
                    self.assertEqual(status, 1 if changed else 0)
                    self.assertEqual(
                        report["projects"][0]["status"], "fail" if changed else "pass"
                    )
                    self.assertEqual(
                        report["projects"][0]["issues"],
                        ["github: configure squash as the only merge method"]
                        if changed
                        else [],
                    )

    def test_github_audit_keeps_unknown_merge_settings_as_inspection_errors(self):
        squash_only = {
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
        }
        invalid = [
            None,
            [],
            {},
            {"data": None},
            {"data": []},
            {"data": {"repository": None}},
        ]
        invalid.append(
            {"data": {"repository": squash_only}, "errors": [{"message": "Denied"}]}
        )
        for field in squash_only:
            invalid.append(
                {
                    "data": {
                        "repository": {
                            key: value
                            for key, value in squash_only.items()
                            if key != field
                        }
                    }
                }
            )
            for value in [None, "false", 0]:
                invalid.append({"data": {"repository": {**squash_only, field: value}}})
        for code in [401, 403, 404]:
            invalid.append(
                HTTPError("https://api.github.com/graphql", code, "Denied", {}, None)
            )
        for result in invalid:
            with self.subTest(result=result):
                status, report = self.audit_merge_settings({}, result)
                self.assertEqual(status, 2)
                self.assertEqual(report["status"], "error")
                self.assertEqual(report["projects"][0]["status"], "error")
                self.assertIn(
                    "settings are unknown", " ".join(report["projects"][0]["issues"])
                )
                self.assertNotIn(
                    "github: configure squash as the only merge method",
                    report["projects"][0]["issues"],
                )

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

    def test_mixed_old_new_channels_cannot_pass_rollout(self):
        self.pins["batches"] = [
            {
                "status": "rolling",
                "pins": NEW_PAIR,
                "previous": PAIR,
                "projects": {"example": SOURCE},
            }
        ]
        lock = lockfile()
        lock["nodes"]["rolling"]["locked"]["rev"] = NEW_UNSTABLE
        self.write("examples/flake.lock", json.dumps(lock))
        self.assertEqual(self.inspect()["status"], "fail")

    def test_separate_locks_cannot_select_different_pairs(self):
        self.pins["batches"] = [
            {
                "status": "paused",
                "pins": NEW_PAIR,
                "previous": PAIR,
                "projects": {"example": SOURCE},
            }
        ]
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
        self.config["projects"]["other"] = {
            **self.config["projects"]["example"],
            "repository": "owner/other",
        }
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

    def test_missing_approval_fails_closed(self):
        self.pins["approved"] = None
        self.assertTrue(
            any(
                "no approved family baseline" in issue
                for issue in self.inspect()["issues"]
            )
        )

    def test_unenrolled_legacy_projects_do_not_enter_audit_scope(self):
        self.members.clear()
        code, report = self.run_policy("audit", str(self.root))
        self.assertEqual(code, 0, report)
        self.assertEqual(report["projects"], [])

    def test_conditional_or_unpinned_callers_fail(self):
        self.workflow["jobs"]["policy"]["if"] = "false"
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertEqual(self.inspect()["status"], "fail")
        self.workflow["jobs"]["policy"]["uses"] = (
            f"{POLICY_REPO}/.github/workflows/check.yml@main"
        )
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertEqual(self.inspect()["status"], "fail")

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
                    "ci: Policy caller job must be named 'Policy'",
                    self.inspect()["issues"],
                )

    def test_caller_matrix_cannot_change_or_duplicate_required_status_names(self):
        self.workflow["jobs"]["policy"]["strategy"] = {
            "matrix": {"system": ["x86_64-linux", "aarch64-linux"]}
        }
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertTrue(any("strategy" in issue for issue in self.inspect()["issues"]))

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
                self.assertEqual(result["status"], "fail")
                self.assertTrue(any("needs" in issue for issue in result["issues"]))

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

    def test_policy_caller_runs_after_title_edits(self):
        self.workflow["on"]["pull_request"] = {
            "types": ["opened", "synchronize", "reopened", "edited"]
        }
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertEqual(self.inspect()["issues"], [])

    def test_policy_caller_requires_every_supported_pr_activity(self):
        activities = ["opened", "synchronize", "reopened", "edited"]
        triggers = ["pull_request", ["pull_request"], {"pull_request": None}]
        triggers.extend(
            {
                "pull_request": {
                    "types": [item for item in activities if item != missing]
                }
            }
            for missing in activities
        )
        for trigger in triggers:
            with self.subTest(trigger=trigger):
                self.workflow["on"] = trigger
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["status"], "fail")

    def test_policy_caller_rejects_path_and_branch_filters(self):
        for restriction in ["paths", "paths-ignore", "branches", "branches-ignore"]:
            with self.subTest(restriction=restriction):
                self.workflow["on"]["pull_request"] = {
                    "types": ["opened", "synchronize", "reopened", "edited"],
                    restriction: ["main"],
                }
                self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
                self.assertEqual(self.inspect()["status"], "fail")

    def test_missing_readme_section_is_reported(self):
        self.write("README.md", "# Example\n\nPurpose.\n")
        self.assertTrue(any("README" in issue for issue in self.inspect()["issues"]))

    def test_stale_rule_links_fail(self):
        self.write(
            "AGENTS.md", f"https://github.com/{POLICY_REPO}/blob/{SOURCE}/POLICY.md"
        )
        self.assertTrue(any("AGENTS.md" in issue for issue in self.inspect()["issues"]))

    def test_wrong_project_cannot_select_other_vm_requirements(self):
        self.workflow["jobs"]["policy"]["with"]["project"] = "another-project"
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertTrue(
            any("own project identity" in issue for issue in self.inspect()["issues"])
        )


class CompatibilityTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.host = "x86_64-linux"
        self.metadata = {"locks": lockfile()}
        self.commands = []
        self.check_returncode = 0
        self.dirty = ""
        self.host_checks = ["behavior"]
        self.fail_stage = None
        self.command_error = None
        self.committed_lock = (self.root / "flake.lock").read_bytes()
        self.addCleanup(patch.stopall)
        patch.object(policy.subprocess, "check_output", side_effect=self.output).start()
        patch.object(policy.subprocess, "run", side_effect=self.run_command).start()

    def output(self, command, **kwargs):
        if command[0] == "git":
            if "show" in command:
                return self.committed_lock
            if "status" in command:
                return self.dirty
            return SOURCE if command[2] == str(self.root) else CHECKER
        raise AssertionError(command)

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if self.command_error:
            raise self.command_error
        if self.fail_stage and command[: len(self.fail_stage)] == self.fail_stage:
            return subprocess.CompletedProcess(command, 1, "")
        if command[:3] == ["nix", "flake", "metadata"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.metadata))
        if command[:2] == ["nix", "eval"]:
            output = (
                self.host if "--impure" in command else json.dumps(self.host_checks)
            )
            return subprocess.CompletedProcess(command, 0, output)
        if command[:3] == ["nix", "flake", "check"]:
            return subprocess.CompletedProcess(command, self.check_returncode)
        raise AssertionError(command)

    def compatibility(self, channel="stable", *options):
        output = (
            Path(self.temp.name)
            / f"evidence-{len(list(Path(self.temp.name).glob('evidence-*')))}"
        )
        return self.run_policy(
            "compatibility",
            str(self.root),
            "--project",
            "example",
            "--channel",
            channel,
            "--output",
            str(output),
            *options,
        )

    def test_unenrolled_compatibility_uses_member_settings_and_approved_pins(self):
        self.config["projects"] = {}
        self.members.clear()
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
                self.assertEqual(report["checkerRevision"], CHECKER)
                self.assertEqual(report["policyRecordsRevision"], CHECKER)
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
                evidence = Path(report["artifacts"])
                self.assertEqual(
                    json.loads((evidence / "result.json").read_text()), report
                )
                self.assertEqual(
                    json.loads((evidence / "metadata.json").read_text()), self.metadata
                )

    def test_registered_candidate_executes_without_approving_or_changing_default_lock(
        self,
    ):
        before = (self.root / "flake.lock").read_bytes()
        self.pins.update(
            approved=None,
            batches=[
                {
                    "id": "next",
                    "status": "candidate",
                    "pins": NEW_PAIR,
                    "projects": {"example": SOURCE},
                }
            ],
        )
        self.metadata["locks"]["nodes"]["arbitrary-node"]["locked"]["rev"] = NEW_STABLE
        code, report = self.compatibility()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "candidate-pass")
        self.assertEqual(report["pinStatus"], "candidate")
        self.assertEqual(report["candidateBatch"], "next")
        self.assertEqual(report["expectedRevision"], NEW_STABLE)
        self.assertEqual((self.root / "flake.lock").read_bytes(), before)
        self.dirty = " M flake.nix"
        code, report = self.compatibility("stable", "--batch", "next")
        self.assertEqual(code, 1, report)
        self.assertIn("clean registered", " ".join(report["issues"]))

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

    def test_supported_native_hosts_are_detected_and_other_hosts_fail(self):
        for host in ["x86_64-linux", "aarch64-linux", "aarch64-darwin"]:
            with self.subTest(host=host):
                self.host = host
                code, report = self.compatibility()
                self.assertEqual(code, 1 if host.endswith("darwin") else 0, report)
                self.assertEqual(report["system"], host)

    def test_active_and_terminal_batches_use_only_the_central_approved_pair(self):
        for state in ["approved", "rolling", "paused", "complete", "withdrawn"]:
            for approved in [PAIR, NEW_PAIR]:
                with self.subTest(state=state, approved=approved):
                    self.pins.update(
                        approved=approved,
                        batches=[
                            {
                                "id": "rollout",
                                "status": state,
                                "pins": NEW_PAIR,
                                "previous": PAIR,
                                "projects": {"example": SOURCE},
                            }
                        ],
                    )
                    self.metadata["locks"]["nodes"]["arbitrary-node"]["locked"][
                        "rev"
                    ] = approved["stable"]
                    code, report = self.compatibility()
                    self.assertEqual(code, 0, report)
                    self.assertEqual(report["expectedRevision"], approved["stable"])
                    self.assertEqual(report["pinStatus"], "approved")
                    self.assertIsNone(report["candidateBatch"])
                    code, report = self.compatibility("stable", "--batch", "rollout")
                    self.assertEqual(code, 1, report)

    def test_candidate_selection_rejects_wrong_commits_and_ambiguity(self):
        self.pins["batches"] = [
            {
                "id": "next",
                "status": "candidate",
                "pins": PAIR,
                "projects": {"example": CHECKER},
            }
        ]
        code, report = self.compatibility("stable", "--batch", "next")
        self.assertEqual(code, 1, report)
        self.assertIn("exact project commit", " ".join(report["issues"]))
        self.pins["batches"][0]["projects"]["example"] = SOURCE
        self.pins["batches"].append({**self.pins["batches"][0], "id": "other"})
        code, report = self.compatibility()
        self.assertEqual(code, 1, report)
        self.assertIn("More than one candidate", " ".join(report["issues"]))
        code, report = self.compatibility("stable", "--batch", "next")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "candidate-pass")

    def test_missing_approval_and_invalid_rollout_records_cannot_run(self):
        self.pins["approved"] = None
        code, report = self.compatibility()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["commands"], [])
        for state in ["rolling", "unknown"]:
            self.pins["batches"] = [
                {
                    "id": "invalid",
                    "status": state,
                    "pins": PAIR,
                    "projects": {"example": SOURCE},
                }
            ]
            code, report = self.compatibility()
            self.assertEqual(code, 2, report)

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

    def test_source_inspection_errors_preserve_execution_evidence(self):
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
        self.assertTrue((Path(report["artifacts"]) / "result.json").is_file())

    def test_record_snapshot_is_captured_before_test_execution(self):
        original = self.run_command

        def change_records(command, **kwargs):
            if command[:3] == ["nix", "flake", "metadata"]:
                records = Path(self.temp.name) / "records/policy/pins.json"
                records.write_text(json.dumps({**self.pins, "approved": NEW_PAIR}))
            return original(command, **kwargs)

        code, before = self.compatibility()
        self.assertEqual(code, 0, before)
        with patch.object(policy.subprocess, "run", side_effect=change_records):
            code, after = self.compatibility()
        self.assertEqual(code, 0, after)
        self.assertEqual(after["expectedRevision"], STABLE)
        self.assertEqual(after["policyRecordsDigest"], before["policyRecordsDigest"])

    def test_evidence_cannot_be_written_inside_the_project(self):
        code, report = self.compatibility(
            "stable", "--output", str(self.root / "evidence")
        )
        self.assertEqual(code, 2, report)
        self.assertIn("outside", report["error"])
        self.assertFalse((self.root / "evidence").exists())

    def test_compatibility_requires_the_committed_root_lock(self):
        self.committed_lock = b"{}"
        code, report = self.compatibility()
        self.assertEqual(code, 1, report)
        self.assertIn("committed root lock", " ".join(report["issues"]))


class RecordTests(unittest.TestCase):
    def test_member_commands_require_explicit_current_records(self):
        for args in [
            ["check", ".", "--project", "example"],
            ["audit", "."],
            ["vm", ".", "--project", "example"],
            ["compatibility", ".", "--project", "example", "--channel", "stable"],
            ["ci", "--project", "example"],
        ]:
            with (
                self.subTest(command=args[0]),
                contextlib.redirect_stderr(io.StringIO()) as errors,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(policy.main(args), 2)
                self.assertIn(
                    "requires --policy-root",
                    json.loads(output.getvalue() or errors.getvalue())["error"],
                )

    def test_cli_version_matches_release_version_file(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            policy.main(["--version"])
        self.assertEqual(error.exception.code, 0)
        version = (policy.SOURCE_ROOT / "VERSION").read_text().strip()
        self.assertEqual(output.getvalue().strip(), f"nixos-project-policy {version}")

    def test_separate_record_snapshot_is_used_and_identified(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "policy").mkdir()
            (root / "policy/projects.json").write_text(
                (source / "policy/projects.json").read_text()
            )
            (root / "policy/members.json").write_text(
                (source / "policy/members.json").read_text()
            )
            pins = {"schemaVersion": 1, "approved": PAIR, "batches": []}
            (root / "policy/pins.json").write_text(json.dumps(pins))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = policy.main(["--policy-root", str(root), "validate"])
            report = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertTrue(report["approvedPins"])
            self.assertEqual(len(report["policyRecordsDigest"]), 64)

    def test_active_batch_cannot_substitute_for_missing_approval(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "policy").mkdir()
            (root / "policy/projects.json").write_text(
                (source / "policy/projects.json").read_text()
            )
            (root / "policy/members.json").write_text(
                (source / "policy/members.json").read_text()
            )
            pins = {
                "schemaVersion": 1,
                "approved": None,
                "batches": [
                    {
                        "id": "invalid",
                        "status": "rolling",
                        "pins": PAIR,
                        "projects": {"nix-libnet": SOURCE},
                    }
                ],
            }
            (root / "policy/pins.json").write_text(json.dumps(pins))
            with self.assertRaisesRegex(ValueError, "approved baseline"):
                policy.load_policy(root)


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
            {"requiredArchitectures": requirements["systems"], "vmTargets": []},
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
        self.assertEqual(f"{caller['name']} / {jobs['vm']['name']}", VM_CHECK)
        self.assertEqual(requirements["ci"]["vmCheck"], VM_CHECK)

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
            "--policy-root ./policy-state check ./project": "Compliance",
            "lint ./project": "Formatting and lint",
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
        title = next(
            step for step in job["steps"] if step.get("name") == "Check PR title"
        )
        self.assertEqual(
            title["if"],
            "matrix.check == 'Compliance' && github.event_name == 'pull_request'",
        )

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
        self.assertIn("--policy-root ./policy-state", step["run"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = root / "policy-state/policy"
            records.mkdir(parents=True)
            (records / "pins.json").write_text(
                json.dumps({"schemaVersion": 1, "approved": PAIR, "batches": []})
            )
            (records / "members.json").write_text(
                json.dumps({"schemaVersion": 1, "members": {}})
            )
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
                    (records / "projects.json").write_text(
                        json.dumps(
                            {
                                "schemaVersion": 2,
                                "policyRepository": POLICY_REPO,
                                "projects": {
                                    "example": {
                                        "repository": "owner/example",
                                        "policyVersion": RELEASE,
                                        "adopted": False,
                                        "vmTargets": [],
                                        "requiredArchitectures": ["x86_64-linux"],
                                    }
                                },
                            }
                        )
                    )
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
                                    "vm_targets": "[]",
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
                        matrix = json.loads(outputs["matrix"])
                        compatibility_matrix = json.loads(
                            outputs["compatibility_matrix"]
                        )
                        self.assertEqual(len(matrix["include"]), 3 * len(architectures))
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
                        f"revision={SOURCE}\nchecker_revision={CHECKER}\nproject_revision={SOURCE}\n"
                        if passes
                        else "",
                    )

    def test_hosted_release_guard_rejects_unpublished_or_mutable_versions(self):
        workflow = yaml.load(
            (policy.SOURCE_ROOT / ".github/workflows/check.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        guard = workflow["jobs"]["records"]["steps"][0]["run"]
        approved = {
            "tag_name": RELEASE,
            "immutable": True,
            "draft": False,
            "prerelease": False,
        }
        cases = [
            (RELEASE, approved, 0, True),
            (RELEASE, {**approved, "immutable": False}, 0, False),
            (RELEASE, {**approved, "draft": True}, 0, False),
            (RELEASE, {**approved, "prerelease": True}, 0, False),
            (RELEASE, {**approved, "tag_name": "v9.0.0"}, 0, False),
            (RELEASE, {}, 0, False),
            (RELEASE, approved, 1, False),
            ("main", approved, 0, False),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            stub = Path(temporary) / "gh"
            stub.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "print(os.environ['TEST_RELEASE'])\n"
                "sys.exit(int(os.environ['TEST_GH_STATUS']))\n"
            )
            stub.chmod(0o755)
            for version, release, api_status, passes in cases:
                with self.subTest(
                    version=version, release=release, api_status=api_status
                ):
                    result = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", guard],
                        env={
                            **os.environ,
                            "PATH": f"{temporary}:{os.environ['PATH']}",
                            "POLICY_VERSION": version,
                            "TEST_RELEASE": json.dumps(release),
                            "TEST_GH_STATUS": str(api_status),
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, passes, result.stderr)

    def test_all_member_jobs_use_one_release_and_record_snapshot(self):
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
                self.assertEqual(
                    checkouts["policy-state"], "${{ needs.records.outputs.revision }}"
                )
                for step in jobs[job]["steps"]:
                    if "nix run" in step.get("run", ""):
                        self.assertIn("--policy-root ./policy-state", step["run"])

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
        self.assertIn("--policy-root ./policy-state", step["run"])
        self.assertNotIn("||", step["run"])
        upload = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        self.assertEqual(upload["if"], "always()")
        default = workflow["jobs"]["policy"]["steps"]
        self.assertTrue(
            any(
                step.get("run")
                == "nix flake check ./project --no-update-lock-file --print-build-logs"
                for step in default
            )
        )

    def test_maintenance_audit_uses_a_member_access_secret(self):
        source = Path(__file__).resolve().parents[1]
        workflow = yaml.load(
            (source / ".github/workflows/maintenance.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        audit = next(
            step
            for step in workflow["jobs"]["audit"]["steps"]
            if "--github" in step.get("run", "")
        )
        self.assertEqual(
            audit["env"].get("GH_TOKEN"), "${{ secrets.MEMBER_AUDIT_TOKEN }}"
        )
        self.assertNotIn("GITHUB_TOKEN", audit["env"])

    def test_pr_workflows_refresh_title_checks(self):
        source = Path(__file__).resolve().parents[1]
        for path in [".github/workflows/ci.yml", "templates/policy-caller.yml"]:
            with self.subTest(path=path):
                workflow = yaml.load(
                    (source / path).read_text(), Loader=yaml.BaseLoader
                )
                self.assertEqual(
                    workflow["on"]["pull_request"],
                    {"types": ["opened", "synchronize", "reopened", "edited"]},
                )

    def test_candidate_worktree_must_match_registered_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "policyRepository": POLICY_REPO,
                "readmeSections": [],
                "projects": {"example": {"policyVersion": "v0.3.0"}},
            }
            pins = {
                "approved": PAIR,
                "batches": [
                    {
                        "id": "candidate",
                        "status": "candidate",
                        "pins": NEW_PAIR,
                        "projects": {"example": SOURCE},
                    }
                ],
            }
            with (
                patch.object(policy, "git_revision", return_value=SOURCE),
                patch.object(
                    policy.subprocess, "check_output", return_value=" M flake.lock\n"
                ),
            ):
                with self.assertRaisesRegex(ValueError, "clean registered"):
                    policy.inspect_project(root, "example", config, pins)

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

    @patch.object(policy.subprocess, "run")
    def test_lint_uses_temporary_copy_and_reports_changes(self, run):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "flake.nix").write_text("{}")

            def format_copy(command, **kwargs):
                if command[1] == "fmt":
                    (kwargs["cwd"] / "flake.nix").write_text("{ }\n")

            run.side_effect = format_copy
            self.assertEqual(
                policy.check_lint(root), ["formatting: changes required in flake.nix"]
            )
            self.assertEqual((root / "flake.nix").read_text(), "{}")

    def test_titles(self):
        for title in [
            "feat: Add a tool",
            "fix(nix)!: Change an option",
            "docs: Clarify setup",
        ]:
            self.assertTrue(policy.TITLE.fullmatch(title))
        for title in ["Update stuff", "fix: ", "fix: Message\nextra"]:
            self.assertFalse(policy.TITLE.fullmatch(title))

    def test_cycle_detection(self):
        self.assertEqual(
            policy.dependency_cycles({"a": ["b"], "b": ["c"], "c": []}), []
        )
        self.assertEqual(
            policy.dependency_cycles({"a": ["b"], "b": ["a"]}), [["a", "b", "a"]]
        )

    @patch.object(policy, "github_get")
    def test_missing_github_gates_are_reported(self, get):
        get.side_effect = [
            {
                "default_branch": "main",
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
            },
            [],
            {"protected": False},
        ]
        issues = policy.check_github({"repository": "owner/example"}, ["Policy"])
        self.assertEqual(len(issues), 2)

    @patch.object(policy, "github_get")
    def test_ruleset_gates_are_recognized(self, get):
        get.side_effect = [
            {
                "default_branch": "main",
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
            },
            [
                {"type": "pull_request"},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": [
                            {"context": name}
                            for name in ["Policy", *COMPATIBILITY_CHECKS]
                        ]
                    },
                },
            ],
        ]
        self.assertEqual(
            policy.check_github({"repository": "owner/example"}, ["Policy"]),
            [],
        )


if __name__ == "__main__":
    unittest.main()
