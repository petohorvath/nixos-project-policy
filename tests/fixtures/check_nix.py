"""Nix answers for `check`: host system, public outputs, shell, formatter.

A fixture flake describes its public outputs in `outputs.json` beside
`flake.nix`: each output name maps to `{names, empty, failed}` as
tools/outputs.nix reports them, or to a string that Nix prints as an
uncatchable evaluation error. Because the file lives in the repo, a release
tag carries the outputs of its own revision.
"""

import json
from pathlib import Path
import subprocess

from tests.fixtures.process import REAL_RUN
from tools import outputs

MODEL = "outputs.json"


class CheckNix:
    def __init__(self):
        self.host = "x86_64-linux"
        self.failing = []
        self.commands = []

    def __call__(self, command, **kwargs):
        if command[0] != "nix":
            return REAL_RUN(command, **kwargs)
        self.commands.append(command)
        if any(command[: len(prefix)] == prefix for prefix in self.failing):
            raise subprocess.CalledProcessError(1, command)
        if command[-1] == "builtins.currentSystem":
            return subprocess.CompletedProcess(command, 0, self.host)
        if command[:2] == ["nix", "eval"] and outputs.FLAKE_VARIABLE in command[-1]:
            return self.describe(command, kwargs["env"])
        if command[:2] in (["nix", "eval"], ["nix", "develop"]):
            return subprocess.CompletedProcess(command, 0, "")
        raise AssertionError(command)

    def describe(self, command, env):
        flake = Path(env[outputs.FLAKE_VARIABLE].removeprefix("path:"))
        model = flake / MODEL
        described = json.loads(model.read_text()) if model.exists() else {}
        mode = env["NIXOS_PROJECT_POLICY_OUTPUTS_MODE"]
        if mode == "list":
            return subprocess.CompletedProcess(command, 0, json.dumps(list(described)))
        selected = (
            [env["NIXOS_PROJECT_POLICY_OUTPUTS_OUTPUT"]]
            if mode == "evaluate"
            else list(described)
        )
        result = {}
        for name in selected:
            value = described[name]
            if isinstance(value, str):
                return subprocess.CompletedProcess(
                    command, 1, "", f"evaluating\n  error: {value}\n"
                )
            result[name] = {"names": [], "empty": [], "failed": [], **value}
            if mode == "names":
                result[name]["failed"] = []
        return subprocess.CompletedProcess(command, 0, json.dumps(result))
