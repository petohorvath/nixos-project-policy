"""Public-output, development-shell, and formatter rules that `check` applies."""

import json

from tests.fixtures.cases import ProjectTestCase
from tests.fixtures.process import commit, git, isolated_git

HELLO = "packages.x86_64-linux.hello"
TOOL = "packages.x86_64-linux.tool"


class PublicOutputTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(isolated_git(self.root.parent))

    def publish(self, **described):
        self.write("outputs.json", json.dumps(described))

    def release(self, tag, **described):
        self.publish(**described)
        commit(self.root)
        git(self.root, "tag", tag)

    def changelog(self, *headings):
        self.write(
            "CHANGELOG.md",
            "# Changelog\n\n"
            + "".join(f"{heading}\n\n- Entry.\n\n" for heading in headings),
        )

    def check(self):
        commit(self.root)
        return self.run_policy("check", str(self.root))

    def issues(self, report, rule):
        return [issue for issue in report["issues"] if issue["rule"] == rule]

    def test_evaluating_public_outputs_pass(self):
        self.release("v1.0.0", packages={"names": [HELLO]})
        code, report = self.check()
        self.assertEqual(code, 0, report)
        for rule in ["outputs-evaluate", "outputs-empty", "outputs-removal"]:
            self.assertEqual(report["rules"][rule], "pass")
        self.assertEqual(report["release"], {"tag": "v1.0.0", "version": None})

    def test_an_output_that_fails_to_evaluate_fails(self):
        self.publish(packages={"names": [HELLO], "failed": [HELLO]})
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["rules"]["outputs-evaluate"], "fail")
        self.assertEqual(
            [issue["output"] for issue in self.issues(report, "outputs-evaluate")],
            [HELLO],
        )

    def test_an_uncatchable_evaluation_error_fails_its_output(self):
        self.publish(packages={"names": [HELLO]}, lib="attribute 'missing' missing")
        code, report = self.check()
        self.assertEqual(code, 1, report)
        [issue] = self.issues(report, "outputs-evaluate")
        self.assertEqual(issue["output"], "lib")
        self.assertIn("attribute 'missing' missing", issue["message"])

    def test_an_empty_namespace_is_a_notice(self):
        self.publish(
            packages={"names": [HELLO], "empty": ["packages.aarch64-linux"]},
            nixosModules={"empty": ["nixosModules"]},
        )
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["rules"]["outputs-empty"], "notice")
        self.assertEqual(
            sorted(
                notice["output"]
                for notice in report["notices"]
                if notice["rule"] == "outputs-empty"
            ),
            ["nixosModules", "packages.aarch64-linux"],
        )

    def test_without_a_release_tag_the_removal_comparison_is_skipped(self):
        self.publish(packages={"names": [HELLO]})
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["rules"]["outputs-removal"], "not-run")
        self.assertEqual(report["release"]["tag"], None)
        [notice] = [n for n in report["notices"] if n["rule"] == "outputs-removal"]
        self.assertIn("v0.1.0", notice["message"])

    def test_a_removal_with_a_patch_bump_fails(self):
        self.changelog("## [1.2.0] - 2026-09-01")
        self.release("v1.2.0", packages={"names": [HELLO, TOOL]})
        self.changelog("## Unreleased", "## [1.2.1] - 2026-09-20", "## [1.2.0]")
        self.publish(packages={"names": [HELLO]})
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["rules"]["outputs-removal"], "fail")
        self.assertEqual(report["release"], {"tag": "v1.2.0", "version": "1.2.1"})
        [issue] = self.issues(report, "outputs-removal")
        self.assertEqual(issue["output"], TOOL)
        self.assertIn("v1.2.0", issue["message"])

    def test_a_removal_without_a_new_release_heading_fails(self):
        self.changelog("## v1.2.0")
        self.release("v1.2.0", packages={"names": [HELLO, TOOL]})
        self.changelog("## Unreleased", "## v1.2.0")
        self.publish(packages={"names": [HELLO]})
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["rules"]["outputs-removal"], "fail")

    def test_a_removal_with_a_minor_or_major_bump_passes(self):
        for heading in ["## [1.3.0] - 2026-09-20", "## 2.0.0", "## v1.3.0-rc.1"]:
            with self.subTest(heading=heading):
                self.setUp()
                self.release("v1.2.0", packages={"names": [HELLO, TOOL]})
                self.changelog("## Unreleased", heading, "## 1.2.0")
                self.publish(packages={"names": [HELLO]})
                code, report = self.check()
                self.assertEqual(code, 0, report)
                self.assertEqual(report["rules"]["outputs-removal"], "pass")

    def test_an_added_output_passes(self):
        self.release("v1.2.0", packages={"names": [HELLO]})
        self.publish(packages={"names": [HELLO, TOOL]}, lib={"names": ["lib.f"]})
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["rules"]["outputs-removal"], "pass")

    def test_the_comparison_uses_the_highest_tag_reachable_from_head(self):
        self.release("v1.2.0", packages={"names": [HELLO, TOOL]})
        self.release("v1.10.0", packages={"names": [HELLO]})
        git(self.root, "checkout", "--quiet", "-b", "other", "v1.2.0")
        # Unreachable from HEAD; it would pass the removal of `tool`.
        self.release("v9.0.0", packages={"names": [TOOL]})
        git(self.root, "checkout", "--quiet", "-")
        self.release("v1.9.0", packages={"names": [HELLO]})
        # Not SemVer release tags.
        git(self.root, "tag", "v2.0")
        git(self.root, "tag", "v3.0.0-rc.1")
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["release"]["tag"], "v1.10.0")

    def test_a_failing_output_does_not_count_as_removed(self):
        self.release("v1.2.0", packages={"names": [HELLO]})
        self.publish(packages="infinite recursion encountered")
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["rules"]["outputs-evaluate"], "fail")
        self.assertEqual(report["rules"]["outputs-removal"], "pass")


class ShellAndFormatterTests(ProjectTestCase):
    def test_check_probes_the_default_shell_and_the_formatter(self):
        code, report = self.run_policy("check", str(self.root))
        self.assertEqual(code, 0, report)
        self.assertEqual(report["rules"]["shell"], "pass")
        self.assertEqual(report["rules"]["formatter"], "pass")
        self.assertIn(
            ["nix", "develop"],
            [command[:2] for command in self.check_nix.commands],
        )

    def test_a_failing_shell_or_formatter_fails_check(self):
        root = str(self.root.resolve())
        for rule, failing in [
            ("shell", ["nix", "develop"]),
            (
                "shell",
                [
                    "nix",
                    "eval",
                    "--no-update-lock-file",
                    "--raw",
                    f"path:{root}#devShells.x86_64-linux.default.drvPath",
                ],
            ),
            (
                "formatter",
                [
                    "nix",
                    "eval",
                    "--no-update-lock-file",
                    "--raw",
                    f"path:{root}#formatter.x86_64-linux.drvPath",
                ],
            ),
        ]:
            with self.subTest(failing=failing):
                self.check_nix.failing = [failing]
                code, report = self.run_policy("check", str(self.root))
                self.assertEqual(code, 1, report)
                self.assertEqual(
                    {r for r, status in report["rules"].items() if status == "fail"},
                    {rule},
                )
                [issue] = report["issues"]
                self.assertEqual(issue["rule"], rule)

    def test_the_shell_option_is_removed(self):
        with self.assertRaises(SystemExit):
            self.run_policy("check", str(self.root), "--shell")

    def test_the_shell_command_is_removed(self):
        with self.assertRaises(SystemExit):
            self.run_policy("shell", str(self.root))

    def test_a_host_without_nix_is_an_error(self):
        self.check_nix.failing = [["nix", "eval", "--raw", "--impure"]]
        code, report = self.run_policy("check", str(self.root))
        self.assertEqual(code, 2, report)
        self.assertEqual(report["status"], "error")
