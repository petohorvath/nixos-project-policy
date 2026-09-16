import copy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
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
SOURCE = "f" * 40
PAIR = {"stable": STABLE, "unstable": UNSTABLE}
NEW_PAIR = {"stable": NEW_STABLE, "unstable": NEW_UNSTABLE}
POLICY_REPO = "petohorvath/nixos-project-policy"


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


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "example"
        self.config = {
            "schemaVersion": 1,
            "policyRepository": POLICY_REPO,
            "systems": ["x86_64-linux", "aarch64-linux"],
            "requiredTools": [],
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
                    "policyRevision": CHECKER,
                    "vmTargets": [],
                    "requiredChecks": ["Policy"],
                }
            },
        }
        self.pins = {"schemaVersion": 1, "approved": PAIR, "batches": []}
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
                f"[Rules](https://github.com/{POLICY_REPO}/blob/{CHECKER}/POLICY.md)\n",
            )
        self.workflow = {
            "on": {
                "pull_request": {
                    "types": ["opened", "synchronize", "reopened", "edited"]
                }
            },
            "jobs": {
                "policy": {
                    "uses": f"{POLICY_REPO}/.github/workflows/check.yml@{CHECKER}",
                    "with": {"policy_revision": CHECKER, "project": "example"},
                }
            },
        }
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.write("flake.lock", json.dumps(lockfile()))

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def inspect(self):
        return policy.inspect_project(self.root, "example", self.config, self.pins)

    def run_policy(self, *args):
        records = Path(self.temp.name) / "records"
        (records / "policy").mkdir(parents=True, exist_ok=True)
        (records / "policy/projects.json").write_text(json.dumps(self.config))
        (records / "policy/pins.json").write_text(json.dumps(self.pins))
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = policy.main(["--policy-root", str(records), *args])
        return code, json.loads(output.getvalue() or errors.getvalue())

    def test_pending_adoption_cannot_pass_a_compliance_check(self):
        self.config["projects"]["example"]["adopted"] = False
        status, report = self.run_policy(
            "check", str(self.root), "--project", "example"
        )
        self.assertEqual(status, 1)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("adoption" in issue for issue in report["issues"]))

    def test_enrollment_readiness_is_explicit_and_separate_from_compliance(self):
        for adopted in [False, True]:
            with self.subTest(adopted=adopted):
                self.config["projects"]["example"]["adopted"] = adopted
                status, report = self.run_policy(
                    "check", str(self.root), "--project", "example", "--readiness"
                )
                self.assertEqual(status, 0)
                self.assertEqual(report["status"], "pass" if adopted else "ready")
                self.assertEqual(report["issues"], [])

    def test_pending_policy_revisions_must_be_immutable(self):
        self.config["projects"]["example"].update(adopted=False, policyRevision="main")
        for file in ["AGENTS.md", "CONTRIBUTING.md", ".github/workflows/policy.yml"]:
            path = self.root / file
            path.write_text(path.read_text().replace(CHECKER, "main"))
        for args in [
            ("validate",),
            ("check", str(self.root), "--project", "example", "--readiness"),
        ]:
            with self.subTest(command=args[0]):
                status, report = self.run_policy(*args)
                self.assertEqual(status, 2)
                self.assertEqual(report["status"], "error")
                self.assertIn("commit", report["error"])

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
                self.assertEqual(report["status"], "fail" if probe_error else "ready")

    def test_readiness_cannot_waive_missing_approval_or_policy_revision(self):
        self.config["projects"]["example"]["adopted"] = False
        for missing in ["pins", "policyRevision"]:
            with self.subTest(missing=missing):
                self.pins["approved"] = None if missing == "pins" else PAIR
                self.config["projects"]["example"]["policyRevision"] = (
                    None if missing == "policyRevision" else CHECKER
                )
                status, report = self.run_policy(
                    "check", str(self.root), "--project", "example", "--readiness"
                )
                self.assertEqual(status, 1)
                self.assertEqual(report["status"], "fail")

    def test_pending_audit_reports_enrollment_readiness(self):
        self.config["projects"]["example"]["adopted"] = False
        status, report = self.run_policy("audit", str(self.root.parent))
        self.assertEqual(status, 0)
        self.assertEqual(report["projects"][0]["status"], "pending-adoption")
        self.assertEqual(report["projects"][0]["assessment"], "ready")
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
                        policy.subprocess, "check_output", side_effect=[SOURCE, ""]
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

    def test_github_audit_requires_member_credentials_only_after_adoption(self):
        for adopted in [True, False]:
            with self.subTest(adopted=adopted):
                self.config["projects"]["example"]["adopted"] = adopted
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
                    self.assertEqual(
                        report["projects"][0]["status"], "pending-adoption"
                    )

    def test_github_audit_recognizes_unprotected_branches_and_ruleset_only_gates(self):
        repository = "https://api.github.com/repos/owner/example"
        rules = [
            {"type": "pull_request"},
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "Policy"}]},
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
        self.config["projects"]["example"]["requiredChecks"] = [
            "Policy",
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
                    "contexts": ["Policy"],
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
        self.assertEqual(len(self.inspect()["issues"]), 2)

    def test_nixpkgs_naming_does_not_require_unused_inputs(self):
        for input_name in ["nixpkgs", "nixpkgs-unstable"]:
            with self.subTest(input_name=input_name):
                lock = lockfile()
                del lock["nodes"]["entry"]["inputs"][input_name]
                self.write("flake.lock", json.dumps(lock))
                self.assertEqual(self.inspect()["issues"], [])

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
        self.write("flake.lock", json.dumps(lock))
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

    def test_missing_approval_fails_closed(self):
        self.pins["approved"] = None
        self.assertTrue(
            any(
                "no approved family baseline" in issue
                for issue in self.inspect()["issues"]
            )
        )

    def test_pending_adoption_is_reported_separately(self):
        config = copy.deepcopy(self.config)
        config["projects"]["example"]["adopted"] = False
        report = policy.audit_family(self.root, config, self.pins)
        self.assertEqual(report["projects"][0]["status"], "pending-adoption")
        self.assertEqual(report["projects"][0]["assessment"], "fail")

    def test_conditional_or_unpinned_callers_fail(self):
        self.workflow["jobs"]["policy"]["if"] = "false"
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertEqual(self.inspect()["status"], "fail")
        self.workflow["jobs"]["policy"]["uses"] = (
            f"{POLICY_REPO}/.github/workflows/check.yml@main"
        )
        self.write(".github/workflows/policy.yml", json.dumps(self.workflow))
        self.assertEqual(self.inspect()["status"], "fail")

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
                self.assertTrue(
                    any("dependencies" in issue for issue in result["issues"])
                )

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
            any("own registered project" in issue for issue in self.inspect()["issues"])
        )


class RecordTests(unittest.TestCase):
    def test_separate_record_snapshot_is_used_and_identified(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "policy").mkdir()
            (root / "policy/projects.json").write_text(
                (source / "policy/projects.json").read_text()
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
                "projects": {"example": {"policyRevision": CHECKER}},
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
        issues = policy.check_github(
            {"repository": "owner/example", "requiredChecks": ["Policy"]}
        )
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
                    "parameters": {"required_status_checks": [{"context": "Policy"}]},
                },
            ],
        ]
        self.assertEqual(
            policy.check_github(
                {"repository": "owner/example", "requiredChecks": ["Policy"]}
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
