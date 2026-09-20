"""Controlled Nix metadata and command outcomes for compatibility scenarios."""

import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from tests.fixtures.data import CHECKER, SOURCE, lockfile
from tests.fixtures.projects import ProjectFixture
from tools import policy


class CompatibilityFixture(ProjectFixture):
    def prepare(self):
        super().prepare()
        self.host = "x86_64-linux"
        self.metadata = {"locks": lockfile()}
        self.commands = []
        self.check_returncode = 0
        self.dirty = ""
        self.host_checks = ["behavior"]
        self.fail_stage = None
        self.command_error = None
        self.committed_lock = (self.root / "flake.lock").read_bytes()
        self.resources.enter_context(
            patch.object(policy.subprocess, "check_output", side_effect=self.output)
        )
        self.resources.enter_context(
            patch.object(policy.subprocess, "run", side_effect=self.run_command)
        )

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
