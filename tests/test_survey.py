"""`survey WORKSPACE`: `check` on every repo directory in a workspace."""

import contextlib
import io
import json
from unittest.mock import patch

from tests.fixtures.cases import RepoTestCase
from tests.fixtures.data import lockfile
from tests.fixtures.process import commit, git, initialize, isolated_git
from tools import data, policy


class SurveyTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.repos[:] = ["owner/alpha", "owner/beta"]
        self.workspace = self.root.parent / "workspace"
        self.workspace.mkdir()
        self.enterContext(isolated_git(self.root.parent))

    def repo(self, name, *, origin=None, flake=True, lock=None, **described):
        root = self.workspace / name
        initialize(root)
        if flake:
            (root / "flake.nix").write_text("{}")
            (root / "flake.lock").write_text(json.dumps(lock or lockfile()))
            (root / "outputs.json").write_text(json.dumps(described))
        else:
            (root / "README.md").write_text("# Notes\n")
        if origin is not None:
            git(root, "remote", "add", "origin", origin)
        commit(root)
        return root

    def survey(self):
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.object(data, "DATA_ROOT", self.write_data()),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = policy.main(["survey", str(self.workspace)])
        self.table = errors.getvalue()
        return code, json.loads(output.getvalue())

    def entries(self, report):
        return {entry["name"]: entry for entry in report["repos"]}

    def test_passing_repos_pass(self):
        self.repo("alpha", origin="https://github.com/owner/alpha.git")
        self.repo("beta", origin="git@github.com:Owner/Beta.git")
        code, report = self.survey()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        entries = self.entries(report)
        self.assertEqual(sorted(entries), ["alpha", "beta"])
        for name in ["alpha", "beta"]:
            self.assertEqual(entries[name]["status"], "pass")
            self.assertEqual(entries[name]["check"]["status"], "pass")
        self.assertEqual(entries["beta"]["repository"], "owner/beta")

    def test_a_failing_repo_fails_the_survey_and_the_others_are_still_checked(self):
        broken = lockfile()
        del broken["nodes"]["entry"]["inputs"]["nixpkgs"]
        self.repo("alpha", origin="https://github.com/owner/alpha", lock=broken)
        self.repo("beta", origin="https://github.com/owner/beta")
        code, report = self.survey()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["status"], "fail")
        entries = self.entries(report)
        self.assertEqual(entries["alpha"]["status"], "fail")
        self.assertEqual(entries["alpha"]["check"]["rules"]["root-nixpkgs"], "fail")
        self.assertEqual(entries["beta"]["status"], "pass")

    def test_a_repo_whose_check_raises_is_an_error_and_the_survey_continues(self):
        self.repo(
            "alpha",
            origin="https://github.com/owner/alpha",
            packages={"names": "malformed"},
        )
        self.repo("beta", origin="https://github.com/owner/beta")
        code, report = self.survey()
        self.assertEqual(code, 1, report)
        entries = self.entries(report)
        self.assertEqual(entries["alpha"]["status"], "error")
        self.assertIn("malformed", entries["alpha"]["error"])
        self.assertNotIn("check", entries["alpha"])
        self.assertEqual(entries["beta"]["status"], "pass")

    def test_a_directory_that_is_not_a_flake_is_reported_without_failing(self):
        self.repo("alpha", origin="https://github.com/owner/alpha")
        self.repo("notes", flake=False)
        code, report = self.survey()
        self.assertEqual(code, 0, report)
        notes = self.entries(report)["notes"]
        self.assertEqual(notes["status"], "not-a-flake")
        self.assertNotIn("check", notes)

    def test_listed_and_unlisted_repos_are_marked(self):
        self.repo("alpha", origin="https://github.com/owner/alpha")
        self.repo("renamed-beta", origin="ssh://git@github.com/owner/beta.git")
        self.repo("gamma", origin="https://github.com/owner/gamma")
        self.repo("beta-fork", origin="https://github.com/other/beta")
        code, report = self.survey()
        self.assertEqual(code, 0, report)
        listed = {name: entry["listed"] for name, entry in self.entries(report).items()}
        self.assertEqual(
            listed,
            {"alpha": True, "renamed-beta": True, "gamma": False, "beta-fork": False},
        )

    def test_without_a_github_origin_the_directory_name_decides_listing(self):
        self.repo("alpha")
        self.repo("gamma", origin="https://example.org/owner/alpha.git")
        code, report = self.survey()
        entries = self.entries(report)
        self.assertEqual(entries["alpha"]["repository"], None)
        self.assertTrue(entries["alpha"]["listed"])
        self.assertFalse(entries["gamma"]["listed"])

    def test_hidden_directories_and_plain_directories_are_skipped(self):
        self.repo("alpha", origin="https://github.com/owner/alpha")
        self.repo(".worktrees")
        (self.workspace / "plain").mkdir()
        (self.workspace / "file.md").write_text("text")
        code, report = self.survey()
        self.assertEqual(code, 0, report)
        self.assertEqual(list(self.entries(report)), ["alpha"])

    def test_the_table_has_one_row_per_repo_and_one_column_per_rule(self):
        broken = lockfile()
        del broken["nodes"]["entry"]["inputs"]["nixpkgs"]
        self.repo("alpha", origin="https://github.com/owner/alpha")
        self.repo("gamma", origin="https://github.com/owner/gamma", lock=broken)
        self.repo("notes", flake=False)
        code, report = self.survey()
        self.assertEqual(report["rules"], list(policy.CHECK_RULES))
        self.assertEqual(len(report["rules"]), 12)
        self.assertEqual(report["table"], self.table.rstrip("\n"))
        header, rule, *rows = report["table"].splitlines()
        self.assertEqual(
            header.split(), ["repo", "listed", "status", *policy.CHECK_RULES]
        )
        self.assertEqual(set(rule), {"-", " "})
        cells = {row.split()[0]: row.split() for row in rows}
        self.assertEqual(sorted(cells), ["alpha", "gamma", "notes"])
        self.assertEqual(cells["alpha"][1:3], ["yes", "pass"])
        self.assertEqual(cells["gamma"][1:3], ["no", "fail"])
        root_column = 3 + policy.CHECK_RULES.index("root-nixpkgs")
        self.assertEqual(cells["gamma"][root_column], "fail")
        self.assertEqual(cells["notes"][2:], ["not-a-flake", *["-"] * 12])

    def test_a_missing_workspace_cannot_be_processed(self):
        self.workspace.rmdir()
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.object(data, "DATA_ROOT", self.write_data()),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = policy.main(["survey", str(self.workspace)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(errors.getvalue())["status"], "error")
