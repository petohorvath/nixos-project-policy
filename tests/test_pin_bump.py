"""Pin-bump proposals, their test matrix, and the patch release they create."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tests.fixtures.data import NEW_PAIR, PAIR
from tests.fixtures.process import commit, git, initialize, isolated_git
from tools import pin_bump


PINS = {"stableBranch": "nixos-26.05", **PAIR}
CHANGELOG = """# Changelog

## Unreleased

- Fix a checker bug.

## 0.5.0

- Initial series.
"""


def legacy_pins(pair):
    return {
        "schemaVersion": 1,
        "approved": pair,
        "stableBranch": "nixos-26.05",
    }


class PinBumpTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name)
        self.enterContext(isolated_git(workspace))
        self.root = workspace / "policy"
        initialize(self.root)
        self.write("VERSION", "0.5.0\n")
        self.write("CHANGELOG.md", CHANGELOG)
        self.write("data/pins.json", json.dumps(PINS, indent=2, sort_keys=True) + "\n")
        self.write("data/repos.json", json.dumps({"repos": ["owner/first"]}) + "\n")
        self.write("policy/pins.json", json.dumps(legacy_pins(PAIR), indent=2) + "\n")
        commit(self.root)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def read(self, name):
        return (self.root / name).read_text()

    def clear_tags(self):
        tags = git(self.root, "tag", "--list").split()
        if tags:
            git(self.root, "tag", "--delete", *tags)

    def tag(self, *names):
        for name in names:
            git(self.root, "tag", name)

    def run_helper(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = pin_bump.main([*arguments, "--root", str(self.root)])
        return code, json.loads(output.getvalue() or errors.getvalue())


class ProposeTests(PinBumpTestCase):
    def propose(self, pair=NEW_PAIR):
        return self.run_helper(
            "propose", "--stable", pair["stable"], "--unstable", pair["unstable"]
        )

    def test_new_pins_are_written_with_the_next_patch_version(self):
        self.tag("v0.4.0", "v0.5.0")
        code, report = self.propose()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "proposed")
        self.assertEqual(report["version"], "0.5.1")
        self.assertEqual(report["tag"], "v0.5.1")
        self.assertEqual(report["seriesTag"], "v0.5")
        self.assertEqual(report["pins"], {"stableBranch": "nixos-26.05", **NEW_PAIR})
        self.assertEqual(
            json.loads(self.read("data/pins.json")),
            {"stableBranch": "nixos-26.05", **NEW_PAIR},
        )
        self.assertEqual(
            self.read("policy/pins.json"),
            json.dumps(legacy_pins(NEW_PAIR), indent=2) + "\n",
        )
        self.assertEqual(self.read("VERSION"), "0.5.1\n")

    def test_changelog_releases_unreleased_entries_with_the_pin_update(self):
        self.tag("v0.5.0")
        code, report = self.propose()
        self.assertEqual(code, 0, report)
        self.assertEqual(
            self.read("CHANGELOG.md"),
            "# Changelog\n\n## Unreleased\n\n## 0.5.1\n\n"
            f"- Update the stable pin (`nixos-26.05`) to `{NEW_PAIR['stable']}` "
            f"and the unstable pin to `{NEW_PAIR['unstable']}`.\n"
            "- Fix a checker bug.\n\n## 0.5.0\n\n- Initial series.\n",
        )

    def test_highest_patch_tag_in_the_series_sets_the_next_version(self):
        self.write("VERSION", "0.5.2\n")
        commit(self.root)
        self.tag("v0.5.0", "v0.5.2", "v0.5.10", "v0.6.0-rc1", "v0.4.99", "v0.5")
        code, report = self.propose()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["version"], "0.5.11")

    def test_unchanged_pins_write_nothing(self):
        self.tag("v0.5.0")
        before = {
            name: self.read(name)
            for name in [
                "VERSION",
                "CHANGELOG.md",
                "data/pins.json",
                "policy/pins.json",
            ]
        }
        code, report = self.propose(PAIR)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "unchanged")
        for name, text in before.items():
            self.assertEqual(self.read(name), text, name)

    def test_untagged_version_is_an_error(self):
        self.write("VERSION", "0.5.1\n")
        commit(self.root)
        for name, tags in {
            "series without a release": ["v0.4.0"],
            "pending patch release": ["v0.4.0", "v0.5.0"],
        }.items():
            with self.subTest(name):
                self.tag(*tags)
                code, report = self.propose()
                self.assertEqual(code, 2, report)
                self.assertEqual(report["status"], "error")
                self.assertIn("v0.5.1", report["error"])
                self.clear_tags()
        self.assertEqual(self.read("VERSION"), "0.5.1\n")

    def test_malformed_revisions_are_an_error(self):
        self.tag("v0.5.0")
        for pair in [
            {"stable": "abc", "unstable": NEW_PAIR["unstable"]},
            {"stable": NEW_PAIR["stable"], "unstable": "D" * 40},
        ]:
            with self.subTest(pair=pair):
                code, report = self.propose(pair)
                self.assertEqual(code, 2, report)
                self.assertEqual(json.loads(self.read("data/pins.json")), PINS)


class MatrixTests(PinBumpTestCase):
    def test_every_listed_repo_runs_both_pins_on_each_system_and_vm_tests(self):
        repos = ["owner/first", "owner/second"]
        self.write("data/repos.json", json.dumps({"repos": repos}))
        code, report = self.run_helper("matrix")
        self.assertEqual(code, 0, report)
        expected = [
            {
                "repository": repository,
                "task": task,
                "system": system,
                "runner": runner,
                "name": f"Tests ({task}, {system})",
            }
            for repository in repos
            for task in ["stable", "unstable"]
            for system, runner in [
                ("x86_64-linux", "ubuntu-24.04"),
                ("aarch64-linux", "ubuntu-24.04-arm"),
            ]
        ] + [
            {
                "repository": repository,
                "task": "vm",
                "system": "x86_64-linux",
                "runner": "ubuntu-24.04",
                "name": "VM tests",
            }
            for repository in repos
        ]
        self.assertCountEqual(report["include"], expected)

    def test_no_listed_repos_give_an_empty_matrix(self):
        self.write("data/repos.json", json.dumps({"repos": []}))
        code, report = self.run_helper("matrix")
        self.assertEqual(code, 0, report)
        self.assertEqual(report, {"include": []})

    def test_malformed_repo_list_is_an_error(self):
        self.write("data/repos.json", json.dumps({"repos": ["not-a-repo"]}))
        code, report = self.run_helper("matrix")
        self.assertEqual(code, 2, report)


class ReleaseTests(PinBumpTestCase):
    def merge_bump(self, version):
        self.write("VERSION", f"{version}\n")
        return commit(self.root)

    def test_merged_bump_is_the_next_patch_release(self):
        self.tag("v0.5.0", "v0.5")
        revision = self.merge_bump("0.5.1")
        code, report = self.run_helper("release")
        self.assertEqual(code, 0, report)
        self.assertEqual(
            report,
            {
                "status": "release",
                "version": "0.5.1",
                "tag": "v0.5.1",
                "seriesTag": "v0.5",
                "revision": revision,
            },
        )

    def test_existing_tag_on_the_merged_commit_is_reported_as_released(self):
        self.tag("v0.5.0")
        self.merge_bump("0.5.1")
        self.tag("v0.5.1")
        code, report = self.run_helper("release")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "released")
        self.assertEqual(report["tag"], "v0.5.1")

    def test_rerun_of_an_older_release_does_not_move_the_series_tag_back(self):
        self.tag("v0.5.0")
        self.merge_bump("0.5.1")
        self.tag("v0.5.1", "v0.5.2")
        code, report = self.run_helper("release")
        self.assertEqual(code, 2, report)
        self.assertEqual(report["status"], "error")

    def test_version_tags_are_never_moved_or_skipped(self):
        cases = {
            "tag exists on another commit": (["v0.5.0", "v0.5.1"], "0.5.1"),
            "version skips a patch": (["v0.5.0"], "0.5.2"),
            "version is already released": (["v0.5.0", "v0.5.1"], "0.5.0"),
            "series has no release": (["v0.4.0"], "0.5.1"),
            "minor release pending": (["v0.5.0"], "0.6.0"),
        }
        for name, (tags, version) in cases.items():
            with self.subTest(name):
                self.clear_tags()
                self.tag(*tags)
                self.merge_bump(version)
                code, report = self.run_helper("release")
                self.assertEqual(code, 2, report)
                self.assertEqual(report["status"], "error")


if __name__ == "__main__":
    unittest.main()
