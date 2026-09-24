"""Controlled release and Nix process adapters for candidate coordination."""

import base64
import contextlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from tests.fixtures.cli import invoke
from tests.fixtures.data import CHECKER, POLICY_REPO, RELEASE
from tests.fixtures.process import REAL_OUTPUT, REAL_RUN
from tools import candidates, policy, records, releases


def published(path):
    if "/releases/tags/" in path:
        return {
            "tag_name": path.rsplit("/", 1)[1],
            "immutable": True,
            "draft": False,
            "prerelease": False,
        }
    return {"object": {"type": "commit", "sha": CHECKER}}


class Services:
    def __init__(self, root):
        self.root = root
        self.host = "x86_64-linux"
        self.commands = []
        self.failure = None
        self.mutation = None
        self.report_mutation = None
        self.additional = "success"

    @contextlib.contextmanager
    def installed(self, *, coordinator=CHECKER):
        def execute(command, **kwargs):
            if command[:2] == ["nix", "run"]:
                with patch.object(policy, "PACKAGED_REVISION", CHECKER, create=True):
                    return self.run(command, **kwargs)
            return self.run(command, **kwargs)

        with (
            patch.object(releases, "public_get", side_effect=self.lookup),
            patch.object(subprocess, "run", side_effect=execute),
            patch.object(subprocess, "check_output", side_effect=self.output),
            patch.object(policy, "PACKAGED_REVISION", coordinator, create=True),
        ):
            yield self

    def lookup(self, path):
        if "/git/commits/" in path:
            return {"sha": policy.git_revision(self.root)}
        if "/contents/" in path:
            requirements = records.read_json(
                policy.SOURCE_ROOT / "policy/requirements.json"
            )
            if declarations_version(self.root) != RELEASE:
                requirements["ci"]["architectureChecks"] = [
                    "Compliance",
                    "Formatting and lint",
                    "Project tests",
                ]
            if declarations_version(self.root) == "v0.1.1":
                requirements.pop("ci")
            return {
                "encoding": "base64",
                "content": base64.b64encode(json.dumps(requirements).encode()).decode(),
            }
        if "/check-runs?" in path:
            return {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "Member / Extra",
                        "head_sha": policy.git_revision(self.root),
                        "status": "completed",
                        "conclusion": self.additional,
                    }
                ]
            }
        return published(path)

    def output(self, command, **kwargs):
        if command[0] == "nix":
            return self.host if kwargs.get("text") else self.host.encode()
        return REAL_OUTPUT(command, **kwargs)

    def run(self, command, **kwargs):
        if command[0] != "nix":
            return REAL_RUN(command, **kwargs)
        command = list(map(str, command))
        self.commands.append(command)
        if command[1] == "run":
            arguments = command[command.index("--") + 1 :]
            operation = arguments[2]
            if declarations_version(self.root) == RELEASE:
                code, report = invoke(*arguments)
            else:
                code, report = self.released_report(arguments)
            if self.report_mutation:
                self.report_mutation(operation, report)
            return subprocess.CompletedProcess(command, code, json.dumps(report), "")
        status = 0
        output = ""
        if command[1:3] == ["flake", "metadata"]:
            graph = records.read_json(self.root / "flake.lock")
            selected = policy.selected_nixpkgs(policy.LockGraph(graph))
            if "--override-input" in command:
                graph["nodes"][selected]["locked"]["rev"] = command[-1].rsplit("/", 1)[
                    1
                ]
            output = json.dumps({"locks": graph})
        elif command[1] == "eval":
            output = self.host if "--impure" in command else '["behavior"]'
            if (
                self.failure == "empty-host-checks"
                and "--apply" in command
                and "--override-input" not in command
            ):
                output = "[]"
        elif command[1:3] == ["flake", "check"]:
            if self.mutation:
                self.mutation(command)
            if self.failure == "tests" and "--override-input" not in command:
                status = 1
            if self.failure == "compatibility" and "--override-input" in command:
                status = 1
        if status and kwargs.get("check"):
            raise subprocess.CalledProcessError(status, command)
        return subprocess.CompletedProcess(
            command, status, output, "controlled failure" if status else ""
        )

    def released_report(self, arguments):
        """Supply process reports; packaged_transition executes the older checkers."""
        record_root = Path(arguments[1])
        config, pins = records.load(record_root)
        version = declarations_version(self.root)
        operation = arguments[2]
        name = (
            arguments[arguments.index("--project") + 1]
            if "--project" in arguments
            else self.root.name
        )
        member = config["projects"][name]
        report = {
            "status": "pass",
            "issues": [],
            "project": name,
            "policyVersion": version,
            "revision": policy.git_revision(self.root),
            "checkerVersion": version,
            "policyRecordsRevision": policy.git_revision(record_root),
            "policyRecordsDigest": records.digest(config, pins, legacy=True),
        }
        if operation == "ci":
            legacy_ci = {
                **config["ci"],
                "architectureChecks": [
                    "Compliance",
                    "Formatting and lint",
                    "Project tests",
                ],
            }
            report.update(status="planned", **policy.ci_plan(member, legacy_ci))
        elif operation in {"check", "compatibility"}:
            report.update(
                status="candidate-ready",
                candidateBatch=arguments[arguments.index("--batch") + 1],
            )
        elif operation == "vm":
            report["targets"] = member["vmTargets"]
        if operation == "compatibility":
            channel = arguments[arguments.index("--channel") + 1]
            batch = next(
                batch
                for batch in pins["batches"]
                if batch["id"] == report["candidateBatch"]
            )
            revision = batch["pins"][channel]
            report.update(
                status="candidate-pass",
                system=self.host,
                channel=channel,
                pinStatus="candidate",
                expectedRevision=revision,
                resolvedRevision=revision,
                checkerRevision=CHECKER,
                sourceDirty=False,
                sourceDigest=candidates.digest(policy.fingerprints(self.root)),
                checks=["behavior"],
                commands=[
                    {
                        "command": [
                            "nix",
                            "flake",
                            "check",
                            str(self.root),
                            "--print-build-logs",
                            "--override-input",
                            "nixpkgs",
                            f"github:NixOS/nixpkgs/{revision}",
                        ],
                        "returncode": 0,
                    }
                ],
            )
        return 0, report


def declarations_version(root):
    return policy.declarations.discover(root, POLICY_REPO)[3]


class BatchServices(Services):
    def __init__(self, roots):
        super().__init__(roots["example"])
        self.roots = roots
        self.failed_member = None

    def lookup(self, path):
        if "/git/commits/" in path:
            name = path.split("/")[2]
            self.root = self.roots[name]
        return super().lookup(path)

    def run(self, command, **kwargs):
        previous = self.root
        command = list(map(str, command))
        if command[0] == "nix":
            if command[1] == "run":
                arguments = command[command.index("--") + 1 :]
                if not arguments[3].startswith("--"):
                    self.root = Path(arguments[3])
                elif "--project" in arguments:
                    self.root = self.roots[arguments[arguments.index("--project") + 1]]
            elif command[1:3] in (["flake", "metadata"], ["flake", "check"]):
                self.root = Path(command[3])
        failure = self.failure
        if self.failed_member and self.root.name != self.failed_member:
            self.failure = None
        try:
            return super().run(command, **kwargs)
        finally:
            self.root, self.failure = previous, failure
