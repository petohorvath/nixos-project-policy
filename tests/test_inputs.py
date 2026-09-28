"""Input rules that `check` applies to a repo's root flake lock."""

import json

from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.data import NEW_STABLE, SOURCE, lockfile, nixpkgs

INPUT_RULES = {
    "extra-nixpkgs",
    "lock",
    "no-policy-input",
    "root-nixpkgs",
    "sibling-follows-nixpkgs",
    "sibling-one-revision",
    "sibling-tag",
}


def sibling(repository, *, ref=None, rev=SOURCE, follows=True):
    owner, repo = repository.split("/")
    original = {"type": "github", "owner": owner, "repo": repo}
    if ref is not None:
        original["ref"] = ref
    node = {
        "locked": {"type": "github", "owner": owner, "repo": repo, "rev": rev},
        "original": original,
    }
    if follows:
        node["inputs"] = {"nixpkgs": ["nixpkgs"]}
    return node


class InputRuleTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.repos[:] = ["petohorvath/nixos-registry"]
        self.lock = lockfile()
        del self.lock["nodes"]["entry"]["inputs"]["nixpkgs-unstable"]
        del self.lock["nodes"]["rolling"]

    def add_input(self, name, node, parent="entry"):
        self.lock["nodes"][parent].setdefault("inputs", {})[name] = name
        self.lock["nodes"][name] = node

    def check(self):
        """Run `check` and keep only the notices of the input rules."""
        self.write("flake.lock", json.dumps(self.lock))
        code, report = self.run_policy("check", str(self.root))
        if "notices" in report:
            report["notices"] = [
                notice for notice in report["notices"] if notice["rule"] in INPUT_RULES
            ]
        return code, report

    def failures(self, report):
        return {(issue["rule"], issue["input"]) for issue in report["issues"]}

    def test_tagged_sibling_passes(self):
        self.add_input("registry", sibling("petohorvath/nixos-registry", ref="v1.2.0"))
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["rules"]["sibling-tag"], "pass")
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["notices"], [])

    def test_root_nixpkgs_must_be_a_locked_nixos_nixpkgs(self):
        cases = {
            "missing": lambda lock: lock["nodes"]["entry"]["inputs"].pop("nixpkgs"),
            "fork": lambda lock: lock["nodes"]["arbitrary-node"]["locked"].update(
                owner="someone"
            ),
            "branch": lambda lock: lock["nodes"]["arbitrary-node"]["locked"].update(
                rev="nixos-unstable"
            ),
            "unlocked": lambda lock: lock["nodes"]["arbitrary-node"]["locked"].pop(
                "rev"
            ),
            "tarball": lambda lock: lock["nodes"]["arbitrary-node"].update(
                locked={"type": "tarball", "url": "https://example.invalid/n.tar"}
            ),
        }
        for case, change in cases.items():
            with self.subTest(case=case):
                self.setUp()
                change(self.lock)
                code, report = self.check()
                self.assertEqual(code, 1, report)
                self.assertEqual(report["rules"]["root-nixpkgs"], "fail")
                self.assertIn(("root-nixpkgs", "nixpkgs"), self.failures(report))

    def test_root_nixpkgs_may_be_any_revision_from_any_branch(self):
        for branch in ["nixos-26.05", "nixos-unstable", "custom-branch"]:
            with self.subTest(branch=branch):
                self.lock["nodes"]["arbitrary-node"] = nixpkgs(NEW_STABLE, branch)
                code, report = self.check()
                self.assertEqual(code, 0, report)
                self.assertEqual(report["rules"]["root-nixpkgs"], "pass")

    def test_root_nixpkgs_resolves_follows_and_renamed_nodes(self):
        self.add_input("library", {"inputs": {"pkgs": "arbitrary-node"}})
        self.lock["nodes"]["entry"]["inputs"]["nixpkgs"] = ["library", "pkgs"]
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["notices"], [])

    def test_missing_or_malformed_root_lock_fails_the_lock_rule(self):
        for case in ["missing", "malformed", "cyclic"]:
            with self.subTest(case=case):
                if case == "missing":
                    (self.root / "flake.lock").unlink(missing_ok=True)
                    code, report = self.run_policy("check", str(self.root))
                else:
                    if case == "malformed":
                        self.write("flake.lock", "[]")
                    else:
                        self.lock["nodes"]["entry"]["inputs"].update(
                            nixpkgs=["alias"], alias=["nixpkgs"]
                        )
                        self.write("flake.lock", json.dumps(self.lock))
                    code, report = self.run_policy("check", str(self.root))
                self.assertEqual(code, 1, report)
                self.assertEqual(report["rules"]["lock"], "fail")
                self.assertEqual(report["rules"]["sibling-tag"], "not-run")
                self.assertEqual(self.failures(report), {("lock", "flake.lock")})

    def test_extra_nixpkgs_input_is_reported_without_failing(self):
        self.add_input("nixpkgs-unstable", nixpkgs(NEW_STABLE, "nixos-unstable"))
        self.add_input("nixpkgs-lib", {})
        self.lock["nodes"]["entry"]["inputs"]["nixpkgs-lib"] = ["nixpkgs"]
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["rules"]["extra-nixpkgs"], "notice")
        self.assertEqual(
            [(n["rule"], n["input"]) for n in report["notices"]],
            [("extra-nixpkgs", "nixpkgs-unstable")],
        )

    def test_commit_sibling_passes_with_a_temporary_marker(self):
        node = sibling("petohorvath/nixos-registry")
        node["original"]["rev"] = SOURCE
        self.add_input("registry", node)
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["rules"]["sibling-tag"], "notice")
        self.assertEqual(
            [(n["rule"], n["input"]) for n in report["notices"]],
            [("sibling-tag", "registry")],
        )
        self.assertIn("temporary", report["notices"][0]["message"])
        self.assertEqual(report["siblings"][0]["reference"], "commit")

    def test_branch_sibling_fails(self):
        for ref in [None, "main", "release-1"]:
            with self.subTest(ref=ref):
                self.add_input(
                    "registry", sibling("petohorvath/nixos-registry", ref=ref)
                )
                code, report = self.check()
                self.assertEqual(code, 1, report)
                self.assertEqual(self.failures(report), {("sibling-tag", "registry")})

    def test_unlisted_repos_of_the_family_are_siblings(self):
        self.add_input("nftypes", sibling("PetoHorvath/nix-nftypes", ref="main"))
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertEqual(self.failures(report), {("sibling-tag", "nftypes")})

    def test_git_url_siblings_are_recognized(self):
        self.add_input(
            "registry",
            {
                "inputs": {"nixpkgs": ["nixpkgs"]},
                "locked": {
                    "type": "git",
                    "url": "https://github.com/petohorvath/nixos-registry.git",
                    "rev": SOURCE,
                },
                "original": {
                    "type": "git",
                    "url": "https://github.com/petohorvath/nixos-registry.git",
                    "ref": "refs/tags/v0.3.1",
                },
            },
        )
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["siblings"][0]["reference"], "tag")

    def test_sibling_at_two_revisions_fails(self):
        self.add_input("registry", sibling("petohorvath/nixos-registry", ref="v1.2.0"))
        self.add_input("zones", sibling("petohorvath/nixos-nftzones", ref="v0.1.0"))
        self.add_input(
            "registry_2",
            sibling("petohorvath/nixos-registry", ref="v1.1.0", rev=NEW_STABLE),
        )
        del self.lock["nodes"]["entry"]["inputs"]["registry_2"]
        self.lock["nodes"]["zones"]["inputs"]["registry"] = "registry_2"
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertEqual(
            self.failures(report),
            {("sibling-one-revision", "petohorvath/nixos-registry")},
        )
        self.assertIn("zones/registry", report["issues"][0]["message"])

    def test_sibling_following_the_consumer_passes_once(self):
        self.add_input("registry", sibling("petohorvath/nixos-registry", ref="v1.2.0"))
        self.add_input("zones", sibling("petohorvath/nixos-nftzones", ref="v0.1.0"))
        self.lock["nodes"]["zones"]["inputs"]["registry"] = ["registry"]
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(len(report["siblings"]), 2)

    def test_sibling_that_does_not_follow_root_nixpkgs_fails(self):
        self.add_input("zones", sibling("petohorvath/nixos-nftzones", ref="v0.1.0"))
        self.add_input("zones-nixpkgs", nixpkgs(NEW_STABLE, "nixos-26.05"), "zones")
        self.lock["nodes"]["zones"]["inputs"]["nixpkgs"] = "zones-nixpkgs"
        del self.lock["nodes"]["zones"]["inputs"]["zones-nixpkgs"]
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertEqual(self.failures(report), {("sibling-follows-nixpkgs", "zones")})

    def test_transitive_sibling_is_checked_for_follows_but_not_for_tag(self):
        self.add_input("zones", sibling("petohorvath/nixos-nftzones", ref="v0.1.0"))
        self.add_input(
            "libnet", sibling("petohorvath/nix-libnet", follows=False), "zones"
        )
        self.lock["nodes"]["libnet"]["inputs"] = {"nixpkgs": ["zones", "nixpkgs"]}
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.lock["nodes"]["libnet"]["inputs"] = {"nixpkgs": "other"}
        self.lock["nodes"]["other"] = nixpkgs(NEW_STABLE, "nixos-26.05")
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertEqual(
            self.failures(report), {("sibling-follows-nixpkgs", "zones/libnet")}
        )

    def test_third_party_inputs_and_their_nixpkgs_are_ignored(self):
        self.add_input(
            "hooks",
            {
                "locked": {
                    "type": "github",
                    "owner": "cachix",
                    "repo": "git-hooks.nix",
                },
                "original": {
                    "type": "github",
                    "owner": "cachix",
                    "repo": "git-hooks.nix",
                },
            },
        )
        self.add_input("hooks-nixpkgs", nixpkgs(NEW_STABLE, "nixos-unstable"), "hooks")
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["notices"], [])
        self.assertEqual(report["siblings"], [])

    def test_example_locks_are_ignored(self):
        self.write("examples/basic/flake.lock", "not a lock")
        self.write(
            "dev/flake.lock",
            json.dumps({"version": 7, "root": "r", "nodes": {"r": {}}}),
        )
        code, report = self.check()
        self.assertEqual(code, 0, report)

    def test_policy_repository_as_input_fails(self):
        for parent in ["entry", "zones"]:
            with self.subTest(parent=parent):
                self.setUp()
                self.add_input(
                    "zones", sibling("petohorvath/nixos-nftzones", ref="v0.1.0")
                )
                self.add_input(
                    "policy",
                    sibling("petohorvath/nixos-project-policy", ref="v0.5.0"),
                    parent,
                )
                code, report = self.check()
                self.assertEqual(code, 1, report)
                path = "policy" if parent == "entry" else "zones/policy"
                self.assertEqual(self.failures(report), {("no-policy-input", path)})

    def test_rules_report_every_rule_id(self):
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(
            list(report["rules"]),
            [
                "extra-nixpkgs",
                "formatter",
                "lock",
                "no-policy-input",
                "outputs-empty",
                "outputs-evaluate",
                "outputs-removal",
                "root-nixpkgs",
                "shell",
                "sibling-follows-nixpkgs",
                "sibling-one-revision",
                "sibling-tag",
            ],
        )
        # The fixture repo has no release tag, so the removal comparison skips.
        self.assertEqual(
            {rule for rule, status in report["rules"].items() if status != "pass"},
            {"outputs-removal"},
        )
        self.assertEqual(report["rules"]["outputs-removal"], "not-run")

    def test_check_needs_no_caller_workflow(self):
        (self.root / ".github/workflows/policy.yml").unlink()
        (self.root / "README.md").unlink()
        (self.root / ".envrc").unlink()
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertNotIn("selectionStatus", report)
