"""Controlled Nix metadata and command outcomes for `test` scenarios."""

import copy
import json
import subprocess
from unittest.mock import patch

from tests.fixtures.data import nixpkgs
from tests.fixtures.repos import RepoFixture
from tools import policy


class NixRunFixture(RepoFixture):
    """A repo whose Nix commands are answered by knobs instead of Nix.

    `metadata` is None to echo the repo's lock with any nixpkgs override
    applied, or a fixed `locks` graph to simulate what Nix resolved.
    """

    def prepare(self):
        super().prepare()
        self.host = "x86_64-linux"
        self.metadata = None
        self.checks = ["behavior"]
        self.commands = []
        self.fail_stage = None
        self.command_error = None
        self.resources.enter_context(
            patch.object(policy.subprocess, "run", side_effect=self.run_command)
        )

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if self.command_error:
            raise self.command_error
        if self.fail_stage and command[: len(self.fail_stage)] == self.fail_stage:
            return subprocess.CompletedProcess(command, 1, "")
        if command[:3] == ["nix", "flake", "metadata"]:
            return subprocess.CompletedProcess(
                command, 0, json.dumps(self.resolve(command))
            )
        if command[:2] == ["nix", "eval"]:
            output = self.host if "--impure" in command else json.dumps(self.checks)
            return subprocess.CompletedProcess(command, 0, output)
        if command[:3] == ["nix", "flake", "check"]:
            return subprocess.CompletedProcess(command, 0)
        raise AssertionError(command)

    def resolve(self, command):
        if self.metadata is not None:
            return {"locks": self.metadata}
        graph = copy.deepcopy(json.loads((self.root / "flake.lock").read_text()))
        if "--override-input" in command:
            position = command.index("--override-input")
            name, reference = command[position + 1 : position + 3]
            # Nix locks the override as a new node, even for a follows input.
            graph["nodes"]["override"] = nixpkgs(reference.rsplit("/", 1)[1], None)
            graph["nodes"][graph["root"]]["inputs"][name] = "override"
        return {"locks": graph}

    def nix(self, prefix):
        return [
            command for command in self.commands if command[: len(prefix)] == prefix
        ]

    def run_test(self, nixpkgs="locked", *options):
        return self.run_policy("test", str(self.root), "--nixpkgs", nixpkgs, *options)
